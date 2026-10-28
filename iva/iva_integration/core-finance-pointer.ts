import {readFileSync,existsSync} from 'node:fs';
import {join} from 'node:path';
import {writeCore,textHash} from '../data/iva-pilot/app/agent/lib/core-write.ts';
import {commitVaultSweep} from '../data/iva-pilot/app/agent/lib/vault-commit.ts';
const vault=process.env.ASSISTANT_VAULT_DIR!;
const before=readFileSync(join(vault,'CORE.md'),'utf8');
const runtime=join(process.cwd(),'../../iva-runtime.json');
const connected=existsSync(runtime) && JSON.parse(readFileSync(runtime,'utf8')).mode==='telegram';
const next=before.replace('актуальные суммы, статусы и шесть отдельных долгов читать там','актуальные суммы, статусы и перечень отдельных долгов читать там').replace('Рабочий Telegram ещё не переключён.',connected?'Telegram подключён к Ива через локальный адаптер одного владельца.':'Рабочий Telegram ещё не переключён.');
if(next!==before){
 const result=await writeCore({vault,next,reason:'Актуальный перечень долгов читать из финансового инструмента, без фиксированного числа в CORE',date:'2026-10-19',source:'current owner clarification; financial instrument',expectedHash:textHash(before),mode:'day'});
 if(!result.ok)throw Error(result.error??'CORE conflict');
 const commit=await commitVaultSweep('memory: point to finance for current debt inventory',vault);if(!commit.ok)throw Error(commit.reason);
}
console.log(JSON.stringify({core_fixed_debt_count_removed:true,historical_sources_unchanged:true}));
