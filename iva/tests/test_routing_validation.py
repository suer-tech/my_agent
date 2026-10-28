import json
import unittest
from iva_integration.gateway import SCHEMA
from iva_integration.routing_validation import validated_routing
from local_finance.codex_cli import CliError, StructuredOutputError

TOOLS={'family_finance':{'parameters':{'type':'object','properties':{'mode':{'enum':['read','propose']},'payload_json':{'type':'string'}},'required':['mode'],'additionalProperties':False}}}
def call(arguments='{"mode":"read","payload_json":"{}"}'):
    return {'name':'family_finance','arguments_json':arguments}
GOOD={'text':'','calls':[call()]}

class CLI:
    def __init__(self,values):self.values=iter(values);self.prompts=[];self.last_usage=None
    def structured(self,prompt,*args,**kwargs):
        self.prompts.append(prompt);self.last_usage={'input_tokens':10,'output_tokens':2}
        value=next(self.values)
        if isinstance(value,Exception):raise value
        return value

class RoutingValidationTests(unittest.TestCase):
    def test_invalid_inner_json_retried_before_any_calls_return(self):
        cli=CLI([{'text':'','calls':[call(),call('{broken')]},GOOD])
        result,usage,retries=validated_routing(cli,'prompt',SCHEMA,TOOLS)
        self.assertEqual(result,GOOD);self.assertEqual(retries,1)
        self.assertEqual(len(cli.prompts),2);self.assertIn('No tool from the rejected response has executed',cli.prompts[1])
        self.assertEqual(usage['input_tokens'],20)

    def test_outer_json_failure_is_retried(self):
        cli=CLI([StructuredOutputError('malformed'),GOOD])
        self.assertEqual(validated_routing(cli,'p',SCHEMA,TOOLS)[0],GOOD)

    def test_nested_payload_shape_tool_and_schema_validated(self):
        invalid=[{'text':7,'calls':[]}, {'text':'','calls':[{'name':'shell','arguments_json':'{}'}]},
                 {'text':'','calls':[call('[]')]}, {'text':'','calls':[call('{"mode":"delete"}')]},
                 {'text':'','calls':[call(json.dumps({'mode':'read','payload_json':'{broken'}))]},
                 {'text':'','calls':[call('{"mode":"read","mode":"propose"}')]},
                 {'text':'','calls':[call(json.dumps({'mode':'read','payload_json':'{"n":NaN}'}))]}]
        for value in invalid:
            with self.subTest(value=value):
                cli=CLI([value,GOOD]);self.assertEqual(validated_routing(cli,'p',SCHEMA,TOOLS)[2],1)

    def test_second_failure_is_bounded_and_contains_no_raw_output(self):
        cli=CLI([{'secret':'private'},{'secret':'private'}])
        with self.assertRaises(StructuredOutputError) as caught:validated_routing(cli,'p',SCHEMA,TOOLS)
        self.assertNotIn('private',str(caught.exception));self.assertEqual(len(cli.prompts),2)

    def test_auth_timeout_and_transport_errors_are_not_retried(self):
        for error in [CliError('auth'),CliError('timeout'),OSError('transport')]:
            cli=CLI([error])
            with self.assertRaises(type(error)):validated_routing(cli,'p',SCHEMA,TOOLS)
            self.assertEqual(len(cli.prompts),1)

if __name__=='__main__':unittest.main()
