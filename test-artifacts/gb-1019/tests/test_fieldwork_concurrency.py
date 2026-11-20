"""真实多线程并发：同一名额并发回报，至多一份有效观察落库。"""
import threading

from src.models import FieldSlot


def test_concurrent_reports_same_slot_only_one_wins(client, auth_headers, make_plan, db):
    plan = make_plan(key="thread-1")
    slot = plan["slots"][0]
    pid = plan["id"]
    headers = auth_headers("analyst")
    results = []

    def worker(key, outcome):
        # 每个线程独立 client，避免共享连接
        from fastapi.testclient import TestClient
        from src.main import app
        local = TestClient(app)
        resp = local.post(
            f"/fieldwork/plans/{pid}/observations", headers=headers,
            json={"slot_id": slot["slot_id"], "observation_key": key, "outcome": outcome})
        results.append((key, resp.status_code, resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}))

    threads = [
        threading.Thread(target=worker, args=("tk-1", "qualified")),
        threading.Thread(target=worker, args=("tk-2", "unqualified")),
        threading.Thread(target=worker, args=("tk-3", "refused")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 恰好一份 200 生效，其余因名额已结清被拒（409）
    ok = [r for r in results if r[1] == 200]
    rejected = [r for r in results if r[1] == 409]
    assert len(ok) == 1
    assert len(rejected) == 2
    db.expire_all()
    row = db.query(FieldSlot).filter_by(id=slot["slot_id"]).one()
    assert row.state == "settled"
    assert row.outcome in ("qualified", "unqualified", "refused")
    assert row.observation_key == ok[0][0]


def test_concurrent_same_key_replay_both_accepted_but_single_effect(client, auth_headers, make_plan, db):
    """相同观察键并发重放：可多次 200，但只产生一份有效观察（幂等）。"""
    plan = make_plan(key="thread-2")
    slot = plan["slots"][0]
    pid = plan["id"]
    headers = auth_headers("analyst")
    statuses = []

    def worker():
        from fastapi.testclient import TestClient
        from src.main import app
        local = TestClient(app)
        resp = local.post(
            f"/fieldwork/plans/{pid}/observations", headers=headers,
            json={"slot_id": slot["slot_id"], "observation_key": "same-obs", "outcome": "qualified"})
        statuses.append(resp.status_code)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 全部 200（首个生效，其余命中幂等 reused）
    assert statuses == [200] * 4
    db.expire_all()
    row = db.query(FieldSlot).filter_by(id=slot["slot_id"]).one()
    assert row.state == "settled" and row.outcome == "qualified"
    assert row.observation_key == "same-obs"


def test_concurrent_out_of_scope_only_one_promotion(client, auth_headers, make_plan, db):
    """并发对同一名额发起不在范围：候补只被顶上一次，其余幂等或冲突。"""
    plan = make_plan(key="thread-oos")
    slot = plan["slots"][0]
    pid = plan["id"]
    headers = auth_headers("analyst")
    outcomes = []

    def worker(key):
        from fastapi.testclient import TestClient
        from src.main import app
        local = TestClient(app)
        resp = local.post(
            f"/fieldwork/plans/{pid}/observations", headers=headers,
            json={"slot_id": slot["slot_id"], "observation_key": key, "outcome": "out_of_scope"})
        outcomes.append((key, resp.status_code))

    threads = [threading.Thread(target=worker, args=(f"oos-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 至少一次成功递补；不应出现 500
    assert all(code in (200, 409) for _, code in outcomes)
    db.expire_all()
    row = db.query(FieldSlot).filter_by(id=slot["slot_id"]).one()
    # 只发生一次顺位递补：replaced_points 恰好含原正选点
    assert row.state == "replaced"
    assert len(row.replaced_points) == 1
    assert row.replaced_points[0] == slot["point_code"]
