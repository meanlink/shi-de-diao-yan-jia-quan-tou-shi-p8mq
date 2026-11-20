"""端到端业务规则：派样幂等、拒访/递补、并发去重、结清发布、工作台。"""
import pytest
from sqlalchemy.exc import IntegrityError

from src.models import FieldSlot


def test_plan_idempotent_retry_keeps_same_draw(client, auth_headers, make_plan, roster_data):
    first = make_plan(key="idem-1")
    # 用相同幂等键重试：恢复原派样，不重新抽签、不换名单
    resp = client.post("/fieldwork/plans", headers=auth_headers("admin"), json={
        "name": "南岭首轮派样", "idempotency_key": "idem-1", "seed": 999,
        "quota": {"中心区": {"primary": 2, "alternate": 1}, "外围区": {"primary": 2, "alternate": 1}},
        "roster": roster_data(8)})
    assert resp.status_code == 201
    again = resp.json()
    assert again["id"] == first["id"]
    assert again["draw"] == first["draw"]
    assert again["seed"] == first["seed"]  # 忽略重试里的新种子


def test_viewer_cannot_create_plan(client, auth_headers):
    resp = client.post("/fieldwork/plans", headers=auth_headers("viewer"), json={
        "name": "无权派样", "idempotency_key": "x", "seed": 1,
        "quota": {"中心区": {"primary": 1, "alternate": 0}},
        "roster": [{"code": "C1", "zone": "中心区"}]})
    assert resp.status_code == 403


def test_drawn_points_do_not_overlap_across_teams(make_plan):
    plan = make_plan()
    for zone in ("中心区", "外围区"):
        a = {p["code"] for p in plan["draw"]["A队"][zone]["primary"] + plan["draw"]["A队"][zone]["alternate"]}
        b = {p["code"] for p in plan["draw"]["B队"][zone]["primary"] + plan["draw"]["B队"][zone]["alternate"]}
        assert a.isdisjoint(b)
    # 名额行只对应正选
    assert len(plan["slots"]) == 8  # 2 队 × 2 区 × 2 正选


def _slot(plan, team, zone, index=0):
    return next(s for s in plan["slots"]
                if s["team"] == team and s["zone"] == zone and s["slot_index"] == index)


