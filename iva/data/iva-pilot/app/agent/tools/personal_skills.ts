import {defineTool} from 'eve/tools';
import {z} from 'zod';
export default defineTool({
 description:'Создавать себе навыки по смыслу текущего поручения владельца и хранить их память между разговорами. list/read — найти и прочитать существующий навык, save — создать или обновить целую инструкцию (name, title, description до 120 символов, instructions, expected_revision=0 для нового), archive — отключить, сохранив историю. Имена personal-латиницей. recall — прочитать записи навыка (key или query, offset/limit до 100); remember — сохранить/уточнить одну запись (key, title, data_json JSON object, expected_revision=0 для новой). Поля объекта дополняют прежние; не теряй ранее подтверждённые сведения. Все записи требуют evidence — точную цитату текущего сообщения владельца. Чтения не требуют цитаты. Навык доступен через load_skill со следующего хода, не требует перезапуска. Инструкцию пишет сама Ива под задачу, без шаблонного классификатора. Не создаёт код, новые подключения или фоновые задачи. Никогда не выдумывай личные предпочтения и просмотренные фильмы. Сначала проверь существующие навыки и память; загружай подходящий навык автоматически, даже если владелец не назвал его.',
 inputSchema:z.object({
  action:z.enum(['list','read','save','archive','recall','remember']),
  name:z.string().optional(),title:z.string().optional(),description:z.string().optional(),
  instructions:z.string().optional(),expected_revision:z.number().int().nonnegative().optional(),
  evidence:z.string().optional(),key:z.string().optional(),data_json:z.string().optional(),
  query:z.string().optional().describe('Подстрока названия/алиаса для поиска объекта. В начале подбора прочитай всю память без query/key. Нет совпадения — не значит память пуста; проверь scope и stored_total.'),offset:z.number().int().nonnegative().optional(),limit:z.number().int().min(1).max(100).optional(),
 }),
 async execute(input){
  const response=await fetch(process.env.PILOT_GATEWAY_URL+'/personal-skills',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+process.env.PILOT_AGENT_TOKEN},body:JSON.stringify(input),signal:AbortSignal.timeout(20000)});
  if(!response.ok)return {ok:false,error:'Хранилище навыков недоступно; сохранение не подтверждено'};
  return response.json();
 }
});
