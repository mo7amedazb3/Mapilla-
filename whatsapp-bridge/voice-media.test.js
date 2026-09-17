'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { normalizeVoice, MAX_AUDIO } = require('./voice-media');
const { SendReceipts } = require('./send-receipts');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const synthetic = format => execFileSync('/usr/bin/ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
    '-c:a', format === 'wav' ? 'pcm_s16le' : format === 'mp4' ? 'aac' : 'libopus',
    ...(format === 'mp4' ? ['-movflags', 'frag_keyframe+empty_moov'] : []),
    '-f', format, 'pipe:1',
]);

test('WebM, MP4, WAV and existing Ogg become valid mono Ogg/Opus voice notes', async () => {
    for (const format of ['webm', 'mp4', 'wav', 'ogg']) {
        const result = await normalizeVoice({data:synthetic(format).toString('base64'),mimetype:'audio/' + format});
        assert.equal(result.mimetype, 'audio/ogg; codecs=opus');
        assert.equal(result.filename, 'voice.ogg');
        const bytes = Buffer.from(result.data, 'base64');
        assert.equal(bytes.subarray(0, 4).toString(), 'OggS');
        const probe = JSON.parse(execFileSync('/usr/bin/ffprobe', [
            '-v','error','-show_streams','-of','json','pipe:0',
        ], {input:bytes}));
        assert.equal(probe.streams[0].codec_name, 'opus');
        assert.equal(probe.streams[0].channels, 1);
        assert.equal(probe.streams[0].sample_rate, '48000');
    }
});

test('empty, corrupt and oversized audio fail explicitly before sending', async () => {
    for (const bytes of [Buffer.alloc(0), Buffer.from('not audio'), Buffer.alloc(MAX_AUDIO + 1)]) {
        await assert.rejects(normalizeVoice({data:bytes.toString('base64')}), /لم يتم إرساله/);
    }
});

test('conversion stays inside durable single-send receipt across concurrent retries and restart', async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'mapilla-voice-test-'));
    try {
        const receipts = new SendReceipts(dir);
        const payload = {data:synthetic('webm').toString('base64'),mimetype:'audio/webm'};
        let conversions = 0, sends = 0;
        const operation = async () => {
            conversions++;
            await normalizeVoice(payload);
            sends++; // Mock only; never call WhatsApp here.
            return {status:'success',messageId:'mock-voice'};
        };
        await Promise.all(Array.from({length:5},()=>receipts.run('test:send','voice-request-123',payload,operation)));
        await new SendReceipts(dir).run('test:send','voice-request-123',payload,operation);
        assert.equal(conversions, 1);
        assert.equal(sends, 1);
        await assert.rejects(receipts.run('test:send','invalid-voice-123',{data:'bad'},async()=>{
            await normalizeVoice({data:Buffer.from('bad').toString('base64')});
            sends++;
        }), /لم يتم إرساله/);
        assert.equal(sends, 1);
    } finally {await fs.rm(dir,{recursive:true,force:true});}
});
