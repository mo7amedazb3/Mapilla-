// Run with: node furniture_mrp/tests/test_product_quantity_status.cjs
// Exercise the actual client helper without an Odoo/browser installation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/src/js/mrp_stage_dashboard.js'), 'utf8')
    .replace(/^import .*;\n/gm, '')
    .replace(/^export /gm, '');
const context = vm.createContext({
    _t: text => text,
    registry: { category: () => ({ add() {} }) },
    Component: class {},
    KanbanController: class {},
    kanbanView: {},
    standardActionServiceProps: {},
});
vm.runInContext(source, context);

const cases = [
    ['not started', { planned_qty: 6, remaining_qty: 6 }, 'remaining', 'متبقي', 6],
    ['mixed working/completed/waiting', { planned_qty: 6, working_qty: 2, completed_qty: 1, remaining_qty: 3 }, 'working', 'قيد التشغيل', 2],
    ['fully completed', { planned_qty: 6, completed_qty: 6 }, 'completed', 'مكتمل', 6],
    ['partly completed and idle', { planned_qty: 6, completed_qty: 2, remaining_qty: 4 }, 'remaining', 'متبقي', 4],
    ['awaiting quality counted once', { planned_qty: 6, working_qty: 4, quality_qty: 4, completed_qty: 2 }, 'working', 'قيد التشغيل', 4],
    ['zero planned is not completed', { planned_qty: 0 }, 'remaining', 'متبقي', 0],
    ['fractional completion tolerance', { planned_qty: 0.3, completed_qty: 0.1 + 0.2 }, 'completed', 'مكتمل', 0.1 + 0.2],
    ['numeric strings', { planned_qty: '6', working_qty: '2', remaining_qty: '4' }, 'working', 'قيد التشغيل', 2],
    ['material warning does not create fourth quantity box', { planned_qty: 6, remaining_qty: 6, has_material_shortage: true }, 'remaining', 'متبقي', 6],
    ['missing payload', undefined, 'remaining', 'متبقي', 0],
    ['invalid numbers', { planned_qty: 'invalid', working_qty: Infinity, completed_qty: NaN }, 'remaining', 'متبقي', 0],
];
for (const [name, values, state, label, quantity] of cases) {
    const before = values && { ...values };
    const actual = context.productQuantityStatus(values);
    assert.deepEqual({ ...actual }, { state, label, quantity }, name);
    assert.deepEqual(values, before, name + ': helper must not mutate quantities');
    const normalized = context.normalizeProductLine(values);
    assert.deepEqual({ ...normalized.quantity_status }, { state, label, quantity }, name + ': normalization');
}
// Other operational indicators must retain their existing semantics.
assert.equal(context.operationalStatus({ completed_qty: 2, planned_qty: 6 }).state, 'running');
assert.equal(context.operationalStatus({ has_material_shortage: true }).state, 'shortage');
console.log(`${cases.length} product quantity status cases and operational-state isolation PASS`);

const Dashboard = vm.runInContext('FurnitureMrpStageDashboardPage', context);
const textileDetails = Object.getOwnPropertyDescriptor(Dashboard.prototype, 'showOrderTextileDetails').get;
for (const stage of ['priming', 'painting', 'carpentry', 'bases', 'finishing', 'tailoring', 'upholstery', 'packaging', undefined]) {
    assert.equal(textileDetails.call({ selectedStageCode: stage }),
        ['tailoring', 'upholstery', 'packaging'].includes(stage), `Shared order detail visibility for ${stage}`);
}
console.log('Shared order details restricted to tailoring/upholstery/packaging across all 8 stages PASS');
