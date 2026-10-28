// Uses real tool schemas, deliberately omits every execute function.
import fs from 'node:fs';
import {generateText,tool} from 'ai';
import {makeCodexModel} from './agent/provider.ts';
const input=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const tools={};
for(const name of ['finance_agent','tender_agent','personal_calendar','reply_format','web_browser']){
  const {default:d}=await import(`./agent/tools/${name}.ts`);
  if(!d.inputSchema) throw new Error(`Missing schema: ${name}`);
  tools[name]=tool({description:d.description,inputSchema:d.inputSchema});
}
const cases=[
 ['expense','Запиши расходы: 1500 на подарок, 858 продукты, 1584 детское питание и ещё 864 продукты.','finance_agent'],
 ['income','Внеси полученный сегодня доход по фрилансу 8000 рублей.','finance_agent'],
 ['credit','Сохрани ставку по тестовому кредиту 20 процентов годовых и остаток 120000 рублей.','finance_agent'],
 ['savings','Рассчитай, сколько откладывать на подарки к Новому году: жене 20000, дочкам по 5000. План пока не включай.','finance_agent'],
 ['tender','Найди подходящие мне актуальные тендеры на разработку сайтов.','tender_agent'],
 ['tender_history','Изучи похожие завершённые торги для закупки 0845500001326000043, цены контрактов, участников и риски заказчика.','tender_agent'],
 ['birthday','Запомни день рождения моей сестры 15 ноября, напоминай каждый год за день.','personal_calendar'],
 ['relative_reminder','Через 30 минут напомни позвонить заказчику.','personal_calendar'],
 ['voice','Я за рулём, дальше читай мне ответы вслух, пока не попрошу писать.','reply_format'],
 ['text','Хватит озвучивать, теперь только пиши мне ответы.','reply_format'],
 ['browser','Открой https://www.ozon.ru/ и посмотри цены на детские подгузники.','web_browser'],
];
const report={scope:'Real native provider and real schemas; tool execution disabled. Production policies carried over for this test only.',cases:[]};
for(const [name,prompt,expected] of cases){
  try{
    const r=await generateText({model:makeCodexModel('gpt-6-luna',`compat-${name}`),
      system:input.system,messages:[{role:'user',content:prompt}],tools,
      abortSignal:AbortSignal.timeout(60000)});
    const calls=r.toolCalls.map(c=>({name:c.toolName,args:c.input}));
    let pass=calls.some(c=>c.name===expected)&&!r.text.includes('/confirm');
    const a=calls.find(c=>c.name===expected)?.args;
    if(name==='expense')pass=pass&&calls.length===1&&['1500','858','1584','864'].every(v=>a.task.includes(v));
    if(name==='birthday')pass=pass&&a.repeat==='yearly'&&a.lead_minutes?.includes(1440);
    if(name==='relative_reminder')pass=pass&&a.after_minutes===30;
    if(name==='voice')pass=pass&&a.format==='voice'&&a.scope==='persistent';
    if(name==='text')pass=pass&&a.format==='text'&&a.scope==='persistent';
    report.cases.push({name,pass,text:r.text,calls});
  }catch(e){report.cases.push({name,pass:false,error:e.name,status:e.statusCode??null});}
  fs.writeFileSync(process.argv[3],JSON.stringify(report,null,2));
}
report.passed=report.cases.filter(c=>c.pass).length;
report.total=report.cases.length;
fs.writeFileSync(process.argv[3],JSON.stringify(report,null,2));
