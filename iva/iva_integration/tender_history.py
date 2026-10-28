"""Selected-notice historical research: evidence gates and reproducible arithmetic.

Uses the existing durable research worker; never submits a bid or writes finance.
"""
from datetime import date,datetime,timedelta
from decimal import Decimal,ROUND_HALF_UP
import json
import re
import statistics

from iva_integration.tender_research import obj,STR,STRINGS,model,dumps,now

GATES=('experience','own_registry','component_registry','staff_minimum','fsb_license','fstek_license','vendor_rights','on_site')
ACCESS=obj({k:{'enum':['not_required','required','unknown']} for k in GATES})
NULLSTR={'type':['string','null']}
MONEY={'type':['number','null'],'minimum':0}
COUNT={'type':['integer','null'],'minimum':0}
MEASURES=('nmc_rub','winner_price_rub','contract_price_rub','submitted','first_admitted','price_participants','final_admitted')
SOURCE=obj({'field':STR,'url':STR,'location':STR,'quote':STR})
BASELINE=obj({'number':STR,'title':STR,'product_type':STR,'scope':STR,
              'technologies':STRINGS,'nmc_rub':MONEY,'expected_contract_date':NULLSTR,
              'access':ACCESS,'financial_terms':STR,'execution_terms':STR,
              'queries':{'type':'array','items':{'type':'string','minLength':1,'maxLength':250},'minItems':1},
              'sources':{'type':'array','items':SOURCE},'unknowns':STRINGS})
ANALOG=obj({'group':{'enum':['strict','conditional','technical_control','excluded']},
            'similarity':{'type':'integer','minimum':0,'maximum':100},'reason':STR,
            'product_type':STR,'technologies':STRINGS,'differences':STRINGS,'access':ACCESS,
            'procedure':{'enum':['auction','quotes','weighted','single_supplier','other','unknown']},
            'procedure_completed':{'type':['boolean','null']},'award_date':NULLSTR,
            'contract_execution':{'enum':['completed','in_progress','terminated','unknown']},
            'price_is_total':{'type':['boolean','null']},
            'winner_name':NULLSTR,'contract_number':NULLSTR,'related_notice_number':NULLSTR,
            'participants':{'type':'array','items':obj({'name':STR,'bid_id':NULLSTR,'status':STR,'price_rub':MONEY,'source':SOURCE})},
            **{k:(MONEY if k.endswith('_rub') else COUNT) for k in MEASURES},
            'sources':{'type':'array','items':SOURCE},'facts':STRINGS,'unknowns':STRINGS})

def policy(job):
    from iva_integration.tender_skills import policy as skill
    return skill('history')+'\nЗадача: '+job['task']+'\nПрофиль: '+job['profile']+'\nПаспорт выбранного тендера: '+(job.get('baseline') or 'пока составляется')

def baseline(job,resources,extracts,cli,texts=()):
    prompt=(policy(job)+'\nСоставь паспорт ВЫБРАННОГО, а не исторического тендера. '
            'Неизвестные требования обозначай unknown. Дата ISO YYYY-MM-DD либо null. '
            'Сформируй запросы к завершённым закупкам по типу продукта/стеку и общей разработке ПО, '
            'не ограничивайся отраслью заказчика. Для sources копируй короткие точные цитаты с URL и пунктом. '
            'Не придумывай смету и квалификацию владельца.\n'+dumps({'resources':resources,'extracts':extracts}))
    value=model(cli,prompt,BASELINE)
    value['sources']=[s for s in value['sources'] if verified_source(s,resources,texts)]
    for gate in GATES:
        if value['access'][gate]!='unknown' and not any(s['field']==gate for s in value['sources']):
            value['access'][gate]='unknown';value['unknowns'].append('Нет первичного подтверждения условия выбранной закупки: '+gate)
    if value['nmc_rub'] is not None and not any(s['field']=='nmc_rub' and quote_has_number(s['quote'],value['nmc_rub'],money=True) for s in value['sources']):
        value['nmc_rub']=None;value['unknowns'].append('НМЦК выбранной закупки не подтверждена цитатой')
    if value['expected_contract_date'] and not any(s['field']=='expected_contract_date' for s in value['sources']):
        value['expected_contract_date']=None
    value['target_url']=job['target_url']
    value['reference_date']=value['expected_contract_date'] or job['created'][:10]
    value['reference_date_basis']='expected_contract' if value['expected_contract_date'] else 'analysis_start_assumption'
    try:date.fromisoformat(value['reference_date'])
    except ValueError:value['reference_date']=job['created'][:10];value['unknowns'].append('Дата предполагаемого контракта не подтверждена; ориентир — дата начала анализа')
    if any(r['state']!='read' for r in resources):value['unknowns'].append('Часть исходных документов выбранного тендера не прочитана полностью')
    roles={e['document_role'] for e in extracts}
    if not {'specification','contract'}.issubset(roles):value['unknowns'].append('Не подтверждены полное ТЗ и контракт выбранного тендера')
    return value