def test_refusal_settles_and_counts_in_denominator(client, auth_headers, make_plan):
    plan = make_plan(key="ref-1")
    slot = _slot(plan, "A队", "中心区")
    resp = client.post(f"/fieldwork/plans/{plan['id']}/observations",
                       headers=auth_headers("analyst"),
                       json={"slot_id": slot["slot_id"], "observation_key": "obs-1",
                             "outcome": "refused"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["slot"]["state"] == "settled"
    assert body["slot"]["outcome"] == "refused"
    # 拒访不产生递补：持有点仍是正选点
    assert body["slot"]["point_code"] == slot["point_code"]
    assert body["slot"]["replaced_points"] == []


def test_out_of_scope_promotes_alternate_in_order(client, auth_headers, make_plan):
    plan = make_plan(key="oos-1")
    slot = _slot(plan, "B队", "外围区")
    primary = slot["point_code"]
    alternates = [p["code"] for p in plan["draw"]["B队"]["外围区"]["alternate"]]
    # 正选查实不在调查范围
    resp = client.post(f"/fieldwork/plans/{plan['id']}/observations",
                       headers=auth_headers("analyst"),
                       json={"slot_id": slot["slot_id"], "observation_key": "oos-a",
                             "outcome": "out_of_scope"})
    assert resp.status_code == 200
    moved = resp.json()["slot"]
    assert moved["state"] == "replaced"
    assert moved["point_code"] == alternates[0]          # 顺位第一候补顶上
    assert moved["replaced_points"] == [primary]
    assert moved["replace_reason"] == "out_of_scope"
    assert moved["outcome"] is None                       # 递补点尚未观察
    # 顶上的候补给出合格观察后才结清
    done = client.post(f"/fieldwork/plans/{plan['id']}/observations",
                       headers=auth_headers("analyst"),
                       json={"slot_id": slot["slot_id"], "observation_key": "obs-b",
                             "outcome": "qualified"})
    assert done.json()["slot"]["state"] == "settled"
    assert done.json()["slot"]["outcome"] == "qualified"


def test_refused_slot_rejects_second_valid_observation(client, auth_headers, make_plan):
    plan = make_plan(key="one-1")
    slot = _slot(plan, "A队", "外围区")
    client.post(f"/fieldwork/plans/{plan['id']}/observations",
                headers=auth_headers("analyst"),
                json={"slot_id": slot["slot_id"], "observation_key": "k1", "outcome": "refused"})
    # 已结清名额再来一份不同的有效观察 -> 拒绝
    resp = client.post(f"/fieldwork/plans/{plan['id']}/observations",
                       headers=auth_headers("analyst"),
                       json={"slot_id": slot["slot_id"], "observation_key": "k2", "outcome": "qualified"})
    assert resp.status_code == 409


def test_same_observation_key_replay_is_idempotent(client, auth_headers, make_plan):
    plan = make_plan(key="replay-1")
    slot = _slot(plan, "A队", "中心区")
    payload = {"slot_id": slot["slot_id"], "observation_key": "same-key", "outcome": "qualified"}
    first = client.post(f"/fieldwork/plans/{plan['id']}/observations",
                        headers=auth_headers("analyst"), json=payload)
    second = client.post(f"/fieldwork/plans/{plan['id']}/observations",
                         headers=auth_headers("analyst"), json=payload)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["reused"] is True
    assert first.json()["slot"] == second.json()["slot"]


def test_concurrent_reports_only_one_valid_observation(db, make_plan):
    """并发回报同一名额：唯一观察键保证至多一份有效观察落库。"""
    plan = make_plan(key="conc-1")
    slot = _slot(plan, "A队", "中心区")
    row = db.query(FieldSlot).filter_by(id=slot["slot_id"]).one()
    row.state, row.outcome, row.observation_key, row.updated_at = "settled", "qualified", "dup", 1.0
    db.commit()
    # 同一计划内再用相同观察键写另一名额 -> 唯一索引拦截
    other = next(s for s in plan["slots"] if s["slot_id"] != slot["slot_id"])
    dup_row = db.query(FieldSlot).filter_by(id=other["slot_id"]).one()
    dup_row.observation_key = "dup"
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_publish_blocked_until_all_settled(client, auth_headers, make_plan):
    plan = make_plan(key="pub-1")
    resp = client.post(f"/fieldwork/plans/{plan['id']}/publish", headers=auth_headers("admin"))
    assert resp.status_code == 409  # 尚有名额未结清


def test_full_cycle_publish_is_idempotent_and_bounds(client, auth_headers, make_plan):
    plan = make_plan(key="cycle-1")
    pid = plan["id"]
    # 全部名额结清：构造 2 合格、1 不合格、1 拒访（其余给合格），并触发一次递补
    slots = plan["slots"]
    refused_slot = _slot(plan, "A队", "中心区", 0)
    client.post(f"/fieldwork/plans/{pid}/observations", headers=auth_headers("analyst"),
                json={"slot_id": refused_slot["slot_id"], "observation_key": "r", "outcome": "refused"})
    unq_slot = _slot(plan, "A队", "中心区", 1)
    client.post(f"/fieldwork/plans/{pid}/observations", headers=auth_headers("analyst"),
                json={"slot_id": unq_slot["slot_id"], "observation_key": "u", "outcome": "unqualified"})
    oos_slot = _slot(plan, "B队", "外围区", 0)
    client.post(f"/fieldwork/plans/{pid}/observations", headers=auth_headers("analyst"),
                json={"slot_id": oos_slot["slot_id"], "observation_key": "o", "outcome": "out_of_scope"})
    # 其余名额一律合格
    used = {refused_slot["slot_id"], unq_slot["slot_id"], oos_slot["slot_id"]}
    counter = 0
    for s in slots:
        if s["slot_id"] in used:
            continue
        counter += 1
        client.post(f"/fieldwork/plans/{pid}/observations", headers=auth_headers("analyst"),
                    json={"slot_id": s["slot_id"], "observation_key": f"q{counter}", "outcome": "qualified"})
    # 递补点也要合格才全部结清
    detail = client.get(f"/fieldwork/plans/{pid}", headers=auth_headers("admin")).json()
    moved = next(s for s in detail["slots"] if s["state"] != "settled")
    client.post(f"/fieldwork/plans/{pid}/observations", headers=auth_headers("analyst"),
                json={"slot_id": moved["slot_id"], "observation_key": "q-final", "outcome": "qualified"})

    detail = client.get(f"/fieldwork/plans/{pid}", headers=auth_headers("admin")).json()
    assert detail["all_settled"] is True
    access = detail["access"]["overall"]
    assert access["due"] == 8 and access["refused"] == 1
    assert access["lower_bound"] == round(6 / 8, 6)   # 6 合格 / 8 应访
    assert access["upper_bound"] == round(7 / 8, 6)   # 拒访按全合格

    first = client.post(f"/fieldwork/plans/{pid}/publish", headers=auth_headers("admin"))
    assert first.status_code == 200 and first.json()["created"] is True
    second = client.post(f"/fieldwork/plans/{pid}/publish", headers=auth_headers("admin"))
    assert second.json()["created"] is False           # 发布重试不重复出批
    assert second.json()["batch_id"] == first.json()["batch_id"]
    assert second.json()["checksum"] == first.json()["checksum"]

    batches = client.get("/fieldwork/batches", headers=auth_headers("admin")).json()
    assert len(batches) == 1


def test_workbench_shows_pending_settled_and_replacement_reason(client, auth_headers, make_plan):
    plan = make_plan(key="wb-1")
    slot = _slot(plan, "B队", "中心区")
    client.post(f"/fieldwork/plans/{plan['id']}/observations", headers=auth_headers("analyst"),
                json={"slot_id": slot["slot_id"], "observation_key": "w1", "outcome": "out_of_scope"})
    wb = client.get("/fieldwork/plans", headers=auth_headers("admin")).json()
    row = next(p for p in wb if p["plan_id"] == plan["id"])
    assert row["settled_count"] == 0
    assert row["slot_count"] == 8
    assert len(row["pending"]) == 8
    assert row["replacements"][0]["reason"] == "out_of_scope"
    assert row["replacements"][0]["team"] == "B队"


def test_alternate_exhausted_blocks_further_replacement(client, auth_headers, make_plan):
    # 每区域仅 1 个候补：两次不在范围后候补用尽
    quota = {"中心区": {"primary": 1, "alternate": 1}, "外围区": {"primary": 1, "alternate": 1}}
    plan = make_plan(key="exhaust-1", quota=quota, points=6)
    slot = _slot(plan, "A队", "中心区")
    first = client.post(f"/fieldwork/plans/{plan['id']}/observations", headers=auth_headers("analyst"),
                        json={"slot_id": slot["slot_id"], "observation_key": "e1", "outcome": "out_of_scope"})
    assert first.status_code == 200  # 唯一候补顶上
    second = client.post(f"/fieldwork/plans/{plan['id']}/observations", headers=auth_headers("analyst"),
                         json={"slot_id": slot["slot_id"], "observation_key": "e2", "outcome": "out_of_scope"})
    assert second.status_code == 409  # 候补已用尽


def test_observation_key_cannot_be_reused_across_slots(client, auth_headers, make_plan):
    plan = make_plan(key="cross-1")
    s1, s2 = plan["slots"][0], plan["slots"][1]
    ok = client.post(f"/fieldwork/plans/{plan['id']}/observations", headers=auth_headers("analyst"),
                     json={"slot_id": s1["slot_id"], "observation_key": "shared", "outcome": "qualified"})
    assert ok.status_code == 200
    clash = client.post(f"/fieldwork/plans/{plan['id']}/observations", headers=auth_headers("analyst"),
                        json={"slot_id": s2["slot_id"], "observation_key": "shared", "outcome": "qualified"})
    assert clash.status_code == 409


def test_point_code_mismatch_is_rejected(client, auth_headers, make_plan):
    plan = make_plan(key="mismatch-1")
    slot = _slot(plan, "A队", "中心区")
    resp = client.post(f"/fieldwork/plans/{plan['id']}/observations", headers=auth_headers("analyst"),
                       json={"slot_id": slot["slot_id"], "observation_key": "m1",
                             "outcome": "qualified", "point_code": "WRONG-CODE"})
    assert resp.status_code == 409
