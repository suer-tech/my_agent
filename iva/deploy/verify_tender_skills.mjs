// Real native schemas and provider, with every tool execute function omitted.
import fs from 'node:fs';
import {readLiveSkills} from './agent/lib/custom-skills.ts';
const input=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const {default:definition}=await import('./agent/tools/tender_agent.ts');
definition.inputSchema.parse({task:'История',action:'history',target_url:input.target});
const skills=await readLiveSkills();
for(const name of ['tender-search','tender-history']){
  if(!skills[name]?.markdown || !skills[name]?.description)throw Error(`Skill not registered: ${name}`);
}
const result={registered:['tender-search','tender-history'],schema:true,executed_tools:0,cases:[]};
if(input.routes){
  const {generateText,tool}=await import('ai');
  const {makeCodexModel}=await import('./agent/provider.ts');
  const tools={tender_agent:tool({description:definition.description,inputSchema:definition.inputSchema})};
  const system='Ты Ива. Оба навыка уже загружены. Диагностическая проверка маршрутизации: инструменты не исполняются; выбери один соответствующий вызов, без промежуточной реплики.\n'+skills['tender-search'].markdown+'\n'+skills['tender-history'].markdown;
  for(const c of [
    {task:'Ищи доступные мне тендеры на разработку программ, сайтов и приложений по всем направлениям.',action:'research'},
    {task:`Я выбрал тендер ${input.target}. Изучи историю максимально близких состоявшихся торгов, участников и снижение цены.`,action:'history'},
  ]){
    const r=await generateText({model:makeCodexModel(input.model,`tender-skills-${c.action}`),system,
      messages:[{role:'user',content:c.task}],tools,toolChoice:'required',abortSignal:AbortSignal.timeout(90000)});
    const call=r.toolCalls.find(x=>x.toolName==='tender_agent');
    const pass=(call?.input?.action??'research')===c.action&&Boolean(call)&&(c.action!=='history'||call.input.target_url===input.target);
    result.cases.push({expected:c.action,pass,args:call?.input??null});
  }
}
result.ok=result.cases.every(c=>c.pass);
fs.writeFileSync(process.argv[3],JSON.stringify(result,null,2));
if(!result.ok)process.exitCode=1;
