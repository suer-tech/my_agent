import {defineTool} from 'eve/tools';
import {z} from 'zod';
export default defineTool({
 description:'Финансовый субагент Sol 6.1 medium с ядром SQLite: ВСЕ вопросы о доходах, расходах, переводах, остатках, кредитах, стратегиях погашения, планировании бюджета и чеках передавай ему. Он сам читает контекст текущего владельца, использует финансовые инструменты, проверяет и сохраняет ясные факты, задаёт только существенные уточнения. task — полная финансовая задача, включая все операции сообщения; не передавай по одной покупке. Не решай финансовую семантику на Luna. Остальные поручения того же сообщения, например календарь, выполни самостоятельно. Не подменяй ответ субагента и не говори о записи до его результата.',
 inputSchema:z.object({task:z.string().min(1).max(12000)}),
 async execute(input){
  const response=await fetch(process.env.PILOT_GATEWAY_URL+'/finance-agent',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+process.env.PILOT_AGENT_TOKEN},body:JSON.stringify(input),signal:AbortSignal.timeout(540000)});
  return response.json();
 }
});
