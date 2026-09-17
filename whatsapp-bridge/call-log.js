'use strict';

function callLogFields(message) {
    if (message.type !== 'call_log') return null;
    // Direction is known; do not guess whether the call was missed, answered,
    // voice/video, or its duration when those details are unavailable.
    return { mediaType: 'call_log', body: message.fromMe ? 'مكالمة صادرة' : 'مكالمة واردة' };
}

module.exports = { callLogFields };
