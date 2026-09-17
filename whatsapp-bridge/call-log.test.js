'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {callLogFields}=require('./call-log');
test('call records keep their actual direction and never masquerade as media',()=>{
 assert.deepEqual(callLogFields({type:'call_log',fromMe:true}),{mediaType:'call_log',body:'مكالمة صادرة'});
 assert.deepEqual(callLogFields({type:'call_log',fromMe:false}),{mediaType:'call_log',body:'مكالمة واردة'});
 for(const type of ['ptt','image','video','chat','location']) assert.equal(callLogFields({type}),null);
});
