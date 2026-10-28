import {readFileSync,existsSync} from 'node:fs';
import {join} from 'node:path';
import {buildReadyPeriods} from '../data/iva-pilot/app/scripts/memory/night-periods.ts';
import {nightModelName,ceiling} from '../data/iva-pilot/app/scripts/memory/night-call.ts';
const vault=process.env.ASSISTANT_VAULT_DIR!;
if(!vault.includes('memory-tests'))throw Error('Synthetic vault required');
const jobs:string[]=[];
const fallback=await buildReadyPeriods(vault,'2026-01-05',{skill:readFileSync(join(process.cwd(),'scripts/memory/instructions/night/period.md'),'utf8'),model:nightModelName,signal:AbortSignal.timeout(500000),paused:new Set()},jobs);
for(const path of ['weekly/2026-W01.md','monthly/2025-12.md','yearly/2025.md']){
 if(!existsSync(join(vault,path)))throw Error('Missing '+path);
 const text=readFileSync(join(vault,path),'utf8');
 if(!text.includes('LIGHTHOUSE-8427') && !text.includes('Маяк'))throw Error('Unrelated summary '+path);
 if(text.includes('mode: fallback'))throw Error('Period fell back without model '+path);
}
if(fallback.length)throw Error('Unexpected period fallback');
console.log(JSON.stringify({native_periods:true,model_calls:ceiling.calls,fallbacks:fallback.length,jobs}));
