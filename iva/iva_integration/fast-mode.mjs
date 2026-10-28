// Scoped subscription Fast mode. Does not touch auth or other network routes.
export function fastModeFetch(fetcher,{enabled=false,record=()=>{}}={}){
 return async(input,init={})=>{
  const url=new URL(typeof input==='string'||input instanceof URL?input:input.url);
  let request;
  if(enabled && url.hostname==='chatgpt.com' && url.pathname==='/backend-api/codex/responses' && typeof init.body==='string'){
   try{
    const body=JSON.parse(init.body);
    if(body && typeof body.model==='string'){
     body.service_tier='priority';init={...init,body:JSON.stringify(body)};
     request={model:body.model,requested:'priority'};
    }
   }catch{/* Non-JSON input retains its original behavior. */}
  }
  const response=await fetcher(input,init);
  if(!request)return response;
  const safeRecord=value=>{try{record({...request,...value});}catch{/* Audit cannot break inference. */}};
  safeRecord({status:response.status});
  if(!response.ok || !response.body || !response.headers.get('content-type')?.includes('text/event-stream'))return response;
  const decoder=new TextDecoder();let buffer='';
  const inspect=line=>{
   if(!line.startsWith('data:'))return;
   try{
    const event=JSON.parse(line.slice(5));
    if(['response.created','response.completed'].includes(event.type))safeRecord({event:event.type,actual:event.response?.service_tier??null});
   }catch{/* Never log model content. */}
  };
  return new Response(response.body.pipeThrough(new TransformStream({
   transform(chunk,controller){
    controller.enqueue(chunk);buffer+=decoder.decode(chunk,{stream:true});
    let newline;
    while((newline=buffer.indexOf('\n'))!==-1){inspect(buffer.slice(0,newline).trim());buffer=buffer.slice(newline+1);}
    if(buffer.length>2_000_000)buffer='';
   },
   flush(){buffer+=decoder.decode();if(buffer)inspect(buffer.trim());},
  })),{status:response.status,statusText:response.statusText,headers:response.headers});
 };
}
