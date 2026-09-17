'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const failure = (message, status = 409) => Object.assign(new Error(message), { status });

// Claim durably BEFORE the external side effect. An interrupted/uncertain send
// is never retried automatically, including after a process restart.
class SendReceipts {
    constructor(directory) { this.directory = directory; this.pending = new Map(); }
    async run(scope, requestId, payload, operation) {
        if (typeof requestId !== 'string' || !/^[a-zA-Z0-9:_-]{8,160}$/.test(requestId)) {
            throw failure('A stable requestId is required; refresh the WhatsApp page.', 400);
        }
        const key = digest(scope + '\0' + requestId);
        const fingerprint = digest(JSON.stringify(payload));
        const active = this.pending.get(key);
        if (active) {
            if (active.fingerprint !== fingerprint) throw failure('Request ID was reused with different content');
            return active.promise;
        }
        const promise = this.execute(key, fingerprint, operation);
        this.pending.set(key, { fingerprint, promise });
        try { return await promise; }
        finally { this.pending.delete(key); }
    }
    async execute(key, fingerprint, operation) {
        await fs.mkdir(this.directory, { recursive: true, mode: 0o700 });
        const filename = path.join(this.directory, key + '.json');
        let file;
        try { file = await fs.open(filename, 'wx', 0o600); }
        catch (error) {
            if (error.code !== 'EEXIST') throw error;
            const stored = JSON.parse(await fs.readFile(filename, 'utf8'));
            if (stored.fingerprint !== fingerprint) throw failure('Request ID was reused with different content');
            if (stored.state === 'complete') return stored.result;
            if (stored.state === 'failed') throw failure(stored.error, stored.status);
            throw failure('حالة الإرسال غير مؤكدة. تم منع تكرار الطلب؛ راجع المحادثة قبل إرسال رسالة جديدة.');
        }
        try {
            await file.writeFile(JSON.stringify({ fingerprint, state: 'pending', created: Date.now() }));
            await file.sync();
        } finally { await file.close(); }
        await this.syncDirectory();
        let receipt;
        try {
            const result = await operation();
            receipt = { fingerprint, state: 'complete', result };
        } catch (error) {
            receipt = { fingerprint, state: 'failed', error: error.message, status: error.status || 502 };
        }
        const temporary = filename + '.' + crypto.randomUUID() + '.tmp';
        const completed = await fs.open(temporary, 'wx', 0o600);
        try { await completed.writeFile(JSON.stringify(receipt)); await completed.sync(); }
        finally { await completed.close(); }
        await fs.rename(temporary, filename);
        await this.syncDirectory();
        if (receipt.state === 'failed') throw failure(receipt.error, receipt.status);
        return receipt.result;
    }
    async syncDirectory() {
        const directory = await fs.open(this.directory, 'r');
        try { await directory.sync(); } finally { await directory.close(); }
    }
}
module.exports = { SendReceipts };
