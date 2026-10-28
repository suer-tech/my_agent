"""Keep browser availability grounded in the actual external-tool manifest."""
import json
import re

def browser_capabilities(tools):
    if 'web_browser' not in tools:return ''
    return '\nCURRENT EXECUTABLE BROWSER: '+json.dumps({'tool':'web_browser','connected':True,'actions':['open','search','read','scroll','close'],'native_cli_tools_are_irrelevant':True})+'\nRoute web_browser in calls[] to browse. Do not deny access because your native CLI has no browser tool. Tool results and the current manifest override stale historical claims.'

def browser_probe_used(messages):
    start=max((i for i,m in enumerate(messages) if m.get('role')=='user'),default=0)
    return any(str(c.get('id','')).startswith('call_browser_capability_probe_') for m in messages[start:] for c in m.get('tool_calls',[]))

def repair_browser_denial(value,tools,messages):
    text=value.get('text','').lower()
    denial=re.search(r'нет\b.{0,80}(?:web_browser|браузер)|(?:web_browser|браузер).{0,80}(?:недоступ|не подключ|отсутств|нет\b)|не.{0,20}(?:доступ|подключ).{0,40}(?:web_browser|браузер)',text,re.S)
    if 'web_browser' not in tools or value.get('calls') or not denial:return value,False
    if browser_probe_used(messages):
        return {'text':'Браузер подключён, но этот запрос завершить не удалось. Актуальные цены не подтверждены.','calls':[]},False
    return {'text':'Проверю подключённый браузер.','calls':[{'name':'web_browser','arguments_json':'{"action":"read"}'}]},True
