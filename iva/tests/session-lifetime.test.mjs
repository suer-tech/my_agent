import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {durableTurn,sessionResumeInfo} from '../iva_integration/durable-turn.mjs';

const id='wrun_TESTSESSION';
function fixture(t,{status='running',ageHours=1,legacy=false,missing=false}={}){
 const dir=mkdtempSync(join(tmpdir(),'iva-lifetime-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
 const runDir=join(dir,'runs');mkdirSync(runDir);
 const createdAt=new Date(Date.now()-ageHours*3600*1000).toISOString();
 const saved={session:{sessionId:id,streamIndex:3},last_used:new Date().toISOString(),turn_count:7,...(legacy?{}:{session_created_at:createdAt})};
 writeFileSync(join(dir,'session.json'),JSON.stringify(saved));
 if(!missing)writeFileSync(join(runDir,id+'.json'),JSON.stringify({runId:id,status,createdAt}));
 const stats={created:0,attached:0,effects:0};
 const response={result:async()=>{stats.effects++;return {status:'completed',message:'status read'};}};
 const session={state:{sessionId:id,streamIndex:4},send:async()=>response};
 const client={sessions:{create:async()=>{stats.created++;assert.equal(JSON.parse(readFileSync(join(dir,'session.json'))).active_source,'status:1');return {session,response};},attach:()=>{stats.attached++;return session;}}};
 return {dir,runDir,saved,stats,send:source=>durableTurn({client,dir,sessionRunDir:runDir,request:{source,message:'read status'}})};
}
for(const status of ['completed','failed','cancelled'])test(`recently used ${status} session is replaced before sending`,async t=>{
 const f=fixture(t,{status});assert.equal((await f.send('status:1')).status,'done');
 assert.equal(f.stats.created,1);assert.equal(f.stats.attached,0);
 assert.equal(JSON.parse(readFileSync(join(f.dir,'session.json'))).turn_count,1);
 // A duplicate delivery returns its cached reply without repeating any action.
 assert.equal((await f.send('status:1')).status,'done');assert.equal(f.stats.effects,1);
});
test('recent last_used cannot revive a session created over a day ago',async t=>{
 const f=fixture(t,{ageHours:25});await f.send('status:1');assert.equal(f.stats.created,1);assert.equal(f.stats.attached,0);
});
test('session older than safety margin is replaced',async t=>{
 const f=fixture(t,{ageHours:23.2});await f.send('status:1');assert.equal(f.stats.created,1);
});
for(const status of ['running','pending'])test(`live ${status} session preserves creation time and turn count`,async t=>{
 const f=fixture(t,{status});await f.send('status:1');assert.equal(f.stats.attached,1);assert.equal(f.stats.created,0);
 const state=JSON.parse(readFileSync(join(f.dir,'session.json')));assert.equal(state.session_created_at,f.saved.session_created_at);assert.equal(state.turn_count,8);
});
test('legacy state uses real engine creation time',async t=>{
 const f=fixture(t,{legacy:true});await f.send('status:1');assert.equal(f.stats.attached,1);assert.ok(JSON.parse(readFileSync(join(f.dir,'session.json'))).session_created_at);
});
test('missing engine record gets a new session',async t=>{
 const f=fixture(t,{missing:true});await f.send('status:1');assert.equal(f.stats.created,1);
});
test('invalid session IDs cannot read outside workflow directory',t=>{
 const f=fixture(t);assert.equal(sessionResumeInfo({...f.saved,session:{sessionId:'../../private'}},f.runDir),null);
});
test('malformed workflow record is handled before submitting',async t=>{
 const f=fixture(t);writeFileSync(join(f.runDir,id+'.json'),'{');await f.send('status:1');assert.equal(f.stats.created,1);
});
