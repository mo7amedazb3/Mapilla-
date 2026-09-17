'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { Readable } = require('node:stream');
const { destination, bounded, directChat, readBody } = require('./server');

test('Egyptian local phones and international numbers resolve consistently', () => {
    assert.equal(destination('01012345678'), '201012345678@c.us');
    assert.equal(destination('+20 101 234 5678'), '201012345678@c.us');
    assert.equal(destination('00441234567890'), '441234567890@c.us');
    assert.equal(destination('123456789012345@lid'), '123456789012345@lid');
});
test('groups, broadcasts and invalid destinations cannot be sent to', () => {
    for (const bad of ['12345@g.us', 'status@broadcast', '', 'abc', '../session']) {
        assert.throws(() => destination(bad));
        assert.equal(directChat(bad), false);
    }
});
test('history request limits remain bounded', () => {
    assert.equal(bounded('10000', 80, 80), 80);
    assert.equal(bounded('-1', 80, 80), 80);
    assert.equal(bounded(null, 25, 50), 25);
    assert.equal(bounded('10', 25, 50), 10);
});
test('JSON endpoint rejects non-object and oversized input', async () => {
    assert.deepEqual(await readBody(Readable.from([Buffer.from('{"clientId":"test"}')])), { clientId: 'test' });
    await assert.rejects(readBody(Readable.from([Buffer.from('[]')])));
    await assert.rejects(readBody(Readable.from([Buffer.alloc(21 * 1024 * 1024)])), /too large/);
});
