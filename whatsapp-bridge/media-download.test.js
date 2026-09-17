'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {downloadMessageMedia}=require('./media-download');

test('resolved original blob is preferred without another CDN download',async()=>{
 let downloads=0;
 const client={pupPage:{evaluate:async(fn,id,limit)=>{
  assert.equal(id,'real-message-id');assert.equal(limit,1024);
  return {data:'YWJj',mimetype:'image/jpeg',filesize:3};
 }}};
 const result=await downloadMessageMedia(client,{id:{_serialized:'real-message-id'},downloadMedia:()=>{downloads++;}},1024);
 assert.equal(result.data,'YWJj');assert.equal(downloads,0);
});

test('missing or unavailable cached blob falls back once and preserves real failures',async()=>{
 for(const throws of [false,true]) {
  let downloads=0;
  const client={pupPage:{evaluate:async()=>{if(throws)throw new Error('released blob');return null;}}};
  const result=await downloadMessageMedia(client,{id:{_serialized:'x'},downloadMedia:async()=>{downloads++;return {data:'audio'};}});
  assert.equal(result.data,'audio');assert.equal(downloads,1);
  await assert.rejects(downloadMessageMedia(client,{id:{_serialized:'x'},downloadMedia:async()=>{throw new Error('unavailable')}}),/unavailable/);
 }
});
