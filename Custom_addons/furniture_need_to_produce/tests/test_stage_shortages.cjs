const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const strip = text => text.replace(/^import .*;$/gm, '').replace(/export /g, '');
const context = vm.createContext({Component:class{}, KanbanController:class{}, standardFieldProps:{}, kanbanView:{Renderer:class{}},
    registry:{category:()=>({add(){}})}, Domain:{and:domains=>({toList:()=>domains.flat()})}});
vm.runInContext(strip(read('static/src/js/stage_shortages.js')) + '\n' +
    strip(read('static/src/js/production_pipeline.js')) + '\n' +
    strip(read('static/src/js/production_route_graph.js')) +
    '\nthis.api={STAGE_SHORTAGE_OPTIONS,stageShortageAction,ProductionWorkspaceController,NeedProduceGraphField};',context);
const {STAGE_SHORTAGE_OPTIONS: stages, stageShortageAction: action, ProductionWorkspaceController: Controller,
    NeedProduceGraphField: Graph} = context.api;
const raw = value => JSON.parse(JSON.stringify(value));
(async () => {
    assert.equal(stages.length,5);
    assert.equal(action('final'),null);
    assert.equal(action('finishing'),'furniture_need_to_produce.action_stage_shortages_preparation');
    assert.equal(action('not-an-action'),null);
    const c = Object.create(Controller.prototype), calls=[];
    c.env={searchModel:{globalContext:{ntp_stage_shortages:'frame'}}};
    c.pipelineBulk={busy:false,confirming:false};
    c.actionService={doAction:async id=>calls.push(id)};
    assert.equal(c.stageShortageLabel,'النجارة');
    assert.equal(stages[0].lane,'frame');
    const replenishmentSource = read('../furniture_stage_replenishment/models/stage_replenishment.py');
    assert.match(replenishmentSource, /'carpentry': 'النجارة',/);
    assert.match(replenishmentSource, /'frame': \('priming', 'carpentry'\)/);
    await c.openStageShortages(stages[0]);assert.equal(calls.length,0);
    await c.openStageShortages(stages[1]);assert.equal(calls[0],action('bases'));
    c.pipelineBulk.confirming=true;await c.openStageShortages(stages[2]);assert.equal(calls.length,1);
    c.pipelineBulk.confirming=false;c.pipelineBulk.busy=true;
    await c.openStageShortages(stages[2]);assert.equal(calls.length,1);
    c.env.searchModel.globalContext={};assert.equal(c.stageShortageLane,'');

    for (const stage of stages) {
        const paging=Object.create(Controller.prototype), domains=[];
        const scope=[['buffer_rule_id','!=',false],['final_rule_id','=',false],['target_lane','=',stage.lane]];
        paging.workspace={};paging.pipelineFilters={modelId:'6'};paging.workspaceRequest=0;
        paging.env={searchModel:{globalContext:{allowed_company_ids:[1]},globalDomain:scope}};
        paging.props={resModel:'furniture.need.to.produce'};
        paging.pipelineOrm={searchRead:async (_model,domain)=>{domains.push(domain);return [{id:11,
            product_id:[1,'صنف'],company_id:[1,'Factory'],state:'draft',target_lane:stage.lane,
            buffer_rule_id:[8,'rule'],preview_json:{stages:[]}}];},
            readGroup:async (_model,domain)=>{domains.push(domain);return [];}};
        await paging.loadWorkspace();
        assert.equal(domains.length,2);
        for (const domain of domains) for (const term of scope) {
            assert.ok(raw(domain).some(item=>JSON.stringify(item)===JSON.stringify(term)));
        }
        assert.equal(paging.workspace.rows[0].target_lane,stage.lane);
    }
    const field=Object.create(Graph.prototype);
    field.props={name:'route_graph',record:{data:{buffer_rule_id:[8,'buffer'],route_graph:{nodes:[],edges:[]}}}};
    for (const code of ['priming','tailoring','painting']) {
        const node={id:'s1',kind:'stage',stage_code:code,stage_id:10,label:code};
        field.props.record.data.route_graph={nodes:[node],edges:[]};
        assert.equal(field.standaloneStage,node);
        field.props.record.data.buffer_rule_id=false;assert.equal(field.standaloneStage,null);
        field.props.record.data.buffer_rule_id=[8,'buffer'];
        field.props.record.data.route_graph.edges=[{from:'stock',to:'s1'}];assert.equal(field.standaloneStage,null);
    }
    for (const code of ['carpentry','bases','finishing','packaging']) {
        field.props.record.data.route_graph={nodes:[{kind:'stage',stage_code:code}],edges:[]};
        assert.equal(field.standaloneStage,null);
    }
    // Min/Max review must dispatch to its own shortages application.
    const patches=[];
    context.StageReplenishmentDashboard=class{};context.FinalReplenishmentDashboard=class{};
    context.patch=(_prototype,extension)=>patches.push(extension);
    vm.runInContext(strip(read('static/src/js/minmax_workspace.js')),context);
    const review=patches[0].openNeedToProduce;
    const targets=[];const dashboard={guardUnsavedChanges:()=>true,state:{activeStage:'bases'},
        isFinalStage:false,action:{doAction:async id=>targets.push(id)}};
    await review.call(dashboard);assert.equal(targets.at(-1),action('bases'));
    dashboard.isFinalStage=true;await review.call(dashboard);
    assert.equal(targets.at(-1),'furniture_need_to_produce.action_need_to_produce');
    dashboard.guardUnsavedChanges=()=>false;await review.call(dashboard);assert.equal(targets.length,2);
    console.log('PASS: independent stage actions, approval scope, final isolation, standalone BoM access and Min/Max routing');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
