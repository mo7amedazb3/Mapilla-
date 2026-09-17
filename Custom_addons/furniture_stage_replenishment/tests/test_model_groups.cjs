// Run with node; exercises the production component with service stubs, no DB.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/src/js/stage_replenishment_dashboard.js'), 'utf8')
    .replace(/^import .*;$/gm, '').replace(/export /g, '');
const sandbox = {Component: class {}, registry: {category: () => ({add() {}})}};
vm.createContext(sandbox);
vm.runInContext(source + '\nthis.Dashboard = StageReplenishmentDashboard;', sandbox);
const app = Object.create(sandbox.Dashboard.prototype);
const names = ['فوتيه', 'شازلونج', 'كنبة كبيرة', 'بف', 'زاوية', 'كنبة صغيرة'];
const sorted = vm.runInContext('compareFurniturePieces', sandbox);
assert.equal([...names].sort(sorted)[0], 'كنبة كبيرة');
assert.equal([...names].sort(sorted).at(-1), 'فوتيه');
for (const name of ['كَنبة كَبيرة', 'كنبه كبيره', 'Large Sofa', '3-seater sofa']) {
    assert.equal(vm.runInContext('furniturePieceRank', sandbox)(name), 0);
}
for (const name of ['فوتية', 'فوتيه', 'فوتيه بيج مون', 'Armchair']) {
    assert.equal(vm.runInContext('furniturePieceRank', sandbox)(name), 2);
}
app.state = {rows: [], stages: [{code:'carpentry'}, {code:'tailoring'}],
    activeStage:'all', status:'all', search:'', selectedIds:{}, dirtyRows:{}};
