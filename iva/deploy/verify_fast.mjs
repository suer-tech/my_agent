// One harmless subscription probe; never emit authorization or raw error bodies.
import {execFileSync} from 'node:child_process';
const started=performance.now();
const tier=process.argv[2]??'priority',model=process.argv[3]??'gpt-6-luna';
if(!['fast','priority'].includes(tier)||!['gpt-6-luna','gpt-6.1-sol'].includes(model))throw Error('Unsupported diagnostic parameters');
const auth=JSON.parse(execFileSync('/opt/iva/.venv/bin/python',['/opt/iva/local_finance/shared_auth.py'],{env:{...process.env,CODEX_HOME:'/home/iva/.codex'},encoding:'utf8',timeout:480000}));
const headers={'Content-Type':'application/json',Authorization:'Bearer '+auth.accessToken,originator:'codex_cli_rs','User-Agent':'codex_cli_rs/0.163.0'};
if(auth.accountId)headers['ChatGPT-Account-Id']=auth.accountId;
const response=await fetch('https://chatgpt.com/backend-api/codex/responses',{
 method:'POST',headers,signal:AbortSignal.timeout(60000),body:JSON.stringify({
  model,service_tier:tier,store:false,stream:true,
  instructions:'Answer the harmless test briefly.',
  input:[{role:'user',content:[{type:'input_text',text:'Reply exactly: FAST_OK'}]}],
  reasoning:{effort:'low'},
 }),
});
if(!response.ok){
 let error;try{error=(await response.json()).error;}catch{}
 console.log(JSON.stringify({ok:false,status:response.status,error_type:error?.type,param:error?.param,code:error?.code}));process.exitCode=1;
}else{
 let buffer='',text='',actual=null;const decoder=new TextDecoder();
 for await(const chunk of response.body){
  buffer+=decoder.decode(chunk,{stream:true});let end;
  while((end=buffer.indexOf('\n'))>=0){
   const line=buffer.slice(0,end).trim();buffer=buffer.slice(end+1);
   if(!line.startsWith('data:'))continue;
   let e;try{e=JSON.parse(line.slice(5));}catch{continue;}
   if(e.type==='response.output_text.delta')text+=e.delta;
   if(e.type==='response.completed')actual=e.response?.service_tier??null;
  }
 }
 console.log(JSON.stringify({ok:text.includes('FAST_OK'),model,requested:tier,actual,seconds:(performance.now()-started)/1000}));
 if(!text.includes('FAST_OK'))process.exitCode=1;
}
