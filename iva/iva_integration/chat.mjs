// Console entry to the actual Iva session engine, not a replacement chat loop.
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {appendFileSync,mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import readline from 'node:readline/promises';
import {stdin,stdout} from 'node:process';
const root=resolve(import.meta.dirname,'..');
const config=JSON.parse(readFileSync(resolve(root,'data/iva-pilot/control.json'),'utf8'));
const {Client}=await import(pathToFileURL(resolve(root,'data/iva-pilot/app/node_modules/eve/dist/public/client/index.js')).href).catch(async()=>{
  const require=createRequire(resolve(root,'data/iva-pilot/app/package.json'));
  return await import(pathToFileURL(require.resolve('eve/client')).href);
});
const client=new Client({host:`http://127.0.0.1:${config.iva_port}`,auth:{bearer:async()=>config.iva_bearer}});
const post=async body=>{
 const r=await fetch(`http://127.0.0.1:${config.gateway_port}/owner/finance`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${config.owner_token}`},body:JSON.stringify(body)});return await r.json();
};
let session;
let voiceEnabled=true;
if(process.argv.includes('--no-voice'))voiceEnabled=false;
async function speak(text){
 const r=await fetch(`http://127.0.0.1:${config.gateway_port}/owner/speech`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${config.owner_token}`},body:JSON.stringify({text:text.slice(0,3500)})});
 if(!r.ok)throw Error('Озвучивание недоступно; текстовый ответ сохранён.');
 const result=await r.json();console.log(`Голос ${result.voice_name??result.provider}: ${result.audio_path}`);
}
async function send(text){
 const now=new Date();const day=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Yekaterinburg',year:'numeric',month:'2-digit',day:'2-digit'}).format(now);
 const hhmm=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Yekaterinburg',hour:'2-digit',minute:'2-digit',hour12:false}).format(now);
 const daily=resolve(root,'data/iva-pilot/app/vault/daily');mkdirSync(daily,{recursive:true});
 const inert=text.replace(/^<!--/gm,'&lt;!--').replace(/^## ((?:[01]\d|2[0-3]):[0-5]\d)\s+(.+)$/gm,'<!-- ## $1 $2 -->');
 appendFileSync(resolve(daily,day+'.md'),`\n## ${hhmm} [owner pilot]\n${inert}\n`);
 let response;
 if(!session){const created=await client.sessions.create({message:text});session=created.session;response=created.response;}
 else response=await session.send({message:text});
 const result=await response.result();
 if(result.status==='failed')throw Error('Iva turn failed; inspect private pilot diagnostics');
 console.log(result.message??'Ход завершён без текстового ответа.');
 if(voiceEnabled && result.message){try{await speak(result.message);}catch(error){console.error(error.message);}}
}
if(process.argv.includes('--once')){await send(process.argv.slice(process.argv.indexOf('--once')+1).join(' '));process.exit();}
const ui=readline.createInterface({input:stdin,output:stdout});
console.log('Ива: консоль сессионного движка. /confirm CODE, /pending, /file PATH, /voice on|off, /new, /exit. Статус Telegram: python iva_integration/telegram_runtime.py status.');
while(true){const text=await ui.question('Вы> ');if(text==='/exit')break;
 try{
  if(text==='/voice on' || text==='/voice off'){voiceEnabled=text.endsWith('on');console.log(voiceEnabled?'Голос включён':'Только текст');continue;}
  if(text==='/new'){session=undefined;continue;}
  if(text==='/pending'){console.log(JSON.stringify(await post({mode:'pending'}),null,2));continue;}
  if(text.startsWith('/confirm ')){console.log(JSON.stringify(await post({mode:'confirm',id:text.slice(9).trim()}),null,2));continue;}
  if(text.startsWith('/file ')){console.log(JSON.stringify(await post({mode:'document',path:text.slice(6).trim()}),null,2));continue;}
  await send(text);
 }catch(error){console.error('Pilot turn failed:',error.message);}
}
ui.close();
