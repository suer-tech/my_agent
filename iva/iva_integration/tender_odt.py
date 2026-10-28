"""Visible ODT body text, without package metadata, XML tags or style payloads."""
import xml.etree.ElementTree as ET

OFFICE='urn:oasis:names:tc:opendocument:xmlns:office:1.0'
TEXT='urn:oasis:names:tc:opendocument:xmlns:text:1.0'
TABLE='urn:oasis:names:tc:opendocument:xmlns:table:1.0'
DRAW='urn:oasis:names:tc:opendocument:xmlns:drawing:1.0'
STYLE='urn:oasis:names:tc:opendocument:xmlns:style:1.0'
MAX_TEXT=32_000_000


def body_text(xml,styles_xml=None):
    if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:
        raise ValueError('XML-сущности запрещены')
    root=ET.fromstring(xml)
    style_roots=[]
    if styles_xml:
        if b'<!DOCTYPE' in styles_xml or b'<!ENTITY' in styles_xml:raise ValueError('XML-сущности запрещены')
        style_roots.append(ET.fromstring(styles_xml))
    style_roots.append(root)
    list_styles={}
    for tree in style_roots:
        for definition in tree.iter(f'{{{TEXT}}}list-style'):
            list_styles[definition.get(f'{{{STYLE}}}name','')]={int(level.get(f'{{{TEXT}}}level','1')):level for level in definition}
    continuations={};last_lists={}
    body=root.find(f'{{{OFFICE}}}body/{{{OFFICE}}}text')
    if body is None:
        raise ValueError('OpenDocument не является текстовым ODT')
    out=[];size=0;paragraph=0

    def emit(value):
        nonlocal size
        size+=len(value)
        if size>MAX_TEXT:raise ValueError('Текст ODT превышает 32 МБ')
        out.append(value)

    def inline(node):
        if node.tag==f'{{{OFFICE}}}binary-data':return ''
        if node.tag==f'{{{TEXT}}}s':
            count=int(node.get(f'{{{TEXT}}}c','1'))
            if not 1<=count<=10000:raise ValueError('Недопустимый повтор пробела ODT')
            return ' '*count
        if node.tag==f'{{{TEXT}}}tab':return '\t'
        if node.tag==f'{{{TEXT}}}line-break':return '\n'
        value=node.text or ''
        for child in node:
            value+=inline(child)+(child.tail or '')
            if len(value)>MAX_TEXT:raise ValueError('Текст ODT превышает 32 МБ')
        return value

    def num(value,kind):
        if kind=='1':return str(value)
        if kind in ('a','A') and value>0:
            letters=''
            while value:value,digit=divmod(value-1,26);letters=chr(97+digit)+letters
            return letters.upper() if kind=='A' else letters
        if kind in ('i','I') and 0<value<4000:
            roman=''
            for number,symbol in [(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),(50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]:
                while value>=number:roman+=symbol;value-=number
            return roman.lower() if kind=='i' else roman
        return None

    def walk(node,parents=(),inherited_style=''):
        nonlocal paragraph
        if node.tag in (f'{{{OFFICE}}}binary-data',f'{{{TEXT}}}tracked-changes'):
            return
        if node.tag in (f'{{{TEXT}}}p',f'{{{TEXT}}}h'):
            value=inline(node)
            if value.strip():
                paragraph+=1;emit(f'\n[ODT абзац {paragraph}] '+value+'\n')
            return
        if node.tag==f'{{{TABLE}}}table':emit('\n[ODT таблица]\n')
        if node.tag==f'{{{TABLE}}}table-row':
            emit('\n[ODT строка таблицы; повторений: '+node.get(f'{{{TABLE}}}number-rows-repeated','1')+']\n')
        if node.tag in (f'{{{TABLE}}}table-cell',f'{{{TABLE}}}covered-table-cell'):
            attrs={key.rsplit('}',1)[-1]:value for key,value in node.attrib.items()
                   if key.rsplit('}',1)[-1] in ('number-columns-repeated','number-columns-spanned','number-rows-spanned')}
            emit('\n[ODT ячейка'+(' '+str(attrs) if attrs else '')+']\n')
        if node.tag==f'{{{TEXT}}}list':
            style=node.get(f'{{{TEXT}}}style-name',inherited_style)
            levels=list_styles.get(style,{})
            level=levels.get(len(parents)+1)
            counter=int(level.get(f'{{{TEXT}}}start-value','1')) if level is not None else 1
            previous=node.get(f'{{{TEXT}}}continue-list')
            if previous in continuations:counter=continuations[previous]+1
            elif node.get(f'{{{TEXT}}}continue-numbering')=='true' and (style,len(parents)) in last_lists:counter=last_lists[(style,len(parents))]+1
            emit('\n[ODT список]\n')
            for item in node:
                if item.tag!=f'{{{TEXT}}}list-item':walk(item,parents,style);continue
                counter=int(item.get(f'{{{TEXT}}}start-value',str(counter)))
                if level is not None and level.tag==f'{{{TEXT}}}list-level-style-number':
                    values=(*parents,counter);display=int(level.get(f'{{{TEXT}}}display-levels','1'))
                    parts=[num(v,levels.get(i+1,level).get(f'{{{STYLE}}}num-format','1')) for i,v in enumerate(values)]
                    if all(x is not None for x in parts):
                        label=level.get(f'{{{STYLE}}}num-prefix','')+'.'.join(parts[-max(1,display):])+level.get(f'{{{STYLE}}}num-suffix','')
                        emit('\n[ODT автоматический номер: '+label+']\n')
                walk(item,(*parents,counter),style)
                counter+=1
            continuations[node.get('{http://www.w3.org/XML/1998/namespace}id','')]=counter-1
            last_lists[(style,len(parents))]=counter-1
            return
        if node.tag==f'{{{TEXT}}}list-item':
            emit('\n[ODT элемент списка; start-value: '+node.get(f'{{{TEXT}}}start-value','не задан')+']\n')
        for child in node:walk(child,parents,inherited_style)

    walk(body)
    media=any(node.tag in (f'{{{DRAW}}}image',f'{{{DRAW}}}object',f'{{{DRAW}}}object-ole') for node in body.iter())
    return ''.join(out),media
