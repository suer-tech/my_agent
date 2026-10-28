import {defineTool} from 'eve/tools';
import {z} from 'zod';
export default defineTool({
 description:'Диск сервера Ивы. status: фактические общий/занятый/свободный объёмы. analyze: крупные каталоги, доступные к очистке кэши и просроченные резервные копии, причины и ожидаемый эффект. Большой каталог сам по себе не мусор. clean: удалить только выбранные candidate_ids из свежего анализа по ЯСНОЙ текущей команде владельца; instruction_quote — полная точная цитата этой команды. Не вызывай clean для просьбы посмотреть место/предложить решения/добавить функционал. Можно сначала analyze, затем clean в том же ходе, если владелец уже поручил очистку. Обычная команда не требует второго разрешения или кода. Произвольные пути, базы, документы, память, браузеры, свежие бэкапы и активные файлы удалить нельзя. При изменении файлов или занятости удаление пропускается. Сообщай фактически удалённый объём и пропуски; free_delta может отличаться из-за параллельной работы.',
 inputSchema:z.object({action:z.enum(['status','analyze','clean']),candidate_ids:z.array(z.string().max(100)).min(1).max(32).optional(),instruction_quote:z.string().max(16000).optional()}),
 async execute(input){
  const response=await fetch(process.env.PILOT_GATEWAY_URL+'/disk-storage',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+process.env.PILOT_AGENT_TOKEN},body:JSON.stringify(input),signal:AbortSignal.timeout(240000)});
  return response.json();
 }
});
