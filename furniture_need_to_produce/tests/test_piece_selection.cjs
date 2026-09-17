const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/src/js/production_pipeline.js'), 'utf8');
const context = vm.createContext({Component:class {}, KanbanController:class {}, standardFieldProps:{},
    kanbanView:{Renderer:class{}}, registry:{category:()=>({add(){}})}, ConfirmationDialog:class {}});
vm.runInContext(source.replace(/^import .*;$/gm,'').replace(/export /g,'')+
    '\nglobalThis.Controller=ProductionWorkspaceController;globalThis.Selector=ProductionPieceSelector;',context);
const plain=x=>JSON.parse(JSON.stringify(x));
(async()=>{
 const c=Object.create(context.Controller.prototype);
 const records=[4,5,6].map(resId=>({resId,selected:false,
  data:{state:'draft',product_id:[9,'شازلونج'],furniture_model_id:[6,'بيج مون']},
  async toggleSelection(selected){this.selected=selected;}}));
 const rows=[4,5,6,7,8].map(id=>({id,product_id:[id===8?10:9,'صنف']}));
 c.workspace={productId:9,rows,groups:[{id:9,name:'شازلونج'},{id:10,name:'فوتيه'}],loading:false,error:false};
 c.pipelineFilters={modelId:'6',modelName:'بيج مون',loading:false,loadError:false};
 c.pipelineBulk={canApprove:true,busy:false,confirming:false};
 c.model={root:{records,context:{allowed_company_ids:[1]},get selection(){return this.records.filter(r=>r.selected);},load:async()=>{}}};
 const selected=[];
 c.confirmPipelineIds=(ids,scope)=>selected.push({ids:[...ids],scope});
 assert.equal(c.workspaceApprovalLabel,'Approve الصنف كله');
 c.approveWorkspaceScope(); assert.deepEqual(selected.pop().ids,[4,5,6,7]); // full product, across pages
 const widget=Object.create(context.Selector.prototype);
 widget.env={ntpPipelineBulk:c.pipelineBulk};widget.props={record:records[0]};
 await widget.onChange({target:{checked:true}});
 await records[2].toggleSelection(true);
 assert.equal(c.workspaceApprovalLabel,'Approve المحدد');
 assert.equal(c.workspaceSelectedPieces.length,2);
 c.approveWorkspaceScope(); assert.deepEqual(plain(selected.pop().ids),[4,6]);
 await c.togglePipelinePage(); assert.equal(c.workspaceSelectedPieces.length,3);
 await c.togglePipelinePage(); assert.equal(c.workspaceSelectedPieces.length,0);
 assert.equal(c.workspaceApprovalLabel,'Approve الصنف كله');
 await records[1].toggleSelection(true);
 await c.clearPipelineSelection(); assert.equal(c.workspaceSelectedPieces.length,0);
 for(const field of ['busy','confirming']) {
  c.pipelineBulk[field]=true;
  c.approveWorkspaceScope(); await c.togglePipelinePage();
  assert.equal(selected.length,0); assert.equal(c.workspaceSelectedPieces.length,0);
  c.pipelineBulk[field]=false;
 }
 c.pipelineBulk.canApprove=false;c.approveWorkspaceScope();assert.equal(selected.length,0);c.pipelineBulk.canApprove=true;
 await records[0].toggleSelection(true);
 c.workspace.productId=10; // still loading old product records
 assert.equal(c.workspaceSelectedPieces.length,0);c.approveWorkspaceScope();assert.equal(selected.length,0);
 c.workspace.productId=9;c.pipelineFilters.modelId='5';
 c.approveWorkspaceScope();assert.equal(selected.length,0);c.pipelineFilters.modelId='6';
 const oldRecords=c.model.root.records;
 c.model.root.records=[]; // new page has no selected records
 assert.equal(c.workspaceSelectedPieces.length,0);
 c.model.root.records=oldRecords;
 c.workspace.productId=0;
 assert.equal(c.workspaceApprovalLabel,'Approve الموديل كله');
 c.approveWorkspaceScope();assert.deepEqual(selected.pop().ids,[4,5,6,7,8]);
 c.workspace.productId=9;
 delete c.confirmPipelineIds;
 let dialog;const calls=[];
 c.pipelineDialog={add:(_,props,options)=>{dialog={props,options};}};
 c.props={resModel:'furniture.need.to.produce'};
 c.pipelineOrm={call:async(...args)=>calls.push(args)};
 c.pipelineNotification={add(){}};c.refreshWorkspace=async()=>{};
 c.approveWorkspaceScope();
 assert.equal(calls.length,0);
 await records[1].toggleSelection(true); // frozen selection must not expand during confirmation
 await dialog.props.confirm();
 assert.equal(calls.length,1);assert.deepEqual(plain(calls[0][2]),[[4]]);
 dialog.options.onClose();
 console.log('PASS: one dynamic approval, exact selected IDs, all-product fallback, page/clear selection, permissions, navigation isolation and frozen confirmation');
})().catch(error=>{console.error(error);process.exitCode=1;});
