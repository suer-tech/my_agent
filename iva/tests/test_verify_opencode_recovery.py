from pathlib import Path
import tempfile
import unittest

from deploy.verify_opencode_recovery import verify,MODEL,WORKFLOW_SENTINEL


class Client:
    def __init__(self):self.calls=0;self.last_execution={}
    def decide(self,prompt):
        self.calls+=1
        self.last_execution={'actual_model':MODEL,'model':MODEL,'cost':0,'transport':'opencode-cli-serve'}
        if self.calls==1:return {'route':'reply','scope':'general_static','reply':'4','ack':'','reply_format':'keep'}
        return {'route':'workflow','scope':'needs_tools','reply':'','ack':'Проверю актуальный прогноз.','reply_format':'keep'}


class RecoveryProbe(unittest.TestCase):
    def test_fresh_synthetic_provenance_routes_and_caches_without_real_workflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=verify(Path(temporary),Client())
            self.assertTrue(result['passed'])
            self.assertEqual(result['model_calls'],2)
            self.assertEqual(result['workflow_stub_calls'],1)
            self.assertEqual(result['financial_operations'],0)
            self.assertEqual(result['cases'][1]['answer'],WORKFLOW_SENTINEL)
            with self.assertRaisesRegex(RuntimeError,'fresh_database'):verify(Path(temporary),Client())

    def test_wrong_actual_provider_cannot_pass_only_because_reply_looks_right(self):
        class WrongProvider(Client):
            def decide(self,prompt):
                result=super().decide(prompt)
                self.last_execution['actual_model']='other/provider'
                return result
        with tempfile.TemporaryDirectory() as temporary:
            result=verify(Path(temporary),WrongProvider())
            self.assertFalse(result['passed'])
            self.assertTrue(all(not row['criteria']['exact_actual_model'] for row in result['cases']))


if __name__=='__main__':unittest.main()
