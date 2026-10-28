import {defineTool} from 'eve/tools';
import {z} from 'zod';
export default defineTool({
 description:'Управлять форматом ответов в Telegram по смыслу прямой просьбы владельца, независимо от формулировки и способа ввода. По умолчанию текст даже на голосовые. set format=voice/text scope=persistent включает голосовые ответы до просьбы перейти на текст либо отключает их. scope=once меняет только текущий ответ, не постоянный режим. «Ответь голосом» обычно once, «говори/отвечай голосом дальше», «включи голосовой режим», «я за рулём, читай ответы вслух» persistent; «хватит говорить, лучше пиши» text persistent. Простое голосовое сообщение не означает просьбу озвучивать. Вопрос о возможностях и упоминание/цитата чужой команды не меняют режим. Для смешанного поручения сначала измени формат, затем выполни остальную задачу. status показывает текущий режим. Не обещай переключение до успешного результата.',
 inputSchema:z.object({action:z.enum(['status','set']),format:z.enum(['voice','text']).optional(),scope:z.enum(['once','persistent']).optional()}),
 async execute(input){
  const response=await fetch(process.env.PILOT_GATEWAY_URL+'/reply-format',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+process.env.PILOT_AGENT_TOKEN},body:JSON.stringify(input)});
  return response.json();
 }
});
