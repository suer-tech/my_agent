import {defineTool} from 'eve/tools';
import {z} from 'zod';
export default defineTool({
 description:'Тендерный субагент Ивы. Сначала загрузи tender-search или tender-history. action=research ищет новые активные тендеры; action=history исследует состоявшиеся аналоги выбранной карточки target_url, участников и цены. Это разные длительные фоновые задачи с обходом всех страниц и чтением документов, без квоты кандидатов. Инструмент возвращает job_id; отдельный сервис продолжает работу и отдаёт полный отчёт файлом. status/report/resume/cancel управляют сохранённой задачей, без нового поиска. quick — короткий вопрос/предпочтение, не исследование. Передавай поручение целиком. Проверяй worker_alive перед обещанием фоновой работы. Не подаёт заявки.',
 inputSchema:z.object({task:z.string().min(1).max(12000),action:z.enum(['research','history','status','report','resume','cancel','quick']).default('research'),job_id:z.string().regex(/^[a-f0-9]{12}$/).optional(),target_url:z.string().url().optional()}),
 async execute(input){
  const response=await fetch(process.env.PILOT_GATEWAY_URL+'/tender-agent',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+process.env.PILOT_AGENT_TOKEN},body:JSON.stringify(input),signal:AbortSignal.timeout(500000)});
  return response.json();
 }
});
