const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/src/context_help.js'), 'utf8');
const code = source.slice(source.indexOf('export function installContextHelp'), source.indexOf('registry.category("services")')).replace('export function', 'function');
const ctx = {}; vm.createContext(ctx); vm.runInContext(code, ctx);
const listeners = new Map(), opened = [];
const root = { addEventListener: (k, fn, capture) => {assert.equal(capture,true); listeners.set(k,fn);},
 removeEventListener: (k,fn,capture) => {assert.equal(capture,true); assert.equal(listeners.get(k),fn); listeners.delete(k);} };
const stop = ctx.installContextHelp(root, props => opened.push(props));
const make = (button, key) => ({ target:{ closest:()=>button }, key, prevented:false, stopped:false,
 preventDefault(){this.prevented=true}, stopPropagation(){this.stopped=true} });
const button = {dataset:{helpMessage:'مراجعة قبل الاعتماد\nموديلك، أصنافه، وكل قطعة بمسارها.',helpTitle:'نبدأ بموديل إيه؟'}};
let event=make(button);listeners.get('click')(event);
assert.equal(opened.length,1);assert.equal(opened[0].message,button.dataset.helpMessage);
assert.ok(event.prevented && event.stopped);
for (const key of ['Enter',' ']) {event=make(button,key);listeners.get('keydown')(event);assert.ok(event.stopped);assert.equal(event.prevented,false);}
for (const key of ['Escape','Tab','ArrowLeft']) {event=make(button,key);listeners.get('keydown')(event);assert.equal(event.stopped,false);}
for (const target of [null,{...button,disabled:true},{dataset:{helpMessage:' '}}]) {event=make(target);listeners.get('click')(event);assert.equal(event.stopped,false);}
assert.equal(opened.length,1);stop();assert.equal(listeners.size,0);
assert.ok(!source.includes('innerHTML'));assert.ok(!source.includes('MutationObserver'));
console.log('Context help: explicit triggers, click isolation, keyboard, disabled/empty, cleanup and plain text passed');

const template = fs.readFileSync(path.join(__dirname, '../static/src/context_help.xml'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../static/src/context_help.css'), 'utf8');
assert.match(template, /contentClass="'o_factory_help_modal'"/);
assert.match(template, /t-set-slot="header"/);
assert.match(template, /t-esc="props.title"/);
assert.match(template, /t-esc="props.message"/);
assert.equal((template.match(/t-on-click="props.close"/g) || []).length, 2);
assert.match(template, /aria-label="إغلاق الشرح"/);
assert.ok(!template.includes('t-raw'));
assert.match(css, /modal-dialog:has\(> \.o_factory_help_modal\)/);
assert.match(css, /max-width: 520px/);
assert.match(css, /prefers-reduced-motion: reduce/);
assert.match(css, /button:focus-visible/);
// Native Dialog remains the focus/keyboard owner, without patching other dialogs.
assert.match(source, /static components = \{ Dialog \}/);
assert.ok(!source.includes('patch('));
const dialogClass = source.slice(source.indexOf('export class ContextHelpDialog'), source.indexOf('export class ContextHelpWidget'))
    .replace('export class', 'class');
const mounted = [];
const dialogContext = { Component: class {}, Dialog: {}, onMounted: fn => mounted.push(fn) };
vm.createContext(dialogContext);
vm.runInContext(dialogClass + '; globalThis.HelpDialog = ContextHelpDialog;', dialogContext);
const helpDialog = new dialogContext.HelpDialog();
helpDialog.props = { title: 'شرح Min / Max' };
const attrs = {};
helpDialog.setup();
const modalRef = {el: null};
helpDialog.setModalRef(modalRef);
assert.deepEqual(attrs, {}, 'No DOM access during child setup');
modalRef.el = {setAttribute: (name,value) => attrs[name] = value};
mounted[0]();
assert.deepEqual(attrs, {'aria-label': helpDialog.props.title, 'aria-modal': 'true'});
helpDialog.labelDialog(null);
console.log('Help design: scoped native dialog, escaped copy, close controls, accessible name and reduced motion passed');
