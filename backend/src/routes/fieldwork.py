from fastapi import APIRouter, Depends
from sqlalchemy import select
from src.config.database import get_db
from src.models import FieldBatch
from src.schemas.fieldwork import PlanCreateInput, ObservationInput
from src.middlewares.auth import current_user, can_write
from src.services import fieldwork
from src.services.audit import record
router = APIRouter(prefix='/fieldwork')


def _plan_dict(plan, slots):
    return {
        'id': plan.id, 'name': plan.name, 'seed': plan.seed,
        'idempotency_key': plan.idempotency_key, 'quota': plan.quota,
        'all_settled': plan.all_settled, 'created_at': plan.created_at,
        'draw': plan.draw,
        'slots': [fieldwork._slot_brief(s) for s in slots],
        'access': fieldwork._access_summary(slots),
    }


@router.post('/plans', status_code=201)
def create_plan(data: PlanCreateInput, db=Depends(get_db), u=Depends(can_write)):
    payload = data.model_dump()
    plan, created = fieldwork.create_plan(db, payload)
    if created:
        record(db, u.email, 'fieldwork.plan.create', {'id': plan.id, 'key': plan.idempotency_key})
        db.commit()
    plan, slots = fieldwork.plan_detail(db, plan.id)
    return _plan_dict(plan, slots)


@router.get('/plans')
def list_plans(db=Depends(get_db), u=Depends(current_user)):
    return fieldwork.workbench(db)


@router.get('/plans/{plan_id}')
def plan_detail(plan_id: int, db=Depends(get_db), u=Depends(current_user)):
    plan, slots = fieldwork.plan_detail(db, plan_id)
    return _plan_dict(plan, slots)


@router.post('/plans/{plan_id}/observations')
def report(plan_id: int, payload: ObservationInput, db=Depends(get_db), u=Depends(can_write)):
    slot, reused = fieldwork.report_observation(db, plan_id, payload.model_dump())
    if not reused:
        record(db, u.email, 'fieldwork.observation',
               {'plan': plan_id, 'slot': slot.id, 'outcome': payload.outcome, 'key': payload.observation_key})
        db.commit()
    plan, slots = fieldwork.plan_detail(db, plan_id)
    return {'reused': reused, 'slot': fieldwork._slot_brief(slot), 'plan': _plan_dict(plan, slots)}


@router.post('/plans/{plan_id}/publish')
def publish(plan_id: int, db=Depends(get_db), u=Depends(can_write)):
    batch, created = fieldwork.publish_batch(db, plan_id)
    if created:
        record(db, u.email, 'fieldwork.batch.publish', {'plan': plan_id, 'batch': batch.id})
        db.commit()
    return {'created': created, 'batch_id': batch.id, 'checksum': batch.checksum,
            'created_at': batch.created_at, 'snapshot': batch.snapshot}


@router.get('/batches')
def batches(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(FieldBatch).order_by(FieldBatch.id.desc()))
    return [{'id': b.id, 'plan_id': b.plan_id, 'checksum': b.checksum,
             'created_at': b.created_at, 'snapshot': b.snapshot} for b in rows]
