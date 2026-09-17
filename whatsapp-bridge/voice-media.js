'use strict';
const { execFile } = require('node:child_process');

const MAX_AUDIO = 12 * 1024 * 1024;

// Chromium records WebM/Opus, which WhatsApp's native PTT preparation rejects.
// Convert locally before invoking any WhatsApp send/upload operation. No shell,
// temporary files, or network/file protocols are allowed in the media decoder.
async function normalizeVoice(media) {
    const input = Buffer.from(media.data || '', 'base64');
    if (!input.length || input.length > MAX_AUDIO) {
        throw new Error('التسجيل الصوتي فارغ أو أكبر من الحد المسموح (12 ميجابايت). لم يتم إرساله.');
    }
    const output = await new Promise((resolve, reject) => {
        const child = execFile('/usr/bin/ffmpeg', [
            '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-protocol_whitelist', 'pipe', '-threads', '1', '-i', 'pipe:0',
            '-map', '0:a:0', '-vn', '-sn', '-dn', '-map_metadata', '-1',
            '-ac', '1', '-ar', '48000', '-c:a', 'libopus', '-b:a', '32k',
            '-application', 'voip', '-threads', '1', '-f', 'ogg', 'pipe:1',
        ], { encoding: 'buffer', timeout: 20000, maxBuffer: MAX_AUDIO, killSignal: 'SIGKILL' }, (error, stdout) => {
            if (error) {
                reject(new Error('تعذّر تجهيز التسجيل الصوتي. لم يتم إرساله؛ جرّب تسجيلًا جديدًا.'));
            } else if (stdout.length < 32 || stdout.subarray(0, 4).toString() !== 'OggS'
                    || !stdout.subarray(0, 128).includes(Buffer.from('OpusHead'))) {
                reject(new Error('صيغة التسجيل الصوتي غير صالحة للإرسال. لم يتم إرساله.'));
            } else resolve(stdout);
        });
        // Invalid input can make ffmpeg exit before consuming all stdin.
        child.stdin.on('error', () => {});
        child.stdin.end(input);
    });
    return { data: output.toString('base64'), mimetype: 'audio/ogg; codecs=opus', filename: 'voice.ogg' };
}

module.exports = { normalizeVoice, MAX_AUDIO };
