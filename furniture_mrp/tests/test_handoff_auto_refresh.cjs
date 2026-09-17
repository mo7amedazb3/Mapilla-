// Run with: node furniture_mrp/tests/test_handoff_auto_refresh.cjs
// Execute the real dashboard and notification service with fake RPCs/timers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness() {
    let now = 0, sequence = 0;
    const timers = new Map(), unmount = [], calls = [], notices = [], subscriptions = new Map();
    const listeners = new Map();
    const bus = {
        trigger(name) { for (const callback of listeners.get(name) || []) callback(); },
    };
    const env = { bus };
    const services = {
        orm: { async call(...args) { calls.push(args); return args[1] === 'furniture_pending_handoff_notifications' ? [] : {}; } },
        notification: { add(message, options = {}) {
            const entry = { message, ...options, closed: false };
            notices.push(entry);
            return () => { if (!entry.closed) { entry.closed = true; options.onClose?.(); } };
        } },
        action: { doAction: async () => {} },
        dialog: {},
        bus_service: { subscribe: (name, callback) => subscriptions.set(name, callback), start() {} },
        multi_tab: { isOnMainTab: () => false },
        'mail.sound_effects': { play() {} },
    };
    const context = vm.createContext({
        _t: text => text,
        registry: { category: () => ({ add() {} }) },
        browser: { addEventListener() {} },
        Component: class {}, KanbanController: class {}, kanbanView: {}, standardActionServiceProps: {},
        useService: name => services[name], useState: value => value,
        useBus: (target, name, callback) => {
            const callbacks = listeners.get(name) || new Set();
            callbacks.add(callback); listeners.set(name, callbacks);
            unmount.push(() => callbacks.delete(callback));
        },
        onWillStart() {}, onWillUnmount: callback => unmount.push(callback),
        setInterval: () => 1, clearInterval() {},
        setTimeout: (callback, delay) => { const id = ++sequence; timers.set(id, { at: now + delay, callback }); return id; },
        clearTimeout: id => timers.delete(id),
    });
    for (const filename of ['store_request_notification_service.js', 'mrp_stage_dashboard.js']) {
        const source = fs.readFileSync(path.join(__dirname, '../static/src/js', filename), 'utf8')
            .replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
        vm.runInContext(source, context, { filename });
    }
    const Dashboard = vm.runInContext('FurnitureMrpStageDashboardPage', context);
    const service = vm.runInContext('furnitureStoreNotificationService', context);
    const event = vm.runInContext('HANDOFF_ACCEPTED_EVENT', context);
    const dashboard = new Dashboard();
    dashboard.env = env; dashboard.props = { action: { context: { test: true } } };
    dashboard.setup(); dashboard.stageDashboard.loading = false;
    const reloads = [];
    dashboard._reloadStage = async (stage, options) => reloads.push({ stage, options, at: now });
    service.start(env, services);
    return {
        dashboard, services, bus, event, notices, calls, timers, reloads,
        resolved: () => subscriptions.get('furniture_store_notification')({ handoff_event: 'resolved' }),
        pending: kind => subscriptions.get('furniture_store_notification')({
            handoff_event: 'pending', handoff_production_id: 42,
            handoff_id: kind === 'stage' ? 7 : 0, handoff_notification_key: kind + ':42',
        }),
        unmount: () => unmount.forEach(callback => callback()),
        async tick(ms) {
            const until = now + ms;
            while (true) {
                const next = [...timers.entries()].filter(([, item]) => item.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
                if (!next) break;
                now = next[1].at; timers.delete(next[0]); await next[1].callback();
            }
            now = until;
        },
    };
}

