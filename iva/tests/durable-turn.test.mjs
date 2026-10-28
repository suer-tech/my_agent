import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,writeFileSync,mkdirSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {durableTurn} from '../iva_integration/durable-turn.mjs';

function fixture(t){
 const dir=mkdtempSync(join(tmpdir(),'iva-turn-test-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
 const stats={created:0,attached:0,effects:0,messages:[]};let fail=false;
 const session={state:{sessionId:'synthetic',streamIndex:1},send:async(message)=>{stats.messages.push(message);return response();}};
 const response=()=>({result:async()=>{stats.effects++;if(fail)throw Error('Lost response AFTER tool commit');return {status:'completed',message:'done'};}});
 const client={sessions:{create:async({message})=>{stats.created++;stats.messages.push(message);
  assert.equal(JSON.parse(readFileSync(join(dir,'session.json'))).active_source,stats.source);
  return {session,response:response()};},attach:()=>{stats.attached++;return session;}}};
 const send=async(source,generation=null)=>{stats.source=source;return durableTurn({client,dir,request:{source,message:'current',engine_generation:generation}});};
 return {dir,stats,send,fail:()=>{fail=true;},heal:()=>{fail=false;}};
}

test('cached success is returned without submitting a second financial action',async(t)=>{
 const f=fixture(t);assert.equal((await f.send('income:1')).status,'done');
 assert.equal((await f.send('income:1')).status,'done');assert.equal(f.stats.effects,1);
 assert.equal((await f.send('expense:2')).status,'done');assert.equal(f.stats.attached,1);
});
test('failure after commit is never replayed; next distinct message gets a fresh session',async(t)=>{
 const f=fixture(t);f.fail();assert.equal((await f.send('expense:1')).status,'uncertain');
 f.heal();assert.equal((await f.send('expense:1')).status,'uncertain');assert.equal(f.stats.effects,1);
 assert.equal((await f.send('read:2')).status,'done');assert.equal(f.stats.created,2);assert.equal(f.stats.attached,0);
 assert.match(f.stats.messages[1],/прежние поручения автоматически не повторяй/);
 assert.equal(JSON.parse(readFileSync(join(f.dir,'session.json'))).active_source,null);
});
test('a process crash with a processing job does not resubmit it',async(t)=>{
 const f=fixture(t);mkdirSync(join(f.dir,'turns'));const hash=createHash('sha256').update('income:1').digest('hex');
 writeFileSync(join(f.dir,'turns',hash+'.json'),JSON.stringify({status:'processing'}));
 writeFileSync(join(f.dir,'session.json'),JSON.stringify({session:{sessionId:'old'},active_source:'income:1',last_used:new Date().toISOString()}));
 assert.equal((await f.send('income:1')).status,'uncertain');assert.equal(f.stats.effects,0);
 assert.equal((await f.send('read:2')).status,'done');assert.equal(f.stats.attached,0);
});
test('concurrent delivery of the same source is claimed once',async(t)=>{
 const f=fixture(t);await Promise.all([f.send('same'),f.send('same')]);assert.equal(f.stats.effects,1);
});

test('engine restart creates a fresh session while cached actions stay cached',async(t)=>{
 const f=fixture(t);assert.equal((await f.send('a','generation-a')).status,'done');
 assert.equal((await f.send('a','generation-b')).status,'done');assert.equal(f.stats.effects,1);
 assert.equal((await f.send('b','generation-b')).status,'done');assert.equal(f.stats.created,2);assert.equal(f.stats.attached,0);
});
test('failure diagnostics exclude arbitrary error text',async(t)=>{
 const dir=mkdtempSync(join(tmpdir(),'iva-safe-errors-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
 const client={sessions:{create:async()=>{throw Error('token=secret-value private message body');}}};
 await durableTurn({client,dir,request:{source:'test:privacy',message:'private prompt'}});
 const log=readFileSync(join(dir,'turn-diagnostics.jsonl'),'utf8');
 assert.ok(!log.includes('secret-value'));assert.ok(!log.includes('private message'));assert.ok(!log.includes('private prompt'));
 assert.equal(JSON.parse(log).stage,'submit');assert.ok(JSON.parse(log).error_fingerprint);
});
