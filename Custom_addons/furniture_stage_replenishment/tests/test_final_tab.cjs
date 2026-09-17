// Run with node. No database writes: both rule models use explicit RPC stubs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/src/js/stage_replenishment_dashboard.js'), 'utf8')
    .replace(/^import .*;$/gm, '').replace(/export /g, '');
const stageModel = 'furniture.mrp.stage.replenishment.rule';
const finalModel = 'furniture.mrp.final.replenishment.rule';
const stage = {stages:[{code:'painting',label:'تصنيع دهانات',total:1}, {code:'carpentry',label:'النجارة',total:1}],
    rows:[{id:1,model:'بيج مون',product:'شازلونج',stage_code:'carpentry',minimum:2,maximum:5,current_qty:3,can_run:true}],
    summary:{total_rules:1}};
const final = {rows:[{id:1,model:'بيج مون',product:'شازلونج',minimum:6,maximum:12,finished_qty:1.25,can_run:true}],
    summary:{total_rules:1,need_production:1}};
const clone = value => JSON.parse(JSON.stringify(value));
const calls = [], notices = [];
const orm = {call:async (model, method, args) => {
    calls.push({model,method,args});
    const result = clone(model===finalModel ? final : stage);
    if (method.endsWith('_update_limits')) Object.assign(result.rows[0], {minimum:args[1],maximum:args[2]});
    if (method.endsWith('_run_now')) return {created_count:0, data:result};
    if (method.endsWith('_open_orders')) return null;
    return result;
}};
const sandbox = {Component:class{}, registry:{category:()=>({add(){}})},
    useService:name=>name==='orm' ? orm : name==='notification' ? {add:(...args)=>notices.push(args)} : {},
    useState:value=>value, onWillStart(){}, console};
vm.createContext(sandbox);
vm.runInContext(source+'\nthis.Dashboard=StageReplenishmentDashboard;',sandbox);
function appFor(context={}) {
    const app = new sandbox.Dashboard();
    app.props={action:{context}}; app.setup(); return app;
}
(async () => {
    const app = appFor();
    await app.loadData(true);
    assert.equal(app.state.activeStage,'carpentry');
    assert.deepEqual(Array.from(app.state.stages, s=>s.code),['carpentry','painting','final']);
    assert.equal(app.state.stages.at(-1).label,'المنتج التام');
    assert.equal(app.state.stages.at(-1).total,1);
    app.toggleRow(app.state.rows[0]);
    app.setStage('final');
    assert.equal(app.selectedCount,0,'Stage selection must not leak through colliding final ID');
    assert.equal(app.ruleModel,finalModel);
    assert.equal(app.activeStageLabel,'المنتج التام');
    assert.equal(app.modelGroups[0].rows[0].current_qty,1.25);
    assert.equal(app.state.rows[0].minimum,6,'Uses original final limits, not stage limits');
    assert.equal(app.state.rows[0].maximum,12);
    assert.equal(app.state.rows[0].id,1,'Never copies or renumbers final rules');
    app.setLimit(app.state.rows[0],'minimum',{target:{value:'7'}});
    app.setStage('carpentry');
    assert.equal(app.state.activeStage,'final','Protect unsaved edits when switching stores');
    await app.saveLimits(app.state.rows[0]);
    assert.equal(calls.at(-1).model,finalModel);
    assert.equal(calls.at(-1).method,'final_replenishment_update_limits');
    assert.deepEqual(Array.from(calls.at(-1).args),[1,7,12]);
    app.setStage('carpentry');
    assert.equal(app.state.rows[0].minimum,2,'Saving final never overwrites stage limits');
    app.setStage('final');
    assert.equal(app.state.rows[0].minimum,7);
    app.state.savingRows[1]=true;
    app.setStage('carpentry');
    assert.equal(app.state.activeStage,'final','In-flight responses stay in their own store');
    app.state.savingRows[1]=false;
    await app.openOrders(app.state.rows[0]);
    assert.equal(calls.at(-1).method,'final_replenishment_open_orders');
    app.toggleVisibleRows();
    await app.runSelected();
    assert.equal(calls.at(-1).model,finalModel);
    assert.equal(calls.at(-1).method,'final_replenishment_run_now');
    assert.deepEqual(Array.from(calls.at(-1).args[0]),[1]);
    await app.runAll();
    assert.deepEqual(Array.from(calls.at(-1).args[0]),[1],'Bulk only targets active final rules');
    await app.syncRules();
    assert.equal(calls.at(-1).method,'final_replenishment_sync_rules');
    app.setStage('carpentry');
    await app.runAll();
    assert.equal(calls.at(-1).model,stageModel);
    assert.equal(calls.at(-1).method,'stage_replenishment_run_now');
    for (const stage_code of ['all','missing','priming']) {
        const legacy=appFor({stage_code}); await legacy.loadData(true);
        assert.equal(legacy.state.activeStage,'carpentry');
    }
    const deepLink=appFor({stage_code:'final'}); await deepLink.loadData(true);
    assert.equal(deepLink.state.activeStage,'final');
    assert.equal(deepLink.state.rows[0].maximum,12);
    assert.equal(JSON.stringify(final),JSON.stringify({rows:[{id:1,model:'بيج مون',product:'شازلونج',minimum:6,maximum:12,finished_qty:1.25,can_run:true}],summary:{total_rules:1,need_production:1}}),'Payloads never mutated');
    const xml=fs.readFileSync(path.join(root,'static/src/xml/stage_replenishment_dashboard.xml'),'utf8');
    assert(!xml.includes("stageTabClass('all')"));
    assert.equal((xml.match(/step="1"/g)||[]).length,2);
    const menu=fs.readFileSync(path.join(root,'views/stage_replenishment_views.xml'),'utf8');
    const rootOverride=menu.match(/<record id="menu_furniture_stage_replenishment_root"[\s\S]*?<\/record>/)[0];
    const childOverride=menu.match(/<record id="menu_furniture_stage_replenishment_all"[\s\S]*?<\/record>/)[0];
    assert(rootOverride.includes('<field name="action" ref="action_furniture_stage_replenishment_dashboard"/>'));
    assert(childOverride.includes('<field name="active" eval="False"/>'));
    assert(/id="menu_furniture_final_replenishment"[\s\S]*?active="False"/.test(menu));
    assert(/id="action_furniture_final_replenishment_dashboard"[\s\S]*?stage_replenishment\.stage_replenishment_dashboard[\s\S]*?'stage_code': 'final'/.test(menu));
    console.log('PASS: unified final tab, exact preserved limits, stock, ID isolation, RPC routing, save guards, removed all tab and legacy links');
})().catch(error=>{console.error(error);process.exitCode=1;});
