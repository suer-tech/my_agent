# Lead Flow: исследование покупателей OCR и подготовка писем

Фактическое состояние на 26 октября 2026 года. Этот документ дополняет
`LEAD-RESEARCH-CONTROL.ru.md` и заменяет прежние заметки «только локальный прототип».

## Что установлено и что ещё выключено

Исследователь установлен на действующем VPS Ивы `5.63.152.247` в `/opt/lead-flow`.
Ива получила нативный инструмент `lead_research`: статус, сохранённые кандидаты
и черновики, ручной запуск, пауза и возобновление отдельного расписания.
Установка control включала сборку native manifest, безопасное обновление
gateway/engine и восстановление прежней активности Telegram/scheduler.

CRM [Lead Flow](https://lead-flow-ten-virid.vercel.app/) обновлена через
[PR #2](https://github.com/suer-tech/lead-flow/pull/2), merge commit `e263639`
(первоначальный PR #1 — `f8eb62a`). Vercel production
`CRssM64M6ThGvaLNcofSArG3xLML` имеет статус `Ready`.
После прямого подтверждения владельца подключён отдельный `CRM_AGENT_TOKEN`
и проверена настоящая запись одной карточки из VPS в продуктивный API.
Интерфейс показывает 122 активные карточки вместо прежних 121: MYFORCE ровно одна,
ID 279, `needs_review`, fit 50, intent 0, три источника, писем в CRM — 0.

Отдельно выявлено ограничение исходящего доступа VPS к Vercel: DNS на host
работает, но прямой HTTPS через оба полученных A-адреса не заработал. Повтор
показал TCP timeout 5 с для `216.198.79.3`; `64.29.17.3` успел открыть TCP за
0,02 с, но TLS завершился timeout 5 с. Не считать оба случая отказом именно TCP.
Через существующий namespace `iva-codex` публичный GET отвечает HTTP 200 с
успешной проверкой TLS. Причина сбоя прямого upstream-маршрута не доказана.
Отдельный CRM bridge установлен и включён, authenticated API проверен через него:
два POST получили HTTP 200 и один и тот же ID карточки. Используется прежний VPN
namespace; глобальные маршруты и ключи VPN не менялись. CRM-токен не передаётся
в публичный reader, браузер или модель.

На момент проверки:

- `/etc/lead-flow/research.json`: `enabled=true`, `mode=ingest`,
  `crm_transport=iva_vpn`;
- `lead-flow-research.timer`: `enabled` / `active`; ближайшая проверка допуска
  после commissioning — 26.10 в 16:30 Екатеринбурга, новый поиск — 27.10 в 09:00;
- `lead-flow-maintenance.timer`: `enabled` / `active`, следующий запуск
  27.10.2026 в 03:30 Екатеринбурга;
- `lead-flow-research.service` и `lead-flow-research-manual.service` — oneshot;
  `inactive` после завершения цикла является нормальным состоянием;
- почтовый транспорт выключен; пароля приложения `ai.studio.ufa@mail.ru` нет;
- настоящий SMTP/IMAP вход, чтение ответов и доставка письма не проверены;
- общее исследование спроса `2f9611a667cc493a` остаётся `paused`.

Расписание включено отдельным действием после проверок. Новые исследования
сохраняют результаты в собственную SQLite и передают допустимые карточки/черновики
в CRM. Это режим `ingest`, без отправки писем.
Ручной запуск scheduled service подтвердил `daily_cycle_already_completed`,
модельных вызовов — 0: завершённый слот 26 октября не переигрывается.
После переключения в ingest это повторно проверено настоящим запуском service:
тот же результат, модели 0, `email_sending=false`, timer enabled/active.
Нативный `resume` управляет таймером, но сам не меняет конфигурационное `enabled`.

## Рабочие файлы и границы данных

| Назначение | Путь |
|---|---|
| Код исследователя и почтового адаптера | `/opt/lead-flow` |
| Контрольные точки, источники, оценки и черновики исследования | `/opt/lead-flow/data/research.sqlite3` |
| Приватная конфигурация исследователя | `/etc/lead-flow/research.json` |
| Приватный ключ CRM, root:iva0640 | `/etc/lead-flow/crm-agent.token` |
| Разрешение фиксированного CRM bridge | `/etc/lead-flow/crm-transport.json` |
| Приватный socket CRM, iva:iva0600 | `/run/lead-flow-crm.sock` |
| Root-owned helper фиксированного CRM API | `/usr/local/lib/lead-flow-crm/request.py` |
| Подтверждение commissioning, root:iva0640 | `/var/lib/lead-flow-crm/commissioning.json` |
| Конфигурация выключенной почты | `/etc/lead-flow/mail.json` |
| Планируемые отдельные почтовые журналы | `/opt/iva/data/lead-flow/mail-dispatch.sqlite3`, `mail-inbox.sqlite3` |
| Ограниченный root helper Ивы | `/usr/local/bin/iva-lead-research-control` |
| История подтверждений управляющих команд | `/var/lib/iva-lead-research/control.json` |
| Материалы установки/отката | `/opt/iva/data/lead-flow-release-20261026` |

Финансовая база Ивы не используется как хранилище лидов. Проверка настоящего
Telegram-поручения читает envelope через `mode=ro` без Ledger-миграций и записей.
Ключи почты, CRM-токен, OAuth и VPN-секреты не выводить в отчёты/логи.

## Как выполняется исследование

Кампания посвящена только покупателям специализированной обработки судебных
документов в России: команды АУ/банкротных юридических компаний и подходящие
CRM-интеграторы. Общие программные/товарные исследования Ивы сюда не смешиваются.

Поисковик находит отдельные компании и ссылки; public HTTP/PDF reader читает
страницы с существующими ограничениями адресов, редиректов и VPN-повтором 403.
Оценщик проверяет компанию, конкретную документную операцию, контакт и источники.
При существенном пробеле допускается один сохранённый адресный допоиск на компанию
в пределах общего лимита. Отдельный критик проверяет выводы, автор готовит
персональный вопрос о процессе, другой критик проверяет письмо. Есть ограниченная
правка, а не бесконечное переписывание до одобрения.

Все модельные операции идут последовательно под существующим research lease.
Chromium не запускается. При занятом ресурсе заявка ждёт в пределах заданного
лимита и сохраняет `waiting_resources`; выполненные стадии не сбрасываются.
Таймер не прерывает активное исследование по 15-минутной ротации браузеров.

Сходная отрасль, вакансия, реклама OCR или наличие ПАУ не доказывают готовность
купить. Профильная компания с проверенным процессом может остаться `needs_review`;
письмо в CRM допускается только при текущей квалификации и разрешённом первом
контакте. Свободные email не конструируются. Старые и анонимные отзывы остаются
свидетельствами для гипотез, без выдуманной привязки к компании.

## Фактически выполненные живые прогоны

| Прогон | Попытки чтения URL | Прочитано | Не прочитано | Кандидаты / готовые письма |
|---|---:|---:|---:|---|
| Первый native preview | 7 | 5 | 2 | 3 отклонены / 0 |
| Второй, `1d108003540a48708b8458474074d4d7` | 20 | 17 | 3 | 4 отклонены / 0 |

Второй цикл завершился `completed` 26.10.2026 в 15:02:01 Екатеринбурга:
17 модельных вызовов, у каждой компании один адресный допоиск. Это завершённый
поисковый проход с отклонёнными кандидатами, а не подтверждённые продажи или
успешное прохождение генерации письма подходящему клиенту.

Три непрочитанных источника второго цикла: недостаточный текст при HTTP 200,
ошибка DNS официального сайта и HTTP 403. Сохранённые отказы также включают
неподтверждённую операцию извлечения судебных данных, контакт из вакансии,
неподходящий готовому профилю сценарий и цитаты, не совпавшие с прочитанным текстом.
Обход доступа или успех VPN на этих источниках из этого результата не следует.

Разбор v2 выявил и дефекты самого процесса: для MYFORCE модель дала цитату
длиннее допустимых 25 слов и заменила буквальное «СРМ» на «CRM»; reader мог
отбрасывать footer с официальными контактами. Поэтому четыре отклонения нельзя
считать доказательством отсутствия потребности у этих компаний.

Pipeline4 установлен: 47 файлов runtime и три статических ресурса для полного
HTTP-контрактного тестирования CRM. После уточнения обратной связи для правки
цитат 70 worker/HTTP/probe тестов прошли на VPS без пропусков. Новая версия
распределяет квоту кандидатов по направлениям поиска, сохраняет её до вызова
модели и прекращает поиск после заполнения общей ёмкости.

Две изолированные native-проверки повторно использовали только сохранённые
публичные страницы MYFORCE. Первая потратила один вызов: исправила цитату
процесса, но оставила 30 слов в цитате компании; валидация отклонила результат.
После точного feedback вторая проверка потратила шесть вызовов: цитата компании
сокращена с 30 до 6 слов, процесса — с 29 до 12; обе дословные, независимый
критик подтвердил профиль и документную операцию. Некотируемые поля и рабочий
снимок сохранены, новых запросов поиска/чтения и обращений к CRM не было.
Отчёты: `data/lead-flow-verification-20261026/myforce-quote-p4.report.json` и
`myforce-quote-p4-feedback.report.json` в том же каталоге.

Это доказательство исправления цитат, но не подтверждённый покупатель: MYFORCE
сохранён только в изолированной проверке как `needs_review`, intent 0. На этом
этапе обе попытки письма отклонены проверяющим, итог `draft_rejected`.
Найдены повтор предложения в шаблоне/поле модели и неуправляемое автором
утверждение в шаблоне. После исправления разделения текста автора/шаблона первая
letter-only проверка также отклонила письмо: автор изменил регистр исходной
цитаты. Источник и цитата теперь закрепляются из проверенной оценки, автор
выдаёт только текст письма и не переписывает сведения о происхождении фактов.

Последняя изолированная native-проверка `myforce-letter-p4-pinned`, run
`b09e37e9f2da4d099bfd79eb3dadc954`, получила готовый локальный черновик за три
вызова — два автора и один проверяющего. Все восемь флагов проверки письма true.
Отчёт: `data/lead-flow-verification-20261026/myforce-letter-p4-pinned.report.json`.
Источники, оценка и исследовательский вердикт не менялись: MYFORCE по-прежнему
`needs_review`, intent 0. Поиска, чтения новых страниц, переоценки, CRM и SMTP
в этой проверке не было. Рабочий второй цикл остался историческим результатом:
четыре отклонённых кандидата, ноль готовых писем. Это проверка подготовки текста
на сохранённом материале, а не новый полный поисковый цикл или доказательство
потребности клиента.

После успешной пробы renderer дополнительно лишён второй фиксированной фразы
о критериях пилота, чтобы убрать повтор. Эта последняя правка не имеет отдельной
модельной проверки; после её доставки все 74 серверных теста прошли за 0,864 с.

Reader уже обновлён отдельным выпуском
`/opt/iva/data/lead-contact-release-20261026`: шесть файлов, 102 серверных теста
в пяти наборах. На `https://kit.arbitrkit.ru/products/pau` обычное чтение вернуло
HTTP 200 и 3990 символов основного текста без контактных фрагментов; явное
`include_contact_fragments=true` сохранило тот же текст и дополнительно вернуло
один фрагмент с `info@arbitrkit.com`. Прямой вызов VPN reader также подтвердил
этот результат. Это подтверждение исправления пропуска footer, а не подтверждение
спроса, связи двух разных доменов или разрешения на отправку письма. По умолчанию
контактное извлечение выключено; отдельные фрагменты допускаются только для
проверки контакта, не личности компании или потребности. Установщик сохранил
маршруты и systemd unit reader, не перезапускал VPN и не писал финансовые данные.

Нативный статус различает `pages_attempted`, `pages_readable`, `pages_failed`;
историческое поле `pages` означает попытки. Не называть 20 попыток двадцатью
прочитанными страницами. Отдельно показываются `drafts_prepared`, причины
ограничений и стадия кандидатов. `email_sent=false` относится к исследователю,
который не выполняет SMTP; это не универсальный монитор стороннего mailer.

## CRM и защита от повторов

Реальная приёмка 26.10.2026:
`/opt/lead-flow/data/probes/myforce-crm-commissioning-20261026-111349-d594c1a3.json`,
`passed=true`, все 12 условий выполнены. Первый authenticated ingest получил
HTTP 200, `created=true`, `lead_id=279`. Повтор того же запроса получил HTTP 200,
`created=false`, `replayed=true`, тот же ID 279. Создания черновика через API,
SMTP и новых модельных вызовов не было. Рабочие таблицы исследователя и сохранённые
материалы изолированной пробы не менялись; её прежние три модельных вызова не
являются вызовами commissioning. UI отдельно подтвердил одну карточку MYFORCE.

`lead-flow-crm.socket` включён и active. Socket активирует ограниченный
`lead-flow-crm@.service` в существующем `iva-codex` namespace; фиксированы hostname,
TLS и только POST `/api/agent/leads`/`drafts`. Вызов не принимает произвольный URL,
заголовки или токен от модели; перенаправления и автоматический сетевой повтор
запрещены. Секрет хранится отдельно в root-owned файле и Vercel Production Secret.
Этот transport не обслуживает почтовые claim/ACK/inbox маршруты: подключение
mailer и проверка его сети остаются отдельной будущей приёмкой.

Нативный gateway действительно прочитан после переключения: 11:15:50 UTC
(16:15:50 Екатеринбурга), `mode=ingest`, `transport=iva_vpn`, socket active,
`proof_status=verified`, `proofscope=scoped_ingest`,
`last_verified_at=2026-10-26T11:15:14.661207+00:00`. При этом
`current_api_health=not_checked`: чтение статуса не делает новых HTTP-запросов.
Подтверждение в `/var/lib/lead-flow-crm/commissioning.json` проверяется по root
владельцу, mode0640, безопасным родительским каталогам, точной схеме/origin/transport,
UTC-дате не в будущем и возрасту до семи дней. Оно доказывает только указанную
проверку записи и повтора, не почту и не экспорт старого рабочего цикла.
Поэтому `last_confirmed_export` из основной исследовательской БД пока null,
оба её исторических цикла имеют 0 CRM ACK и 0 черновиков. Статус не импортирует
изолированные проверки в production history. Копия ответа gateway:
`data/lead-crm-status-release-20261026/post-commissioning-gateway.json`.

Миграции аддитивны и сохраняют прежние карточки. Новое исследование обновляет
машинные оценки только более свежими наблюдениями. Ручные заметки и стадия
сохраняются; контакт обновляется лишь пока совпадает с последним машинным
fingerprint. Replay ingestion возвращает сохранённый `original_created` и
текущую `initial_draft_allowed`, учитывая архив, suppression и историю писем.

Любое первое письмо, включая `uncertain` и `cancelled`, блокирует автоматическое
создание нового. Ручная стадия после `new` также исключает повторный холодный
контакт. Ответы 409/422 для отдельного кандидата сохраняются как ограничение
и не останавливают весь экспорт; сетевой сбой/5xx сохраняет `pending_publish`.
CRM не считает SMTP acceptance подтверждением доставки адресату.

## Расписание, обслуживание и проверка

Установленный research timer проверяет допуск каждые 15 минут с 09:00 до 21:45
Екатеринбурга (`Persistent=false`). `scheduled.py` допускает один новый цикл
в сутки либо продолжение незавершённого checkpoint; завершённый дневной цикл
не повторяется. Проверка допуска не вызывает модель. Таймер теперь включён.
Проверка scheduled service после включения вернула `daily_cycle_already_completed`
без моделей: незавершённых циклов для продолжения нет. Последующие проверки
26 октября не создадут новый поиск; ближайший новый дневной слот —
27.10.2026 в 09:00 Екатеринбурга. Старый general demand job остаётся paused.

Включён `lead-flow-maintenance.timer` на 03:30 Екатеринбурга. `maintenance.py`
делает отдельные резервные копии и может компактировать сырые тела источников
старых завершённых циклов через 14 дней, сохраняя цитаты, решения, результаты и
ACK. Проверка backup/restore 26.10.2026 в 15:06:58 Екатеринбурга прошла 31 из 31 условий на
изолированной online-копии: восстановление совпало с исходным снимком до retention,
два старых тела компактированы только на копии, active/pending_publish, цитаты и
ACK сохранены. Проверены committed WAL, отдельный почтовый журнал, блокировки и
нехватка места. Хеши рабочих таблиц до/после совпали: 2 цикла, 7 кандидатов,
27 записей страниц. Отчёт — `data/lead-flow-maintenance-acceptance-20261026.json`;
серверная копия — `/tmp/lead-flow-maintenance-acceptance-6mdysd_9/report.json`.
Затем production maintenance действительно выполнен 26.10.2026 в 15:10:14
Екатеринбурга, exit0: сохранена одна БД, `compacted=0`, `pruned=0`, модельных
вызовов — 0. Рабочая приватная копия:
`/opt/iva/data/lead-flow/backups/lead-flow-20261026-101014.tar.gz`.
Таймер обслуживания включён, следующий запуск 27.10.2026 в 03:30 Екатеринбурга.
Это подтверждает первый рабочий backup, но не исполнение будущего ночного запуска.
Изолированный артефакт в `/tmp` не считать долговременной рабочей копией.

Текущие проверки: полный локальный pytest — 149 тестов и 49 subtests;
87 серверных worker/transport/doctor проверок прошли за 1,211 с;
14 Linux-проверок отдельного CRM helper и семь Linux-проверок commissioning;
43 проверки native status/control/installer/audit прошли после установки metadata
за 0,194 с. Reader ранее прошёл 102 проверки, PostgreSQL —
27 условий приёмки на отдельной реальной базе. Ранний worker-набор имел
53 проверки с одной пропущенной; он не подменяет более поздний полный прогон.
PostgreSQL приёмка не заменяла отдельные реальные 12 условий commissioning,
описанные выше. SMTP/IMAP в эти доказательства не входят.

При дальнейшем обновлении сохранять исследовательскую БД, почтовую дедупликацию,
неопределённые отправки, suppression и ручные правки. Код worker несовместимой
версии нельзя подставлять в активный checkpoint: fingerprint и этапы должны
совпадать. Установщик `deploy/install_lead_flow.py` не включает таймеры; control
обновляется на безопасной границе, с короткой admission-транзакцией и без
удержания финансового writer lock во время ожидания Telegram/сборки приложения.

Для заключительной проверки подготовлен `deploy/verify_lead_flow_runtime.py`.
Он читает статус, состояния systemd, паузу старого исследования, наличие native
tool/instructions в generated/compiled manifest и сверяет файлы с заранее
проверенными хешами. Модели, SMTP/IMAP и credentials не используются; SQLite
открывается `mode=ro`, без Ledger-миграций. Единственный создаваемый артефакт —
приватный JSON в каталоге выпуска, mode0600. Старые ожидаемые хеши не заменяются
текущими автоматически. Для обновлённых после staging файлов нужно передать
актуальный reviewed manifest через `--manifest`. При обновлении одного пути
собрать единый manifest с явно проверенными overrides: конфликтующие хеши
одного пути в нескольких manifests намеренно отклоняются.

```console
/opt/iva/.venv/bin/python /opt/iva/deploy/verify_lead_flow_runtime.py --require-idle --expected-research enabled --expected-mode ingest --manifest <reviewed-combined-manifest.json>
```

Умолчание helper для research остаётся disabled, поэтому текущее enabled
задаётся явно, как выше; mode ingest также задаётся явно, maintenance enabled
совпадает. Если состояние меняется по поручению владельца,
ожидания задаются явно через `--expected-research`, `--expected-maintenance`,
`--expected-mode`. Локально 9 изолированных тестов audit helper прошли.
Предыдущий полный серверный audit ДО последнего переключения CRM-конфига
выполнил 98 из 98 условий, включая 74 заранее
проверенных файловых хеша, активные службы, включённые таймеры, свободный
исследовательский ресурс и paused general schedule. Отчёт:
`/opt/iva/data/lead-flow-release-20261026/final-runtime-verification.json`;
приватная локальная копия — `data/lead-flow-verification-20261026/`.
Этот audit не подключается к CRM/SMTP/IMAP и не подменяет их приёмку. Новый полный
audit после commissioning и переключения в ingest прошёл: 107 из 107 условий,
80 проверенных файловых хешей, включая root-owned CRM helper и его units,
активный приватный транспорт и подтверждение scoped_ingest. Финальная версия
с русскими подписями исследования — commit `82ee987`, Vercel production
`DevwsoiGAuFEcLPE41JLaUkMBZzm`, Ready. Основной отчёт обновлён по прежнему пути;
реальный gateway status после него уже проверен отдельно, как описано выше.


## OCR acquisition throughout the day — 2026-10-26

Owner now authorizes broader acquisition and one first discovery email per
company. This supersedes daily-only/qualified-only OCR admission. Campaign
ocr-russia-v3: 120-minute local slots 09:00–21:00, up to12companies/72URL attempts/
100model calls per slot, eight diversified queries, max84candidates in seven daily
slots. Preserve checkpoints and the shared research lease; missed slots are not
replayed. General demand schedule2f9611a667cc493a stays PAUSED.

New contact_ready means independently verified supported document process,
company attribution and official business contact, with demand still unknown.
It can create an independently checked discovery letter without inventing buyer
intent. CRM owner policy permits queueing these first letters; one initial letter
per company/recipient across campaigns, suppression/refusals/uncertain outcomes
remain durable. Claim budget counts all started attempts: max100companies/local
day, at least300seconds apart, window09–22Yekaterinburg.

PR3 merged776355b; Vercel Production Cc9uz91CyFnqs97dzKbFoV39J2zg Ready.
Runtime deployed in /opt/lead-flow, root-owned private CRM helper extended with
fixed claim/result/replies/heartbeat/maintenance routes, no arbitrary paths or
credentials in models. lead-flow-mailer.timer is enabled every5minutes09–21;
service ConditionPathExists=/etc/lead-flow/mail-app-password. Credentials remain
ABSENT; mail.json enabled/send_enabled=false, CRM_OUTREACH_ENABLED=0. Do not
claim emails are being sent. Mail activation needs the actual owner-supplied app
password and separate SMTP/IMAP/receipt checks. Password must never be printed.

Research/source history backed up before cutover. Release:
/opt/iva/data/lead-discovery-release-20261026. Compiled native control release:
/opt/iva/data/lead-discovery-control-20261026; idle engine rebuild and graceful
Telegram restore passed with protected state unchanged. Previous compiled backup
was preserved by using a fresh control release. New supervised live cycle
793f49b3637c4da2b83f1b4639fd9474 started11:52:54UTC; it is not yet verified complete.
Server155tests/49subtests and14root-helper tests passed; private claim got actual
HTTP409 while global outbound is disabled, with zero SMTP calls.

### Проверка первой расширенной выборки и уточнение профилей

Полный свежий цикл793f49b3637c4da2b83f1b4639fd9474 завершился26.10в12:10:39UTC:12компаний,62попытки/56прочитанных страниц,69модельных вызовов,4карточки CRM(280–283),3ready письма. Это pipeline5; SMTP0. UI подтвердил3готовых и отключённую отправку.

Pipeline6 после него разрешает независимую проверку релевантной судебной практики/продукта для первого discovery-вопроса без доказательства уже существующей операции извлечения. Факт операции/спроса остаётся неизвестным. Подходящие компании без контакта сохраняются карточками; адреса HR/резюме, прессы, privacy/DPO,abuse,no-reply исключены. Допоиск только ради unmetneed отключён для discovery. Бюджет до100вызовов, жесткий конфигурационный максимум140. SMTP/ACK завершаются на своей безопасной границе без systemd timeout.

Повторная проверка старых потенциальных отказов выполняется явно через worker.reconsider, один новый цикл на исходный run_id. Исходные кандидаты/источники не переписываются. Новый независимый critic/автор/проверяющий используют сохранённые страницы; discovery не повторяется. Прерванный бюджетом кандидат продолжает чтение только непрочитанных URL. Новые CRM ACK остаются в основной исследовательской БД и видны Иве. Не называть это новым интернет-поиском. Тесты pipeline6:158полных локальных/49subtests и137целевых серверных/40subtests;27реальных PostgreSQL условий прошли на отдельной БД. Native profile-control установлен из свежего release, engine/gateway восстановлены, защищённое состояние сохранено.

Final acquisition verification26Oct: source cycle793f49b3637c4da2b83f1b4639fd9474 remained completed/69calls/12candidates/62pages and IDs280–283. Reconsideration76d65a82f3e947d1a288c0d2798974e6 completed12:17:04UTC,9calls:adlawyers.ru284,delfarvater.ru285,lgsl.ru286,each with ready first draft. Across those same12distinct discovered companies:7CRM cards,6ready letters,0SMTP. This is live proof of the broader profile gate, not a new discovery of3companies. The original source run was preserved. Research timer restored enabled/active; next ordinary slot17:00 admitted real run3e9ebb91c0c94c95b45af63f06c6846f,still running at final gateway check. Do not reset it. The general demand schedule remains paused. Mailer timer enabled/active but password absent, mail flags false, CRM_OUTREACH_ENABLED0. Final reviewed audit114/114 (85source hashes), actual gateway reported120-minute interval/max12/discovery true and live CRM acknowledgements. Reports:/opt/iva/data/lead-flow-release-20261026/discovery-final-verification.json and /opt/iva/data/lead-discovery-release-20261026/gateway-final.json. PR4 mergedce4c4d1 after PR3. Full local158tests/49subtests; server137targeted/40subtests, earlier full155/49, realPostgreSQL27/27, helper14/14.

UI follow-up de0bbd0 clarifies that acquisition_runs currently records mailer passes, not the separate research DB. The empty mail journal is not evidence of an idle researcher. Integration status words now fit their cards. Only static JS/CSS were atomically updated under application-deploy.lock while the independent research worker continued; worker/model inputs and credentials were untouched. Fixed source preimages/manifest are in lead-discovery-release-20261026/ui-manifest.json.

Mailbox activation26Oct supersedes earlier absent/disabled OCR mail notes. Owner supplied the app password in a private local .env; only the selected value was transferred, never printed, and is stored /etc/lead-flow/mail-app-password iva:iva0600. Production mail.json enabled/send_enabled=true; CRM_OUTREACH_ENABLED=1 applied in Vercel deployment8ZGUTohJYzZeqiBh6vgpXJPcmAcZ Ready. MFA verification was completed by the owner through Vercel. SMTP authentication/NOOP and read-only IMAP passed. Initial baseline skipped113old messages,0ingested/0sent. Mailer timer enabled/active09–22Yekaterinburg every5minutes,max1send/run andCRM100companies/day/300seconds spacing. Three actual SMTP DATA250 messages had distinct IDs,3CRM ACKs,minimum318seconds between attempts at final check. Provider acceptance is not recipient inbox delivery; Sent-folder mutation is intentionally not implemented by this transport. The mailing queue and local journal, not the mail provider Sent folder, are authoritative for attempts and receipts. No automatic retry of uncertain sends.

Native lead_research.status.mailer now reads the separate journal mode=ro and exposes transport outcomes/last_run/configured switches without credentials, recipients, bodies or claim tokens. Top-level email_sent=false is still researcher-only. Read-only Python metadata update used deploy/install_lead_mail_status.py and safe gateway reload; enginePID/finances/profiles/schedules were preserved. New native privacy/readonly tests passed40/43subtests combined with audit tests. Mailer pacing/off-hours reasons no longer masquerade as queue_empty;19local and19server tests/7subtests passed. Activation/proofs/preimages:/opt/iva/data/lead-mail-activation-20261026; final-verification.json and native-status/verification.json. Source researchschedule and browser/MAXlocks unchanged; old general research stayspaused.


## 2026-10-26: outreach copy ai-benchmark-v2
The owner confirmed measured module performance: 30 minutes to 1 minute per document and 95% extraction quality on their benchmark. These are owner-reported benchmark results, not independently reproduced here or guaranteed for every client. Additional desired fields are offered as individual configuration validated on document samples; no universal out-of-box format support is promised.
All 10 never-attempted queued letters passed independent review and were revised through the authenticated compare-and-swap CRM endpoint. Their source snapshots were unchanged. The five earlier SMTP-accepted messages were excluded and will not be resent. Evidence: /opt/lead-flow/data/copy-ai-benchmark-v2/final.json (passed=true, planned=10, revised=10, smtp_calls=0). Current-copy overlays preserve historical research. Sending and the lead-flow research timer were restored after review; general demand research remains paused. Code commit cc551fe24460f6e8bb3192bb328037cafe5bf749.
