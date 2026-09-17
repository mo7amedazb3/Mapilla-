'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { sendConfirmed } = require('./send-confirmation');

const pn = '201012345678@c.us';
const lid = '123456789012345@lid';
const message = (id = 'actual-id', changes = {}) => ({
    id: { _serialized: id }, fromMe: true, to: lid, body: 'hello', hasMedia: false,
    timestamp: Math.floor(Date.now() / 1000), ...changes,
});
function fakeClient(send) {
    const client = new EventEmitter();
    client.calls = 0;
    client.getContactLidAndPhone = async () => [{ pn, lid }];
    client.sendMessage = async (...args) => { client.calls++; return send(client, ...args); };
    return client;
}

test('normal response returns its real message ID and asks for send completion', async () => {
    const client = fakeClient(async (_, target, text, options) => {
        assert.equal(options.waitUntilMsgSent, true);
        return message();
    });
    assert.equal((await sendConfirmed(client, pn, 'hello')).id._serialized, 'actual-id');
    assert.equal(client.calls, 1);
    assert.equal(client.listenerCount('message_create'), 0);
});
test('undefined library result uses the outgoing event, including a PN to LID change', async () => {
    const client = fakeClient(async c => { c.emit('message_create', message()); return undefined; });
    assert.equal((await sendConfirmed(client, pn, 'hello')).id._serialized, 'actual-id');
    assert.equal(client.calls, 1);
    assert.equal(client.listenerCount('message_create'), 0);
});
test('a late outgoing event confirms the same single send', async () => {
    const client = fakeClient(async c => { setTimeout(() => c.emit('message_create', message()), 10); });
    assert.equal((await sendConfirmed(client, pn, 'hello', {}, 100)).id._serialized, 'actual-id');
    assert.equal(client.calls, 1);
});
test('unrelated, incoming, old and mismatched messages never produce a false success', async () => {
    const client = fakeClient(async c => {
        c.emit('message_create', message('other', { to: '441234567890@c.us' }));
        c.emit('message_create', message('incoming', { fromMe: false }));
        c.emit('message_create', message('old', { timestamp: 1 }));
        c.emit('message_create', message('different', { body: 'different' }));
    });
    await assert.rejects(sendConfirmed(client, pn, 'hello', {}, 10), error => error.status === 504);
    assert.equal(client.calls, 1);
    assert.equal(client.listenerCount('message_create'), 0);
});
test('a real send failure is preserved and is not retried', async () => {
    const client = fakeClient(async () => { throw new Error('disconnected'); });
    await assert.rejects(sendConfirmed(client, pn, 'hello'), /disconnected/);
    assert.equal(client.calls, 1);
    assert.equal(client.listenerCount('message_create'), 0);
});
test('two identical concurrent requests get distinct confirmations', async () => {
    const client = fakeClient(async c => {
        const id = 'send-' + c.calls;
        await new Promise(resolve => setTimeout(resolve, 10));
        c.emit('message_create', message(id));
    });
    const results = await Promise.all([sendConfirmed(client, pn, 'hello'), sendConfirmed(client, pn, 'hello')]);
    assert.deepEqual(results.map(m => m.id._serialized), ['send-1', 'send-2']);
    assert.equal(client.calls, 2);
});
test('media confirmations match attachments and captions', async () => {
    const client = fakeClient(async c => c.emit('message_create', message('media', { hasMedia: true, body: 'caption' })) && undefined);
    const result = await sendConfirmed(client, pn, { mimetype: 'image/png', data: 'test' }, { caption: 'caption' });
    assert.equal(result.id._serialized, 'media');
});
