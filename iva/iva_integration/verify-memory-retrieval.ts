import {registerHooks} from 'node:module';
import {existsSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
// The production Eve bundle resolves TypeScript sources for .js imports.
// Give this standalone source-level test the same resolution without editing Iva.
registerHooks({resolve(specifier,context,nextResolve){
 if(specifier.endsWith('.js') && specifier.startsWith('.') && context.parentURL){
  const source=new URL(specifier.slice(0,-3)+'.ts',context.parentURL);
  if(source.protocol==='file:' && existsSync(fileURLToPath(source)))return nextResolve(source.href,context);
 }
 return nextResolve(specifier,context);
}});
const {searchMemory}=await import('../data/iva-pilot/app/agent/tools/memory_search.ts');
const vault=process.env.ASSISTANT_VAULT_DIR!;
if(!vault.includes('memory-tests'))throw Error('Synthetic vault required');
const result=await searchMemory({query:'LIGHTHOUSE-8427',scope:['cards/projects'],limit:2});
if(!result.hits?.length)throw Error('Missing project retrieval');
const project=readFileSync(join(vault,result.hits[0].file),'utf8');
if(!project.includes('Актуальный срок — 25 ноября') || !project.includes('20 ноября устарел'))throw Error('Correction lost');
const unknown=await searchMemory({query:'Тестовый родственник',scope:['cards/contacts'],limit:1});
if(!unknown.hits?.length)throw Error('Unknown contact not retrievable');
const contact=readFileSync(join(vault,unknown.hits[0].file),'utf8');
if(!contact.includes('дата рождения неизвестна'))throw Error('Unknown replaced');
console.log(JSON.stringify({native_search_relevant_scope:true,corrected_fact_retrievable:true,unknown_fact_retrievable:true,project_hits:result.hits.length,contact_hits:unknown.hits.length}));
