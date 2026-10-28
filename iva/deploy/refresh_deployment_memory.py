"""Correct outdated deployment facts, retaining every previous card in a backup."""
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[1]
V=ROOT/'data/iva-pilot/app/vault'
B=ROOT/'data/memory-before-native-refresh'
B.mkdir(mode=0o700,exist_ok=False)
names=['CORE.md','cards/notes/предпочтения-и-сведения-владельца.md',
       'cards/projects/финансовый-инструмент-sqlite.md',
       'library/owner-confirmed-profile-2026-10-19.md']
for name in names:
    dst=B/name;dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(V/name,dst)
core=(V/'CORE.md').read_text()
core=core.replace('Сейчас Windows, VPS позже.', 'Рабочая Ива находится на Ubuntu VPS в /opt/iva; Windows — рабочая копия разработки, не источник актуальных финансов.')
core=core.replace('[[library/owner-confirmed-profile-2026-10-19]]',
                  '[Исходный профиль от 19 октября](library/owner-confirmed-profile-2026-10-19.md). Текущее состояние: [развёртывание](cards/projects/развёртывание-ивы.md)')
core=core.replace('Для естественного голоса выбран Cartesia Tatiana в пилоте, оценка владельца ещё не получена.',
                  'Озвучивание Cartesia работает на VPS; владелец подтвердил, что голос устраивает. Формат ответа переключается только по его просьбе.')
core=core.replace('Механизмы hh.ru/тендеров есть у владельца, код/доступы не предоставлены; интеграции не считать подключёнными.',
                  'Поиск тендеров ЕИС подключён через tender_agent, по запросу. Профиль участия хранится в тендерной базе. Интеграция hh.ru не подтверждена.')
core=core.replace('инструмент family_finance/finance и SQLite','субагент finance_agent и финансовое ядро SQLite')
core=core.replace('Telegram подключён к Ива через локальный адаптер одного владельца.',
                  'Telegram подключён к Ива через серверный адаптер одного владельца. Календарь и напоминания доступны через personal_calendar.')
(V/'CORE.md').write_text(core)
card=V/'cards/projects/развёртывание-ивы.md'
card.write_text('''---
type: "project"
description: "Актуальное развёртывание Ивы и подключённые инструменты."
tags: ["ива","развёртывание"]
status: "active"
created: "2026-10-20"
truth_date: "2026-10-20"
source: "Поручение владельца и проверки рабочего сервера 20 октября 2026"
---
# Развёртывание Ивы

Рабочая Ива выполняется на Ubuntu VPS, приложение /opt/iva. Актуальная финансовая база — /opt/iva/data/local-finance.sqlite3. Windows-копия базы является старым снимком.
Основная модель — gpt-6-luna low через встроенное подключение подписки ChatGPT. Финансы и тендеры делегируются gpt-6.1-sol medium через Codex CLI.
Работают Telegram-бот, Cartesia, finance_agent, tender_agent, personal_calendar и долговременная память. Личные чаты Telegram и почта не подключены. Поиск тендеров только по запросу; новые заявки не отправляются.
Текущие суммы, ставки, число долгов и исполнение платежей брать из финансового реестра, а не из старых карточек. Предпочтения по тендерам читать через tender_agent.
Ранее записанные «VPS позже», «тендеры не подключены», «шесть долгов» описывают прошлое состояние и не являются актуальной конфигурацией.
Для чтения файлов используй точный vault-относительный путь с расширением .md; wiki-ссылка без расширения не является именем файла.
''')
for name in names[1:]:
    p=V/name
    text=p.read_text()
    note='\n## Актуализация 20 октября 2026\n\nТекст ниже/выше описывает состояние на дату исходной записи. VPS и тендерный инструмент уже подключены; текущая конфигурация: [развёртывание Ивы](cards/projects/развёртывание-ивы.md). Для read_file используй путь `cards/projects/развёртывание-ивы.md`. Актуальные финансовые сведения проверяй через finance_agent, не по историческим числам этой карточки.\n'
    p.write_text(text+note)
print('deployment_memory_refreshed_with_backup')
