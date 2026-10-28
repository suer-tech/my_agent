import { defineTool } from "eve/tools";
import { z } from "zod";

export default defineTool({
  description: "Семейный финансовый реестр SQLite. Для общего бюджета: family_forecast(as_of,until,reserve?,income_amounts? {ref:RUB},income_dates? {occurrence:ISO|null},expense_remaining? {ref:RUB}), family_scenarios(as_of,until,delay20_until? и параметры family_forecast), family_allocation(as_of,until,allocations? {debt_ref:RUB}), debt_inventory(as_of), income_history(ref?). actual_balance отдельно от ожиданий. Неизвестные суммы зарплат не нулевые; ориентир группы не делить по датам. Также read: contract, snapshot, balances, calendar, forecast, loan_scenario, card_scenario, debt_term_info. Propose в Telegram: ясное поручение сохраняется сразу, без повторного подтверждения; income_receive для фактического поступления. Pending: предложения владельцу. Вход RUB строки, выход целые копейки. source стабилен. Ясное поручение владельца уже разрешает изменение; никаких кодов подтверждения. Подключённый финансовый реестр; в Telegram режиме единая рабочая база.",
  inputSchema: z.object({ mode:z.enum(["read","propose","pending"]),action:z.string().optional(),payload_json:z.string().default("{}"),source:z.string().optional() }),
  async execute(input) {
    const response=await fetch(process.env.PILOT_GATEWAY_URL+"/finance",{
      method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+process.env.PILOT_AGENT_TOKEN},
      body:JSON.stringify({...input,payload:JSON.parse(input.payload_json)}),
    });
    return await response.json();
  },
});
