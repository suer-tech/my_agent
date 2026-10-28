"""Actionable bounded finance failures and a guard against identical blind retries."""
import json

def tool_failure(error):
    permission=isinstance(error,PermissionError)
    return {'ok':False,'retryable':False,'error':{'code':'FINANCE_AUTHORIZATION_REQUIRED' if permission else 'FINANCE_ARGUMENTS_INVALID','message':'Для этой операции нет подтверждённого сервером разбора текущего сообщения. Нужно уточнить неясные данные; повтор того же вызова ничего не исправит.' if permission else 'Аргументы не соответствуют финансовому контракту. Прочитайте contract и исправьте поля; тот же вызов повторять нельзя.'}}

def signature(call):
    args=json.loads(call['arguments_json'])
    if 'payload_json' in args:args['payload_json']=json.loads(args['payload_json'])
    return json.dumps(args,sort_keys=True,ensure_ascii=False)

def stop_failed_finance_retries(value,messages):
    start=max((i for i,m in enumerate(messages) if m.get('role')=='user'),default=0)
    calls={};failed={}
    for m in messages[start:]:
        for c in m.get('tool_calls',[]):
            f=c.get('function',{})
            if 'finance' in f.get('name',''):
                try:calls[c['id']]=signature({'arguments_json':f['arguments']})
                except (ValueError,KeyError,TypeError):pass
        if m.get('role')=='tool' and m.get('tool_call_id') in calls:
            content=m.get('content','')
            if isinstance(content,list):content=''.join(p.get('text','') for p in content)
            try:result=json.loads(content)
            except (ValueError,TypeError):continue
            if isinstance(result,dict) and isinstance(result.get('content'),list):
                try:result=json.loads(''.join(p.get('text','') for p in result['content']))
                except (ValueError,TypeError):continue
            if isinstance(result,dict) and result.get('retryable') is False:
                failed[calls[m['tool_call_id']]]=result['error']['message']
    kept=[];reason=None
    for call in value['calls']:
        blocked=failed.get(signature(call)) if 'finance' in call['name'] else None
        if blocked:reason=blocked
        else:kept.append(call)
    return {**value,'calls':kept,'text':reason if reason and not kept else value['text']}
