import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fastModeFetch} from '../iva_integration/fast-mode.mjs';
const url='https://chatgpt.com/backend-api/codex/responses';

test('Fast is scoped to subscription Responses and leaves model and reasoning unchanged',async()=>{
 const calls=[];
 const fetch=fastModeFetch(async(input,init)=>{calls.push({input,init});return new Response('ok');},{enabled:true});
 const body={model:'gpt-6-luna',reasoning:{effort:'low'},input:[]};
 await fetch(url,{body:JSON.stringify(body),headers:{Authorization:'private'}});
 assert.deepEqual(JSON.parse(calls[0].init.body),{...body,service_tier:'priority'});
 assert.equal(calls[0].init.headers.Authorization,'private');
 await fetch('https://auth.openai.com/oauth/token',{body:JSON.stringify(body)});
 assert.deepEqual(JSON.parse(calls[1].init.body),body);
});

test('disabled Fast leaves the request untouched',async()=>{
 const init={body:'{"model":"gpt-6-luna"}'};
 const fetch=fastModeFetch(async(input,actual)=>{assert.equal(actual,init);return new Response('ok');});
 await fetch(url,init);
});

test('SSE bytes are preserved and audit records only processing tier metadata',async()=>{
 const audit=[];
 const sse='data: '+JSON.stringify({type:'response.created',response:{service_tier:'priority',output:[{secret:'never log me'}]}})+'\n\n'+'data: '+JSON.stringify({type:'response.completed',response:{service_tier:'priority'}})+'\n\n';
 const bytes=new TextEncoder().encode(sse);
 const fetch=fastModeFetch(async()=>new Response(new ReadableStream({start(c){c.enqueue(bytes.slice(0,13));c.enqueue(bytes.slice(13));c.close();}}),{headers:{'content-type':'text/event-stream'}}),{enabled:true,record:r=>audit.push(r)});
 const response=await fetch(url,{body:'{"model":"gpt-6-luna"}'});
 assert.equal(await response.text(),sse);
 assert.equal(audit.at(-1).actual,'priority');
 assert.equal(JSON.stringify(audit).includes('never log me'),false);
});

test('audit failure cannot trigger a second request',async()=>{
 let calls=0;
 const fetch=fastModeFetch(async()=>{calls++;return new Response('error',{status:400});},{enabled:true,record:()=>{throw Error('disk');}});
 assert.equal((await fetch(url,{body:'{"model":"gpt-6-luna"}'})).status,400);
 assert.equal(calls,1);
});
