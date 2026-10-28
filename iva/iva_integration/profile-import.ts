import {readFileSync,existsSync,mkdirSync,renameSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {writeCore,textHash} from '../data/iva-pilot/app/agent/lib/core-write.ts';
import {appendDaily} from '../data/iva-pilot/app/agent/lib/vault-daily.ts';
import {commitVaultSweep} from '../data/iva-pilot/app/agent/lib/vault-commit.ts';
const app=process.cwd(),vault=process.env.ASSISTANT_VAULT_DIR!;
const profile=JSON.parse(readFileSync(join(app,'../owner-profile-confirmed.json'),'utf8'));
const source=join(vault,'library/owner-confirmed-profile-2026-10-19.md');
if(!existsSync(source)){
  const archive=join(vault,'library/pilot-test-archive');mkdirSync(archive,{recursive:true});
  const daily=join(vault,'daily/2026-10-19.md');
  if(existsSync(daily)){
    const raw=readFileSync(daily,'utf8');
    if(raw.includes('CEDAR-IVA-7319') && raw.includes('Это проверка голоса') && !raw.includes('Подтверждённые сведения владельца'))renameSync(daily,join(archive,'console-before-profile.md'));
  }
  // Archive only the known synthetic Cedar card, not owner cards.
  const {readdirSync}=await import('node:fs');
  const walk=(dir:string)=>{for(const entry of readdirSync(dir,{withFileTypes:true})){const path=join(dir,entry.name);if(entry.isDirectory())walk(path);else if(entry.name.endsWith('.md') && readFileSync(path,'utf8').includes('CEDAR-IVA-7319'))renameSync(path,join(archive,entry.name));}};
  walk(join(vault,'cards'));
  mkdirSync(join(vault,'library'),{recursive:true});
  writeFileSync(source,'# Подтверждённый профиль владельца\n\nИсточник: текущий чат с владельцем, подтверждено пользователем 19 октября 2026; ссылки на Telegram не выдумываются.\n\n'+profile.facts.map((s:string)=>'- '+s).join('\n')+'\n');
  appendDaily('[text]','Подтверждённые сведения владельца из текущего чата (19 октября 2026). Сохранить как факты с этим источником; не копировать секреты или изменяемые финансовые суммы.\n'+profile.facts.join('\n'));
}
const before=readFileSync(join(vault,'CORE.md'),'utf8');const seed=readFileSync(join(app,'vault-template/CORE.md'),'utf8');
let changed=false;
if(before===seed){const result=await writeCore({vault,next:profile.core,reason:'Подтверждённый пользователем профиль; текущий чат 19.10.2026',date:'2026-10-19',source:'[[library/owner-confirmed-profile-2026-10-19]]',expectedHash:textHash(before),mode:'day'});if(!result.ok)throw Error(result.error??'CORE conflict');changed=true;}
const commit=await commitVaultSweep('memory: owner-confirmed profile; archive synthetic pilot history',vault);if(!commit.ok)throw Error(commit.reason);
console.log(JSON.stringify({owner_profile_source_saved:true,core_initialized:changed,subsequent_core_edits_preserved:!changed,synthetic_history_archived_separately:true}));
