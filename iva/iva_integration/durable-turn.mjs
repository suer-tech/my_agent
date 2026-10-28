import {readFileSync,writeFileSync,renameSync,existsSync,mkdirSync,appendFileSync,statSync} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';

const atomic=(path,value)=>{
 const tmp=path+'.'+process.pid+'.tmp';
 writeFileSync(tmp,JSON.stringify(value),{encoding:'utf8',flush:true});renameSync(tmp,path);
};

// Eve sessions expire from creation, not from their most recent message.
// The local workflow record also detects completion before the age limit.
export function sessionResumeInfo(saved,runDir,now=Date.now()){
 let createdAt=saved.session_created_at;
 if(runDir){
  const id=saved.session?.sessionId;
  if(typeof id!=='string'||!/^wrun_[A-Za-z0-9]+$/.test(id))return null;
  try{
   const run=JSON.parse(readFileSync(join(runDir,id+'.json'),'utf8'));
   if(run.runId!==id||!['pending','running'].includes(run.status))return null;
   createdAt=run.createdAt;
  }catch{return null;}
 }
 const age=now-Date.parse(createdAt);
 return Number.isFinite(age)&&age>=0&&age<23*3600*1000?{createdAt}:null;
}

export async function durableTurn({client,dir,request,sessionRunDir,onAccepted=()=>{},consume=response=>response.result()}){
 mkdirSync(join(dir,'turns'),{recursive:true});
 const key=createHash('sha256').update(request.source).digest('hex');
 const job=join(dir,'turns',key+'.json'),sessionFile=join(dir,'session.json');
 const uncertain=()=>({status:'uncertain',source:request.source,error:'Previously started turn requires reconciliation'});
 // Claim the source atomically. Crashes and concurrent/repeated deliveries must
 // never submit the same owner command to Eve a second time.
 try{writeFileSync(job,JSON.stringify({status:'processing',source:request.source,started:new Date().toISOString()}),{encoding:'utf8',flag:'wx',flush:true});}
 catch(error){
  if(error.code!=='EEXIST')throw error;
  try{const cached=JSON.parse(readFileSync(job,'utf8'));return cached.status==='done'?cached:uncertain();}
  catch{return uncertain();}
 }
 const generation=request.engine_generation??null;
 let session,sessionCreatedAt,resumed=false,stage='load_session',resultStatus=null;
 const diagnostic=(state,error)=>{try{const path=join(dir,'turn-diagnostics.jsonl');if(existsSync(path)&&statSync(path).size>1048576)renameSync(path,path+'.1');
 const raw=String(error?.message??'');
 const message=!raw?'':/usable reply/i.test(raw)?'empty_or_incomplete_reply':/timeout|timed out/i.test(raw)?'timeout':/not found|404/i.test(raw)?'session_not_found':/rate.?limit|429/i.test(raw)?'rate_limit':/unauthori|forbidden|401|403/i.test(raw)?'authentication':/context.{0,20}(limit|length)|too many tokens/i.test(raw)?'context_limit':'unclassified';
 const fingerprint=raw?createHash('sha256').update(raw).digest('hex'):undefined;
 appendFileSync(path,JSON.stringify({at:new Date().toISOString(),source_id:key,state,stage,result_status:resultStatus,error_type:error?.name,code:/^[A-Za-z0-9_.-]{1,60}$/.test(String(error?.code??''))?error.code:undefined,message,error_fingerprint:fingerprint})+'\n',{mode:0o600});}catch{}};
 try{
  const saved=existsSync(sessionFile)?JSON.parse(readFileSync(sessionFile,'utf8')):{};
  onAccepted(key);
  // Persist authorization before sending: model calls can start immediately.
  atomic(sessionFile,{...saved,active_source:request.source,engine_generation:generation});
  const resumeInfo=sessionResumeInfo(saved,sessionRunDir);
  let response;stage='submit';
  if((saved.engine_generation??null)===generation&&saved.session&&!saved.needs_recovery&&!saved.active_source&&resumeInfo&&(saved.turn_count??20)<20&&(!saved.pendingQuestions?.length||(saved.pendingQuestions.length===1&&saved.pendingQuestions[0].allowFreeform!==false))){
   resumed=true;sessionCreatedAt=resumeInfo.createdAt;
   session=client.sessions.attach(saved.session.sessionId,{streamIndex:saved.session.streamIndex});
   response=saved.pendingQuestions?.length===1 && saved.pendingQuestions[0].allowFreeform!==false
    ?await session.respond([{requestId:saved.pendingQuestions[0].requestId,text:request.message}])
    :await session.send(request.message);
  }else{
   const message=saved.needs_recovery||saved.active_source
    ?'Предыдущая сессия завершилась с ошибкой. Используй сохранённую память и финансовый реестр; прежние поручения автоматически не повторяй. Обработай только текущее сообщение.\n\n'+request.message:request.message;
   sessionCreatedAt=new Date().toISOString();
   const created=await client.sessions.create({message});session=created.session;response=created.response;
  }
  atomic(sessionFile,{engine_generation:generation,session:session.state,session_created_at:sessionCreatedAt,last_used:new Date().toISOString(),active_source:request.source});
  stage='consume';const result=await consume(response);resultStatus=result.status;
  stage='validate_result';
  const questions=(result.inputRequests??[]).filter(q=>q.kind==='question'&&typeof q.prompt==='string'&&q.prompt.trim());
  if(!result.message?.trim()&&questions.length) result.message=questions.map(q=>q.prompt+(q.options?.length?'\n'+q.options.map(o=>o.label).join(' / '):'')).join('\n\n');
  if(!['completed','waiting'].includes(result.status)||!result.message?.trim())throw Error('Iva turn did not complete with a usable reply');
  const output={status:'done',source:request.source,message:result.message??'Ход завершён без текстового ответа.',session:session.state,finished:new Date().toISOString()};
  // Durable reply first: if the process dies now it is returned from cache.
  atomic(job,output);
  atomic(sessionFile,{engine_generation:generation,session:session.state,session_created_at:sessionCreatedAt,last_used:new Date().toISOString(),active_source:null,needs_recovery:false,turn_count:resumed?(saved.turn_count??0)+1:1,pendingQuestions:questions});
  diagnostic('done');return output;
 }catch(error){
  diagnostic('error',error);
  // A tool may already have committed. Do not rerun the current message.
  // A different, subsequent message will use a fresh session instead.
  const output={status:'uncertain',source:request.source,error:'Iva turn failed; reconcile before replay',session:session?.state};
  if(existsSync(sessionFile)){
   const archived=join(dir,'session-failed-'+key+'.json');
   writeFileSync(archived,readFileSync(sessionFile),{flush:true});
  }
  atomic(sessionFile,{engine_generation:generation,session:session?.state,last_used:new Date().toISOString(),active_source:null,needs_recovery:true});
  // If persisting the session failed after a successful reply, retain it.
  try{const cached=JSON.parse(readFileSync(job,'utf8'));if(cached.status==='done')return cached;}catch{}
  atomic(job,output);return output;
 }
}
