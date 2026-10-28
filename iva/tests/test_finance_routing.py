import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from iva_integration.finance_routing import capability_instructions,repair_false_missing_claim,capability_probe_used


class FinanceRoutingTests(unittest.TestCase):
    def test_false_denial_becomes_actual_read_not_a_write(self):
        value={'text':'В доступном наборе нет финансового реестра. Подключи family_finance.','calls':[]}
        result,changed=repair_false_missing_claim(value,{'family_finance':{}})
        self.assertTrue(changed)
        args=json.loads(result['calls'][0]['arguments_json'])
        self.assertEqual(args,{'mode':'read','action':'contract','payload_json':'{}'})
        _,changed=repair_false_missing_claim({'text':'В текущем манифесте инструмента family_finance нет.','calls':[]},{'family_finance':{}})
        self.assertTrue(changed)

    def test_probe_is_bounded_per_current_owner_turn(self):
        messages=[{'role':'user','content':'Запиши доход'},{'role':'assistant','tool_calls':[{'id':'call_finance_capability_probe_1'}]},{'role':'tool','tool_call_id':'call_finance_capability_probe_1','content':'contract'}]
        self.assertTrue(capability_probe_used(messages))
        value={'text':'Нет финансового инструмента','calls':[]}
        result,changed=repair_false_missing_claim(value,{'family_finance':{}},True)
        self.assertFalse(changed);self.assertFalse(result['calls']);self.assertIn('подключён',result['text'])
        self.assertFalse(capability_probe_used(messages+[{'role':'user','content':'Следующая просьба'}]))

    def test_real_absence_and_backend_errors_not_fabricated(self):
        value={'text':'Нет финансового инструмента','calls':[]}
        self.assertEqual(repair_false_missing_claim(value,{}),(value,False))
        actual={'text':'Сервис вернул ошибку запроса, запись не выполнена','calls':[]}
        self.assertEqual(repair_false_missing_claim(actual,{'family_finance':{}}),(actual,False))

    def test_valid_write_is_preserved_and_alias_supported(self):
        value={'text':'','calls':[{'name':'family-finance__finance','arguments_json':'{"mode":"propose"}'}]}
        self.assertEqual(repair_false_missing_claim(value,{'family-finance__finance':{}}),(value,False))
        contract={'read':{'contract':'()'},'propose':{'stage':'(data,source)'}}
        text=capability_instructions({'family-finance__finance':{}},contract)
        self.assertIn('family-finance__finance',text);self.assertIn('"connected": true',text)
        self.assertIn('one-off',text);self.assertIn('/confirm',text)


if __name__=='__main__':unittest.main()
