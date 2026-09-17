const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/src/js/production_pipeline.js'),'utf8');
const ctx=vm.createContext({Component:class{},KanbanController:class{},standardFieldProps:{},kanbanView:{Renderer:class{}},
 registry:{category:()=>({add(){}})},Domain:{and:domains=>({toList:()=>domains.flat()})}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../../furniture_stage_replenishment/static/src/js/stage_replenishment_dashboard.js'),'utf8').replace(/^import .*;$/gm,'').replace(/export /g,''),ctx);
vm.runInContext(source.replace(/^import .*;$/gm,'').replace(/export /g,'')+
 '\nglobalThis.api={groupPipelineProducts,ProductionWorkspaceController};',ctx);
const {groupPipelineProducts:group,ProductionWorkspaceController:Controller}=ctx.api;
const plain=x=>JSON.parse(JSON.stringify(x));
const policy={company_id:[1,'Factory'],product_id:[77,'شازلونج'],target_lane:'packaging',final_rule_id:[23,'Final'],buffer_rule_id:false};
const rows=[4,5,6].map(id=>({...policy,id,state:'draft',preview_json:{stages:[]}}));
const approved=[{...policy,__count:3}];
const baseline=plain(group(rows));
const snapshot=JSON.stringify({rows,approved});
const partial=group(rows,approved)[0];
assert.equal(partial.isPartiallyApproved,true);assert.equal(partial.approvedCount,3);
assert.deepEqual(plain(partial.rows.map(r=>r.id)),[4,5,6]);
assert.deepEqual(plain(partial.time),baseline[0].time);
assert.equal(JSON.stringify({rows,approved}),snapshot);
assert.equal(group(rows)[0].isPartiallyApproved,false);
assert.equal(group(rows,[{...policy,__count:0}])[0].isPartiallyApproved,false);
assert.equal(group([] ,approved).length,0);
for(const change of [{company_id:[2,'Other']},{product_id:[78,'Other']},{target_lane:'tailoring'},
 {final_rule_id:[99,'Other']},{buffer_rule_id:[9,'Buffer']},{final_rule_id:false}]) {
 assert.equal(group(rows,[{...policy,...change,__count:100}])[0].isPartiallyApproved,false);
}
assert.equal(group([...rows,{...policy,id:7,state:'draft'}],approved)[0].approvedCount,3); // no per-row double count
const mixed=group([...rows,{...policy,id:8,state:'draft',product_id:[78,'فوتيه']}],approved);
assert.equal(mixed.find(g=>g.id===78).isPartiallyApproved,false);
(async()=>{
 const c=Object.create(Controller.prototype);c.workspace={};c.workspaceRequest=0;
 c.pipelineFilters={modelId:'6'};c.props={resModel:'furniture.need.to.produce'};
 c.env={searchModel:{globalDomain:[['company_id','=',1]],globalContext:{allowed_company_ids:[1]}}};
 let countQueries=0;
 c.pipelineOrm={searchRead:async()=>rows,readGroup:async(model,domain,fields,groupby,options)=>{
  countQueries++;
  for(const term of [['state','=','approved'],['furniture_model_id','=',6],['company_id','=',1],['product_id','in',[77]]])
   assert(plain(domain).some(d=>JSON.stringify(d)===JSON.stringify(term)));
  assert.equal(options.lazy,false);assert.deepEqual(plain(options.context.allowed_company_ids),[1]);
  assert(fields.includes('final_rule_id'));assert(fields.includes('target_lane'));
  return approved;
 }};
 await c.loadWorkspace();assert.equal(countQueries,1);assert.equal(c.workspace.groups[0].isPartiallyApproved,true);
 let approvedIds;c.confirmPipelineIds=ids=>approvedIds=plain(ids);
 c.approveWorkspace('product',77);assert.deepEqual(approvedIds,[4,5,6]); // never approve existing approved pieces again
 let resolve;
 c.pipelineOrm.readGroup=()=>new Promise(r=>{resolve=r;});
 const old=c.loadWorkspace();await new Promise(setImmediate);
 c.pipelineFilters.modelId='';await c.loadWorkspace();resolve(approved);await old;
 assert.equal(c.workspace.groups.length,0);assert.equal(c.workspace.rows.length,0);
 c.pipelineFilters.modelId='6';c.pipelineOrm.searchRead=async()=>[];
 c.pipelineOrm.readGroup=()=>{throw new Error('No count request needed without draft pieces');};
 await c.loadWorkspace();assert.equal(c.workspace.error,false);assert.equal(c.workspace.groups.length,0);
 console.log('PASS: partial ribbon, 3 approved / 3 pending, exact policy/company matching, unchanged quantities/time/approval IDs and stale-request isolation');
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
