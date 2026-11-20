"""派样、回报、顺位递补与批次发布的有状态服务。

并发约束（与具体数据库无关，SQLite/Postgres 均成立）：
- 有效观察与递补都用带状态条件的原子 UPDATE，并以 (计划, 观察键) 唯一索引兜底，
  因此回报与递补并发时，同一名额至多收下一份有效观察。
- 同一观察键重放幂等返回；跨名额盗用同一观察键被唯一索引拒绝。
- 拒访只封账不递补；只有查实不在调查范围才沿候补顺位顶上。
"""
import time

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.models import SamplingPlan, FieldSlot, FieldBatch
from src.services.fieldwork_logic import draw_plan, access_rate_bounds, TEAMS
from src.utils.serialization import checksum

VALID_OUTCOMES = ('qualified', 'unqualified', 'refused')


def _get_plan(db, plan_id):
    plan = db.get(SamplingPlan, plan_id)
    if not plan:
        raise HTTPException(404, '派样计划不存在')
    return plan


def _get_slot(db, slot_id):
    slot = db.get(FieldSlot, slot_id)
    if not slot:
        raise HTTPException(404, '名额不存在')
    return slot


def create_plan(db, data):
    key = str(data.get('idempotency_key') or '').strip()
    if not key:
        raise HTTPException(422, '派样幂等键不能为空，中断重试依赖它恢复原派样')
    # 中断后恢复原派样：同键重试直接取回原计划，绝不重新抽签、不换名单
    existing = db.scalar(select(SamplingPlan).where(SamplingPlan.idempotency_key == key))
    if existing:
        return existing, False
    name = str(data.get('name') or '').strip()
    if len(name) < 2:
        raise HTTPException(422, '计划名称去除空格后至少需要两个字符')
    try:
        draw = draw_plan(data['roster'], data['quota'], data['seed'])
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    plan = SamplingPlan(
        idempotency_key=key, name=name, seed=int(data['seed']), quota=data['quota'],
        draw=draw, roster=data['roster'], all_settled=False, created_at=time.time(),
    )
    db.add(plan)
    db.flush()
    # 名额行只对应正选名额；候补点保存在 plan.draw 中按需顺位递补
    for team in TEAMS:
        for zone, groups in draw[team].items():
            for index, point in enumerate(groups['primary']):
                db.add(FieldSlot(
                    plan_id=plan.id, team=team, zone=zone, slot_index=index,
                    is_alternate=False, point_code=point['code'],
                    primary_point_code=point['code'], state='primary_pending',
                    replaced_points=[], updated_at=time.time(),
                ))
    db.flush()
    return plan, True


def report_observation(db, plan_id, payload):
    """对一个名额回报观察。返回 (slot, reused)；reused=True 表示命中幂等未重复入账。"""
    plan = _get_plan(db, plan_id)
    slot = _get_slot(db, payload['slot_id'])
    if slot.plan_id != plan.id:
        raise HTTPException(422, '该名额不属于此派样计划')
    key = str(payload.get('observation_key') or '').strip()
    if not key:
        raise HTTPException(422, '观察幂等键不能为空，并发回报依赖它去重')
    outcome = payload['outcome']
    if outcome not in VALID_OUTCOMES + ('out_of_scope',):
        raise HTTPException(422, '观察结果必须是 qualified/unqualified/refused/out_of_scope')
    point_code = payload.get('point_code')
    if point_code and point_code != slot.point_code:
        raise HTTPException(409, f'回报点位 {point_code} 与名额当前持有点 {slot.point_code} 不一致')

    # 同一观察键在计划内唯一：先识别重放或盗用
    prior = db.scalar(select(FieldSlot).where(
        FieldSlot.plan_id == plan.id, FieldSlot.observation_key == key))
    if prior is not None:
        if prior.id == slot.id:
            return prior, True  # 同一观察键重放：幂等返回，不重复生效
        raise HTTPException(409, '该观察键已用于其他名额，同一名额至多收下一份有效观察')

    now = time.time()
    if outcome == 'out_of_scope':
        # 查实不在调查范围不计有效观察：乐观条件更新，沿候补顺位顶上，并发下只成功一次
        for _ in range(5):
            current = db.get(FieldSlot, slot.id)
            if current is None:
                raise HTTPException(404, '名额不存在')
            if current.observation_key == key:
                return current, True  # 同键重放，幂等返回
            if current.state == 'settled':
                raise HTTPException(409, '该名额已结清，无法再发起递补')
            nxt = _next_alternate(plan, current)
            if nxt is None:
                raise HTTPException(409, f'{current.team}/{current.zone} 候补已用尽，无法继续顺位递补')
            new_replaced = list(current.replaced_points) + [current.point_code]
            try:
                rows = db.execute(
                    update(FieldSlot)
                    .where(FieldSlot.id == current.id,
                           FieldSlot.state != 'settled',
                           FieldSlot.point_code == current.point_code)
                    .values(point_code=nxt['code'], replaced_points=new_replaced,
                            replace_reason='out_of_scope', state='replaced', outcome=None,
                            observation_key=key, updated_at=now)
                ).rowcount
            except IntegrityError:
                db.rollback()
                prior = db.scalar(select(FieldSlot).where(
                    FieldSlot.plan_id == plan.id, FieldSlot.observation_key == key))
                if prior is not None and prior.id == slot.id:
                    return prior, True
                raise HTTPException(409, '该递补事件已提交，请勿重复发起') from None
            if rows == 1:
                db.flush()
                return db.get(FieldSlot, slot.id), False
            db.rollback()  # 持有点已被并发改动，按最新状态重试
        raise HTTPException(409, '并发递补冲突，请稍后重试')

    # qualified/unqualified/refused 为有效观察：条件原子更新，仅未结清名额可写
    # WHERE state != 'settled' 保证并发下只有一份能把名额置为 settled
    try:
        rows = db.execute(
            update(FieldSlot)
            .where(FieldSlot.id == slot.id, FieldSlot.state != 'settled')
            .values(observation_key=key, outcome=outcome, state='settled', updated_at=now)
        ).rowcount
    except IntegrityError:
        # 观察键唯一索引兜底（并发同键/跨名额盗用）
        db.rollback()
        prior = db.scalar(select(FieldSlot).where(
            FieldSlot.plan_id == plan.id, FieldSlot.observation_key == key))
        if prior is not None and prior.id == slot.id:
            return prior, True
        raise HTTPException(409, '该名额已被并发回报结清，同一名额至多收下一份有效观察') from None
    if rows == 0:
        db.rollback()  # 释放可能持有的写事务，保证后续读取最新提交
        latest = db.get(FieldSlot, slot.id)
        if latest is not None and latest.observation_key == key:
            return latest, True
        raise HTTPException(409, '该名额已被并发回报结清，同一名额至多收下一份有效观察')
    db.flush()
    _refresh_settled(db, plan)
    return db.get(FieldSlot, slot.id), False


