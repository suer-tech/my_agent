// Only explicitly user-facing, model-authored updates cross this boundary.
// Reasoning, specialist outputs and arbitrary tool data are never announcements.
export async function* progressStream(response,{source,onProgress}){
 const seen=new Set(),inputs=new Map();
 const publish=async(id,message)=>{
  if(typeof message!=='string')return;
  message=message.trim();
  if(id && !seen.has(id) && message && message.length<=700){
   seen.add(id);
   try{await onProgress({kind:'progress',source,id,message});}catch{/* Delivery must not replay work. */}
  }
 };
 for await(const event of response){
  if(event.type==='action.input.appended' && event.data?.toolName==='user_update'){
   const {callId,inputTextDelta}=event.data;
   const text=(inputs.get(callId)??'')+(inputTextDelta??'');
   if(text.length<=4096){
    inputs.set(callId,text);
    try{await publish(callId,JSON.parse(text).message);}catch{/* Incomplete JSON. */}
   }
  }
  // This tool only announces intent. Relay its validated input as soon as it
  // arrives, before the model finishes generating other calls in the same step.
  if(event.type==='actions.requested'){
   for(const action of event.data?.actions??[]){
    if(action.kind==='tool-call' && action.toolName==='user_update')await publish(action.callId,action.input?.message);
   }
  }
  const result=event.data?.result;
  if(event.type==='action.result' && event.data?.status==='completed' &&
     result?.kind==='tool-result' && result.toolName==='user_update' && !result.isError &&
     result.output?.ok===true && typeof result.output.message==='string'){
   await publish(result.callId??event.meta?.id,result.output.message);
  }
  yield event;
 }
}
