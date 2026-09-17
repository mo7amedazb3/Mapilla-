'use strict';

// Use the already-resolved original media, not its thumbnail. WhatsApp may have
// the decrypted Blob while a second CDN download returns 404 (notably new sends).
async function downloadMessageMedia(client, message, maxBytes = 12 * 1024 * 1024) {
    const cached = await client.pupPage.evaluate(async (id, limit) => {
        const msg = window.require('WAWebCollections').Msg.get(id);
        const data = msg?.mediaData;
        if (data?.mediaStage !== 'RESOLVED' || !data.mediaBlob) return null;
        try {
            const blob = await data.mediaBlob.getBlob();
            if (!(blob instanceof Blob) || !blob.size || blob.size > limit) return null;
            const base64 = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result.split(',')[1]);
                reader.onerror = reject;
                reader.readAsDataURL(blob);
            });
            return {data:base64, mimetype:msg.mimetype || blob.type,
                filename:msg.filename || '', filesize:blob.size};
        } catch (_) { return null; }
    }, message.id._serialized, maxBytes).catch(() => null);
    return cached || message.downloadMedia();
}

module.exports = { downloadMessageMedia };
