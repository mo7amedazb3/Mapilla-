'use strict';

// A WhatsApp Web update can change the PN/LID key while sending. The upstream
// sendMessage() then returns undefined even though message_create has fired.
// Observe that event before sending, correlate it, and never resend as recovery.
const sendChains = new WeakMap();

async function sendOnceConfirmed(client, chatId, content, options = {}, timeoutMs = 15000) {
    const recipients = new Set([chatId]);
    try {
        for (const item of await client.getContactLidAndPhone([chatId])) {
            if (item.lid) recipients.add(item.lid);
            if (item.pn) recipients.add(item.pn);
        }
    } catch (_) { /* Exact chat ID remains usable when alias lookup is unavailable. */ }

    const started = Math.floor(Date.now() / 1000);
    const candidates = new Map();
    let resolveEvent;
    let timer;
    const observed = new Promise(resolve => { resolveEvent = resolve; });
    const listener = message => {
        if (!message?.fromMe || !message.id?._serialized || !recipients.has(message.to)) return;
        if (message.timestamp && message.timestamp < started - 1) return;
        if (typeof content === 'string') {
            if (message.hasMedia || message.body !== content) return;
        } else {
            if (!message.hasMedia || (message.body || '') !== (options.caption || '')) return;
        }
        candidates.set(message.id._serialized, message);
        resolveEvent(message);
    };
    client.on('message_create', listener);
    try {
        const returned = await client.sendMessage(chatId, content, { ...options, waitUntilMsgSent: true });
        if (returned?.id?._serialized) return returned;
        if (!candidates.size) {
            timer = setTimeout(() => resolveEvent(null), timeoutMs);
            await observed;
        }
        if (candidates.size === 1) return candidates.values().next().value;
        const error = new Error('تعذّر تأكيد حالة الرسالة. راجع المحادثة قبل إعادة الإرسال لتجنّب التكرار.');
        error.status = 504;
        throw error;
    } finally {
        clearTimeout(timer);
        client.removeListener('message_create', listener);
    }
}

function sendConfirmed(client, chatId, content, options = {}, timeoutMs) {
    const previous = sendChains.get(client) || Promise.resolve();
    const result = previous.catch(() => {}).then(() => sendOnceConfirmed(client, chatId, content, options, timeoutMs));
    sendChains.set(client, result);
    return result;
}

module.exports = { sendConfirmed };
