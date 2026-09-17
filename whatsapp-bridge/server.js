'use strict';

const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { Client, Chat, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const { sendConfirmed } = require('./send-confirmation');
const { SendReceipts } = require('./send-receipts');
const { contactProfile, boundedRead } = require('./contact-profile');
const { normalizeVoice } = require('./voice-media');
const { downloadMessageMedia } = require('./media-download');
const { callLogFields } = require('./call-log');

const MAX_MEDIA = 12 * 1024 * 1024;
const MAX_BODY = 20 * 1024 * 1024;
const directChat = id => typeof id === 'string' && /^\d+@(c\.us|lid)$/.test(id);
function destination(value) {
    const input = String(value || '').trim();
    if (directChat(input)) return input;
    if (input.includes('@')) throw new Error('Only individual chats are supported');
    let digits = input.replace(/[^0-9]/g, '');
    if (digits.startsWith('00')) digits = digits.slice(2);
    if (/^01\d{9}$/.test(digits)) digits = '2' + digits;
    if (!/^\d{8,15}$/.test(digits)) throw new Error('A valid international phone number is required');
    return digits + '@c.us';
}
function bounded(value, fallback, max) {
    const number = Number(value);
    return Number.isInteger(number) && number > 0 ? Math.min(number, max) : fallback;
}
async function readBody(req) {
    let size = 0;
    const chunks = [];
    for await (const chunk of req) {
        size += chunk.length;
        if (size > MAX_BODY) throw Object.assign(new Error('Request is too large'), { status: 413 });
        chunks.push(chunk);
    }
    const parsed = JSON.parse(Buffer.concat(chunks).toString() || '{}');
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Invalid JSON object');
    return parsed;
}

async function main() {
    const config = JSON.parse(await fs.readFile(process.env.WA_CONFIG || '/etc/whatsapp-bridge.json', 'utf8'));
    const stateRoot = config.stateDir || '/var/lib/whatsapp-bridge';
    const sendReceipts = new SendReceipts(path.join(stateRoot, 'send-receipts'));
    const sessions = new Map();
    let stopping = false;
    let draining = false;
    const log = (event, details = '') => console.log(new Date().toISOString(), event, details);

    function callback(instance, event, data) {
        // Status heartbeats must not update the instance while history import
        // is committing its last_history_sync in a repeatable-read transaction.
        const pending = instance.callbackChain.catch(() => {}).then(() => deliver(instance, event, data));
        instance.callbackChain = pending;
        return pending;
    }
    async function deliver(instance, event, data) {
        if (!instance.cookie) {
            const sessionResponse = await fetch(config.odooUrl + '/web/login?db=' + encodeURIComponent(instance.database), {
                redirect: 'manual', signal: AbortSignal.timeout(10000),
            });
            const match = (sessionResponse.headers.get('set-cookie') || '').match(/session_id=([^;]+)/);
            if (!match) throw new Error('Could not select the Odoo database');
            instance.cookie = 'session_id=' + match[1];
        }
        const response = await fetch(config.odooUrl + '/whatsapp/bridge/event', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-WhatsApp-Bridge-Token': config.token, Cookie: instance.cookie },
            body: JSON.stringify({ database: instance.database, clientId: instance.id, event, data }),
            signal: AbortSignal.timeout(30000),
        });
        if (!response.ok) { instance.cookie = null; throw new Error('Odoo callback HTTP ' + response.status); }
        const result = await response.json();
        if (result.status !== 'success') throw new Error('Odoo callback rejected event');
    }

    async function drain() {
        if (draining || stopping) return;
        draining = true;
        try {
            const files = (await fs.readdir(path.join(stateRoot, 'outbox'))).filter(f => f.endsWith('.json')).sort();
            for (const file of files) {
                const filename = path.join(stateRoot, 'outbox', file);
                const item = JSON.parse(await fs.readFile(filename, 'utf8'));
                const instance = sessions.get(item.clientId);
                if (!instance) continue;
                try {
                    await callback(instance, 'message', item.data);
                    await fs.unlink(filename);
                } catch (error) {
                    log('delivery_retry', error.message);
                    break;
                }
            }
        } catch (error) { log('outbox_error', error.message); }
        finally { draining = false; }
    }

    async function queue(instance, data) {
        const key = crypto.createHash('sha256').update(instance.id + data.waMessageId).digest('hex');
        const filename = path.join(stateRoot, 'outbox', `${key}.json`);
        const temporary = filename + '.' + crypto.randomUUID() + '.tmp';
        await fs.writeFile(temporary, JSON.stringify({ clientId: instance.id, data }), { mode: 0o600 });
        await fs.rename(temporary, filename);
        void drain();
    }

    function publicStatus(instance) {
        return { clientId: instance.id, status: instance.status, qr: instance.qr || null, host_phone: instance.phone || null };
    }
    async function publishStatus(instance) {
        try { await callback(instance, 'status', publicStatus(instance)); }
        catch (error) { log('status_retry', error.message); }
    }
    function setStatus(instance, status, qr = '') {
        instance.status = status;
        instance.qr = qr;
        if (status !== 'READY' && status !== 'AUTHENTICATED') instance.phone = '';
        log('status', instance.id + ' ' + status);
        instance.statusChain = instance.statusChain.catch(() => {}).then(() => publishStatus(instance));
    }

    async function phoneFor(instance, chatId, contact) {
        if (chatId.endsWith('@c.us')) return chatId.split('@')[0];
        try {
            const [mapping] = await instance.client.getContactLidAndPhone([chatId]);
            if (mapping?.pn?.endsWith('@c.us')) return mapping.pn.split('@')[0];
        } catch (_) {}
        if (contact?.id?._serialized?.endsWith('@c.us')) return contact.id.user;
        return '';
    }
    async function messageData(instance, message, includeMedia = true) {
        const chatId = message.fromMe ? message.to : message.from;
        if (!directChat(chatId)) return null;
        const profile = await contactProfile(instance, chatId);
        const contact = profile.contact;
        const mobile = await phoneFor(instance, chatId, contact);
        if (!mobile) { log('unresolved_contact', 'message retained by WhatsApp for later sync'); return null; }
        const data = {
            clientId: instance.id, hostPhone: instance.phone, chatId, mobile,
            sender: mobile, senderName: profile.name || mobile, profilePicUrl: profile.profilePicUrl,
            displayPhone: '+' + mobile, fromMe: Boolean(message.fromMe),
            body: message.body || '', timestamp: message.timestamp,
            waMessageId: message.id._serialized,
        };
        Object.assign(data, callLogFields(message));
        if (message.type === 'location' && message.location) {
            data.mediaType = 'location';
            data.body = `https://www.google.com/maps?q=${message.location.latitude},${message.location.longitude}`;
        }
        if (message.hasQuotedMsg) {
            const quoted = await message.getQuotedMessage().catch(() => null);
            if (quoted) {
                data.quotedWaMessageId = quoted.id._serialized;
                data.quotedBody = quoted.body || `[${quoted.type}]`;
                data.quotedSender = quoted.fromMe ? instance.phone : mobile;
            }
        }
        if (message.hasMedia) {
            data.mediaType = message.type;
            if (includeMedia) {
                const media = await boundedRead(downloadMessageMedia(instance.client, message, MAX_MEDIA), 6000);
                if (media && Buffer.byteLength(media.data, 'base64') <= MAX_MEDIA) {
                    data.mediaB64 = media.data;
                    data.mediaMime = media.mimetype;
                    data.mediaFilename = media.filename || '';
                }
            }
            if (!data.body) data.body = `[${message.type}]`;
        }
        if (message.vCards?.length) data.vcardData = message.vCards.join('\n');
        return data;
    }

    async function start(instance) {
        if (instance.starting || ['QR', 'AUTHENTICATED', 'READY'].includes(instance.status)) return;
        instance.starting = true;
        clearTimeout(instance.reconnect);
        if (instance.client) await instance.client.destroy().catch(() => {});
        setStatus(instance, 'INITIALIZING');
        const client = new Client({
            authStrategy: new LocalAuth({ clientId: instance.id, dataPath: path.join(stateRoot, 'auth') }),
            webVersionCache: { type: 'local', path: path.join(stateRoot, 'web-cache') },
            puppeteer: { headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'] },
            authTimeoutMs: 120000, qrMaxRetries: 0,
            deviceName: 'Mapilla Odoo', browserName: 'Chrome',
        });
        instance.client = client;
        client.on('qr', qr => setStatus(instance, 'QR', qr));
        client.on('authenticated', () => setStatus(instance, 'AUTHENTICATED'));
        client.on('ready', () => {
            instance.phone = client.info?.wid?.user || '';
            setStatus(instance, 'READY');
            void drain();
            void initialHistory(instance).catch(() => {});
        });
        client.on('auth_failure', () => setStatus(instance, 'AUTH_FAILURE'));
        client.on('disconnected', reason => {
            setStatus(instance, reason === 'LOGOUT' ? 'OFFSESS' : 'OFFLINE');
            if (!stopping && reason !== 'LOGOUT') instance.reconnect = setTimeout(() => void start(instance), 15000);
        });
        client.on('message_create', message => {
            instance.messageChain = instance.messageChain.catch(() => {}).then(async () => {
                const data = await messageData(instance, message);
                if (data) await queue(instance, data);
            }).catch(error => log('message_error', error.message));
        });
        try { await client.initialize(); }
        catch (error) {
            log('initialize_error', error.message);
            setStatus(instance, 'OFFLINE');
            if (!stopping) instance.reconnect = setTimeout(() => void start(instance), 30000);
        } finally { instance.starting = false; }
    }

    for (const item of config.instances) {
        if (!/^[a-z0-9][a-z0-9_-]{2,63}$/.test(item.id)) throw new Error('Invalid configured instance ID');
        sessions.set(item.id, { ...item, status: 'OFFLINE', qr: '', phone: '', starting: false,
            client: null, messageChain: Promise.resolve(), statusChain: Promise.resolve(),
            callbackChain: Promise.resolve(), sync: null, historyStatus: 'pending' });
    }

    function initialHistory(instance) {
        if (instance.sync) return instance.sync;
        clearTimeout(instance.syncRetry);
        instance.historyStatus = 'running';
        instance.sync = history(instance, 80, 50, row => callback(instance, 'sync', {
            clientId: instance.id, hostPhone: instance.phone, chats: [row],
        }))
            .then(async data => {
                instance.historyStatus = 'complete';
                log('history_complete', JSON.stringify({ chats: data.chats.length,
                    messages: data.chats.reduce((total, chat) => total + chat.messages.length, 0) }));
                return data;
            }).catch(error => {
                instance.historyStatus = 'retrying';
                log('history_error', error.message);
                if (!stopping) instance.syncRetry = setTimeout(() => {
                    if (instance.status === 'READY') void initialHistory(instance).catch(() => {});
                }, 60000);
                throw error;
            }).finally(() => { instance.sync = null; });
        return instance.sync;
    }

    async function history(instance, chatLimit, messageLimit, onChat = null) {
        // Do not serialize lastReceivedKey: current WhatsApp can expose it without
        // _serialized, making the library's getChats() fail on an IndexedDB lookup.
        // History only needs the chat identity/title; fetchMessages reads the messages.
        const rows = await instance.client.pupPage.evaluate(limit => {
            return window.require('WAWebCollections').Chat.getModelsArray()
                .filter(chat => /^\d+@(c\.us|lid)$/.test(chat.id?._serialized))
                .sort((a, b) => (b.t || 0) - (a.t || 0)).slice(0, limit)
                .map(chat => ({
                    id: { _serialized: chat.id._serialized, user: chat.id.user, server: chat.id.server },
                    formattedTitle: chat.formattedTitle, t: chat.t, isGroup: false,
                }));
        }, chatLimit);
        const chats = rows.map(row => new Chat(instance.client, row));
        const result = { clientId: instance.id, hostPhone: instance.phone, chats: [] };
        let bytes = 0;
        // Publish all identities first, so an expired attachment cannot delay
        // names/photos for the remaining contacts. Bound WhatsApp lookups to four.
        if (onChat) {
            for (let offset = 0; offset < chats.length; offset += 4) {
                await Promise.all(chats.slice(offset, offset + 4).map(async chat => {
                    const profile = await contactProfile(instance, chat.id._serialized, chat.name);
                    const mobile = await phoneFor(instance, chat.id._serialized, profile.contact);
                    if (mobile) await onChat({ chatId: chat.id._serialized, mobile,
                        name: profile.name || mobile, profilePicUrl: profile.profilePicUrl,
                        displayPhone: '+' + mobile, timestamp: chat.timestamp, messages: [] });
                }));
            }
        }
        for (const chat of chats) {
            const profile = await contactProfile(instance, chat.id._serialized, chat.name);
            const contact = profile.contact;
            const mobile = await phoneFor(instance, chat.id._serialized, contact);
            if (!mobile) continue;
            const row = { chatId: chat.id._serialized, mobile, name: profile.name || mobile, profilePicUrl: profile.profilePicUrl,
                displayPhone: '+' + mobile, timestamp: chat.timestamp, messages: [] };
            const messages = await boundedRead(chat.fetchMessages({ limit: messageLimit }), 15000, []);
            for (const message of messages) {
                const data = await messageData(instance, message, bytes < 32 * 1024 * 1024);
                if (data) { row.messages.push(data); bytes += data.mediaB64?.length || 0; }
            }
            result.chats.push(row);
            if (onChat) await onChat(row);
        }
        return result;
    }

    const server = http.createServer(async (req, res) => {
        res.setHeader('Content-Type', 'application/json');
        res.setHeader('Cache-Control', 'no-store');
        try {
            const url = new URL(req.url, 'http://localhost');
            if (url.pathname === '/health' && req.method === 'GET') {
                res.end(JSON.stringify({ status: 'ok', instances: [...sessions.values()].map(i => ({ clientId: i.id, status: i.status, history: i.historyStatus })) }));
                return;
            }
            if (req.headers.origin) throw Object.assign(new Error('Browser access is not allowed'), { status: 403 });
            const body = req.method === 'POST' ? await readBody(req) : {};
            const instance = sessions.get(body.clientId || url.searchParams.get('clientId'));
            if (!instance) throw Object.assign(new Error('Unknown instance'), { status: 404 });
            if (req.method === 'GET' && url.pathname === '/api/v2/status') {
                res.end(JSON.stringify(publicStatus(instance))); return;
            }
            if (req.method === 'POST' && url.pathname === '/api/v2/init') {
                void start(instance);
                res.end(JSON.stringify({ status: 'success', ...publicStatus(instance) })); return;
            }
            if (req.method === 'POST' && url.pathname === '/api/v2/logout') {
                clearTimeout(instance.reconnect);
                if (instance.client) await instance.client.logout();
                setStatus(instance, 'OFFSESS');
                res.end(JSON.stringify({ status: 'success' })); return;
            }
            if (instance.status !== 'READY') throw Object.assign(new Error('WhatsApp is not connected. Scan the QR code first.'), { status: 503 });
            if (req.method === 'GET' && url.pathname === '/api/v2/sync') {
                if (!instance.sync) {
                    instance.sync = history(instance, bounded(url.searchParams.get('chatLimit'), 80, 80),
                        bounded(url.searchParams.get('messageLimit'), 25, 50)).finally(() => { instance.sync = null; });
                }
                res.end(JSON.stringify(await instance.sync)); return;
            }
            if (req.method === 'POST' && url.pathname === '/send') {
                const chatId = destination(body.phone);
                const options = { sendSeen: false };
                if (body.quotedMessageId) options.quotedMessageId = String(body.quotedMessageId);
                let content = String(body.message || '');
                if (body.mediaBase64) {
                    if (Buffer.byteLength(body.mediaBase64, 'base64') > MAX_MEDIA) throw new Error('Media exceeds the 12 MB limit');
                    content = new MessageMedia(body.mediaMimeType || 'application/octet-stream', body.mediaBase64, body.mediaName || 'attachment');
                    options.caption = String(body.message || '');
                    options.sendMediaAsSticker = Boolean(body.mediaIsSticker);
                    options.sendAudioAsVoice = (body.mediaMimeType || '').startsWith('audio/');
                } else if (!content.trim()) throw new Error('An empty message cannot be sent');
                const fingerprint = { chatId, message: body.message || '', media: body.mediaBase64 || '',
                    mediaName: body.mediaName || '', mediaMimeType: body.mediaMimeType || '', options };
                const result = await sendReceipts.run(instance.id + ':send', body.requestId, fingerprint, async () => {
                    if (options.sendAudioAsVoice) {
                        const voice = await normalizeVoice(content);
                        content = new MessageMedia(voice.mimetype, voice.data, voice.filename);
                    }
                    const message = await sendConfirmed(instance.client, chatId, content, options);
                    return { status: 'success', messageId: message.id._serialized };
                });
                res.end(JSON.stringify(result)); return;
            }
            if (req.method === 'POST' && url.pathname === '/media') {
                const message = await instance.client.getMessageById(String(body.messageId || ''));
                if (!message || !message.hasMedia) throw Object.assign(new Error('Media is not available'), {status:404});
                const data = await messageData(instance, message, true);
                if (!data?.mediaB64) throw Object.assign(new Error('Media is no longer available from WhatsApp'), {status:404});
                res.end(JSON.stringify(data)); return;
            }
            if (req.method === 'POST' && url.pathname === '/forward') {
                const target = destination(body.chatId);
                const result = await sendReceipts.run(instance.id + ':forward', body.requestId,
                    { messageId: body.messageId, target }, async () => {
                        const message = await instance.client.getMessageById(String(body.messageId || ''));
                        if (!message) throw Object.assign(new Error('Message not found'), { status: 404 });
                        await message.forward(target);
                        return { status: 'success' };
                    });
                res.end(JSON.stringify(result)); return;
            }
            throw Object.assign(new Error('Route not found'), { status: 404 });
        } catch (error) {
            res.statusCode = error.status || 400;
            res.end(JSON.stringify({ error: error.message }));
        }
    });
    server.requestTimeout = 180000;
    server.listen(config.port || 3000, '127.0.0.1', () => log('listening', '127.0.0.1:' + (config.port || 3000)));
    const deliveryTimer = setInterval(() => void drain(), 10000);
    const statusTimer = setInterval(() => {
        for (const instance of sessions.values()) void publishStatus(instance);
    }, 10000);
    for (const instance of sessions.values()) void start(instance);
    async function shutdown() {
        if (stopping) return;
        stopping = true;
        clearInterval(deliveryTimer); clearInterval(statusTimer);
        server.close();
        for (const instance of sessions.values()) {
            clearTimeout(instance.reconnect);
            clearTimeout(instance.syncRetry);
            if (instance.client) await instance.client.destroy().catch(() => {});
        }
        process.exit(0);
    }
    process.on('SIGTERM', shutdown); process.on('SIGINT', shutdown);
}

if (require.main === module) main().catch(error => { console.error(error.message); process.exit(1); });
module.exports = { destination, bounded, directChat, readBody };
