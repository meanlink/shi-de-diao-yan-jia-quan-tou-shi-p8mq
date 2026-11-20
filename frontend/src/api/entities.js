import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const fieldwork={
 plans:()=>api('/fieldwork/plans'),
 plan:id=>api('/fieldwork/plans/'+id),
 createPlan:data=>api('/fieldwork/plans',{method:'POST',body:JSON.stringify(data)}),
 observe:(id,data)=>api('/fieldwork/plans/'+id+'/observations',{method:'POST',body:JSON.stringify(data)}),
 publish:id=>api('/fieldwork/plans/'+id+'/publish',{method:'POST'}),
 batches:()=>api('/fieldwork/batches')
};
