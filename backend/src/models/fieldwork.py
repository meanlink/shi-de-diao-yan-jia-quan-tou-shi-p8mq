from sqlalchemy import String, JSON, Double, Integer, ForeignKey, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class SamplingPlan(Base):
    """按区域从服务点名册抽出的两队派样计划：正选+候补，两队点位不重叠。"""
    __tablename__ = 'sampling_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    # 业务幂等键：同一派样请求重试只取同一抽签结果，不另起名单
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    seed: Mapped[int] = mapped_column(Integer)
    # 每队每区域的正选名额与候补名额，结构 {zone: {"primary": n, "alternate": m}}
    quota: Mapped[dict] = mapped_column(JSON)
    # 抽签结果（含每队每区域有序正选/候补点），候补递补沿此顺位推进
    draw: Mapped[dict] = mapped_column(JSON, default=dict)
    roster: Mapped[list] = mapped_column(JSON)
    all_settled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Double)


class FieldSlot(Base):
    """一个名额（队×区域×序号）。观察沿正选->候补顺位推进，拒访封账不递补。"""
    __tablename__ = 'field_slots'
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('sampling_plans.id'), index=True)
    team: Mapped[str] = mapped_column(String(20))
    zone: Mapped[str] = mapped_column(String(80))
    # 该队该区域内的名额序号（正选在前、候补在后）
    slot_index: Mapped[int] = mapped_column(Integer)
    is_alternate: Mapped[bool] = mapped_column(Boolean, default=False)
    # 名额当前持有/最近持有的服务点编号
    point_code: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(30), default='primary_pending')
    # 正选点（用于拒访后仍追溯应访数）
    primary_point_code: Mapped[str] = mapped_column(String(60), default='')
    # 有效观察：qualified / unqualified / refused
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 观察幂等键，保证并发回报同一名额至多收下一份有效观察
    observation_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # 递补缘由（如 out_of_scope）及被换下的点，便于工作台展示
    replace_reason: Mapped[str] = mapped_column(String(40), default='')
    replaced_points: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (
        UniqueConstraint('plan_id', 'team', 'zone', 'slot_index', name='uq_slot_quota'),
        # 同一观察键在同一计划内只能生效一次
        Index('ix_slot_observation', 'plan_id', 'observation_key', unique=True),
    )


class FieldBatch(Base):
    """名额全部结清后发布的不可变分析批次；发布重试不重复出批。"""
    __tablename__ = 'field_batches'
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('sampling_plans.id'), unique=True)
    checksum: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Double)
