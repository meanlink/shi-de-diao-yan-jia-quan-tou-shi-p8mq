"""纯领域逻辑：抽签不重叠、可复现、容量校验、通达率上下界。"""
import pytest

from src.services.fieldwork_logic import draw_plan, access_rate_bounds


def test_draw_is_reproducible_and_teams_disjoint():
    points = []
    for zone, prefix in (("中心区", "C"), ("外围区", "W")):
        for i in range(10):
            points.append({"code": f"{prefix}{i:02d}", "zone": zone})
    quota = {"中心区": {"primary": 3, "alternate": 2},
             "外围区": {"primary": 2, "alternate": 1}}
    first = draw_plan(points, quota, seed=1019)
    second = draw_plan(points, quota, seed=1019)
    assert first == second  # 同种子可复现，中断重试不改名单

    for zone in quota:
        a_codes = [p["code"] for p in first["A队"][zone]["primary"] + first["A队"][zone]["alternate"]]
        b_codes = [p["code"] for p in first["B队"][zone]["primary"] + first["B队"][zone]["alternate"]]
        # 两队点位不重叠
        assert set(a_codes).isdisjoint(set(b_codes))
        # 正选/候补数量正确
        assert len(first["A队"][zone]["primary"]) == quota[zone]["primary"]
        assert len(first["A队"][zone]["alternate"]) == quota[zone]["alternate"]
        # 同队内部不重复
        assert len(a_codes) == len(set(a_codes))
        assert len(b_codes) == len(set(b_codes))


def test_different_seed_changes_order():
    points = [{"code": f"C{i:02d}", "zone": "中心区"} for i in range(12)]
    quota = {"中心区": {"primary": 2, "alternate": 2}}
    a = draw_plan(points, quota, seed=1)["A队"]["中心区"]["primary"]
    b = draw_plan(points, quota, seed=2)["A队"]["中心区"]["primary"]
    # 不同种子通常产生不同排列（极小概率相同，这里种子差异足够）
    assert [p["code"] for p in a] != [p["code"] for p in b]


def test_draw_rejects_when_roster_too_small():
    points = [{"code": f"C{i}", "zone": "中心区"} for i in range(3)]
    quota = {"中心区": {"primary": 2, "alternate": 1}}  # 两队共需 6 个
    with pytest.raises(ValueError):
        draw_plan(points, quota, seed=7)


def test_draw_rejects_duplicate_code_and_bad_quota():
    dup = [{"code": "C1", "zone": "中心区"}, {"code": "C1", "zone": "中心区"}]
    with pytest.raises(ValueError):
        draw_plan(dup, {"中心区": {"primary": 1, "alternate": 0}}, seed=1)
    good = [{"code": "C1", "zone": "中心区"}, {"code": "C2", "zone": "中心区"}]
    with pytest.raises(ValueError):
        draw_plan(good, {"中心区": {"primary": 0, "alternate": 0}}, seed=1)


def test_access_bounds_refusal_counts_in_denominator():
    # 4 个应访：2 合格、1 不合格、1 拒访
    slots = [{"outcome": "qualified"}, {"outcome": "qualified"},
             {"outcome": "unqualified"}, {"outcome": "refused"}]
    result = access_rate_bounds(slots)
    assert result["due"] == 4 and result["qualified"] == 2 and result["refused"] == 1
    assert result["lower_bound"] == 0.5       # 拒访全不合格：2/4
    assert result["upper_bound"] == 0.75      # 拒访全合格：3/4


def test_access_bounds_no_due_returns_none():
    assert access_rate_bounds([{"outcome": None}])["lower_bound"] is None
