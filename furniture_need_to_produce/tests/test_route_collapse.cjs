// Standalone UI-state regression: node furniture_need_to_produce/tests/test_route_collapse.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/src/js/production_route_graph.js'), 'utf8');
const component = source.slice(source.indexOf('let graphSequence = 0;'), source.indexOf('registry.category("fields")'));
const calls = [];
const context = vm.createContext({
    Component: class {}, standardFieldProps: {}, useState: state => state,
    onWillUnmount: () => {},
    useService: name => ({call: () => calls.push(name), doAction: () => calls.push(name)}),
});
vm.runInContext(component.replace('export class ', 'class ') + '\nglobalThis.GraphField = NeedProduceGraphField;', context);
const first = new context.GraphField(), second = new context.GraphField();
first.setup(); second.setup();
assert.equal(first.ui.expanded, undefined);
assert.equal(second.ui.expanded, undefined);
assert.notEqual(first.graphId, second.graphId);
assert.equal(first.toggleRoute, undefined);
assert.deepEqual(calls, []);
const template = fs.readFileSync(path.join(__dirname, '../static/src/xml/production_route_graph.xml'), 'utf8');
assert.ok(template.includes('aria-label="مسار إنتاج القطعة"'));
assert.ok(!template.includes('ui.expanded'));
assert.ok(!template.includes('o_ntp_graph_toggle'));
// Odoo's kanban hotkeys otherwise swallow native Enter button activation.
assert.ok(template.includes('t-on-keydown.stop="() => {}"'));
assert.ok(template.includes('t-on-keyup.stop="() => {}"'));
console.log('PASS: routes always visible, no collapse control, keyboard isolation retained');