def query_window(job,target,horizon,previous=0):
    """Nonoverlapping publication windows; actual award recency is checked later."""
    asof=min(date.fromisoformat(job['created'][:10]),date.fromisoformat(target['reference_date']))
    start=asof-timedelta(days=horizon)
    end=asof-timedelta(days=previous+1) if previous else asof
    queries=list(dict.fromkeys(target['queries']+['разработка программного обеспечения','разработка приложения','создание программного обеспечения']))
    nmc=target.get('nmc_rub')
    options={'published_from':start.strftime('%d.%m.%Y'),'published_to':end.strftime('%d.%m.%Y')}
    if nmc and nmc>0:options.update(price_min=round(nmc/4,2),price_max=round(nmc*4,2))
    return [(f'{q} [история {previous}–{horizon} дн.]',q,options) for q in queries]

def verified_source(source,resources,texts):
    if not source.get('quote') or not any(r['url']==source.get('url') for r in resources):return False
    normalize=lambda s:re.sub(r'\s+',' ',s).strip()
    quote=normalize(source['quote'])
    return any(quote in normalize(t) for url,t in texts if url==source['url'])

def quote_has_number(quote,value,money=False):
    expected=Decimal(str(value))
    for match in re.finditer(r'\d[\d \u00a0]*(?:[.,]\d+)?',quote):
        token=match[0]
        try:
            if Decimal(re.sub(r'[ \u00a0]','',token).replace(',','.'))==expected:
                if not money:return True
                after=quote[match.end():]
                before=quote[max(0,match.start()-70):match.start()]
                if re.match(r'\s*(?:₽|руб|RUB|российск\w*\s+руб)',after,re.I) or re.search(r'(?:цен[аыу]|НМЦ[КД])[^\d]{0,50}$',before,re.I):return True
        except Exception:pass
    return False

def verify(value,job,target,resources,texts):
    """A model cannot upgrade unknowns into numeric facts or comparable prices."""
    v=json.loads(dumps(value));gaps=v['unknowns']
    sources=[s for s in v['sources'] if verified_source(s,resources,texts)]
    if len(sources)!=len(v['sources']):gaps.append('Часть ссылок/цитат не подтверждена сохранёнными оригиналами')
    v['sources']=sources
    v['participants']=[p for p in v['participants'] if verified_source(p['source'],resources,texts)]
    for participant in v['participants']:
        if participant['price_rub'] is not None and not quote_has_number(participant['source']['quote'],participant['price_rub'],money=True):participant['price_rub']=None
    for field in ('winner_name','contract_number','related_notice_number'):
        if not any(s['field']==field for s in sources):v[field]=None
    for field,unknown in [('procedure','unknown'),('procedure_completed',None),('contract_execution','unknown'),('price_is_total',None)]:
        if not any(s['field']==field for s in sources):
            v[field]=unknown;gaps.append('Нет первичного подтверждения: '+field)
    for field in MEASURES:
        if v[field] is not None and not any(s['field']==field and quote_has_number(s['quote'],v[field],money=field.endswith('_rub')) for s in sources):
            v[field]=None;gaps.append('Нет проверяемого первоисточника числа: '+field)
    for gate in GATES:
        if v['access'][gate]!='unknown' and not any(s['field']==gate for s in sources):
            v['access'][gate]='unknown';gaps.append('Не подтверждено условие: '+gate)
    if not any(s['field']=='award_date' for s in sources):v['award_date']=None
    if v['award_date']:
        try:
            award=date.fromisoformat(v['award_date'])
            if award>min(date.fromisoformat(job['created'][:10]),date.fromisoformat(target['reference_date'])):raise ValueError()
            v['age_days']=(date.fromisoformat(target['reference_date'])-award).days
        except ValueError:v['award_date']=None;gaps.append('Некорректная/будущая дата итогов');v['age_days']=None
    else:v['age_days']=None
    if v['submitted'] is not None:
        if any(v[k] is not None and v[k]>v['submitted'] for k in ('first_admitted','price_participants','final_admitted')):
            gaps.append('Противоречивые количества заявок/допущенных')
    if any(r['state']!='read' for r in resources):gaps.append('Комплект аналогичной закупки прочитан не полностью')
    roles={r.get('document_role') for r in value.get('_extracts',[])}
    # Both primary and technical-control rows remain visible; only a strict
    # cohort may feed descriptive pricing statistics.
    if v['procedure']=='single_supplier':v['group']='excluded';gaps.append('Единственный поставщик не измеряет конкуренцию')
    elif v['procedure']=='weighted' or v['contract_execution']=='terminated':v['group']='technical_control'
    elif v['group']=='strict':
        access_equal=all(v['access'][k]==target['access'][k] and v['access'][k]!='unknown' for k in GATES)
        if (not access_equal or target['unknowns'] or gaps or v['procedure'] not in ('auction','quotes')
            or v['procedure_completed'] is not True or not v['award_date'] or v['price_is_total'] is not True
            or not {'specification','contract','admission','protocol'}.issubset(roles)):
            v['group']='conditional';gaps.append('Полная сопоставимость и первичные документы не подтверждены')
    v['winning_discount_percent']=discount(v['nmc_rub'],v['winner_price_rub']) if v['price_is_total'] else None
    v['contract_discount_percent']=discount(v['nmc_rub'],v['contract_price_rub']) if v['price_is_total'] else None
    v['unknowns']=list(dict.fromkeys(gaps));v.pop('_extracts',None)
    return v

