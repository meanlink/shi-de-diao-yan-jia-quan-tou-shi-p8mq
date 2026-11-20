import {fieldwork} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,fmt,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {parseCSV} from '../utils/csv.js';
import {SlotState,Outcome} from '../constants/enums.js';

const canWrite=()=>session.user.role!=='viewer';
const pct=v=>v==null?'—':(v*100).toFixed(2)+'%';

function accessBlock(access){
 const o=access.overall;
 return '<div class="metrics"><div><span>应访数（含拒访）</span><strong>'+fmt(o.due)+'</strong></div>'
  +'<div><span>实际合格</span><strong>'+fmt(o.qualified)+'</strong></div>'
  +'<div><span>拒访点</span><strong>'+fmt(o.refused)+'</strong></div>'
  +'<div><span>通达率下界（拒访全不合格）</span><strong>'+esc(pct(o.lower_bound))+'</strong></div>'
  +'<div><span>通达率上界（拒访全合格）</span><strong>'+esc(pct(o.upper_bound))+'</strong></div></div>';
}

function slotRows(slots){
 const cols=[
  {key:'team',label:'队伍'},{key:'zone',label:'区域'},{key:'slot_index',label:'序号'},
  {key:'primary_point_code',label:'正选点'},{key:'point_code',label:'当前点'},
  {key:'state',label:'状态'},{key:'outcome',label:'结果'},{key:'replace_reason',label:'递补缘由'}
 ];
 const rows=slots.map(s=>({...s,
  state:SlotState[s.state]||s.state,
  outcome:s.outcome?(Outcome[s.outcome]||s.outcome):'—',
  replace_reason:s.replace_reason?(Outcome[s.replace_reason]||s.replace_reason):(s.replaced_points?.length?'顺位递补':'—')
 }));
 return '<div class="table-wrap"><table><thead><tr>'+cols.map(c=>'<th>'+esc(c.label)+'</th>').join('')
  +(canWrite()?'<th>回报操作</th>':'')+'</tr></thead><tbody>'
  +rows.map((r,i)=>'<tr class="'+(r.state==='已结清'?'is-settled':'')+'">'+cols.map(c=>'<td>'+esc(fmt(r[c.key]))+'</td>').join('')
   +(canWrite()?'<td>'+(r.state==='已结清'?'<span class="hint">已封账</span>'
     :'<div class="slot-actions"><button class="button small" data-obs="qualified" data-slot="'+slots[i].slot_id+'">合格</button>'
      +'<button class="button small" data-obs="unqualified" data-slot="'+slots[i].slot_id+'">不合格</button>'
      +'<button class="button small" data-obs="refused" data-slot="'+slots[i].slot_id+'">拒访</button>'
      +'<button class="button small warn" data-obs="out_of_scope" data-slot="'+slots[i].slot_id+'">不在范围→递补</button></div>')+'</td>':'')
  +'</tr>').join('')+'</tbody></table></div>';
}

export async function fieldworkPage(id){
 workspace.fieldPlans=await fieldwork.plans();
 const selected=id?await fieldwork.plan(id):(workspace.fieldPlans[0]?await fieldwork.plan(workspace.fieldPlans[0].plan_id):null);
 workspace.selectedFieldPlan=selected?.id??null;
 return heading('06 / FIELD DISPATCH','派样工作台 · 抽签/拒访/递补/发布',
  '按区域从服务点名册为两队抽正选与候补，两队点位不重叠；拒访计入应访不递补，查实不在范围才顺位顶上。')
 +'<div class="workbench-grid"><section class="panel"><div class="section-title"><h2>新建派样抽签</h2><span>中断重试同键恢复原名单</span></div>'
 +'<form id="plan-form"><label>计划名称<input name="name" required minlength="2" placeholder="例如：南岭首轮派样"></label>'
 +'<label>派样幂等键（中断恢复用）<input name="idempotency_key" required value="draw-'+Date.now()+'" ></label>'
 +'<label>随机种子<input name="seed" type="number" value="1019" required></label>'
 +'<div class="parameter-grid"><label>中心区正选<input name="p_c" type="number" value="2" min="1"></label>'
 +'<label>中心区候补<input name="a_c" type="number" value="1" min="0"></label>'
 +'<label>外围区正选<input name="p_w" type="number" value="2" min="1"></label>'
 +'<label>外围区候补<input name="a_w" type="number" value="1" min="0"></label></div>'
 +'<label>上传名册 CSV（code,name,zone）或粘贴 JSON<input type="file" id="roster-file" accept=".json,.csv"></label>'
 +'<label>名册 JSON<textarea name="roster" rows="5" placeholder="[{&quot;code&quot;:&quot;C001&quot;,&quot;name&quot;:&quot;中心1号&quot;,&quot;zone&quot;:&quot;中心区&quot;}]" required></textarea></label>'
 +'<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>抽签并生成派样 ↗</button>'
 +'<p class="hint">每区域名册点数须 ≥ 两队（正选+候补）之和；同种子结果可复现。</p></form></section>'
 +'<section class="method-sheet"><p class="eyebrow">DISPATCH RULES</p><h2>口径与并发约束</h2>'
 +'<ul><li>两队点位不重叠，正选优先、候补顺位递补。</li><li>拒访仍计入应访数，不触发递补。</li>'
 +'<li>仅“查实不在调查范围”才换下一个候补。</li><li>同一名额并发回报至多一份有效观察。</li>'
 +'<li>各名额全部结清才能发布批次，发布重试不重复出批。</li></ul></section></div>'
 +(selected?planPanel(selected):'<section class="panel empty">尚无派样计划，先在上方抽签。</section>');
}

