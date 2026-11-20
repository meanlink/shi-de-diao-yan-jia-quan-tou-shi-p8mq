import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, SamplingPlan, FieldSlot, FieldBatch
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.services.fieldwork import create_plan, report_observation
from src.utils.security import hash_password
from src.utils.serialization import checksum


def _seed_fieldwork(db):
    """幂等的演示派样：含一次拒访与一次不在范围递补，展示工作台口径。"""
    if db.scalar(select(SamplingPlan).where(SamplingPlan.idempotency_key == 'demo-dispatch-1019')):
        return
    roster = [{'code': f'C{i:03d}', 'name': f'中心区服务点{i}', 'zone': '中心区'} for i in range(1, 9)]
    roster += [{'code': f'W{i:03d}', 'name': f'外围区服务点{i}', 'zone': '外围区'} for i in range(1, 9)]
    quota = {'中心区': {'primary': 2, 'alternate': 1}, '外围区': {'primary': 2, 'alternate': 1}}
    plan, created = create_plan(db, {'name': '南岭首轮派样 · 演示', 'idempotency_key': 'demo-dispatch-1019',
                                     'seed': 1019, 'quota': quota, 'roster': roster})
    if not created:
        return
    db.flush()
    slots = list(db.query(FieldSlot).filter_by(plan_id=plan.id).order_by(FieldSlot.id).all())
    # 第一个名额拒访（计入应访、不递补）；第二个名额查实不在范围后顺位递补再合格
    refused, oos = slots[0], slots[1]
    report_observation(db, plan.id, {'slot_id': refused.id, 'observation_key': 'demo-refused', 'outcome': 'refused'})
    report_observation(db, plan.id, {'slot_id': oos.id, 'observation_key': 'demo-oos', 'outcome': 'out_of_scope'})
    report_observation(db, plan.id, {'slot_id': oos.id, 'observation_key': 'demo-oos-qualified', 'outcome': 'qualified'})
    db.add(Audit(actor='seed', action='demo.fieldwork', detail={'plan': plan.id}, at=time.time()))


def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        _seed_fieldwork(db)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,SamplingPlan,FieldSlot,FieldBatch]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
