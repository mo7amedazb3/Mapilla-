// node factory_biometric_connection_alert/tests/test_alert_dismissal.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../static/src/connection_alert.js'),'utf8');
const template = fs.readFileSync(path.join(__dirname,'../static/src/connection_alert.xml'),'utf8');
const offline = {enabled:true,devices:[{id:1,name:'Test device',last_seen:'2026-09-07 17:03:57'}]};
const tick = () => new Promise(resolve=>setImmediate(resolve));

function mount(enabled=true) {
    const h={response:offline,calls:0,fail:false,intervals:new Map(),listeners:new Map()};
    let cleanup;
    const context={
        Component:class {}, useState:value=>value, onWillUnmount:fn=>{cleanup=fn;},
        session:{factory_biometric_connection_alert:enabled}, user:{context:{}},
        deserializeDateTime:()=>({toFormat:()=> '07/09/2026 20:03:57'}),
        registry:{category:()=>({add(){}})},
        window:{addEventListener:(name,fn)=>h.listeners.set(name,fn),removeEventListener:name=>h.listeners.delete(name)},
        browser:{setInterval:fn=>{h.intervals.set(1,fn);return 1;},clearInterval:id=>h.intervals.delete(id),setTimeout:()=>2,clearTimeout(){}},
        rpc:(url,params)=>{
            assert.equal(params.method,'get_connection_alert_status');h.calls++;
            const request=h.fail ? Promise.reject(new Error('offline RPC')) : Promise.resolve(h.response);
            request.abort=()=>{};return request;
        },
    };
    vm.runInNewContext(source.replace(/^import .*;$/gm,'').replace(/export /g,'')+'\nglobalThis.Alert=BiometricConnectionAlert;',context);
    h.component=new context.Alert();h.component.setup();h.unmount=()=>cleanup();return h;
}
(async()=>{
    const h=mount();await tick();
    assert.equal(h.calls,1);assert.equal(h.component.state.dismissed,false);
    assert.equal(h.component.state.devices.length,1);
    h.component.dismiss();assert.equal(h.component.state.dismissed,true);
    for(let i=0;i<3;i++){await h.component.refresh();assert.equal(h.component.state.dismissed,true);}
    assert.ok(h.intervals.size,'Closing does not stop monitoring');
    h.listeners.get('focus')();await tick();assert.equal(h.component.state.dismissed,true);
    h.fail=true;await h.component.refresh();
    assert.equal(h.component.state.error,true);assert.equal(h.component.state.dismissed,true);
    assert.equal(h.component.state.devices.length,1,'RPC failure does not imply recovery');
    const reopened=mount();await tick();assert.equal(reopened.component.state.dismissed,false);
    reopened.unmount();
    h.fail=false;h.response={enabled:true,devices:[]};await h.component.refresh();
    assert.equal(h.component.state.dismissed,false);assert.equal(h.component.state.error,false);
    h.response=offline;await h.component.refresh();
    assert.equal(h.component.state.devices.length,1);assert.equal(h.component.state.dismissed,false);
    h.component.dismiss();h.response={enabled:false,devices:[]};await h.component.refresh();
    assert.equal(h.intervals.size,0);assert.equal(h.component.enabled,false);
    h.unmount();assert.equal(h.listeners.size,0);
    const disabled=mount(false);await tick();assert.equal(disabled.calls,0);assert.equal(disabled.listeners.size,0);disabled.unmount();
    assert.ok(!/localStorage|sessionStorage/.test(source));
    assert.ok(template.includes('!state.dismissed and (state.devices.length or state.error)'));
    assert.ok(template.includes('aria-label="إغلاق التنبيه"') && template.includes('t-on-click="dismiss"'));
    assert.ok(!template.includes('لم يصل اتصال من الجهاز منذ دقيقتين'));
    console.log('PASS: close, polling/focus suppression, fresh page, recovery/new outage, RPC failure, disabled scope, cleanup and template');
})().catch(error=>{console.error(error);process.exitCode=1;});
