import {test} from 'node:test';
import assert from 'node:assert/strict';
import {progressStream} from '../iva_integration/progress-stream.mjs';

const update=(id,message='Сверю остаток с финансовым агентом.')=>({
 type:'action.result',meta:{id},data:{status:'completed',result:{
  kind:'tool-result',callId:id,toolName:'user_update',output:{ok:true,message},
 }},
});
async function* stream(events){yield* events;}

test('model-authored update is emitted before the long action completes',async()=>{
 const updates=[];let workFinished=false;
 async function* response(){
  yield update('first');
  assert.equal(updates.length,1);
  assert.equal(workFinished,false);
  workFinished=true;
  yield {type:'message.completed',data:{finishReason:'stop',message:'Проверено.'}};
 }
 const events=[];
 for await(const event of progressStream(response(),{source:'telegram:owner:1:0',onProgress:v=>updates.push(v)}))events.push(event);
 assert.equal(events.length,2);
 assert.equal(updates[0].source,'telegram:owner:1:0');
 assert.equal(updates[0].message,'Сверю остаток с финансовым агентом.');
});

test('reasoning, raw tool data, nested events, final messages and failed tools stay private',async()=>{
 const updates=[];const failed=update('bad');failed.data.status='failed';
 const other=update('other');other.data.result.toolName='finance_agent';
 const events=[{type:'reasoning.completed',data:{reasoning:'private'}},
  {type:'subagent.child-event',data:{event:update('nested')}},other,failed,
  {type:'message.completed',data:{finishReason:'stop',message:'Final'}}];
 for await(const event of progressStream(stream(events),{source:'test',onProgress:v=>updates.push(v)})){}
 assert.deepEqual(updates,[]);
});

test('reconnected duplicate event and malformed updates are not sent twice',async()=>{
 const updates=[];
 for await(const event of progressStream(stream([update('same'),update('same'),update('empty',' '),update('long','x'.repeat(701))]),{source:'test',onProgress:v=>updates.push(v)})){}
 assert.equal(updates.length,1);
});

test('a progress delivery failure cannot abort or repeat the work',async()=>{
 let completed=0;
 async function* response(){yield update('first');completed++;yield {type:'session.completed',data:{}};}
 const events=[];
 for await(const event of progressStream(response(),{source:'test',onProgress:()=>{throw Error('offline');}}))events.push(event);
 assert.equal(completed,1);assert.equal(events.length,2);
});

test('validated announcement is relayed before other action inputs and deduplicated at result',async()=>{
 const updates=[];
 async function* response(){
  yield {type:'actions.requested',data:{actions:[{kind:'tool-call',toolName:'user_update',callId:'first',input:{message:'Проверю расчёт с финансовым агентом.'}}]}};
  assert.equal(updates.length,1);
  yield {type:'action.input.appended',data:{toolName:'finance_agent',inputTextDelta:'private task'}};
  yield update('first','Проверю расчёт с финансовым агентом.');
 }
 for await(const event of progressStream(response(),{source:'test',onProgress:v=>updates.push(v)})){}
 assert.equal(updates.length,1);
});

test('complete announcement arguments stream before the rest of the model step',async()=>{
 const updates=[];
 async function* response(){
  yield {type:'action.input.appended',data:{toolName:'user_update',callId:'first',inputTextDelta:'{"message":"Сейчас'}};
  assert.equal(updates.length,0);
  yield {type:'action.input.appended',data:{toolName:'user_update',callId:'first',inputTextDelta:' проверю календарь."}'}};
  assert.equal(updates.length,1);
  yield {type:'actions.requested',data:{actions:[{kind:'tool-call',toolName:'user_update',callId:'first',input:{message:'Сейчас проверю календарь.'}}]}};
  yield update('first','Сейчас проверю календарь.');
 }
 for await(const event of progressStream(response(),{source:'test',onProgress:v=>updates.push(v)})){}
 assert.equal(updates.length,1);
});
