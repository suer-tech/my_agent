import {defineMcpClientConnection} from 'eve/connections';
export default defineMcpClientConnection({
  url:process.env.PILOT_GATEWAY_URL+'/mcp',
  description:'Семейные финансы: существующий SQLite-реестр с точными банковскими графиками, картами, долгами и переменными расходами. Инструмент finance имеет только read/propose/pending. Деньги RUB strings -> kopecks. Ясное прямое поручение владельца разрешает немедленную запись; код подтверждения не нужен.',
  auth:{getToken:async()=>({token:process.env.PILOT_AGENT_TOKEN??''})},
});
