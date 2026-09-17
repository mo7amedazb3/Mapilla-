const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/src/js/production_pipeline.js'), 'utf8');
const document = {body: {}, documentElement: {}, querySelector: () => null};
let reducedMotion = false;
const window = {matchMedia: query => {
    assert.equal(query, '(prefers-reduced-motion: reduce)');
    return {matches: reducedMotion};
}};
const context = vm.createContext({document, window, Component: class {}, KanbanController: class {},
    standardFieldProps: {}, kanbanView: {Renderer:class{}}, registry: {category: () => ({add() {}})}, ConfirmationDialog: class {}});
vm.runInContext(source.replace(/^import .*;$/gm, '').replace(/export /g, '') +
    '\nglobalThis.Controller=ProductionWorkspaceController;globalThis.acceptKey=isWorkspaceTopKey;', context);
const target = (kind = '') => ({
    matches: () => kind === 'piece-checkbox',
    closest: selector => selector === 'input'
        ? ['input', 'piece-checkbox', 'other-checkbox'].includes(kind)
        : ['button', 'link', 'select', 'textarea', 'editable', 'role-button', 'role-textbox'].includes(kind),
});
const event = (kind = '', extra = {}) => ({key: 'Enter', target: target(kind),
    preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...extra});
assert(context.acceptKey(event()));
assert(context.acceptKey(event('piece-checkbox')));
for (const kind of ['input', 'other-checkbox', 'button', 'link', 'select', 'textarea', 'editable', 'role-button', 'role-textbox']) {
    assert(!context.acceptKey(event(kind)), kind);
}
for (const field of ['defaultPrevented', 'isComposing', 'repeat', 'altKey', 'ctrlKey', 'metaKey', 'shiftKey']) {
    assert(!context.acceptKey(event('', {[field]: true})), field);
}
assert(!context.acceptKey(event('', {key: 'Space'})));
assert(!context.acceptKey(event('', {keyCode: 229})));
const c = Object.create(context.Controller.prototype);
let scrolls = 0;
const root = {isConnected: true, getClientRects: () => [1], contains: () => true};
c.rootRef = {el: root}; c.env = {}; c.pipelineBulk = {};
c.scrollWorkspaceToTop = () => scrolls++;
let e = event('piece-checkbox');
c.onWorkspaceTopKeyDown(e);
assert.equal(scrolls, 1); assert(e.defaultPrevented && e.stopped);
const release = event(); c.onWorkspaceTopKeyUp(release);
assert(release.defaultPrevented && release.stopped); assert.equal(c.workspaceReturnHandled, false);
const untouched = event(); c.onWorkspaceTopKeyUp(untouched); assert(!untouched.defaultPrevented);
for (const field of ['busy', 'confirming']) {
    c.pipelineBulk[field] = true; c.onWorkspaceTopKeyDown(event()); delete c.pipelineBulk[field];
}
c.env.inDialog = true; c.onWorkspaceTopKeyDown(event()); delete c.env.inDialog;
document.querySelector = () => ({}); c.onWorkspaceTopKeyDown(event()); document.querySelector = () => null;
root.isConnected = false; c.onWorkspaceTopKeyDown(event()); root.isConnected = true;
root.getClientRects = () => []; c.onWorkspaceTopKeyDown(event()); root.getClientRects = () => [1];
c.pipelineDestroyed = true; c.onWorkspaceTopKeyDown(event()); c.pipelineDestroyed = false;
root.contains = () => false; c.onWorkspaceTopKeyDown(event());
assert.equal(scrolls, 1, 'dialogs, inactive views, busy actions and outside targets must not scroll');
c.onWorkspaceTopKeyDown(event('', {target: document.body}));
assert.equal(scrolls, 2, 'unfocused page Enter returns to the top');
delete c.scrollWorkspaceToTop;
const calls = [];
root.scrollTo = options => calls.push({container: 'action', ...options});
root.querySelector = selector => {
    assert.equal(selector, '.o_content');
    return {scrollTo: options => calls.push({container: 'content', ...options})};
};
c.onPageChangeScroll = () => assert.fail('shortcut must not change native pager scrolling');
for (const isSmall of [false, true]) {
    c.env.isSmall = isSmall;
    for (const reduced of [false, true]) {
        reducedMotion = reduced; c.scrollWorkspaceToTop();
        assert.deepEqual(calls.pop(), {container: isSmall ? 'action' : 'content', top: 0,
            behavior: reduced ? 'instant' : 'smooth'});
    }
}
console.log('PASS: smooth Enter/Return, desktop/mobile containers, reduced-motion preference, checkbox selection, native input/button behavior, modifiers, composition, dialog and view isolation');