const rows = [
    {id:4, model:'مارلي', product:'فوتيه', stage_code:'tailoring', current_qty:2.125, can_run:true, status:'to_produce'},
    {id:1, model:'بيج مون', product:'شازلونج', stage_code:'carpentry', current_qty:3, can_run:false, status:'ok'},
    {id:3, model:'بيج مون', product:'فوتيه', stage_code:'carpentry', current_qty:0, can_run:true, status:'to_produce'},
    {id:2, model:'بيج مون', product:'شازلونج', stage_code:'tailoring', current_qty:6, can_run:true, status:'to_produce'},
    {id:5, model:false, product:'صنف', stage_code:'other', current_qty:-1, can_run:false, status:'working'},
];
app.state.rows = rows;
const before = JSON.stringify(rows);
const groups = app.modelGroups;
assert.equal(groups.length, 3);
const model = groups.find(g => g.name === 'بيج مون');
assert.deepEqual(Array.from(model.rows, r => r.id), [1,2,3]);
assert.equal(model.rows[0], rows[1], 'Keeps original row identity');
assert.deepEqual(Array.from(model.rows, r => r.current_qty), [3,6,0], 'Never sums different stage balances');
assert.equal(JSON.stringify(rows), before, 'No mutation/reordering of input data');
assert.equal(new Set(groups.flatMap(g => Array.from(g.rows, r => r.id))).size, rows.length);
app.toggleVisibleRows();
assert.equal(app.selectedCount, 3);
assert.equal(app.allVisibleSelected, true);
assert.equal(app.state.selectedIds[1], undefined, 'Unavailable row is not selected');
app.toggleRow(rows[1]);
assert.equal(app.state.selectedIds[1], undefined);
let requested;
app.runIds = async ids => { requested = ids; };
(async () => {
    await app.runSelected();
    assert.deepEqual(Array.from(requested).sort(), [2,3,4]);
    await app.runRow(rows[3]);
    assert.deepEqual(Array.from(requested), [2], 'Action still targets exact stage rule');
    app.setStage('tailoring');
    assert.equal(app.selectedCount, 0);
    assert.equal(app.modelGroups.flatMap(g => g.rows).length, 2);
    assert(app.modelGroups.every(g => g.rows.every(r => r.stage_code === 'tailoring')));
    app.setStage('all');
    assert.equal(app.state.activeStage, 'tailoring', 'Removed all-stages tab cannot be selected');
    app.state.activeStage = 'all'; // Isolated grouping fixture covering multiple stages.
    app.updateSearch({target:{value:'شازلونج'}});
    assert.equal(app.modelGroups.length, 1);
    assert.equal(app.modelGroups[0].rows.length, 2);
    app.setStatus({target:{value:'ok'}});
    assert.equal(app.modelGroups[0].rows[0].id, 1);
    app.updateSearch({target:{value:'no matching model'}});
    assert.equal(app.modelGroups.length, 0);
    const xml = fs.readFileSync(path.join(root, 'static/src/xml/stage_replenishment_dashboard.xml'), 'utf8');
    const table = xml.slice(xml.indexOf('<table'), xml.indexOf('</table>'));
    assert(table.includes('t-foreach="modelGroups"'));
    assert(table.includes('scope="rowgroup"'));
    assert.equal((table.split('</thead>')[0].match(/<th\b/g) || []).length, 6);
    for (const restored of ['t-att-value="row.minimum"', 't-att-value="row.maximum"', 'this.saveLimits(row)', 'colspan="6"']) {
        assert(table.includes(restored), restored + ' must remain available in the grouped table');
    }
    for (const removed of ['row.recipe', 'row.forecast_qty', 'row.reserved_qty', 'row.qty_to_produce', 'statusLabel(row)']) {
        assert(!table.includes(removed), removed + ' must not be a visible column');
    }
    assert(table.includes('formatQty(row.current_qty)'));
    assert(table.includes('this.openOrders(row)'));
    assert(table.includes('this.runRow(row)'));
    assert.equal((table.match(/step="1"/g) || []).length, 2);
    assert(!table.includes('step="0.001"'));
    assert(table.includes('<small t-esc="row.unit"/>'), 'Keep Units from the original payload');
    const numberFormat = source.match(/this.quantityFormatter = (new Intl.NumberFormat\([\s\S]*?\));/)[1];
    app.quantityFormatter = vm.runInContext(numberFormat, sandbox);
    assert.equal(app.formatQty(0), '0');
    assert.equal(app.formatQty(12), '12');
    assert.equal(app.formatQty(1234.125), '1,234.125');
    app.state.search = '';
    app.state.status = 'all';
    app.state.savingRows = {};
    const messages = [], calls = [];
    app.notification = {add: (...args) => messages.push(args)};
    app.orm = {call: async (model, method, args) => {
        calls.push({model, method, args});
        return {rows: JSON.parse(JSON.stringify(rows)).map(r => r.id === args[0] ? {...r, minimum:args[1], maximum:args[2]} : r),
            stages:app.state.stages, summary:{}};
    }};
    const target = rows[0], pending = rows[2];
    app.setLimit(target, 'minimum', {target:{value:'1.25'}});
    app.setLimit(target, 'maximum', {target:{value:'4.5'}});
    app.setLimit(pending, 'minimum', {target:{value:'2'}});
    app.setLimit(pending, 'maximum', {target:{value:'8'}});
    assert(app.state.dirtyRows[target.id]);
    await app.saveLimits(target);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].model, 'furniture.mrp.stage.replenishment.rule');
    assert.equal(calls[0].method, 'stage_replenishment_update_limits');
    assert.deepEqual(Array.from(calls[0].args), [4,1.25,4.5]);
    assert(!app.state.dirtyRows[4]);
    assert(app.state.dirtyRows[3], 'Saving one row retains edits in another model');
    assert.equal(app.state.rows.find(r=>r.id===3).maximum, '8');
    assert.equal(app.state.savingRows[4], false);
    const saved = app.state.rows.find(r=>r.id===4);
    for (const [min,max] of [[-1,4], [5,4], ['bad',4]]) {
        saved.minimum = min; saved.maximum = max;
        await app.saveLimits(saved);
        assert.equal(calls.length, 1, 'Invalid values must not reach RPC');
        assert.equal(messages.at(-1)[1].type, 'warning');
    }
    console.log('PASS: grouping, stock, filters, selection, action IDs, Min/Max editors, save validation and pending edit preservation');
})().catch(error => { console.error(error); process.exitCode = 1; });
