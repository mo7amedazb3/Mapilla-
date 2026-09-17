'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { serializedMessageKey } = require('./message-key');
const before = `return window
            .require('WAWebCollections')
            .Msg.get(newMsgKey._serialized);`;
const after = `// MAPILLA: WhatsApp may replace the PN remote with a LID during send.
        // Match the exact generated message key, never by text or timestamp.
        const sentMessages = window.require('WAWebCollections').Msg;
        return sentMessages.get(newMsgKey._serialized) || sentMessages.getModelsArray().find(
            candidate => candidate.id?.fromMe && candidate.id.id === newMsgKey.id
        );`;
const modelBefore = '        const msg = message.serialize();';
const modelAfter = modelBefore + `
        // MAPILLA: preserve the real MsgKey across current WhatsApp serialization.
        const serializeMapillaKey = ${serializedMessageKey.toString()};
        const serializedKey = serializeMapillaKey(msg.id);
        if (serializedKey) msg.id = { ...msg.id, _serialized: serializedKey };`;
function replaceOnce(source, original, replacement) {
    if (source.includes(replacement)) return source;
    if (source.split(original).length !== 2) throw new Error('Unsupported whatsapp-web.js send implementation; compatibility patch not applied');
    return source.replace(original, replacement);
}
function patchSource(source) {
    return replaceOnce(source, before, after);
}
function install() {
    const filename = path.join(path.dirname(require.resolve('whatsapp-web.js/package.json')), 'src/util/Injected/Utils.js');
    const source = fs.readFileSync(filename, 'utf8');
    const patched = replaceOnce(patchSource(source), modelBefore, modelAfter);
    if (patched !== source) fs.writeFileSync(filename, patched);
}
if (require.main === module) install();
module.exports = { patchSource, before, after, modelBefore, modelAfter, replaceOnce };
