"""Synthetic fixtures only: no user financial or bank data."""
from pathlib import Path
import pymupdf
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]
folder=ROOT/'data/attachment-fixtures';folder.mkdir(parents=True,exist_ok=True)
font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',32)
image=Image.new('RGB',(900,1100),'white');draw=ImageDraw.Draw(image)
text='ДЕМО МАГАЗИН\nСИНТЕТИЧЕСКИЙ ЧЕК\nДата покупки 18.10.2026\n\nХлеб              100,00\nМолоко            200,00\n\nИТОГ              300,00 RUB\nНАЛИЧНЫМИ        1000,00\nСДАЧА             700,00\n\nТЕСТ, НЕ РЕАЛЬНАЯ ПОКУПКА'
draw.multiline_text((45,45),text,font=font,fill='black',spacing=20)
image.save(folder/'receipt.png')
pdf=pymupdf.open()
pages=[['20.10.2026   5900.12   5000.00   900.12   0.00   10000.00','23.11.2026   5900.13   5000.00   900.13   0.00    5000.00'],['21.12.2026   5100.00   5000.00   100.00   0.00       0.00']]
for index,rows in enumerate(pages):
    temp=pymupdf.open();p=temp.new_page(width=850,height=600)
    p.insert_text((35,45),'ALFA BANK DEMO - PAYMENT SCHEDULE (SYNTHETIC)',fontsize=17)
    p.insert_text((35,85),'Currency: RUB. Page '+str(index+1)+' of 2. Not a real contract.',fontsize=12)
    p.insert_text((35,135),'Date       Payment Principal Interest Fees Remaining balance',fontname='cour',fontsize=12)
    for n,row in enumerate(rows):p.insert_text((35,180+n*40),row,fontname='cour',fontsize=13)
    target=pdf.new_page(width=850,height=600)
    if index==1:target.insert_image(target.rect,stream=p.get_pixmap(matrix=pymupdf.Matrix(1.5,1.5),alpha=False).tobytes('png'))
    else:pdf.delete_page(len(pdf)-1);pdf.insert_pdf(temp)
    temp.close()
pdf.save(folder/'bank-schedule.pdf')
for index,page in enumerate(pdf):page.get_pixmap(matrix=pymupdf.Matrix(1,1),alpha=False).save(folder/f'bank-page-{index+1}.png')
pdf.close()
for encoding in ('utf-8','utf-16','cp1251'):(folder/f'list-{encoding}.txt').write_text('Планируемые ежемесячные траты\nСвязь: 100.00 RUB\nДата пока неизвестна',encoding=encoding)
print('Synthetic receipt, two-page PDF (second page scanned), and three text encodings created.')
