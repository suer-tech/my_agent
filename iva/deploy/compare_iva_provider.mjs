// Run from the Iva app directory; diagnostic tools have no executors.
import fs from 'node:fs';
import {generateText, jsonSchema, tool} from 'ai';
import {makeCodexModel} from './agent/provider.ts';
const input=JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const tools=Object.fromEntries(input.tools.map(t=>[t.function.name, tool({
  description:t.function.description,inputSchema:jsonSchema(t.function.parameters)
})]));
try {
  const started=Date.now();
  const result=await generateText({
    model:makeCodexModel('gpt-6-luna','isolated-provider-comparison'),
    system:input.system,messages:input.messages,tools,
    abortSignal:AbortSignal.timeout(90000),
  });
  const report={
    text:result.text,calls:result.toolCalls.map(c=>({name:c.toolName,args:c.input})),
    seconds:(Date.now()-started)/1000,usage:result.usage
  };
  if(result.toolCalls.length) {
    const follow=await generateText({
      model:makeCodexModel('gpt-6-luna','isolated-provider-comparison'),
      system:input.system,tools,
      messages:[...input.messages,...result.response.messages,{
        role:'tool',content:result.toolCalls.map(c=>({
          type:'tool-result',toolCallId:c.toolCallId,toolName:c.toolName,
          output:{type:'json',value:input.mock_result}
        }))
      }],
      abortSignal:AbortSignal.timeout(90000)
    });
    report.after_mock_result={text:follow.text,calls:follow.toolCalls.map(c=>({name:c.toolName,args:c.input}))};
  }
  fs.writeFileSync(process.argv[3],JSON.stringify(report));
} catch(e) {
  // Never serialize raw provider responses or auth headers.
  fs.writeFileSync(process.argv[3],JSON.stringify({
    error:String(e.name),status:e.statusCode??null,
    message:String(e.message).replace(/Bearer\s+\S+/gi,'Bearer [redacted]').slice(0,500)
  }));
  process.exitCode=1;
}