def discount(initial,price):
    if initial is None or price is None or initial<=0:return None
    return float(((Decimal(str(initial))-Decimal(str(price)))*100/Decimal(str(initial))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))

def review(job,notice,resources,extracts,texts,cli):
    target=json.loads(job['baseline'])
    value=model(cli,policy(job)+'\nСопоставь этот исторический аналог с выбранным тендером. '
                'sources.field: имя числового поля, одного access-поля, award_date или другого факта. '
                'Для КАЖДОГО числа и каждого известного барьера дай точную цитату из ОРИГИНАЛА '
                '(не из пересказа), URL, пункт/страницу. Если точного источника нет — null/unknown. '
                'Подано, первые части, торговалось и финально допущено — разные поля; не выводи одно из другого. '
                'Не смешивай предложение победителя, подписанный договор и оплату. '
                'Вмешанные полевые/аппаратные/очные работы при чистой удалённой цели исключают основную группу. '
                'strict означает подтверждённое равенство существенных барьеров; single supplier и weighted отдельно.\n'
                +dumps({'card':json.loads(notice['card']),'extracts':extracts,'originals':texts}),ANALOG)
    value['_extracts']=extracts
    result=verify(value,job,target,resources,texts)
    result['notice_number']=notice['number']
    return {'decision':'rejected' if result['group']=='excluded' else 'conditional',
            'score':result['similarity'],'reason':result['reason'],
            'admission_status':'uncertain','execution_status':'uncertain','platform_fee_verified':False,
            'blockers':result['unknowns'],'facts':result['facts'],'next_action':'Обсуждение цены после сопоставления и расчёта затрат выбранной закупки',
            'history':result,'document_review':'partial' if result['unknowns'] else 'complete',
            'documents':resources,'status_rechecked_at':now()}

def rows(research,identity):
    return [json.loads(r[0])['history'] for r in research.db.execute("SELECT review FROM research_notices WHERE job=? AND review IS NOT NULL",(identity,)) if 'history' in json.loads(r[0])]

def maybe_expand(research,job):
    plan=json.loads(job.get('history_plan') or '{}');horizon=plan.get('horizon',90)
    # This is a minimum for statistical usefulness, never a cap on discoveries
    # or a reason to stop any activated query before its last page.
    if sum(r['group']=='strict' for r in rows(research,job['id']))>=3 or horizon>=365:return False
    next_horizon=180 if horizon==90 else 365
    add_queries(research,job,json.loads(job['baseline']),next_horizon,horizon)
    return True

def add_queries(research,job,target,horizon,previous=0):
    values=query_window(job,target,horizon,previous)
    with research.db:
        research.db.executemany('INSERT OR IGNORE INTO research_queries(job,query,search_text,mode,options) VALUES(?,?,?,\'history\',?)',
                               [(job['id'],key,text,dumps(options)) for key,text,options in values])
        plan=json.loads(job.get('history_plan') or '{}');plan['horizon']=horizon
        plan.setdefault('windows',[]).append({'days_from':previous,'days_to':horizon,'queries':len(values),'filters':values[0][2]})
        research.db.execute("UPDATE research_jobs SET baseline=?,history_plan=?,stage='discovery',updated=? WHERE id=?",(dumps(target),dumps(plan),now(),job['id']))

