"""Opt-in contact extraction and transport: no live network or browser sessions."""
import hashlib
import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from iva_integration import demand_sources as sources, demand_network as network
from iva_integration.demand_content_quality import extract_contact_fragments, extract_html_body

SPEC = importlib.util.spec_from_file_location('contact_reader_service', Path(__file__).resolve().parents[1] / 'deploy/research_fetch_service.py')
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)
URL = 'https://official.example/contacts'
BODY = 'Our company implements software integrations for teams handling court documents.'


def contact_hash(fragments):
    return hashlib.sha256(json.dumps(fragments, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def evidence(*, body=BODY, fragments=None):
    result = {'ok': True, 'url': URL, 'canonical_url': URL, 'http_status': 200, 'status': 'ok',
              'text': body, 'title': 'Company', 'error_category': None,
              'content_hash': hashlib.sha256(body.encode()).hexdigest()}
    if fragments is not None:
        result.update(contact_fragments=fragments, contact_readable=bool(fragments), contact_fragments_hash=contact_hash(fragments))
    return result


class Response:
    def __init__(self, html='', status=200, **headers):
        self.status = status; self.headers = Message()
        self.headers['Content-Type'] = 'text/html; charset=utf-8'
        for key,value in headers.items(): self.headers[key.replace('_','-')] = value
        self.content = io.BytesIO(html.encode())
    def getheader(self,key,default=None): return self.headers.get(key,default)
    def read(self,size): return self.content.read(size)


class Connection:
    def __init__(self,response): self.response=response; self.sock=None; self.requested=None
    def request(self,*args,**kwargs): self.requested=(args,kwargs)
    def getresponse(self): return self.response
    def close(self): pass


class FakeSocket:
    def __init__(self,result): self.raw=json.dumps(result).encode()+b'\n'; self.sent=None
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def settimeout(self,value): pass
    def connect(self,path): pass
    def sendall(self,value): self.sent=value
    def recv(self,size): value=self.raw;self.raw=b'';return value


class ContactExtraction(unittest.TestCase):
    def extract(self, html):
        soup = BeautifulSoup(html,'html.parser'); before=str(soup)
        result=extract_contact_fragments(soup)
        self.assertEqual(str(soup),before)
        self.assertLessEqual(len(result),4)
        self.assertTrue(all(0<len(item)<=400 for item in result))
        return result

    def test_visible_footer_header_and_official_contact_text_without_domain_guessing(self):
        html='<main>'+BODY+'</main><footer>ООО КИТ <a href="mailto:info@arbitrkit.com">info@arbitrkit.com</a></footer>'
        soup=BeautifulSoup(html,'html.parser')
        self.assertNotIn('info@arbitrkit.com',extract_html_body(soup)[0])
        self.assertEqual(self.extract(html),['ООО КИТ info@arbitrkit.com'])
        self.assertEqual(self.extract('<header>Contact office@another.example</header>'),['Contact office@another.example'])
        self.assertEqual(self.extract('<section id="contacts">Email info@official.example</section>'),['Email info@official.example'])

    def test_hidden_scripts_comments_templates_and_css_do_not_leak_addresses(self):
        html='''<style>.private-contact {display:none} #secret {visibility:hidden}</style><footer>
            <!-- comment@hidden.example --><script>let email="script@hidden.example"</script>
            <style>.x {content:"style@hidden.example"}</style><template>template@hidden.example</template>
            <noscript>noscript@hidden.example</noscript><svg><text>svg@hidden.example</text></svg>
            <span hidden>attribute@hidden.example</span><div aria-hidden="TRUE">aria@hidden.example</div>
            <span style="display: none !important">display@hidden.example</span>
            <div style="opacity:0.0">opacity@hidden.example</div><div class="sr-only">screen@hidden.example</div>
            <span class="private-contact">css@hidden.example</span><div id="secret">id@hidden.example</div>
            <dialog>dialog@hidden.example</dialog><div inert>inert@hidden.example</div>
            <details><summary>More</summary>collapsed@hidden.example</details>
            <span>Public info@official.example</span></footer>'''
        result=self.extract(html)
        self.assertTrue(result)
        self.assertNotIn('hidden.example',' '.join(result))
        self.assertIn('info@official.example',' '.join(result))

    def test_hidden_parent_blocks_all_descendant_contact_scopes(self):
        self.assertEqual(self.extract('<div hidden><footer>secret@hidden.example</footer></div>'),[])
        self.assertEqual(self.extract('<style>footer .secret {display:none}</style><footer><div class="secret">secret@hidden.example</div></footer>'),[])

    def test_literal_mailto_only_never_query_js_or_decoding(self):
        self.assertEqual(self.extract('<a href="mailto:info@official.example">Написать нам</a>'),['mailto:info@official.example'])
        html='''<a href="mailto:info@official.example?cc=hidden@other.example">Написать</a>
            <a href="javascript:email('script@hidden.example')">Связаться</a>
            <a href="mailto:info%40official.example">Email</a>
            <a href="mailto:hidden@hidden.example" hidden>Write</a>
            <a href="mailto:empty@hidden.example"><img src="icon"></a>'''
        self.assertEqual(self.extract(html),[])
        self.assertEqual(self.extract('<a href="mailto:info@official.example?subject=hello">info@official.example</a>'),['info@official.example'])

    def test_bounds_dedup_and_literal_windows(self):
        visible=' '.join(('Text ' * 90)+f'office{i}@official.example '+('context ' * 20) for i in range(8))
        result=self.extract('<footer>'+visible+'</footer>')
        self.assertEqual(len(result),4)
        self.assertTrue(all(item in ' '.join(visible.split()) for item in result))
        self.assertEqual(len(self.extract('<header>info@official.example</header><footer>info@official.example</footer>')),1)


class ContactFetch(unittest.TestCase):
    def read(self, html, *, optin=None, status=200):
        connection=Connection(Response(html,status=status))
        with patch.object(network,'enabled',return_value=False), patch.object(sources,'_addresses',return_value=['8.8.8.8']), \
             patch.object(sources,'_connection',return_value=connection),patch.object(sources,'timestamp',return_value='2026-10-26T12:00:00+00:00'):
            result=sources.fetch_source(URL,**({} if optin is None else {'include_contact_fragments':optin}))
        return result,connection

    def test_default_and_explicit_false_are_identical_body_semantics(self):
        html='<title>Company</title><main>'+BODY+'</main><footer>info@arbitrkit.com</footer>'
        default,_=self.read(html);false,_=self.read(html,optin=False);optin,connection=self.read(html,optin=True)
        self.assertEqual(default,false)
        self.assertNotIn('contact_fragments',default)
        self.assertEqual({k:v for k,v in optin.items() if not k.startswith('contact_')},default)
        self.assertTrue(optin['contact_readable'])
        self.assertEqual(optin['contact_fragments'],['info@arbitrkit.com'])
        self.assertEqual(optin['contact_fragments_hash'],contact_hash(optin['contact_fragments']))
        self.assertNotIn('Cookie',connection.requested[1]['headers'])
        self.assertNotIn('Authorization',connection.requested[1]['headers'])

    def test_contact_only_is_explicitly_not_body_evidence(self):
        result,_=self.read('<title>Contacts</title><footer>Email info@official.example</footer>',optin=True)
        self.assertFalse(result['ok']);self.assertEqual(result['text'],'')
        self.assertIsNone(result['content_hash']);self.assertTrue(result['contact_readable'])
        self.assertEqual(result['http_status'],200)
        self.assertIn(result['error_category'],('insufficient_content','no_readable_text'))

    def test_denials_captcha_and_redirect_guards_cannot_supply_contact_evidence(self):
        for status,html in [(401,'<footer>auth@hidden.example</footer>'),(403,'<footer>denial@hidden.example</footer>'),
                            (429,'<footer>rate@hidden.example</footer>'),
                            (200,'<title>Just a moment...</title><main>Verify you are human before continuing.</main><footer>captcha@hidden.example</footer>')]:
            result,_=self.read(html,optin=True,status=status)
            self.assertFalse(result['ok']);self.assertNotIn('contact_fragments',result)
        first=Connection(Response(status=302,Location='http://127.0.0.1/private'))
        with patch.object(network,'enabled',return_value=False),patch.object(sources,'_addresses',return_value=['8.8.8.8']), \
             patch.object(sources,'_connection',return_value=first) as connect:
            result=sources.fetch_source(URL,include_contact_fragments=True)
        self.assertEqual(result['error_category'],'disallowed_url');self.assertEqual(connect.call_count,1)
        self.assertNotIn('contact_fragments',result)

    def test_non_boolean_optin_rejected_before_fetch(self):
        with patch.object(network,'fetch') as fetch:
            for value in ('true',1,None,{}):
                with self.assertRaises(ValueError):sources.fetch_source(URL,include_contact_fragments=value)
            fetch.assert_not_called()


class ContactRouting(unittest.TestCase):
    def test_403_retry_and_cached_route_preserve_optin(self):
        blocked={'ok':False,'url':URL,'canonical_url':URL,'http_status':403,'error_category':'access_denied'}
        success=evidence(fragments=['info@official.example'])
        with patch.object(network,'enabled',return_value=True),patch.object(network,'_entry',return_value=None), \
             patch.object(network,'_claim',return_value=True),patch.object(network,'_remember'), \
             patch.object(network,'vpn_fetch',return_value=success) as vpn:
            result=network.fetch(URL,Mock(return_value=blocked),include_contact_fragments=True)
            vpn.assert_called_once_with(URL,include_contact_fragments=True)
            self.assertEqual(result['contact_fragments'],success['contact_fragments'])
            self.assertEqual(result['network']['reason'],'http_403')
        prior={'status':'vpn_verified','expires_at':200,'reason':'http_403'}
        with patch.object(network,'enabled',return_value=True),patch.object(network,'time') as clock, \
             patch.object(network,'_entry',return_value=prior),patch.object(network,'vpn_fetch',return_value=success) as vpn:
            clock.time.return_value=100;direct=Mock()
            result=network.fetch(URL,direct,include_contact_fragments=True)
            direct.assert_not_called();vpn.assert_called_once_with(URL,include_contact_fragments=True)
            self.assertEqual(result['network']['reason'],'cached_http_403')

    def test_captcha_and_failed_probe_cooldown_do_not_bypass(self):
        for category,claim in (('access_challenge',True),('access_denied',False)):
            original={'ok':False,'url':URL,'canonical_url':URL,'http_status':403,'error_category':category}
            with patch.object(network,'enabled',return_value=True),patch.object(network,'_entry',return_value=None), \
                 patch.object(network,'_claim',return_value=claim),patch.object(network,'vpn_fetch') as vpn:
                result=network.fetch(URL,Mock(return_value=original),include_contact_fragments=True)
                vpn.assert_not_called();self.assertFalse(result['ok'])


class ContactRpc(unittest.TestCase):
    def setUp(self):
        unix=patch.object(network.socket,'AF_UNIX',getattr(network.socket,'AF_UNIX',1),create=True)
        unix.start();self.addCleanup(unix.stop)

    def test_default_and_false_wire_identical_true_explicit(self):
        for option in (None,False,True):
            payload=evidence(fragments=['info@official.example']) if option else evidence()
            client=FakeSocket(payload)
            with patch.object(network.socket,'socket',return_value=client):
                result=network.vpn_fetch(URL,**({} if option is None else {'include_contact_fragments':option}))
            self.assertEqual(result,payload)
            expected={'action':'fetch','url':URL}
            if option:expected['include_contact_fragments']=True
            self.assertEqual(json.loads(client.sent),expected)

    def test_service_strict_boolean_exact_keys_and_isolated_worker(self):
        for value in ('true',1,None,{},[]):
            with self.assertRaises(ValueError):service.validated_request(json.dumps({'action':'fetch','url':URL,'include_contact_fragments':value}))
        for extra in ({'headers':{'Cookie':'secret'}},{'method':'POST'},{'command':'echo nope'}):
            with self.assertRaises(ValueError):service.validated_request(json.dumps({'action':'fetch','url':URL,'include_contact_fragments':True,**extra}))
        for option in (False,True):
            completed=SimpleNamespace(returncode=0,stdout=json.dumps(evidence()).encode())
            with patch.object(service.subprocess,'run',return_value=completed) as execute:
                service.execute(json.dumps({'action':'fetch','url':URL,'include_contact_fragments':option}))
            expected={'action':'fetch','url':URL}
            if option:expected['include_contact_fragments']=True
            self.assertEqual(json.loads(execute.call_args.kwargs['input']),expected)
            self.assertIn('-I',execute.call_args.args[0]);self.assertEqual(execute.call_args.kwargs['timeout'],20)

    def test_contact_metadata_integrity_bounds_and_restriction_gate(self):
        valid=evidence(fragments=['info@official.example'])
        cases=[dict(valid,contact_fragments_hash='wrong'),dict(valid,contact_readable='yes'),
               dict(valid,contact_fragments=['x'*401]),dict(valid,contact_fragments=['x']*5),
               dict(valid,http_status=403,status='blocked',ok=False,error_category='access_denied'),
               dict(valid,canonical_url='http://127.0.0.1/private')]
        for value in cases:
            with patch.object(network.socket,'socket',return_value=FakeSocket(value)),self.assertRaises(ValueError):
                network.vpn_fetch(URL,include_contact_fragments=True)
        contact_only=dict(valid,ok=False,text='',content_hash=None,status='empty',error_category='insufficient_content')
        with patch.object(network.socket,'socket',return_value=FakeSocket(contact_only)):
            self.assertTrue(network.vpn_fetch(URL,include_contact_fragments=True)['contact_readable'])


if __name__=='__main__':unittest.main()
