'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { SendReceipts } = require('./send-receipts');
const { patchSource, before, after } = require('./patch-send-result');
const { serializedMessageKey } = require('./message-key');

async function store(t) {
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'wa-receipts-test-'));
    t.after(() => fs.rm(directory, { recursive: true, force: true }));
    return { directory, receipts: new SendReceipts(directory) };
}
const result = { status: 'success', messageId: 'true_real_generated_key' };
const payload = { phone: 'test-recipient', message: '..' };

test('five Odoo transaction retries perform exactly one external send', async t => {
    const { receipts } = await store(t); let sends = 0;
    for (let i = 0; i < 5; i++) {
        assert.deepEqual(await receipts.run('instance:send', 'request-123', payload, async () => { sends++; return result; }), result);
    }
    assert.equal(sends, 1);
});
test('concurrent copies share the same result, without a second send', async t => {
    const { receipts } = await store(t); let sends = 0;
    const send = async () => { sends++; await new Promise(r => setTimeout(r, 15)); return result; };
    const responses = await Promise.all(Array.from({length:5}, () => receipts.run('instance:send', 'request-123', payload, send)));
    assert.equal(sends, 1); assert(responses.every(r => r.messageId === result.messageId));
});
test('success remains deduplicated after a service restart', async t => {
    const { receipts, directory } = await store(t); let sends = 0;
    const send = async () => { sends++; return result; };
    await receipts.run('instance:send', 'request-123', payload, send);
    assert.deepEqual(await new SendReceipts(directory).run('instance:send', 'request-123', payload, send), result);
    assert.equal(sends, 1);
});
test('uncertain send is recorded and never retried, including after restart', async t => {
    const { receipts, directory } = await store(t); let sends = 0;
    const send = async () => { sends++; throw Object.assign(new Error('confirmation missing'), {status:504}); };
    for (let i = 0; i < 5; i++) await assert.rejects(receipts.run('instance:send', 'request-123', payload, send), /confirmation missing/);
    await assert.rejects(new SendReceipts(directory).run('instance:send', 'request-123', payload, send), /confirmation missing/);
    assert.equal(sends, 1);
});
test('pending durable claim blocks a replay after interruption', async t => {
    const { receipts, directory } = await store(t); let release, sends = 0;
    const started = new Promise(resolve => { release = resolve; });
    const original = receipts.run('instance:send', 'request-123', payload, async () => { sends++; release(); return new Promise(() => {}); });
    void original; await started;
    await assert.rejects(new SendReceipts(directory).run('instance:send', 'request-123', payload, async () => { sends++; }), /منع تكرار/);
    assert.equal(sends, 1);
});
test('reusing a nonce with different content fails closed', async t => {
    const { receipts } = await store(t); let sends = 0;
    const send = async () => { sends++; return result; };
    await receipts.run('instance:send', 'request-123', payload, send);
    await assert.rejects(receipts.run('instance:send', 'request-123', {...payload,message:'other'}, send), /different content/);
    assert.equal(sends, 1);
});
test('two deliberate messages with different IDs both send, even if text is identical', async t => {
    const { receipts } = await store(t); let sends = 0;
    for (const key of ['request-one','request-two']) await receipts.run('instance:send', key, payload, async () => { sends++; return result; });
    assert.equal(sends, 2);
});
test('missing ID is rejected before any external send', async t => {
    const { receipts } = await store(t); let sends = 0;
    await assert.rejects(receipts.run('instance:send', undefined, payload, async () => { sends++; }), /requestId is required/);
    assert.equal(sends, 0);
});
test('compatibility patch recovers exactly the original generated key after PN to LID change', () => {
    const sent = {id:{fromMe:true,id:'generated-123',_serialized:'true_123@lid_generated-123'}};
    const unrelated = {id:{fromMe:true,id:'unrelated'},body:'..'};
    const window = {require: () => ({Msg:{get:() => undefined,getModelsArray:() => [unrelated,sent]}})};
    const key = {id:'generated-123',_serialized:'true_123@c.us_generated-123'};
    assert.equal(new Function('window','newMsgKey', before)(window,key), undefined);
    assert.equal(new Function('window','newMsgKey', after)(window,key), sent);
    assert.equal(new Function('window','newMsgKey', after)(window,{id:'missing'}), undefined);
    assert.equal(patchSource(patchSource(before)), after);
    assert.throws(() => patchSource('changed upstream implementation'), /Unsupported/);
});

test('current WhatsApp serialized message key without _serialized keeps its exact identity', () => {
    const key = {fromMe:true,remote:{_serialized:'123456@lid'},id:'GENERATED', $1:'true_123456@lid_GENERATED'};
    assert.equal(serializedMessageKey(key), key.$1);
    assert.equal(serializedMessageKey({...key,remote:'123456@lid'}), key.$1);
    assert.equal(serializedMessageKey({...key,fromMe:false}), 'false_123456@lid_GENERATED');
    assert.equal(serializedMessageKey({fromMe:false,remote:'123@g.us',id:'KEY',participant:{_serialized:'456@lid'}}), 'false_123@g.us_KEY_456@lid');
    assert.equal(serializedMessageKey({_serialized:'legacy-exact-key'}), 'legacy-exact-key');
    assert.equal(serializedMessageKey({id:'incomplete'}), null);
    assert.equal(serializedMessageKey({...key,participant:{}}), null);
});

test('patched serializer returns an ID usable by the actual Message class and send confirmation', async () => {
    const {modelBefore,modelAfter,replaceOnce} = require('./patch-send-result');
    const {Message} = require('whatsapp-web.js');
    const {sendConfirmed} = require('./send-confirmation');
    const {EventEmitter} = require('node:events');
    const raw = {id:{fromMe:true,remote:'123456@lid',id:'GENERATED',$1:'true_123456@lid_GENERATED'},body:'fixture',type:'chat',t:Math.floor(Date.now()/1000),from:'999@lid',to:'123456@lid',ack:2};
    const serialize = new Function('message',modelAfter+'\nreturn msg;');
    const result = serialize({serialize:()=>raw});
    assert.equal(result.id._serialized, raw.id.$1);
    assert.equal(replaceOnce(modelAfter,modelBefore,modelAfter),modelAfter);
    const client = new EventEmitter();
    client.getContactLidAndPhone = async()=>[];
    let sends=0;
    client.sendMessage = async()=>{sends++;return new Message(client,result);};
    assert.equal((await sendConfirmed(client,'123456@lid','fixture',{},1)).id._serialized,raw.id.$1);
    assert.equal(sends,1);
});

test('contact names/photos use bounded reads, cache results, and respect unavailable photos', async () => {
    const {contactProfile,boundedRead}=require('./contact-profile');
    let calls=0;
    const instance={client:{getContactById:async()=>({name:'WhatsApp contact'}),pupPage:{evaluate:async()=>{calls++;return 'https://example.invalid/profile.jpg';}}}};
    const profile=await contactProfile(instance,'123@lid');
    assert.equal(profile.name,'WhatsApp contact');
    assert.equal((await contactProfile(instance,'123@lid')).profilePicUrl,profile.profilePicUrl);
    assert.equal(calls,1);
    instance.client.pupPage.evaluate=async()=>{throw new Error('private');};
    assert.equal((await contactProfile(instance,'456@lid')).profilePicUrl,null);
    assert.equal(await boundedRead(new Promise(()=>{}),2,'unavailable'),'unavailable');
});