def summary(research,job):
    target=json.loads(job['baseline']);values=rows(research,job['id'])
    cohorts={}
    counted=set()
    for r in sorted(values,key=lambda r:r.get('award_date') or '',reverse=True):
        price=r['contract_price_rub'] if r['contract_price_rub'] is not None else r['winner_price_rub']
        reduction=discount(r['nmc_rub'],price)
        if r['group']=='strict' and reduction is not None:
            identity=r.get('contract_number') or r.get('related_notice_number') or r.get('notice_number')
            if identity and identity in counted:continue
            if identity:counted.add(identity)
            key=r['procedure']+' / '+r['product_type']+' / '+('до 90 дней' if r.get('age_days') is not None and r['age_days']<=90 else 'старше 90 дней')
            cohorts.setdefault(key,[]).append(reduction)
    stats={k:{'n':len(v),'min_percent':min(v),'median_percent':statistics.median(v),'max_percent':max(v)} for k,v in cohorts.items() if len(v)>=3}
    scenarios=[]
    if target.get('nmc_rub'):
        for key,stat in stats.items():
            scenarios.append({'cohort':key,'discount_percent':stat['median_percent'],
                              'reference_price_rub':round(target['nmc_rub']*(1-stat['median_percent']/100),2),
                              'meaning':'Описательный пересчёт исторической медианы, не рекомендуемая заявка и не вероятность победы'})
    return {'target':target,'cohorts':stats,'reference_scenarios':scenarios,
            'cost_floor_rub':None,'cost_floor_status':'Не установлен: нужны подтверждённые затраты, налог, поддержка, комиссия и резерв; обеспечение учитывается отдельно как ликвидность.',
            'warning':'Малая/неоднородная группа не даёт надёжного прогноза. Завершённые торги не доказывают успешное исполнение и прибыль.'}

def render(research,job):
    target=json.loads(job['baseline']);s=summary(research,job);details=[]
    groups={'strict':'сопоставимый','conditional':'условный','technical_control':'контрольный','excluded':'исключён'}
    methods={'auction':'аукцион','quotes':'запрос котировок','weighted':'конкурс с баллами','single_supplier':'единственный поставщик','other':'иная','unknown':'неизвестно'}
    lines=[
        '# История аналогичных торгов и ориентиры цены',
        f"Выбранная закупка: [{target['number']}]({job['target_url']}) — {target['title']}",
        'Сроковой ориентир: '+target['reference_date']+(' (дата анализа: дата контракта не подтверждена)' if target.get('reference_date_basis')=='analysis_start_assumption' else '')+'; свежесть — по дате итогов, а не обновления карточки.',
        'Стек: '+', '.join(target['technologies']),
        'Периоды поиска: '+', '.join(f"{w['days_from']}–{w['days_to']} дней" for w in json.loads(job.get('history_plan') or '{}').get('windows',[])),
        '', '| Аналог / итоги | Группа / процедура | Подано / первые / торговалось / финал | НМЦК / победитель / договор | Снижение по договору | Исполнение |',
        '| --- | --- | --- | --- | --- | --- |']
    for row in research.db.execute('SELECT number,card,review FROM research_notices WHERE job=? AND review IS NOT NULL',(job['id'],)):
        rev=json.loads(row['review']);h=rev.get('history')
        if not h:continue
        card=json.loads(row['card']);val=lambda k:'неизвестно' if h.get(k) is None else str(h[k])
        reduction='неизвестно' if h.get('contract_discount_percent') is None else val('contract_discount_percent')+'%'
        lines.append(f"| [{row['number']}]({card['url']}) / {val('award_date')} | {groups[h['group']]} / {methods[h['procedure']]} | "+' / '.join(val(k) for k in ('submitted','first_admitted','price_participants','final_admitted'))+' | '+' / '.join(val(k) for k in ('nmc_rub','winner_price_rub','contract_price_rub'))+f" | {reduction} | {h['contract_execution']} |")
        details.extend(['',f"## Аналог {row['number']}",h['reason'],'Отличия: '+'; '.join(h['differences'])])
        if h.get('winner_name'):details.append('Победитель: '+h['winner_name'])
        details.extend('- Пробел: '+v for v in h['unknowns'])
        details.extend(f"- {v['field']}: [{v['location']}]({v['url']}) — {v['quote']}" for v in h['sources'])
        details.extend(f"- Участник {p['name']}: {p['status']}; цена {p['price_rub'] if p['price_rub'] is not None else 'неизвестна'}; [источник]({p['source']['url']})" for p in h.get('participants',[]))
    lines+=details
    lines.extend(['','## Цена выбранной закупки'])
    if not s['cohorts']:lines.append('Подтверждённых независимых аналогов в однородной группе недостаточно для медианы и ценового прогноза.')
    for key,stat in s['cohorts'].items():lines.append(f"{key}: n={stat['n']}, снижение {stat['min_percent']}–{stat['max_percent']}%, медиана {stat['median_percent']}%.")
    for item in s['reference_scenarios']:lines.append(f"Исторический пересчёт для {item['cohort']}: {item['reference_price_rub']} ₽. {item['meaning']}.")
    lines.extend([s['cost_floor_status'],s['warning'],'Все значения выше — факты конкретных процедур с источниками. При отсутствии пригодной группы статистика и рекомендуемая цена не выдумываются.'])
    return '\n'.join(lines)
