// Standalone component-method regression checks; no Odoo or browser writes.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/src/js/attendance_dashboard.js'), 'utf8')
    .replace(/import\s+[\s\S]*?from\s+['"][^'"]+['"];\s*/g, '')
    .replace('export class FactoryAttendanceDashboard', 'class FactoryAttendanceDashboard');
const timers = new Map();
const intervals = new Map();
let serial = 0;
const hooks = {};
const ctx = vm.createContext({
    Component: class {},
    registry: { category: () => ({ add() {} }) },
    useService: () => ({}), useRef: () => ({el: null}), useState: value => value,
    onWillStart: fn => { hooks.start = fn; },
    onMounted: fn => { hooks.mounted = fn; },
    onWillDestroy: fn => { hooks.destroy = fn; },
    document: {
        body: { classList: { add() {}, remove() {} } },
        addEventListener() {},
        removeEventListener() {},
    },
    setTimeout: (fn, delay) => { const id = ++serial; timers.set(id, {fn, delay}); return id; },
    clearTimeout: id => timers.delete(id),
    setInterval: (fn, delay) => { const id = ++serial; intervals.set(id, {fn, delay}); return id; },
    clearInterval: id => intervals.delete(id),
});
vm.runInContext(source + '\nglobalThis.Dashboard = FactoryAttendanceDashboard;', ctx);
const dashboard = new ctx.Dashboard();
dashboard.setup();
assert.equal(dashboard.statusLabel('not_started'), '—');
assert.equal(dashboard.statusLabel('absent'), 'غياب');
assert.equal(dashboard.statusLabel('at_work'), 'حاضر');
assert.equal(dashboard.statusLabel('checked_out'), 'انصرف');
assert.equal(dashboard.followToday, true);
dashboard.state.data.today = '2026-09-12';
dashboard.loadDashboard = async () => {};
const fakeEvent = {preventDefault() {}, stopPropagation() {}};
dashboard.chooseCalendarDate({future: false, iso: '2026-09-11'}, fakeEvent);
assert.equal(dashboard.followToday, false);
dashboard.chooseToday(fakeEvent);
assert.equal(dashboard.followToday, true);
const loads = [];
dashboard.loadDashboard = options => loads.push(options);
dashboard.scheduleStatusRefresh(1001);
assert.equal(timers.size, 1);
assert.equal(timers.get(dashboard.statusRefreshTimer).delay, 1001);
const previous = dashboard.statusRefreshTimer;
dashboard.scheduleStatusRefresh(3001);
assert.equal(timers.size, 1);
assert.ok(!timers.has(previous));
const callback = timers.get(dashboard.statusRefreshTimer).fn;
timers.delete(dashboard.statusRefreshTimer);
callback();
assert.equal(loads.length, 1);
assert.equal(loads[0].silent, true);
assert.equal(dashboard.statusRefreshTimer, null);
for (const invalid of [false, null, undefined, NaN, 0, -1, '1000']) {
    dashboard.scheduleStatusRefresh(500);
    dashboard.scheduleStatusRefresh(invalid);
    assert.equal(timers.size, 0);
}
dashboard.scheduleStatusRefresh(1);
assert.equal(timers.get(dashboard.statusRefreshTimer).delay, 50);
hooks.mounted();
hooks.destroy();
assert.equal(timers.size, 0);
assert.equal(intervals.size, 0);
console.log('PASS: dash labels, shift-start refresh, replacement/cancellation, invalid delays, unmount cleanup');
