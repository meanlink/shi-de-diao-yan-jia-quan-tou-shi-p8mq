"""隔离测试基座：独立 SQLite 库，覆盖 get_db，不触碰宿主 Postgres/Redis 数据。"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND = "/workspace/backend"
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, ".pydeps"))

_tmp = tempfile.mkdtemp(prefix="fieldwork-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ.setdefault("JWT_SECRET", "test-secret-fieldwork")
os.environ.setdefault("ARTIFACT_DIR", _tmp)

from src.config.database import Base, get_db  # noqa: E402
from src.main import app  # noqa: E402
from src.models import User  # noqa: E402
from src.utils.security import hash_password, token_for  # noqa: E402

engine = create_engine(
    os.environ["DATABASE_URL"], connect_args={"check_same_thread": False}
)
TestingSession = sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture()
def db():
    session = TestingSession()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client():
    # 不使用 with 触发 lifespan（避免 migrate/seed 与 Redis 依赖）
    return TestClient(app)


def _ensure_user(db, role):
    email = f"{role}@demo.local"
    user = db.query(User).filter_by(email=email).one_or_none()
    if not user:
        user = User(email=email, name=f"测试-{role}", role=role,
                    password=hash_password("Demo123!"))
        db.add(user)
        db.commit()
    return user


@pytest.fixture()
def auth_headers(db):
    def _make(role="admin"):
        user = _ensure_user(db, role)
        token = token_for(user)
        return {"Authorization": f"Bearer {token}"}
    return _make


def roster(points_per_zone=8):
    """构造中心区/外围区各若干点的名册。"""
    rows = []
    for zone, prefix in (("中心区", "C"), ("外围区", "W")):
        for i in range(points_per_zone):
            code = f"{prefix}{i+1:03d}"
            rows.append({"code": code, "name": f"{zone}服务点{i+1}", "zone": zone})
    return rows


QUOTA = {"中心区": {"primary": 2, "alternate": 1},
         "外围区": {"primary": 2, "alternate": 1}}


@pytest.fixture()
def roster_data():
    return roster


@pytest.fixture()
def make_plan(client, auth_headers):
    def _make(key="draw-key-1", seed=1019, quota=None, points=8, name="南岭首轮派样"):
        resp = client.post(
            "/fieldwork/plans",
            headers=auth_headers("admin"),
            json={"name": name, "idempotency_key": key, "seed": seed,
                  "quota": quota or QUOTA, "roster": roster(points)},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()
    return _make
