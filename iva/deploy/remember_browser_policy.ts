import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import writeCard from '../data/iva-pilot/app/agent/tools/write_card.ts';
import {writeCore,textHash} from '../data/iva-pilot/app/agent/lib/core-write.ts';

const source='Прямое поручение владельца в Codex 23.10.2026: «заложи в память ивы что по очереди запускать можно работу макса и треадс - чередовать их работу чтобы не было зависаний»; также сообщил: «все вход выполнен удачно и все прогрузилсоь».';
const text='На общем VPS с 2 ГБ RAM работу браузеров MAX и Threads чередовать: одновременно разрешена только одна браузерная задача MAX либо Threads. Перед переключением дождаться завершения текущего действия и сохранить его результат, штатно закрыть предыдущий браузер с сохранением профиля, проверить освобождение памяти, затем запускать следующий. Не прерывать незавершённую отправку, не обходить блокировки MAX и не увеличивать лимиты ради параллельного запуска; Иву сохранять работающей. Если безопасно переключиться нельзя, отложить следующую задачу и сообщить владельцу. Владелец сообщил об успешном входе в Threads; в каком браузере сохранена сессия и её доступность серверному исполнителю требуют отдельной проверки. Это постоянное правило владельца; автоматический переключатель и публикации Threads этим сохранением не устанавливаются.';
const result=await writeCard.execute({operation:'fact',type:'decision',title:'Чередование браузеров MAX и Threads',text,description:'На VPS MAX и Threads работают по очереди, сохраняя профили и приоритет Ивы.',source,tags:['MAX','Threads','VPS']});
if(!result.ok)throw Error(JSON.stringify(result));
const vault=process.env.ASSISTANT_VAULT_DIR!;
const before=readFileSync(join(vault,'CORE.md'),'utf8');
const rule='MAX и Threads на общем VPS запускать по очереди: завершить действие и закрыть предыдущий браузер, сохранить профиль, проверить память; не прерывать отправку и не останавливать Иву. Правило владельца 23.10.2026.';
let coreResult={ok:true};
if(!before.includes(rule))coreResult=await writeCore({vault,next:before.trimEnd()+'\n\n'+rule+'\n',reason:'Прямое поручение владельца о чередовании MAX и Threads',date:'2026-10-23',source:'Codex, прямое сообщение владельца 23.10.2026',expectedHash:textHash(before),mode:'day'});
if(!coreResult.ok)throw Error(JSON.stringify(coreResult));
console.log(JSON.stringify({card_saved:result.ok,core_saved:coreResult.ok,core_readback:readFileSync(join(vault,'CORE.md'),'utf8').includes(rule)}));
