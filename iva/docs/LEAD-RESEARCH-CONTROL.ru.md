# Управление отдельным исследователем OCR-лидов

Установлено 26.10.2026; фактическое состояние проверяется через
`lead_research status` и службы systemd. Расписание включено, с 26.10.2026 после
реального commissioning используется `mode=ingest`, `crm_transport=iva_vpn`;
настоящая отправка выключена. Сегодняшний слот завершён, следующий новый поиск
27.10 в 09:00 Екатеринбурга; проверка допуска не вызывает модели повторно.
Сводка доказательств и ограничений — `LEAD-FLOW-DEPLOYMENT.ru.md`.

`lead_research` — отдельный нативный инструмент Ивы, не модификация расписания
общего исследования спроса. Он работает с `/opt/lead-flow/data/research.sqlite3`,
конфигурацией `/etc/lead-flow/research.json` и тремя фиксированными unit:

- `lead-flow-research.service`: `scheduled.py` проверяет дневной допуск, затем
  запускает worker с `--scheduled` при наличии работы;
- `lead-flow-research-manual.service`: ручной проход, тот же worker без `--scheduled`;
- `lead-flow-research.timer`: проверка каждые15минут с09:00до21:45 Екатеринбурга,
  один новый цикл в сутки или продолжение checkpoint, `Persistent=false`.

## Команды Иве

- «Покажи статус поиска клиентов OCR» — `status`: живые состояния службы и таймера,
  последние циклы, число модельных вызовов/прочитанных страниц и стадии кандидатов.
  CRM-блок разделяет конфигурацию/socket, сохранённые ACK и ограниченное
  commissioning-подтверждение, не обещает текущую доступность API.
- «Покажи найденных клиентов OCR и письма» — `candidates`: последний цикл либо
  заданный ID, максимум 10 карточек с источниками, ограничениями и черновиками.
- «Запусти поиск клиентов OCR» — `run`: один запуск существующего manual unit.
  Сохранённый незавершённый цикл продолжается; уже завершённый дневной слот
  не создаётся повторно. Команда не включает регулярное расписание.
- «Приостанови поиск клиентов OCR» — `pause`: выключает будущие запуски таймера.
  Уже работающий цикл, включая его ожидание ресурса, не прерывается. Это пауза
  расписания, а не обещание немедленного прекращения текущего процесса.
- «Возобнови ежедневный поиск клиентов OCR» — `resume`: включает существующий
  таймер, без немедленного воспроизведения пропущенного запуска. `enabled=true`
  в исследовательской конфигурации — отдельное условие, иначе возвращается ошибка.

`accepted` означает принятую systemd команду, а не завершённый поиск. Карточка и
черновик не означают отправленное письмо. Этот инструмент никогда не запускает
SMTP, не подтверждает доставку и не меняет старое расписание demand_research.

26.10.2026 отдельный ingest MYFORCE и идемпотентный повтор подтверждены двумя
HTTP 200 и одним `lead_id=279`. Основная исследовательская БД не переписана:
её старые циклы имеют 0 CRM ACK, поэтому `last_confirmed_export=null` корректен.
Нативный статус показывает root-owned `/var/lib/lead-flow-crm/commissioning.json`
как `proof_status=verified`, `proofscope=scoped_ingest`, с `last_verified_at`.
Проверяются точная схема, root/mode0640, origin/transport и UTC-дата до семи дней,
без будущего времени. `current_api_health=not_checked` всегда сохраняет границу:
status не делает запросов к CRM, не читает ключ и не трактует active socket как
успешную авторизацию. Suppressed/archived/неверные ACK не считаются карточками.

## Права и установка

Изменения требуют полного точного текущего сообщения владельца Telegram в
`instruction_quote`. Проверяется настоящий private envelope в состоянии
processing; цитата из сайта, старое сообщение или пересланный текст не дают прав.
Таблицы envelope читаются через отдельное `mode=ro` соединение без Ledger/setup
и без финансовых записей. Статус и карточки читаются без управляющих полномочий.

Контроль выполняет `deploy/lead_research_control.py`, устанавливаемый root как
`/usr/local/bin/iva-lead-research-control`, root:root0755. Каталог
`/var/lib/iva-lead-research` должен иметь root:iva0750; файл контрольных результатов
создаётся атомарно root:iva0640. В sudoers допускается только фиксированный helper
без аргументов:

```sudoers
iva ALL=(root) NOPASSWD: /usr/local/bin/iva-lead-research-control ""
```

stdin содержит только `action` из трёх значений и 64-значный digest owner request.
Пути, имена unit и shell-команды от модели не принимаются. Helper сериализует
команды flock, сохраняет inflight до systemd-вызова и подавляет повтор одного
request. Неизвестный результат после аварии или timeout помечается uncertain;
он проверяется через status, автоматически не повторяется.

