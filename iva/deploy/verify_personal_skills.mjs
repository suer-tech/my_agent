// Real Luna decisions and real tool schema; writes only to a temporary store.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const config=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const {generateText,tool}=await import('ai');
const {z}=await import('zod');
const {makeCodexModel}=await import(pathToFileURL(path.join(config.app,'agent/provider.ts')).href);
const {default:definition}=await import(pathToFileURL(config.definition).href);
const policy=fs.readFileSync(config.policy,'utf8');
const movie=fs.readFileSync(config.movie,'utf8');
let body='',source='',calls=[];
async function request(input){
 const response=await fetch(config.endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({input,source,body})});
 const output=await response.json();calls.push({action:input.action,name:input.name,key:input.key,ok:output.ok,...(input.action==='recall'?{entries:output.entries}: {})});return output;
}
const tools={
 personal_skills:tool({description:definition.description,inputSchema:definition.inputSchema,execute:request}),
 load_skill:tool({description:'Загрузить навык.',inputSchema:z.object({name:z.string()}),execute:async({name})=>({ok:true,name,instructions:name==='personal-movie-discovery'?movie:policy})}),
 user_update:tool({description:'Короткая содержательная реплика перед работой.',inputSchema:z.object({message:z.string()}),execute:async({message})=>({ok:true,message})}),
 web_browser:tool({description:'Прочитать публичный веб-источник с данными о фильмах; это изолированная тестовая выдача, не живой поиск.',inputSchema:z.object({action:z.enum(['search','open','read']),query:z.string().optional(),url:z.string().optional()}),execute:async input=>{
  calls.push({action:'web_fixture',input});
  return {ok:true,test_fixture:true,films:[{title:'Начало',original:'Inception',year:2010,url:'https://en.wikipedia.org/wiki/Inception'},{title:'Исходный код',original:'Source Code',year:2011,url:'https://en.wikipedia.org/wiki/Source_Code'},{title:'Прибытие',original:'Arrival',year:2016,url:'https://en.wikipedia.org/wiki/Arrival_(film)'}]};
 }}),
};
const results=[];
async function turn(id,message,extra=''){
 body=message;source='isolated-personal-skills:'+id;calls=[];
 // New message history on every case: persistence must come from the tool.
 const messages=[{role:'user',content:message}];let answer='';
 for(let n=0;n<9;n++){
  const r=await generateText({model:makeCodexModel('gpt-6-luna',`personal-skills-${id}-${n}`),system:'Ты Ива, отвечай по-русски. Выполняй поручения инструментами, сохранение только по успешному результату. Политика навыков:\n'+policy+'\n'+extra,messages,tools,abortSignal:AbortSignal.timeout(90000)});
  messages.push(...r.response.messages);answer=r.text;
  if(r.toolCalls.length===0)break;
 }
 const row={id,answer,calls};results.push(row);console.log(JSON.stringify(row));return row;
}
if(!config.fresh){
 await turn('create','Ива, создай себе навык personal-verification-books для подбора книг под мои вкусы. Сначала узнавай, какие прочитанные книги понравились и почему, ищи похожие в интернете, помни прочитанное и не советуй его заново. Мои вкусы пока неизвестны.');
 await turn('remember','Я смотрел фильм «Начало» / Inception (2010), очень понравились многослойный сюжет и атмосфера. Запомни в навыке personal-movie-discovery, что я его уже смотрел, чтобы больше не предлагать. «Прибытие» я пока не смотрел.','Используй personal-movie-discovery; он доступен. Не объявляй Прибытие просмотренным.');
}
await turn('fresh-recommend','Подбери пару похожих на мои любимые фильмов. Сначала проверь сохранённую память, затем используй браузер. Сведения выдачи в этой проверке тестовые; так и обозначь их. Просмотренное не предлагай. Запомни предложенные фильмы со статусом recommended, не отмечая их просмотренными.','Навык personal-movie-discovery уже есть, но вкусы и просмотренное в этом новом разговоре неизвестны: получи их из personal_skills recall.\n'+movie);
fs.writeFileSync(config.output,JSON.stringify({cases:results,fixture_browser:true},null,2));
