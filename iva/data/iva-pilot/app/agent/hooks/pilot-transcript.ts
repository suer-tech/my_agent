import {defineHook} from 'eve/hooks';
import {appendDaily} from '../lib/vault-daily.ts';
export default defineHook({events:{
  'message.completed':(event,ctx)=>{
    if(ctx.channel.kind!=='http' || ctx.session.parent || event.data.finishReason==='tool-calls')return;
    const text=(event.data.message??'').trim();
    if(text)appendDaily('[iva pilot]',text);
  },
}});
