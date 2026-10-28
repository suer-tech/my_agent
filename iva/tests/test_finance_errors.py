import json
import unittest
from iva_integration.finance_errors import tool_failure,stop_failed_finance_retries

def call(action='record_paid_expense',source='telegram:owner:1'):
    return {'name':'family_finance','arguments_json':json.dumps({'mode':'propose','action':action,'payload_json':'{}','source':source})}

class FinanceErrorTests(unittest.TestCase):
    def messages(self):
        c=call()
        return [{'role':'user','content':'actual request'},
                {'role':'assistant','tool_calls':[{'id':'failed','function':{'name':c['name'],'arguments':c['arguments_json']}}]},
                {'role':'tool','tool_call_id':'failed','content':json.dumps(tool_failure(PermissionError('private internal details')))}]
    def test_denied_write_returns_reason_without_internal_details(self):
        result=tool_failure(PermissionError('secret'))
        self.assertFalse(result['retryable']);self.assertNotIn('secret',json.dumps(result))
        self.assertEqual(result['error']['code'],'FINANCE_AUTHORIZATION_REQUIRED')
    def test_same_failed_call_is_not_executed_twice(self):
        result=stop_failed_finance_retries({'text':'retry','calls':[call()]},self.messages())
        self.assertFalse(result['calls']);self.assertIn('повтор',result['text'])
    def test_fixed_call_and_other_tools_are_not_blocked(self):
        c=call('stage');result=stop_failed_finance_retries({'text':'fixed','calls':[c]},self.messages())
        self.assertEqual(result['calls'],[c])
        result=stop_failed_finance_retries({'text':'new request','calls':[call()]},self.messages()+[{'role':'user','content':'new request'}])
        self.assertTrue(result['calls'])

if __name__=='__main__':unittest.main()
