// node furniture_need_to_produce/tests/test_production_workspace.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/src/js/production_pipeline.js'), 'utf8');
const context = vm.createContext({
    Component: class {}, KanbanController: class {}, standardFieldProps: {}, kanbanView: {Renderer: class {}},
    registry: {category: () => ({add() {}})},
    Domain: {and: domains => ({toList: () => domains.flat()})},
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../../furniture_stage_replenishment/static/src/js/stage_replenishment_dashboard.js'), 'utf8').replace(/^import .*;$/gm, '').replace(/export /g, ''), context);
vm.runInContext(source.replace(/^import .*;$/gm, '').replace(/export /g, '') +
    '\nglobalThis.api={estimatePipelineGroup,groupPipelineProducts,workspaceIllustration,workspaceModelTheme,pipelineModelOptions,ProductionWorkspaceController,ProductionPipelineController};', context);
const {estimatePipelineGroup: estimate, groupPipelineProducts: group, ProductionWorkspaceController: Controller,
    ProductionPipelineController: Base} = context.api;
const raw = value => JSON.parse(JSON.stringify(value));
const orderedProducts = group(['فوتيه','شازلونج','كنبة كبيرة','بف','كنبة صغيرة'].map((name,id)=>({id,product_id:[id,name],preview_json:{stages:[]}})));
assert.equal(orderedProducts[0].name,'كنبة كبيرة');
assert.equal(orderedProducts.at(-1).name,'فوتيه');
function piece(id, product=1, ready=false) {
    const specs = ready ? [['packaging', []]] : [
        ['priming', []], ['carpentry', ['priming']], ['bases', ['carpentry']],
        ['finishing', ['bases']], ['tailoring', []], ['painting', []],
        ['upholstery', ['finishing','tailoring']], ['packaging', ['upholstery','painting']],
    ];
    return {id, product_id:[product, 'صنف '+product], company_id:[1,'Factory'], state:'draft',
        preview_json:{stages:specs.map(([code, dependencies]) => ({key:code, dependencies,
            operations:[{stage_code:code,estimated_hours:2}]}))}, unknown_incoming_wait:false};
}
(async () => {
    const theme = context.api.workspaceModelTheme;
    const modelIds = [5,6,7,850,851,852,853,854,855,856,857,858];
    assert.equal(new Set(modelIds.map(theme)).size,modelIds.length);
    for (const id of modelIds) {
        assert.equal(theme(id),theme(String(id)));
        assert.match(theme(id),/^--ntp-model-hue:\d+\.\d{2};--ntp-model-accent-hue:\d+\.\d{2}$/);
    }
    for (const invalid of [undefined,null,false,true,{},[],0,-1,1.5,Infinity,NaN,'','bad; color:red',Number.MAX_SAFE_INTEGER+1]) {
        assert.equal(theme(invalid),'');
    }
    const models = [{furniture_model_id:[6,'بيج مون']},{furniture_model_id:[5,'مارلي']}];
    const colors = values => raw(context.api.pipelineModelOptions(values).map(option=>[option.id,theme(option.id)])).sort();
    assert.deepEqual(colors(models),colors([...models].reverse()));
    assert.deepEqual(colors(models),colors([{furniture_model_id:[6,'renamed']},models[1]]));
    assert.equal(colors([models[0]])[0][1],theme(6));
    assert.equal(Controller.prototype.modelThemeStyle(6),theme(6));
    const hasPending = rows => Controller.prototype.hasPendingPieces(rows);
    assert.equal(hasPending(),false);
    assert.equal(hasPending([]),false);
    assert.equal(hasPending([{state:'approved'},{state:'done'},{state:'cancelled'}]),false);
    assert.equal(hasPending([{state:'draft'}]),true);
    assert.equal(hasPending([{state:'approved'},{state:'draft'}]),true);
    assert.equal(hasPending([{state:'approved'},{state:'approved'}]),false);
    assert.equal(context.api.workspaceIllustration('شازلونج'), 'chaise');
    assert.equal(context.api.workspaceIllustration('فوتيه'), 'armchair');
    assert.equal(context.api.workspaceIllustration('كنبة كبيرة'), 'sofa');
    assert.equal(context.api.workspaceIllustration('صنف آخر'), 'collection');
    assert.equal(context.api.workspaceIllustration(), 'collection');
    const original = [piece(1),piece(2),piece(3)];
    const snapshot = JSON.stringify(original);
    assert.equal(estimate([original[0]]).hours,12);
    assert.equal(estimate(original.slice(0,2)).hours,14);
    assert.equal(estimate(original).hours,16);
    assert.equal(estimate(original).days,1.6);
    const parallel = piece(40);
    const stages = parallel.preview_json.stages;
    stages.splice(2, 2, {key:'finish_pair', dependencies:['carpentry'], parallel_operations:true,
        operations:[{stage_code:'bases',estimated_hours:2},{stage_code:'finishing',estimated_hours:2}]});
    stages.find(stage=>stage.key==='upholstery').dependencies=['finish_pair','tailoring'];
    assert.equal(estimate([parallel]).hours,10);
    stages[2].operations[0].estimated_hours=5;
    assert.equal(estimate([parallel]).hours,13); // Join waits for slower bases too.
    assert.equal(JSON.stringify(original),snapshot);
    assert.equal(estimate([piece(1,1,true),piece(2,1,true),piece(3,1,true)]).hours,6);
    const unknown = piece(1,1,true); unknown.unknown_incoming_wait=true;
    assert.equal(estimate([unknown]).unknown,true);
    const fractional = piece(1,1,true); fractional.preview_json.stages[0].operations[0].estimated_hours=0.5;
    assert.equal(estimate([fractional]).hours,0.5);
    assert.equal(estimate([]).hours,0);
    assert.equal(estimate([{id:1,preview_json:{stages:[]}}]).hours,0);
    assert.equal(estimate([{id:1}]).hours,null);
    const cycle=piece(1,1,true); cycle.preview_json.stages[0].dependencies=['packaging'];
    assert.equal(estimate([cycle]).hours,null);
    const invalid=piece(1); invalid.preview_json.stages[0].operations[0].estimated_hours=-2;
    assert.equal(estimate([invalid]).hours,null);
    const otherCompany=piece(2); otherCompany.company_id=[2,'Other'];
    assert.equal(estimate([piece(1),otherCompany]).hours,12);
    const rows = Array.from({length:26},(_,i)=>piece(i+1,i<13?1:2,true));
    const groups = group(rows);
    assert.deepEqual(raw(groups.map(g=>[g.id,g.rows.length,g.time.hours])),[[1,13,26],[2,13,26]]);
    assert.equal(estimate(rows).hours,52);

    const c = Object.create(Controller.prototype);
    c.workspace={rows,groups,selectedProducts:[2],loading:false,error:false};
    c.pipelineFilters={modelName:'بيج مون'};
    const selections=[];
    c.confirmPipelineIds=(ids,label)=>selections.push([ids,label]);
    c.approveWorkspace('model'); c.approveWorkspace('product',1); c.approveWorkspace('selected');
    assert.equal(selections[0][0].length,26);
    assert.deepEqual(raw(selections[1][0]),Array.from({length:13},(_,i)=>i+1));
    assert.deepEqual(raw(selections[2][0]),Array.from({length:13},(_,i)=>i+14));
    assert.equal(c.workspaceSelectionCount,13);
    c.workspace.loading=true; c.approveWorkspace('model'); assert.equal(selections.length,3);

    const home = Object.create(Controller.prototype);
    let homeTarget;
    home.setWorkspaceLocation = id => { homeTarget=id; };
    home.clearPipelineModelFilter();
    assert.equal(homeTarget,0); // Explicit home button reuses read-only model reset.
    const template=fs.readFileSync(path.join(__dirname,'../static/src/xml/production_pipeline.xml'),'utf8');
    assert.match(template,/class="o_ntp_workspace_home" t-on-click="clearPipelineModelFilter"/);
    assert.match(template,/aria-label="الرئيسية — اختيار الموديل"/);
    assert.match(template,/button.clickParams.name !== 'action_refresh' and !evalViewModifier\(button.invisible\)/);
    assert.match(template,/<MultiRecordViewButton[\s\S]+?className="refreshButton.className \+ ' o_ntp_workspace_refresh'"[\s\S]+?clickParams="refreshButton.clickParams"[\s\S]+?string="'تحديث'"/);
    assert.match(template,/refreshButton.display === 'always' and !evalViewModifier\(refreshButton.invisible\)/);
    assert.match(template,/disabled="pipelineBulk.busy or pipelineBulk.confirming or workspace.loading or pipelineFilters.loading"/);
    assert.doesNotMatch(template,/o_ntp_workspace_model_picker|<select\b/);
    const css=fs.readFileSync(path.join(__dirname,'../static/src/css/production_pipeline.css'),'utf8');
    assert.match(css,/\.o_ntp_workspace_models\s*\{[^}]*grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/);
    assert.match(css,/@container ntp-model-catalog \(max-width:440px\)/);
    assert.match(template,/t-name="furniture_need_to_produce.PendingApprovalBadge"/);
    assert.match(template,/hasPendingPieces\(group.rows\)/);
    assert.match(template,/hasPendingPieces\(workspace.productId \? workspaceProduct\?\.rows : workspace.rows\)/);
    const views=fs.readFileSync(path.join(__dirname,'../views/production_plan_views.xml'),'utf8');
    assert.doesNotMatch(views,/class="o_ntp_piece_header"|class="o_ntp_piece_state"/);
    assert.match(views,/<article class="o_ntp_piece_pipeline">\s*<section class="o_ntp_piece_eta o_ntp_piece_eta_top"/);
    assert.match(views,/name="critical_path_hours" widget="need_produce_compact_time"/);
    assert.match(template,/o_ntp_workspace_model_card o_ntp_model_theme[^>]+modelThemeStyle\(option.id\)/);
    for (const area of ['nav','summary','products']) {
        assert.match(template,new RegExp('o_ntp_workspace_'+area+' o_ntp_model_theme[^>]+modelThemeStyle\\(pipelineFilters.modelId\\)'));
    }

    const bulk=Object.create(Base.prototype), calls=[];
    let dialog;
    bulk.pipelineBulk={canApprove:true,busy:false,confirming:false};
    bulk.pipelineDialog={add:(_,props,options)=>{dialog={props,options};}};
    bulk.model={root:{context:{allowed_company_ids:[1]},load:async()=>{}}};
    bulk.props={resModel:'furniture.need.to.produce'};
    bulk.pipelineOrm={call:async(...args)=>calls.push(args)};
    bulk.pipelineNotification={add() {}};
    context.ConfirmationDialog=class {};
    const ids=Array.from({length:26},(_,i)=>i+1);
    bulk.confirmPipelineIds(ids,'بيج مون');
    ids.push(999);
    assert.equal(calls.length,0); // review before mutation
    bulk.confirmPipelineIds([999],'another'); // blocked during confirmation
    await dialog.props.confirm();
    assert.equal(calls.length,1);
    assert.deepEqual(raw(calls[0][2][0]),Array.from({length:26},(_,i)=>i+1));
    assert.deepEqual(raw(calls[0][3].context.allowed_company_ids),[1]);
    dialog.options.onClose();
    bulk.pipelineBulk.canApprove=false;
    bulk.confirmPipelineIds([1],'denied'); assert.equal(calls.length,1);

    const paging=Object.create(Controller.prototype);
    paging.workspace={}; paging.pipelineFilters={modelId:'6'}; paging.workspaceRequest=0;
    paging.env={searchModel:{globalDomain:[['company_id','=',1]],globalContext:{allowed_company_ids:[1]}}};
    paging.props={resModel:'furniture.need.to.produce'};
    const queryCalls=[];
    paging.pipelineOrm={readGroup:async()=>[],searchRead:async(model,domain,fields,options)=>{
        queryCalls.push({domain,options});
        return queryCalls.length===1 ? Array.from({length:500},(_,i)=>piece(i+1,1,true)) : [piece(501,1,true)];
    }};
    await paging.loadWorkspace();
    assert.equal(paging.workspace.rows.length,501);
    assert.equal(paging.workspace.groups[0].rows.length,501);
    assert.equal(queryCalls.length,2);
    assert.ok(queryCalls[1].domain.some(term=>term[0]==='id' && term[2]===500));
    assert.ok(queryCalls[0].domain.some(term=>term[0]==='furniture_model_id' && term[2]===6));
    assert.ok(queryCalls[0].domain.some(term=>term[0]==='state' && term[2]==='draft'));
    assert.ok(queryCalls[0].domain.some(term=>term[0]==='company_id' && term[2]===1));
    let resolveOld;
    paging.pipelineOrm.searchRead=()=>new Promise(resolve=>{resolveOld=resolve;});
    const pending=paging.loadWorkspace();
    paging.pipelineFilters.modelId=''; await paging.loadWorkspace();
    resolveOld([piece(1000)]); await pending;
    assert.equal(paging.workspace.rows.length,0); // switched model: ignore stale result
    console.log('PASS: parallel timing, unknown waits, grouping, 26-piece approval snapshot, permissions, 501-row paging and stale responses');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