function planPanel(plan){
 const wb=workspace.fieldPlans.find(p=>p.plan_id===plan.id)||{};
 return '<section class="panel"><div class="section-title"><h2>'+esc(plan.name)+'</h2>'
 +(plan.all_settled?'<span class="status completed">全部结清</span>':'<span class="status running">进行中</span>')+'</div>'
 +'<p class="hint">已结清 '+(wb.settled_count??0)+' / '+(wb.slot_count??plan.slots.length)+' 个名额 · 递补 '+(wb.replacements?.length||0)+' 次</p>'
 +accessBlock(plan.access)
 +(plan.all_settled&&canWrite()?'<button class="primary" id="publish-batch">发布分析批次（不可变快照）</button>':'')
 +(!plan.all_settled?'<p class="warning">尚有名额未结清，结清后才能发布。</p>':'')
 +'<div class="section-title"><h3>待访 / 已结清 / 递补缘由</h3></div>'+slotRows(plan.slots)
 +((wb.replacements?.length)?'<div class="section-title"><h3>递补记录</h3></div><ul class="questions">'
   +wb.replacements.map(r=>'<li>'+esc(r.team)+' / '+esc(r.zone)+'：正选 '+esc(r.primary_point_code)+' → 当前 '+esc(r.point_code)+'，缘由 '+(Outcome[r.reason]||r.reason)+'</li>').join('')+'</ul>':'')
 +'</section>';
}

export function bindFieldwork(refresh){
 const file=document.querySelector('#roster-file');
 file?.addEventListener('change',async e=>{
  const f=e.target.files[0];if(!f)return;
  try{const text=await f.text();const data=f.name.endsWith('.csv')?parseCSV(text):JSON.parse(text);
   document.querySelector('[name=roster]').value=JSON.stringify(data,null,2);}
  catch(err){notify(err.message,true);}
 });
 submit(document.querySelector('#plan-form'),async f=>{
  const roster=JSON.parse(f.get('roster'));
  const quota={'中心区':{primary:Number(f.get('p_c')),alternate:Number(f.get('a_c'))},
   '外围区':{primary:Number(f.get('p_w')),alternate:Number(f.get('a_w'))}};
  const plan=await fieldwork.createPlan({name:f.get('name'),idempotency_key:f.get('idempotency_key'),
   seed:Number(f.get('seed')),quota,roster});
  notify('派样已生成（同键重试将恢复此名单）');
  workspace.selectedFieldPlan=plan.id;location.hash='#/fieldwork/'+plan.id;await refresh();
 });
 document.querySelectorAll('[data-obs]').forEach(btn=>btn.onclick=async()=>{
  const pid=workspace.selectedFieldPlan;
  const key='obs-'+btn.dataset.slot+'-'+Date.now();
  try{await fieldwork.observe(pid,{slot_id:Number(btn.dataset.slot),observation_key:key,outcome:btn.dataset.obs});
   notify(btn.dataset.obs==='out_of_scope'?'已顺位递补候补点':'回报已入账');await refresh();}
  catch(err){notify(err.message,true);}
 });
 document.querySelector('#publish-batch')?.addEventListener('click',async()=>{
  try{const r=await fieldwork.publish(workspace.selectedFieldPlan);
   notify(r.created?'批次已发布，校验指纹 '+r.checksum.slice(0,16)+'…':'该计划批次已存在，未重复出批');await refresh();}
  catch(err){notify(err.message,true);}
 });
}
