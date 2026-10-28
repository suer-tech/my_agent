"""Strict digital Alfa schedule layout; header-checked columns, every row validated."""
from datetime import datetime
import re
import pdfplumber
from finance.core import money,rub

ROW=re.compile(r'^\s*(\d{1,4})\s+(\d{2}\.\d{2}\.\d{4})\s+(.+)$')
AMOUNT=re.compile(r'(?<![\d.])-?\d[\d \u00a0\u202f]*[.,]\d{2}')


def extract(path):
    with pdfplumber.open(path) as pdf:
        texts=[page.extract_text() or '' for page in pdf.pages]
    flat=' '.join(' '.join(texts).casefold().split())
    required=('сумма задолженности','основному долгу','сумма погашения','основного долга','процентов','комиссия','общая сумма')
    if not ('альфа' in flat or 'alfa-bank' in flat) or not all(s in flat for s in required):return None
    rows=[];numbers=[]
    for page,text in enumerate(texts,1):
        for line in text.splitlines():
            match=ROW.match(line)
            if not match:continue
            values=AMOUNT.findall(match[3])
            if len(values)!=5:raise ValueError('Строка банковского графика содержит неоднозначное число денежных колонок; оригинал сохранён.')
            balance,principal,interest,fees,payment=[money(v.replace('\u202f',' ')) for v in values]
            if principal+interest+fees!=payment:raise ValueError('Банковская строка не прошла сверку основного долга, процентов и комиссии')
            number=int(match[1]);numbers.append(number)
            rows.append({'date':datetime.strptime(match[2],'%d.%m.%Y').date().isoformat(),'payment':rub(payment),'principal':rub(principal),'interest':rub(interest),'fees':rub(fees),'balance':None,'balance_before':rub(balance),'page':page,'row_ref':f'page:{page}:payment:{number}'})
    if not rows:return None
    if numbers!=list(range(1,len(numbers)+1)):raise ValueError('Нумерация банковского графика неполная или повторяется: нельзя подтвердить полноту строк')
    for before,after in zip(rows,rows[1:]):
        if money(before['balance_before'])-money(before['principal'])!=money(after['balance_before']):raise ValueError('Остаток основного долга не сошёлся между строками графика')
    if money(rows[-1]['balance_before'])!=money(rows[-1]['principal']):raise ValueError('Последняя строка не закрывает основной долг: нужен полный график')
    declared=re.search(r'срок кредита\s+(\d+)\s+месяц',flat)
    if declared and int(declared[1])!=len(rows):raise ValueError('Число строк графика не совпадает со сроком, указанным в PDF')
    return {'kind':'loan_schedule','bank':'Альфа','currency':'RUB','rows':rows,'receipt':None,'questions':[],
            'pages_covered':list(range(1,len(texts)+1)),'candidate_row_count':len(rows),
            'parser':'alfa-digital-v1','page_texts':[{'page':i+1,'text':t} for i,t in enumerate(texts)]}
