import {defineTool} from 'eve/tools';
import {z} from 'zod';
export default defineTool({
 description:'Работающий локальный календарь и Telegram-напоминания. add: title,at (ISO с датой/временем, без зоны — Екатеринбург), repeat once/daily/weekly/monthly/yearly, kind reminder/birthday/event/deadline, lead_minutes (например [1440,60,0]), requires_ack. Для дней рождения ежегодный повтор, по умолчанию за день; неизвестное время — 09:00 местного времени. list: календарь и незавершённое, start/end ISO; status: пульс диспетчера и неопределённые доставки; update/cancel по id. done/snooze по notice_id, minutes. Отвечать только по результату, сообщить точную дату, повтор и уведомления. Важные reminder/deadline требуют отметки выполнения и дают до трёх повторов через 1/2/3 дня после срока. Доставка идёт отдельным диспетчером без участия модели, даже во время диалога. Финансовые платежи остаются в family_finance, не дублировать.',
 inputSchema:z.object({action:z.enum(['add','list','update','cancel','done','snooze','status']),title:z.string().optional(),at:z.string().optional(),after_minutes:z.number().int().optional(),repeat:z.enum(['once','daily','weekly','monthly','yearly']).optional(),kind:z.enum(['reminder','birthday','event','deadline']).optional(),lead_minutes:z.array(z.number().int()).optional(),requires_ack:z.boolean().optional(),id:z.string().optional(),notice_id:z.string().optional(),minutes:z.number().int().optional(),start:z.string().optional(),end:z.string().optional()}),
 async execute(input){
  const response=await fetch(process.env.PILOT_GATEWAY_URL+'/calendar',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+process.env.PILOT_AGENT_TOKEN},body:JSON.stringify(input)});
  return response.json();
 }
});