def _next_alternate(plan, slot):
    """纯计算：返回本队本区域下一个尚未使用的候补点，用尽返回 None。"""
    groups = plan.draw[slot.team][slot.zone]
    used = set(slot.replaced_points) | {slot.point_code}
    return next((point for point in groups['alternate'] if point['code'] not in used), None)


def _refresh_settled(db, plan):
    pending = db.scalar(
        select(FieldSlot.id).where(
            FieldSlot.plan_id == plan.id, FieldSlot.state != 'settled'
        ).limit(1)
    )
    plan.all_settled = pending is None


def plan_detail(db, plan_id):
    plan = _get_plan(db, plan_id)
    slots = list(db.scalars(
        select(FieldSlot).where(FieldSlot.plan_id == plan.id)
        .order_by(FieldSlot.team, FieldSlot.zone, FieldSlot.slot_index)
    ))
    return plan, slots


def workbench(db):
    """工作台：待访、已结清及递补缘由，按计划汇总。"""
    plans = list(db.scalars(select(SamplingPlan).order_by(SamplingPlan.id.desc())))
    out = []
    for plan in plans:
        slots = list(db.scalars(select(FieldSlot).where(FieldSlot.plan_id == plan.id)))
        pending = [s for s in slots if s.state != 'settled']
        settled = [s for s in slots if s.state == 'settled']
        replacements = [
            {'slot_id': s.id, 'team': s.team, 'zone': s.zone,
             'primary_point_code': s.primary_point_code, 'point_code': s.point_code,
             'replaced_points': s.replaced_points, 'reason': s.replace_reason}
            for s in slots if s.replaced_points
        ]
        out.append({
            'plan_id': plan.id, 'name': plan.name, 'all_settled': plan.all_settled,
            'quota': plan.quota,
            'pending': [_slot_brief(s) for s in pending],
            'settled_count': len(settled), 'slot_count': len(slots),
            'replacements': replacements,
            'access': _access_summary(slots),
        })
    return out


def _slot_brief(slot):
    return {
        'slot_id': slot.id, 'team': slot.team, 'zone': slot.zone,
        'slot_index': slot.slot_index, 'point_code': slot.point_code,
        'primary_point_code': slot.primary_point_code, 'state': slot.state,
        'outcome': slot.outcome, 'replace_reason': slot.replace_reason,
        'replaced_points': slot.replaced_points,
    }


def _access_summary(slots):
    overall = access_rate_bounds([{'outcome': s.outcome} for s in slots])
    by_team, by_zone = {}, {}
    for team in sorted({s.team for s in slots}):
        by_team[team] = access_rate_bounds([{'outcome': s.outcome} for s in slots if s.team == team])
    for zone in sorted({s.zone for s in slots}):
        by_zone[zone] = access_rate_bounds([{'outcome': s.outcome} for s in slots if s.zone == zone])
    return {'overall': overall, 'by_team': by_team, 'by_zone': by_zone}


def publish_batch(db, plan_id):
    """各名额结清才发布；发布重试不重复出批。返回 (batch, created)。"""
    plan = _get_plan(db, plan_id)
    existing = db.scalar(select(FieldBatch).where(FieldBatch.plan_id == plan.id))
    if existing:
        return existing, False
    slots = list(db.scalars(select(FieldSlot).where(FieldSlot.plan_id == plan.id)))
    if any(s.state != 'settled' for s in slots):
        raise HTTPException(409, '尚有未结清名额，全部结清后才能发布分析批次')
    snapshot = {
        'plan_id': plan.id, 'name': plan.name, 'seed': plan.seed,
        'generated_at': time.time(),
        'access': _access_summary(slots),
        'slots': [_slot_brief(s) for s in slots],
    }
    digest = checksum(snapshot)
    batch = FieldBatch(plan_id=plan.id, checksum=digest, snapshot=snapshot, created_at=time.time())
    db.add(batch)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(FieldBatch).where(FieldBatch.plan_id == plan.id))
        return existing, False
    return batch, True