const handoff = { production_id: 42, handoff_id: 7, key: 'stage:7', kind: 'stage', quality_ready: true };
(async () => {
    for (const stage of ['priming', 'carpentry', 'bases', 'finishing', 'painting', 'tailoring', 'upholstery', 'packaging']) {
        const h = harness();
        h.dashboard.stageDashboard.selectedStageCode = stage;
        h.dashboard.stageDashboard.appliedDateFrom = '2026-09-01';
        h.dashboard.stageDashboard.appliedDateTo = '2026-09-07';
        await h.dashboard.acceptPendingHandoff(handoff);
        assert.equal(h.reloads.length, 0, stage + ': no immediate reload');
        await h.tick(999); assert.equal(h.reloads.length, 0);
        await h.tick(1); assert.equal(h.reloads.length, 1);
        assert.equal(h.reloads[0].stage, stage);
        assert.equal(h.reloads[0].options.preserveOrderSelection, true);
        assert.equal(h.dashboard.stageDashboard.appliedDateFrom, '2026-09-01');
        assert.equal(h.dashboard.stageDashboard.appliedDateTo, '2026-09-07');
        assert.equal(h.calls.filter(call => call[1] === 'action_accept_handoff_transfer').length, 1);
        h.unmount();
    }
    for (const kind of ['lane', 'stage']) {
        const h = harness(); h.pending(kind);
        const toast = h.notices.find(notice => notice.buttons);
        await toast.buttons[0].onClick();
        assert.equal(toast.closed, true);
        assert.equal(h.calls.some(call => call[1] === 'action_dismiss_handoff_notification'), false);
        await h.tick(999); assert.equal(h.reloads.length, 0);
        await h.tick(1); assert.equal(h.reloads.length, 1);
    }
    for (const path of ['dashboard', 'toast']) {
        const h = harness();
        h.services.orm.call = async () => { throw new Error('Transfer rejected'); };
        if (path === 'dashboard') await h.dashboard.acceptPendingHandoff(handoff);
        else { h.pending('stage'); await h.notices.find(notice => notice.buttons).buttons[0].onClick(); }
        await h.tick(5000);
        assert.equal(h.reloads.length, 0, path + ': failed acceptance must not refresh');
        assert.equal(h.notices.at(-1).type, 'danger');
    }
    {
        const h = harness(); h.pending('stage'); await h.tick(2000);
        assert.equal(h.reloads.length, 0, 'Pending notification is not acceptance');
        h.notices.find(notice => notice.buttons).onClose(); await h.tick(2000);
        assert.equal(h.reloads.length, 0, 'Dismissing a notification is not acceptance');
        await h.dashboard.acceptPendingHandoff({ ...handoff, quality_ready: false });
        assert.equal(h.calls.some(call => call[1] === 'action_accept_handoff_transfer'), false);
    }
    {
        const h = harness(); h.bus.trigger(h.event); await h.tick(400); h.resolved();
        assert.equal(h.timers.size, 1, 'RPC and websocket acknowledgement coalesce');
        await h.tick(999); assert.equal(h.reloads.length, 0);
        await h.tick(1); assert.equal(h.reloads.length, 1);
        await h.tick(3000); assert.equal(h.reloads.length, 1);
    }
    for (const busy of ['loading', 'busyHandoffKey', 'busyBatchToken', 'busyOrderActionKey']) {
        const h = harness(); h.bus.trigger(h.event); h.dashboard.stageDashboard[busy] = true;
        await h.tick(1000); assert.equal(h.reloads.length, 0);
        h.dashboard.stageDashboard[busy] = false;
        h.dashboard.stageDashboard.selectedStageCode = 'packaging';
        await h.tick(1000); assert.equal(h.reloads[0].stage, 'packaging', 'Do not return to an old stage');
    }
    {
        const h = harness(); h.bus.trigger(h.event); h.unmount();
        await h.tick(5000); h.bus.trigger(h.event); h.dashboard._scheduleHandoffRefresh();
        assert.equal(h.reloads.length, 0); assert.equal(h.timers.size, 0);
    }
    {
        const h = harness(); h.dashboard._reloadStage = async () => { throw new Error('Network error'); };
        h.resolved(); await h.tick(1000);
        assert.equal(h.notices.at(-1).type, 'warning', 'Refresh failure must not retry stock acceptance');
        assert.equal(h.calls.some(call => call[1] === 'action_accept_handoff_transfer'), false);
    }
    console.log('PASS: all 8 stages, both acceptance paths/kinds, exact 1s delay, failure/dismissal/quality guards, websocket, coalescing, busy state, navigation, unmount, reload error.');
})().catch(error => { console.error(error); process.exitCode = 1; });
