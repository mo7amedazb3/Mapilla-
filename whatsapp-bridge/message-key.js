'use strict';

// Serialize the actual structured WhatsApp MsgKey. Current Web builds no longer
// include the legacy _serialized property in message.serialize().id.
function serializedMessageKey(key) {
    if (!key || typeof key !== 'object') return null;
    if (typeof key._serialized === 'string' && key._serialized) return key._serialized;
    const wid = value => typeof value === 'string' ? value : value?._serialized;
    const remote = wid(key.remote);
    const participant = key.participant ? wid(key.participant) : null;
    if (typeof key.fromMe !== 'boolean' || !remote || typeof key.id !== 'string' || !key.id) return null;
    if (key.participant && !participant) return null;
    return `${key.fromMe}_${remote}_${key.id}${participant ? '_' + participant : ''}`;
}

module.exports = { serializedMessageKey };
