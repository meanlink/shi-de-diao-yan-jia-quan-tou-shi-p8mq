# 派样 / 拒访 / 递补 / 批次发布 — 测试记录

本目录记录“实地调研加权透视”新增的派样领域验收。测试与源码分离，源码不含本目录。

## 范围与口径

- 抽签按区域进行，A/B 两队正选与候补点位不重叠，同一种子可复现。
- 派样幂等键：中断后同键重试恢复原派样，不重新抽签、不换名单。
- 拒访计入应访数且不递补；仅“查实不在调查范围”才沿候补顺位递补。
- 通达率：下界按拒访点全不合格、上界按全合格，分母均为含拒访的应访数。
- 并发回报/递补：同一名额至多一份有效观察；观察键唯一，重放幂等、跨名额盗用拒绝。
- 各名额全部结清才能发布批次；发布重试不重复出批。
- 工作台可查待访、已结清及递补缘由。

## 测试环境（本地实际执行）

- 宿主无 Docker、无系统 pip；通过 `get-pip.py --user --break-system-packages` 安装 pip，
  再以 `pip install --target backend/.pydeps` 隔离安装运行/测试依赖，未污染系统 Python。
- 数据库使用隔离的临时 SQLite（`conftest.py` 覆盖 `get_db`），不触碰宿主 Postgres；
  Redis 限流中间件在无 Redis 时按既有逻辑静默放行。
- 关键依赖：fastapi 0.115.12、SQLAlchemy 2.0.41、pydantic 2.x、PyJWT、httpx、pytest。

复跑命令（在仓库根的 backend 同级）：

```bash
PYTHONPATH=/workspace/backend/.pydeps:/workspace/backend \
  python3 -m pytest -q /workspace/test-artifacts/gb-1019/tests
```

## 文件

- `conftest.py`：隔离 SQLite 基座、TestClient、角色令牌、名册与建计划夹具。
- `test_fieldwork_logic.py`：纯函数——抽签可复现/不重叠/容量与入参校验、通达率上下界。
- `test_fieldwork_api.py`：端到端 HTTP——幂等派样、权限、拒访、顺位递补、候补用尽、
  观察键去重、点位不符、结清前禁止发布、完整结清后发布幂等与上下界、工作台汇总。
- `test_fieldwork_concurrency.py`：真实多线程——同名额并发回报仅一份生效、
  同键并发重放只产生一份观察、并发不在范围只递补一次。

## 结果（实际执行）

- 全量：`23 passed`（含 3 个真实多线程并发用例）。
- 并发用例独立重复 5 次均通过，无抖动。
- 端到端冒烟（建计划→拒访→不在范围递补→全结清→发布→重试）通过：
  8 应访中 7 合格 1 拒访，通达率下界 0.875、上界 1.0；发布重试 `created=false` 且批次号与校验指纹不变。
- `scripts.seed_demo` 连续执行两次计数稳定，演示派样幂等（含一次拒访、一次不在范围递补）。

## 已知限制

- 测试库为 SQLite，其 `with_for_update` 为空操作；因此并发正确性依赖“带状态条件的原子 UPDATE +
  (计划,观察键) 唯一索引”，该机制在 Postgres 同样成立，Postgres 下另享行级锁。
- 未做浏览器端到端截图（环境无浏览器）；前端仅做了 `node --check` 语法校验与接线核对。
