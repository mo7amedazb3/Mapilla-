const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
const ctx=vm.createContext({Component:class{},KanbanController:class{},standardFieldProps:{},kanbanView:{Renderer:class{}},registry:{category:()=>({add(){}})}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/src/js/production_pipeline.js'),'utf8').replace(/^import .*;$/gm,'').replace(/export /g,'')+'\nthis.Time=ProductionPieceTime;',ctx);
const time=Object.create(ctx.Time.prototype);
for(const [value,expected] of [[10,'10'],[1,'1'],[1.2,'1.2'],[0.25,'0.25'],[0,'0'],[12.34,'12.34']]){
 time.props={name:'hours',record:{data:{hours:value}}}; assert.equal(time.value,expected);
}
console.log('PASS integer times trim zeros, fractional time retained, English digits');
const views=fs.readFileSync(path.join(__dirname,'../views/production_plan_views.xml'),'utf8');
const templates=fs.readFileSync(path.join(__dirname,'../static/src/xml/production_pipeline.xml'),'utf8');
assert.match(views,/o_ntp_piece_eta_top"[^>]*>\s*<field name="state" widget="need_produce_selector"/);
assert.match(templates,/t-if="pipelinePageSelected">إلغاء تحديد الكل<\/t><t t-else="">تحديد الكل/);
assert.doesNotMatch(templates,/o_ntp_clear_selection/);
console.log('PASS checkbox before ETA and one select-all toggle');
