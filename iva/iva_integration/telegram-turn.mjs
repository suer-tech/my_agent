// One durable Telegram turn through the actual Eve/Iva session engine.
import {createRequire} from 'node:module';
import {readFileSync,mkdirSync,appendFileSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {durableTurn} from './durable-turn.mjs';
import {progressStream} from './progress-stream.mjs';
const root=resolve(import.meta.dirname,'..');
const config=JSON.parse(readFileSync(join(root,'data/iva-pilot/control.json'),'utf8'));
const require=createRequire(join(root,'data/iva-pilot/app/package.json'));
const {Client,MessageResponse}=await import(pathToFileURL(require.resolve('eve/client')).href);
const client=new Client({host:`http://127.0.0.1:${config.iva_port}`,auth:{bearer:async()=>config.iva_bearer}});
let input='';for await(const chunk of process.stdin)input+=chunk;
const request=JSON.parse(input);
if(typeof request.message!=='string'||!request.source||request.message.length>80000)throw Error('Invalid turn');
const dir=resolve(request.state_dir??join(root,'data/iva-telegram'));mkdirSync(join(dir,'turns'),{recursive:true});
const output=await durableTurn({client,dir,request,sessionRunDir:join(root,'data/iva-pilot/app/.eve/.workflow-data/runs'),consume:response=>new MessageResponse({
 sessionId:response.sessionId,
 cancelTurn:()=>response.cancel(),
 createStream:()=>progressStream(response,{source:request.source,onProgress:value=>{
  process.stdout.write(JSON.stringify(value)+'\n');
 }}),
}).result(),onAccepted:(key)=>{
if(!request.skip_daily){
 const now=new Date(),options={timeZone:'Asia/Yekaterinburg'};
 const day=new Intl.DateTimeFormat('en-CA',{...options,year:'numeric',month:'2-digit',day:'2-digit'}).format(now);
 const hhmm=new Intl.DateTimeFormat('en-GB',{...options,hour:'2-digit',minute:'2-digit',hour12:false}).format(now);
 const inert=request.message.replace(/^<!--/gm,'&lt;!--').replace(/^## ((?:[01]\d|2[0-3]):[0-5]\d)\s+(.+)$/gm,'<!-- ## $1 $2 -->');
 const daily=join(root,'data/iva-pilot/app/vault/daily');mkdirSync(daily,{recursive:true});
 appendFileSync(join(daily,day+'.md'),`\n## ${hhmm} [owner telegram]\n<!-- telegram-turn:${key} -->\n${inert}\n`);
}
}});
console.log(JSON.stringify(output));
if(output.status!=='done')process.exit(2);
