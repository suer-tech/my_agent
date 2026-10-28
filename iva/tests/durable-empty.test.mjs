import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {durableTurn} from '../iva_integration/durable-turn.mjs';
for(const message of ['',undefined,'   ']){
 test(`empty response ${JSON.stringify(message)} cannot become success or replay`,async(t)=>{
  const dir=mkdtempSync(join(tmpdir(),'iva-empty-'));
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  let effects=0;
  const client={sessions:{create:async()=>({
   session:{state:{sessionId:'test',streamIndex:1}},
   response:{result:async()=>{effects++;return {status:'completed',message};}}
  })}};
  const request={source:'test:empty',message:'test'};
  assert.equal((await durableTurn({client,dir,request})).status,'uncertain');
  assert.equal((await durableTurn({client,dir,request})).status,'uncertain');
  assert.equal(effects,1);
  assert.equal(JSON.parse(readFileSync(join(dir,'session.json'))).needs_recovery,true);
 });
}