Перед установкой нужны обычные deployment locks, безопасная граница gateway/
Telegram и сохранение предыдущих файлов. `prepare_pilot.py` теперь сохраняет и
копирует `iva_integration/lead_research.ts` в
`data/iva-pilot/app/agent/tools/lead_research.ts`. Полный prepare не требуется
только ради копирования одного инструмента: он меняет и другие конфигурации.
После копирования tool нужен штатный build приложения и безопасная загрузка
новой версии gateway. Эти действия выполняет основной установщик.

## Проверка

`python -m unittest tests.test_lead_research -v`: 29 изолированных тестов проходят;
вместе с 5 проверками установщика и 9 audit helper — 43 на VPS, 0,194 с.
Статус отдельно считает попытки чтения,
действительно прочитанные страницы, ошибки, готовые черновики и причины отказов.
Проверены read-only данные/отсутствующая БД, фильтрация приватных полей, настоящий
owner envelope, старые/пересланные сообщения, дедуп команды, nonblocking запуск,
таймерная пауза без остановки службы, конфигурация возобновления, неопределённый
результат и авария между systemd и сохранением подтверждения. systemd и sudo в
тестах заменены контролируемыми заглушками. Отдельно проверен реальный gateway
после безопасного reload: выдаёт новый metadata-контракт; engine не перезапускался,
Telegram восстановлен, финансовые данные/профили сохранены. Release и preimages:
`/opt/iva/data/lead-crm-status-release-20261026`.

Обновление26.10: OCR теперь использует двухчасовые слоты09–21, максимум12кандидатов за проход. Native status возвращает research_interval_minutes/max_candidates_per_cycle/discovery_outreach. Timer next-elapse означает только проверку допуска, а не гарантированный новый цикл. contact_ready допускает вопрос о потребности с неизвестным спросом; исследователь сам не отправляет письма.

Mailbox activation26Oct supersedes earlier absent/disabled OCR mail notes. Owner supplied the app password in a private local .env; only the selected value was transferred, never printed, and is stored /etc/lead-flow/mail-app-password iva:iva0600. Production mail.json enabled/send_enabled=true; CRM_OUTREACH_ENABLED=1 applied in Vercel deployment8ZGUTohJYzZeqiBh6vgpXJPcmAcZ Ready. MFA verification was completed by the owner through Vercel. SMTP authentication/NOOP and read-only IMAP passed. Initial baseline skipped113old messages,0ingested/0sent. Mailer timer enabled/active09–22Yekaterinburg every5minutes,max1send/run andCRM100companies/day/300seconds spacing. Three actual SMTP DATA250 messages had distinct IDs,3CRM ACKs,minimum318seconds between attempts at final check. Provider acceptance is not recipient inbox delivery; Sent-folder mutation is intentionally not implemented by this transport. The mailing queue and local journal, not the mail provider Sent folder, are authoritative for attempts and receipts. No automatic retry of uncertain sends.

Native lead_research.status.mailer now reads the separate journal mode=ro and exposes transport outcomes/last_run/configured switches without credentials, recipients, bodies or claim tokens. Top-level email_sent=false is still researcher-only. Read-only Python metadata update used deploy/install_lead_mail_status.py and safe gateway reload; enginePID/finances/profiles/schedules were preserved. New native privacy/readonly tests passed40/43subtests combined with audit tests. Mailer pacing/off-hours reasons no longer masquerade as queue_empty;19local and19server tests/7subtests passed. Activation/proofs/preimages:/opt/iva/data/lead-mail-activation-20261026; final-verification.json and native-status/verification.json. Source researchschedule and browser/MAXlocks unchanged; old general research stayspaused.


## 2026-10-26: outreach copy ai-benchmark-v2
The owner confirmed measured module performance: 30 minutes to 1 minute per document and 95% extraction quality on their benchmark. These are owner-reported benchmark results, not independently reproduced here or guaranteed for every client. Additional desired fields are offered as individual configuration validated on document samples; no universal out-of-box format support is promised.
All 10 never-attempted queued letters passed independent review and were revised through the authenticated compare-and-swap CRM endpoint. Their source snapshots were unchanged. The five earlier SMTP-accepted messages were excluded and will not be resent. Evidence: /opt/lead-flow/data/copy-ai-benchmark-v2/final.json (passed=true, planned=10, revised=10, smtp_calls=0). Current-copy overlays preserve historical research. Sending and the lead-flow research timer were restored after review; general demand research remains paused. Code commit cc551fe24460f6e8bb3192bb328037cafe5bf749.
