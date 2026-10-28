
import {ProxyAgent,fetch as proxyFetch} from 'undici';
import {appendFileSync} from 'node:fs';
import {fastModeFetch} from '/opt/iva/iva_integration/fast-mode.mjs';
const direct=globalThis.fetch;
const proxy=new ProxyAgent('http://10.203.77.2:8888');
const routed=(input,init={})=>{
 const url=new URL(typeof input==='string'||input instanceof URL?input:input.url);
 return ['chatgpt.com','auth.openai.com'].includes(url.hostname)?proxyFetch(input,{...init,dispatcher:proxy}):direct(input,init);
};
globalThis.fetch=fastModeFetch(routed,{
 enabled:process.env.IVA_SERVICE_TIER==='fast',
 record:value=>appendFileSync('/opt/iva/data/fast-mode-audit.jsonl',JSON.stringify({at:new Date().toISOString(),...value})+'\n',{mode:0o600}),
});
