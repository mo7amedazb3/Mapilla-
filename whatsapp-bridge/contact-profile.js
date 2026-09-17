'use strict';
async function boundedRead(promise, milliseconds, fallback = null) {
    let timer;
    try {
        return await Promise.race([promise, new Promise(resolve => { timer = setTimeout(() => resolve(fallback), milliseconds); })]);
    } catch (_) { return fallback; }
    finally { clearTimeout(timer); }
}
async function contactProfile(instance, chatId, fallback = '') {
    instance.profileCache ||= new Map();
    const cached = instance.profileCache.get(chatId);
    if (cached && cached.expires > Date.now()) return cached.value;
    const contact = await boundedRead(instance.client.getContactById(chatId), 5000);
    // getProfilePicUrl() currently serializes getChat(), which fails on changed
    // lastReceivedKey objects. The native API accepts the raw chat model.
    const picture = await boundedRead(instance.client.pupPage.evaluate(async id => {
        try {
            const chat = await window.WWebJS.getChat(id, { getAsModel: false });
            if (!chat) return null;
            const pic = await window.require('WAWebContactProfilePicThumbBridge').requestProfilePicFromServer(chat);
            return pic?.eurl || null;
        } catch (_) { return null; } // Includes privacy-restricted pictures.
    }, chatId), 5000);
    const value = { name: contact?.name || contact?.pushname || fallback,
        profilePicUrl: picture, contact };
    instance.profileCache.set(chatId, { value, expires: Date.now() + (picture ? 3600000 : 300000) });
    return value;
}
module.exports = { contactProfile, boundedRead };
