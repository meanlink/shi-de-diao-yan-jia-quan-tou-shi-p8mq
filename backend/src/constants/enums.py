from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
class SlotState(str, Enum):
    # 正选待访：名册正选点尚未结清
    primary_pending = 'primary_pending'
    # 已递补待访：正选查实不在调查范围，候补顺位顶上，等待观察
    replaced = 'replaced'
    # 已结清：观察已收（合格/不合格）或拒访已定，名额封账
    settled = 'settled'
class ObservationKind(str, Enum):
    qualified = 'qualified'
    unqualified = 'unqualified'
    refused = 'refused'
    out_of_scope = 'out_of_scope'
class ReplaceReason(str, Enum):
    out_of_scope = 'out_of_scope'
