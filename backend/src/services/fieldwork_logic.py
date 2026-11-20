"""派样抽签与通达率口径的纯函数，不接触数据库，便于独立复核。"""
import random

TEAMS = ('A队', 'B队')


def _normalise_roster(roster):
    """名册点：{code, name, zone}。校验编号唯一、区域非空。"""
    if not isinstance(roster, list) or not roster:
        raise ValueError('服务点名册至少需要一个服务点')
    seen, points = set(), []
    for index, row in enumerate(roster):
        if not isinstance(row, dict):
            raise ValueError(f'名册第 {index + 1} 行结构非法')
        code = str(row.get('code', '')).strip()
        zone = str(row.get('zone', '')).strip()
        name = str(row.get('name', code)).strip() or code
        if not code:
            raise ValueError(f'名册第 {index + 1} 行缺少服务点编号')
        if not zone:
            raise ValueError(f'服务点 {code} 缺少所属区域')
        if code in seen:
            raise ValueError(f'服务点编号重复：{code}')
        seen.add(code)
        points.append({'code': code, 'name': name, 'zone': zone})
    return points


def _normalise_quota(quota, zones):
    """quota: {zone: {"primary": p, "alternate": a}}。返回规整后的非负整数名额。"""
    if not isinstance(quota, dict) or not quota:
        raise ValueError('请按区域提供正选与候补名额')
    normalised = {}
    for zone in zones:
        cell = quota.get(zone)
        if not isinstance(cell, dict):
            raise ValueError(f'区域 {zone} 缺少名额配置')
        try:
            primary = int(cell.get('primary'))
            alternate = int(cell.get('alternate', 0))
        except (TypeError, ValueError):
            raise ValueError(f'区域 {zone} 的名额必须为整数') from None
        if primary < 1 or alternate < 0:
            raise ValueError(f'区域 {zone} 正选至少 1 个、候补不能为负')
        normalised[zone] = {'primary': primary, 'alternate': alternate}
    return normalised


def draw_plan(roster, quota, seed):
    """按区域为 A/B 两队抽正选与候补，同一点位在两队之间不重叠。

    每个区域内用同一随机种子洗牌后顺序切分：A 队正选+候补、B 队正选+候补，
    因此抽签确定后可复现，且任何服务点不会同时进入两队名单。
    返回 {team: {zone: {"primary": [point...], "alternate": [point...]}}}。
    """
    points = _normalise_roster(roster)
    try:
        seed_int = int(seed)
    except (TypeError, ValueError):
        raise ValueError('随机种子必须为整数') from None
    by_zone = {}
    for point in points:
        by_zone.setdefault(point['zone'], []).append(point)
    zones = sorted(by_zone)
    normalised = _normalise_quota(quota, zones)
    result = {team: {} for team in TEAMS}
    rng = random.Random(seed_int)
    for zone in zones:
        pool = list(by_zone[zone])
        rng.shuffle(pool)
        need_a = normalised[zone]['primary'] + normalised[zone]['alternate']
        need_b = normalised[zone]['primary'] + normalised[zone]['alternate']
        if len(pool) < need_a + need_b:
            raise ValueError(
                f'区域 {zone} 名册仅 {len(pool)} 个点，无法满足两队不重叠共 {need_a + need_b} 个名额'
            )
        cursor = 0
        for team in TEAMS:
            need = normalised[zone]['primary'] + normalised[zone]['alternate']
            chosen = pool[cursor:cursor + need]
            cursor += need
            result[team][zone] = {
                'primary': chosen[:normalised[zone]['primary']],
                'alternate': chosen[normalised[zone]['primary']:],
            }
    return result


def access_rate_bounds(slots):
    """拒访点给通达率带来的上下界。

    应访数 = 有效观察名额数（拒访仍计入应访，不因递补而消失）。
    上界：拒访点按全合格计；下界：拒访点按全不合格计。
    合格/不合格观察按实际结果计，已递补点的合格结果计入当前持有点。
    """
    due = qualified = refused = 0
    for slot in slots:
        outcome = slot.get('outcome')
        if outcome in ('qualified', 'unqualified', 'refused'):
            due += 1
        if outcome == 'qualified':
            qualified += 1
        elif outcome == 'refused':
            refused += 1
    if due == 0:
        return {'due': 0, 'qualified': 0, 'refused': 0, 'lower_bound': None, 'upper_bound': None}
    return {
        'due': due,
        'qualified': qualified,
        'refused': refused,
        # 下界：拒访全不合格，分子不变
        'lower_bound': round(qualified / due, 6),
        # 上界：拒访全合格，分子加上拒访数
        'upper_bound': round((qualified + refused) / due, 6),
    }
