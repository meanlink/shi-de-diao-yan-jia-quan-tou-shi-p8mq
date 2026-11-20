from pydantic import BaseModel, Field


class RosterPoint(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(default='', max_length=120)
    zone: str = Field(min_length=1, max_length=80)


class QuotaCell(BaseModel):
    primary: int = Field(ge=1, le=100000)
    alternate: int = Field(default=0, ge=0, le=100000)


class PlanCreateInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=120)
    seed: int
    # 区域 -> 正选/候补名额
    quota: dict[str, QuotaCell]
    roster: list[RosterPoint] = Field(min_length=1, max_length=100000)


class ObservationInput(BaseModel):
    slot_id: int
    observation_key: str = Field(min_length=1, max_length=120)
    # qualified 合格 / unqualified 不合格 / refused 拒访 / out_of_scope 查实不在调查范围
    outcome: str = Field(min_length=1, max_length=30)
    point_code: str | None = Field(default=None, max_length=60)
