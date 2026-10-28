# Перенос Ивы на Ubuntu

**OCR-лиды — 26 октября 2026:** отдельный исследователь установлен в
`/opt/lead-flow`; нативный `lead_research` подключён к Иве. CRM обновлена через
PR #2 (`e263639`, после PR #1 `f8eb62a`), production Ready. CRM-токен подключён
по подтверждению владельца. Реальная запись MYFORCE и повтор получили HTTP 200
и один ID 279; UI: 122 активные карточки, MYFORCE одна, needs_review/fit 50/intent 0,
три источника, писем 0. Конфиг `enabled=true`, `mode=ingest`, `crm_transport=iva_vpn`; research timer
включён. Scheduled service вернул `daily_cycle_already_completed` без моделей;
ближайший новый поиск — 27.10 в 09:00 Екатеринбурга. Maintenance timer включён,
следующий запуск 27.10 в 03:30 Екатеринбурга;
первый рабочий backup выполнен 26.10 в 15:10:14, одна БД, без очистки и моделей.
SMTP/IMAP выключены без пароля
приложения выбранного ящика. Два реальных preview прошли: первый — 5 прочитанных
страниц из 7 попыток и 3 отклонённых кандидата; второй — 17 из 20, 17 модельных
вызовов, 4 отклонённых кандидата. В этих рабочих циклах готовых писем и отправок — 0.
Отдельный contact-reader установлен и проверен 102 серверными тестами:
реальное прямое/VPN чтение вернуло контакт из footer при явном opt-in,
сохранив основной текст и прежний режим по умолчанию. Pipeline4 установлен,
70 серверных worker/HTTP/probe тестов прошли. Изолированная проверка на сохранённых
страницах исправила цитаты MYFORCE с 30/29 до 6/12 слов. Поздняя изолированная
letter-only проверка подготовила один черновик за три вызова, все восемь флагов
проверяющего true; MYFORCE остаётся needs_review/intent0, CRM/SMTP не вызывались.
После неё удалён повтор фиксированной фразы renderer без новой модельной пробы.
Локально прошли 149 тестов и 49 subtests; VPS: 87 worker/transport/doctor за 1,211 с,
14 Linux helper, 7 Linux commissioning, 43 native за 0,194 с. Реальный API commissioning
выполнил 12 условий без моделей/draft API/SMTP, основные research tables сохранены.
Из host VPS оба A-адреса Vercel дают timeout при работающем DNS; публичный GET
через существующий VPN даёт 200/TLS verified. Причина upstream не доказана.
Отдельный CRM bridge установлен и enabled/active, authenticated ingestion
подтверждён; ключ остаётся в приватном root-owned файле, не в public reader.
Нативный status после переключения показывает verified/scoped_ingest и дату
11:15:14 UTC; current_api_health=not_checked, старые cycles имеют 0 ACK/0 drafts.
Полный audit после прежнего commissioning прошёл107/107. Более поздняя версия OCR описана в конце LEAD-FLOW-DEPLOYMENT.ru.md.
Старое исследование спроса остаётся paused. Код, контрольные точки, проверки,
неподтверждённые части и правила дальнейшего развёртывания:
[LEAD-FLOW-DEPLOYMENT.ru.md](LEAD-FLOW-DEPLOYMENT.ru.md).

**Публичные источники — 26 октября 2026, повтор HTTP 403:** по прямому поручению
владельца установлен `/opt/iva/data/demand-http403-release-20261026`.
Обычный прямой HTTP 403 теперь вызывает один запрос через существующий VPN;
предположение о геоблокировке для этого не требуется. Причина маршрута и оба
HTTP-результата сохраняются. Прежние ограничения CAPTCHA/входа/429, публичных
адресов, точного хоста и бюджета повторов сохранены. VPN не перенастраивается.
Расписание исследования остаётся на паузе. Детали и фактическая проверка:
`docs/DEMAND-HTTP403-VPN.ru.md`.

**OSINT — 26 октября 2026, проверка достаточности свидетельств:** установлен
`/opt/iva/data/demand-coverage-release-20261026`, 12 файлов, 321 серверный тест.
Новые циклы проверяют, отвечают ли источники на вопрос поручения, и выполняют
ограниченный адресный поиск недостающих свидетельств внутри прежних бюджетов.
Сохраняются незакрытые вопросы, контрольные точки и архивные продолжения.
15 условий живого повтора пройдены: прайс признан недостаточным, выполнен новый
поиск, отказ HTTP 403 сохранён как пробел. Новых зарубежных отзывов этот повтор
не получил; это не полный живой цикл. Рабочие данные сохранены, службы активны,
ресурс исследования освобождён. Регулярное исследование остаётся на паузе.
Подробности и границы проверки: `docs/DEMAND-EVIDENCE-COVERAGE.ru.md`.

**OpenCode — 25 октября 2026, 18:53 Екатеринбург:** восстановлен внутренний
provider scope сервиса: при ложном global healthy провайдеры отвечали HTTP 503,
сессии оставались без ответов, Ива переходила на Codex через 15 секунд.
Безопасный перезапуск восстановил настоящие ответы. Добавлены provider preflight,
общий inference flock и root-owned `iva-opencode-health.timer` раз в минуту.
Он допускает перезапуск только после трёх подтверждённых 503 в безопасной паузе,
с интервалом минимум 15 минут и максимум двумя попытками за час.
Release `/opt/iva/data/opencode-repair-20261025`: 38 серверных тестов и два
настоящих запроса PrimaryBridge прошли; 2190/1930 мс, стоимость 0. Рабочий
обработчик во второй проверке был заглушкой. Выбор OpenCode, модель, финансовые
операции и расписания сохранены. Подробности: `docs/OPENCODE-RECOVERY-20261025.ru.md`.

**Согласование исследований — 25 октября 2026, 18:42 Екатеринбург:** установлен
`/opt/iva/data/demand-release-20261025-review-retry`. Исправлены точные ключи
решений координатора, ID источников дочерних запросов, внешние кавычки цитат,
обрезка текста отчёта до 800 символов и наследование международного контекста.
Отчёт роли объясняет необходимость углубления (`branch_reason`) и использует
минимум `medium`. Временные повторы не создают отдельных уведомлений; реальные
технические ограничения остаются видны в last_error. Обновление расписания
сохраняет текущий цикл/экспорт/разрешение на ресурсы и состояние ошибки.
318 серверных тестов и четыре настоящих вызова модели на изолированных копиях
прошли; финансы, расписания и outbox при установке не изменены. В рабочей истории
уже есть четыре завершённые дочерние проверки первого уровня. Живая диагностика
подтвердила допуск задачи второго уровня, но не выполняла её поиск. Подробности:
`docs/DEMAND-MULTIAGENT.ru.md`, release `verification/review-report.json`.

**Команда исследователей — 25 октября 2026:** новые циклы используют отдельное
планирование, четыре независимых контекста исследователей, согласование дочерних
проверок и итоговое сопоставление. Задания сохраняются в SQLite и выполняются
последовательно под прежним разрешением на ресурсы. Установки:
`/opt/iva/data/demand-release-20261025-agents` и `-agents-quality`.
280 серверных тестов прошли. Полный живой прогон проверил четыре роли и экспорт
отчёта, но не запросил дочерних задач; фактический `medium` подтверждён отдельным
живым вызовом планировщика после исправления сброса при перечитывании профиля. Подробности и
границы доказательств: `docs/DEMAND-MULTIAGENT.ru.md`.

**Исследования спроса — предыдущее расширение 25 октября 2026:** установка
`/opt/iva/data/demand-release-20261025-v3-r2` и `monitor-followup` внутри неё.
Четыре поисковые партии до 48 кандидатов, анализ по три страницы, очередь
уточняющих вопросов со своими критериями и источниками. Задание
`2f9611a667cc493a` и его ежечасный cron сохранены. Исследование получает отдельное
разрешение координатора и удерживает owner.lock до завершения всего цикла;
MAX/Threads уступают на безопасной границе и возобновляются после освобождения
ресурсов. Штатные этапы продолжаются сразу; таймер не прерывает исследование.
Паузы исследования исключаются из 40-минутного бюджета Threads, не вызывают
ложных событий ожидания и не запускают новую модельную диагностику Threads.
После шести подряд технических сбоев ресурсы освобождаются на время восстановления
с сохранением контрольной точки. Финансовые записи и настройки расписаний при
установке сверены и не изменены. 235 серверных проверок прошли; описание,
ограничения и живая диагностика — `docs/DEMAND-RESEARCH.ru.md`.

**Чередование браузеров 23 октября 2026:** установлен `iva-browser-coordinator`, автоматически чередующий очередь MAX/Threads ходами по 15 минут с мягкой передачей между завершёнными действиями. Общий flock исключает одновременный Chromium; старый MAX guard сохранён. Нативный инструмент Ивы `browser_control` подключён и проверен реальным чтением status. Сборщик/классификатор/рассылка MAX сохраняют прогресс; переходы MAX → Threads → MAX проверены, реальные сообщения не отправлялись. Серверный профиль Threads требует входа, публикации ещё не установлены. Документация, ограничения и проверки: `docs/BROWSER-TURNS.ru.md`. Не перезаписывать патчи исходным установщиком MAX. Перезапуск управляющего сервиса не останавливает текущий браузер.

**Сохранение без кодов 20 октября 2026:** текущий Sol использует `finance_apply` для всех внутренних изменений реестра (весь WRITE, кроме stage; факты — record_actual/record_receipt), а не finance_propose. Сведения о кредитах/картах, графики, ставки и оставшиеся платежи, счета, статусы, исправления/void и планы применяются по ясному текущему поручению с точной цитатой. `local_finance_changes` сохраняет источник, параметры и результат для защиты от повторов; изменения атомарны. Legacy live propose также не создаёт кодов: применяет ясное поручение либо возвращает конкретную проблему. Главная Ива и финансовый навык больше не требуют повторного разрешения; документы не являются инструкциями. Старые команды /confirm оставлены только для совместимости, не требуются и не рекламируются. Реальные банковские переводы этим реестром не выполняются.

Проверки: 257 локальных тестов; 52 целевых проверки на VPS. Полная реальная цепочка Ива → Sol прочитала восстановленные сведения. Отдельная реальная Sol записала тестовую карту через finance_apply в изолированную временную базу: 1 карта, 0 фактических проводок, 0 pending-кодов; повтор источника вернул cached=true. Отчёты на сервере: data/credit-readback-verification.json и data/finance-write-verification.json. Рабочая база в этой проверке записи не использовалась.

По прямому поручению владельца сверены документы 19 октября и восстановлены две заблокированные записи: Т-Банк (`debt_save`, прежний черновик 6d64c6af, который был superseded) и карта Сбер (`card_save`, 7881c518). Перед применением сделана резервная копия. Проверены неизменность всех фактических операций и идентичностей долгов, повторное применение возвращает duplicate=true. Остаток и компоненты задолженности, ставки с условиями и 59 платежей Сбера сохранены с происхождением в notes; теперь debt_inventory также возвращает notes. Не путать общую задолженность с основным долгом или суммой будущих платежей. Скрипт reconcile_credit_details.py по умолчанию проверяет копию, --apply предназначен только для этих двух ранее порученных записей на VPS.

**Формат ответов 20 октября 2026:** текущий режим по умолчанию — текст (`local_meta.voice_mode=off`), в том числе на входящие голосовые. Ива на Luna понимает свободную формулировку владельца и вызывает нативный инструмент `reply_format` (`/reply-format` gateway): persistent voice/text сохраняется в SQLite до отмены, once меняет только текущий ответ. Инструмент привязан к текущему обрабатываемому прямому сообщению владельца; документы и старые сообщения не дают права переключать режим. Транспорт читает результат до отправки текущего ответа. `/voice on|off|auto` и `/say` остаются явными командами совместимости. 34 теста транспорта/голоса проходят на Windows и VPS; реальная Luna проверена на разных формулировках включения, отключения и разовой озвучки. Просьбы о формате проходят через Иву, без словаря фиксированных фраз.

**Восстановление Telegram 20 октября 2026:** прокси возвращал HTTP CONNECT 500 из-за ошибки DNS; после неудачных стартов Telegram исчерпал лимит systemd и остался выключенным. Прокси перезапущен, DNS-файл namespace дополнительно закреплён через `BindReadOnlyPaths` в его unit. Старт Telegram теперь повторяет безопасные getMe/getWebhookInfo с задержкой до восстановления сети, допускает остановку и не повторяет финансовые действия. Ошибки токена и существующий webhook по-прежнему останавливают запуск. Регрессия: 17 тестов транспорта на Windows и VPS. Ожидавшее голосовое 608703910 обработано на сервере, текст и голос отправлены (state=done, reply_voice=true); режим ответов auto сохранён.

Целевой сервер: Green Uranium, 185.182.110.82, Ubuntu 26.04 LTS x86_64, 956 MiB RAM, swap 1.5 GiB. Проект `/opt/iva`, пользователь `iva`. Node 24.22.0, Codex CLI 0.163.0-alpha.10.2. Codex использует существующую подписку через перенесённый файловый login.

**Перенос завершён 19 октября 2026:** сервер самостоятельно восстановил службы после настоящей перезагрузки. Telegram polling работает; заранее созданное тестовое напоминание доставлено ровно один раз (message_id 2188), затем тест закрыт. Luna low и Sol 6.1 medium реально ответили через VPN до и после перезагрузки. Полная цепочка Ива → finance_agent → канонический реестр проверена без изменения операций. Снимок содержит 7 операций, 10 месячных статей и 6 документов; проверены отпечаток операций, таблицы, целостность SQLite и модель STT. Восстановление приватной резервной копии также проверено. Эти числа относятся к моменту переноса; новые сообщения могут их изменить.

**Выборочный VPN по прямому поручению владельца:** официальный AmneziaWG-Go в сетевом пространстве `iva-codex`, настройки `/etc/iva-vpn/client.conf` (root:600). Главный маршрут и SSH остаются прямыми. Codex запускается через `/usr/local/bin/iva-codex` и ограниченный sudo-helper, который входит в namespace и сбрасывает привилегии к iva; завершает дочернюю группу при таймауте/гибели вызывающего процесса. Прокси Telegram слушает только внутренний адрес 10.203.77.2:8888 и принимает соединения только с 10.203.77.1. Все Telegram API и скачивания идут через TELEGRAM_HTTPS_PROXY. Cartesia и браузер не используют этот прокси. В namespace нет прямого запасного маршрута при отказе туннеля. Исходный адрес сервера без VPN получает от OpenAI 403/unsupported_country_region_territory; через предоставленный туннель реальные модели доступны. Глобальный VPN, общий HTTPS_PROXY и GUI Amnezia не устанавливались.

**Состояние на компьютере:** бот, память и движок остановлены; ярлыки автозапуска перенесены в приватную папку data/vps-deploy/disabled-startup. Маркер data/remote-deployment.json защищает от случайного запуска второй копии. Локальная база — архивный снимок, живые изменения сохраняются на VPS.

Службы: `iva-vpn`, `iva-telegram-proxy`, `iva@gateway`, `iva@engine`, `iva@telegram`, `iva@memory`. Все включены в автозагрузку. Приватные резервные копии — `/opt/iva/data/server-backups`, ежедневно около 03:30 Екатеринбург, хранятся 14 дней (`iva-backup.timer`). Копии на этом же VPS не защищают от потери всего сервера; отдельное внешнее хранилище пока не подключено.

Подготовлены Linux-обнаружение ffmpeg/whisper.cpp, отдельные systemd-службы gateway, engine, telegram и memory под пользователем iva, перенос конфигурации с путей Windows на `/opt/iva`, согласованный SQLite-снимок и закрытый пакет с исходными документами, памятью и текущим Codex-login. Пароль сервера в пакет или исходники не сохраняется. Финальный пакет содержит секреты: передавать только по SSH, права 600; не публиковать.

Проверка состава без остановки бота: `python deploy/prepare_bundle.py`.

Порядок переноса после восстановления SSH:

1. Проверить ОС, свободное место, SSH host key и имеющиеся службы; подготовить зависимости без запуска второго Telegram-poller.
2. Остановить локальный Telegram: `python iva_integration/telegram_runtime.py stop`; дождаться `worker_status.state=stopped` и отсутствия active_source. Остановить `python iva_integration/memory_scheduler.py stop`, дождаться окончания текущей работы с памятью.
3. Создать финальный пакет `python deploy/prepare_bundle.py --cutover`. Снимок проходит SQLite integrity_check. Завершённые операции/Telegram offset/идентичности сохраняются; новое серверное общение начинает отдельную сессию, прежняя сессия архивируется.
4. Передать пакет по SSH и распаковать в `/` только на проверенном сервере. Запустить `bash /opt/iva/deploy/bootstrap.sh`. Скрипт устанавливает зависимости и запускает только gateway/engine. Bootstrap предназначен для первоначальной установки, не для слепого повторного запуска поверх работающего сервера.
5. От пользователя iva проверить реальный Codex CLI (Luna low и Sol 6.1 medium), цепочку делегирования на чтение, финансовые записи только на отдельной тестовой базе, память, даты/часовой пояс, PDF/изображения, Playwright, Cartesia → Opus → whisper.cpp. Проверить равенство числа операций и остатков финальному локальному снимку. Запуск сервисов: через deploy/service.py; Windows-supervisor на сервере не используется.
6. После успешной проверки включить `systemctl enable --now iva@telegram iva@memory`. Проверить polling без Telegram 409 и доставку согласованного тестового напоминания. Проверить systemd restart и загрузку сервера, затем убедиться, что локальные службы не перезапускаются автоматически. До завершения этих проверок не заявлять независимость от компьютера.
7. Настроить и проверить приватную резервную копию SQLite, документов и vault. Код и данные держать под iva; интерфейсы gateway/engine слушают только 127.0.0.1.

Если серверный запуск не прошёл и серверный poller ещё не принимал новые сообщения, остановить его и восстановить локальный Telegram из неизменённой локальной базы. Если сервер уже записал новые данные, сначала остановить его, сверить и перенести актуальную серверную базу обратно; не запускать старую локальную копию параллельно.

Перенос файлового Codex-login на доверенный headless-сервер предусмотрен [официальной документацией OpenAI](https://learn.chatgpt.com/docs/auth#fallback-authenticate-locally-and-copy-your-auth-cache). Содержимое auth.json не выводить в чат/логи. Модели сохраняются: Luna low — главная Ива, Sol 6.1 medium — финансовый субагент.


**Накопительные цели, 20 октября 2026:** savings_preview/list/save/progress/status подключены к финансовому субагенту. Предварительный расчёт отделён от включения в бюджет по естественному согласию в следующем сообщении. Резервирование накоплений включено в family_forecast, фактических расходов не создаёт. Напоминания обслуживает существующий финансовый транспорт (за день и еженедельно при пропуске). Реальный Sol прошёл два шага расчёт → согласие на временной базе: одна цель, ноль операций, ноль кодов. Подробности: docs/SAVINGS.ru.md. Рабочие цели не созданы без товаров, цен и срока владельца.


**Тендерный субагент, 20 октября 2026:** native tool tender_agent → /tender-agent → Codex gpt-6.1-sol medium. Отдельная SQLite data/tender-agent.sqlite3: профиль, предпочтения с цитатой владельца, кэш ЕИС и результаты ходов. Исходные критерии в iva_integration/tender_profile.json; дальнейшие изменения записываются в SQLite. Поиск только по запросу, заявок/подписания нет. Код селекторов адаптирован из suer-tech/tender_agent_system, commit 513ebd38e335ed7e263d213fe365e837e56d0aee. Исторической витрины нет: history/contracts/contracts223/complaints читаются под конкретное исследование. ЕИС доступна прямым HTTPS; data/eis-ca.pem скачивается deploy/setup_eis_ca.py с официального gu-st.ru с обычной TLS-проверкой и подключается только к SSL-контексту клиента ЕИС. Системное доверие и VPN не менялись. Дополнительная зависимость requirements-tenders.txt. Чтение PDF-текста и DOCX поддержано; OCR сканов, произвольные архивы и XLSX-вложения в этом модуле пока не подключены. Страницы/документы ограничены по объёму, ограничения возвращаются модели. Ошибки не считаются пустой выдачей. Нативная цепочка проверена deploy/verify_tender_readback.py, реальный Sol с живым поиском — deploy/verify_tender_search.py. Отчёты в data/tender-*-verification.json. 271 общий тест и 7 целевых тестов; сервисы активны.


**Проверка обычного запроса поиска, 20 октября 2026:** deploy/verify_tender_user_request.py отправляет естественную просьбу через настоящий EveBridge в отдельную диагностическую сессию без доставки Telegram. Первый прогон выявил повторную делегацию после лимита чтений. Исправлены завершение Sol строгой схемой calls=[] при 8 чтениях и политика одного поискового поручения перед ответом Luna. Диагностические вызовы получают уникальные источники, чтобы новый поиск не возвращал старый кэш. Повторная цепочка завершилась за 124.8 с, 1 вызов субагента, ответ 1833 символа (без обрезки). Затем исправлено и отдельно проверено реальной Luna сохранение низкого приоритета закупки с 70% оценки опыта и известного отсутствия опыта владельца: deploy/verify_tender_summary.py, ответ 2075 символов, без новых вызовов. Отчёты data/tender-user-request-verification.json и data/tender-summary-verification.json. Найдены условные кандидаты, допуск не подтверждён без ТЗ/контрактов. 272 теста локально, 8 тендерных на VPS; сервисы активны.


## Прямое подключение подписки, 20 октября 2026

Рабочая Ива переведена на встроенный MODEL_PROVIDER=codex (Responses через подписку ChatGPT), основная модель gpt-6-luna / low. Финансовый и тендерный субагенты остаются gpt-6.1-sol / medium через Codex CLI. Gateway сохраняется для инструментов, финансовой авторизации, браузера и календаря.

Авторизация: local_finance/shared_auth.py читает единственный актуальный /home/iva/.codex/auth.json. Native-запрос и CLI-работник используют общий flock; CLI держит его до сохранения обновлённого login. Refresh выполняется через VPN, атомарно сохраняет новый токен в общий файл. Отдельной расходящейся копии refresh token нет. Реальное принудительное обновление и последующий вызов Ива → финансовый Sol прошли.

Настройки и инструкции воспроизводит deploy/configure_native.py; prepare_pilot.py сохраняет выбранного native-провайдера по data/iva-provider.json. Только chatgpt.com и auth.openai.com маршрутизируются через scoped undici.fetch proxy. Старый прямой family_finance исключён; используется finance_agent. Пустой ответ модели не помечается успешным и не вызывает повтор финансового действия.

Откат: root запускает /opt/iva/.venv/bin/python /opt/iva/deploy/rollback_native.py (через deploy/remote.py), после завершения активного пользовательского хода. Снимок кода и настроек: /opt/iva/data/native-cutover-backup (0700). Скрипт возвращает старый провайдер и пересобирает приложение. Текущая финансовая база, память, Telegram offset и обновлённый OAuth-login НЕ откатываются. safety-finance.sqlite3 предназначен только для аварийного исследования, не для обычного отката.

Проверка рабочего подключения: data/native-live-verification.json. Диагностическая сессия прочитала живой реестр через финансового субагента; Telegram API подтвердил sendMessage и sendVoice (Cartesia/Opus). Пользовательский режим озвучки не менялся. Все семь служб/таймеров активны; SQLite quick_check=ok. Это проверка фактической отправки API, не подтверждение прослушивания на телефоне. Регрессия: 272 прежних Python-теста, 3 новых проверки shared auth, 7 проверок durable-turn/пустого ответа. Полный отдельный приёмочный прогон до переключения описан в data/native-e2e-reports/acceptance-summary.json.


**Проверка памяти, календаря и тендеров после переключения:** deploy/verify_native_features.py выполнил реальные ходы рабочего движка: чтение памяти и календаря, один поиск Ива → tender_agent → Sol → ЕИС (110,5 с, три кандидата с ограничениями). Одно явно тестовое событие создано через Calendar; обычный работающий dispatcher доставил его в Telegram (2242), затем тест отменён. Это не проверка нового входящего Telegram-сообщения на создание события. Все прежние операции и события совпали со снимком перед переключением; все 13 прежних Markdown-файлов на месте. Дневник дополнился ответами диагностической сессии через pilot-transcript hook, поэтому строгая побайтовая проверка всех файлов возвращает false: файл не утрачен.

Ночной обработчик рабочего приложения с текущей авторизацией успешно создал сводку и карточку тестового проекта в временной копии vault (deploy/verify_native_memory_job.py); искусственные факты в память владельца не внесены. Регулярное время обработки — 04:00 Екатеринбурга.

Обнаружены старые утверждения CORE о неподключённом VPS/тендерах и ссылка без расширения .md. Обновлена текущая конфигурация, сохранён исторический профиль с примечанием и резервной копией data/memory-before-native-refresh. Новая сессия реально прочитала обновлённые файлы и подтвердила сервер и tender_agent. Отчёты: data/native-features-verification.json, data/native-memory-job-verification.json, data/native-memory-readback.json. Проверка не гарантирует безотказность будущих внешних сервисов и не подтверждает допуск найденных закупок без изучения документов.


**PDF без подписи — исправление авторизации 20 октября:** обновление Т-Банка из сообщения 608703915 отклонялось потому, что source/владелец были правильными, но caption был пуст. Добавлен отдельный ограниченный путь document_updates.apply_existing_debt_document: только прямое текущее вложение владельца, распознанный банк и единственный существующий долг/карта, сумма и дата проверяются по тексту документа. Меняются остаток/дата/примечания, сохраняются идентичность, график и параметры платежа. Новый долг, операция и отметка оплаты этим путём не создаются. Текст документа не даёт права на произвольные действия. Неоднозначная привязка требует уточнения. Повтор одного document_id возвращает сохранённый результат; обработка атомарна.

Рабочий gateway перезапущен с исправлением. По поручению владельца восстановлена ранее отклонённая запись: 148505.64 RUB на 2026-10-20. Сначала проверено на копии, затем применено к живой базе с резервной копией data/tbank-before-document-recovery.sqlite3. Проверены неизменность операций, идентичностей долгов и периодов/отметок оплаты. Отчёт data/tbank-document-recovery.json; скрипт deploy/recover_tbank_statement.py ограничен именно этой записью. Шесть тестов нового пути включают реальный Telegram-конверт без подписи через специалиста, повтор, чужое/пересланное вложение, неверную сумму/долг, старое сообщение и неоднозначный банк.


## Recovery verification — 2026-10-22

Live code and pre-repair backups: `/opt/iva/data/repair-20261022`. Financial recovery is idempotent; original operations preserved. Restricted collateral remains owned but excluded from available cash. Ozon document schedule attached to its existing debt (17 periods); no payment assumed paid. Owner confirmed both Dune films watched.

Memory dependency override: install `deploy/systemd/iva-memory-dependencies.conf` as `/etc/systemd/system/iva@memory.service.d/dependencies.conf`, then daemon-reload. Memory must survive gateway/engine restarts. Missed nightly run completed successfully.

Verification: 415 existing tests passed (17 skipped); focused repair tests and durable-turn question/answer replay passed. Live read-only Iva turn confirmed balances, schedule, calendar and film memory; real native question/answer cycle completed. Reports: `live-readback.json`, `live-question.json`, `recovery-report.json` under the repair directory. All six principal services active; SQLite quick_check OK. Tender research resumed after disposable cache cleanup; research completion is not implied. Windows financial snapshot was not changed.


## Задачи по расписанию — 22 октября 2026

Инструмент `scheduled_tasks` принимает прямые поручения владельца на создание/изменение расписания без кодов подтверждения. Поддерживаются интервал в минутах (от 5), числовой cron из пяти полей, часовой пояс, приостановка, возобновление, отмена, статус и история запусков. Это выполнение поручения, а обычные напоминания остаются в `personal_calendar`.

Исполнитель `iva@scheduler` запускается через `deploy/service.py scheduler`, включён в systemd. Состояние, курсоры почты, результаты и очередь уведомлений находятся в таблицах `scheduled_*` канонической SQLite, включаются в её существующую резервную копию. Поток heartbeat работает отдельно от длительной проверки. После простоя пропущенные интервалы объединяются в одну проверку; одновременно одна задача исполняется только одним worker. Повторяются только безопасные чтения, с задержкой при ошибке. Неопределённая доставка Telegram отмечается `uncertain` и не повторяется автоматически.

`mail_watch` проверяет новые UID в выбранной папке IMAP и фильтрует отправителей по OR-условиям `queries`. При создании проверяется реальное подключение и запоминается текущий UIDNEXT: старые письма не рассылаются. UIDVALIDITY сохраняется и проверяется. Новые письма читаются порциями по 100, курсор и все части уведомления сохраняются атомарно; накопившийся хвост обрабатывается следующим запуском через минуту. Письма не помечаются прочитанными. Этот режим не расходует модель на каждом запуске.

`agent` выполняет ограниченный цикл текущей модели Codex с инструментами чтения почты, публичного браузера, календаря и финансов. Не использует глобальную Telegram-сессию или её разрешение на запись. Запись расходов, отправка почты, покупки, shell и изменение расписаний из фоновой задачи отсутствуют на границе инструментов. Результат отправляется при выполнении условия; режим changed подавляет одинаковые сводки. Содержимое источников не является инструкцией владельца.

Установка: `deploy/install_scheduled_tasks.py /opt/iva/data/scheduled-release-20261022`. Резервная копия исходников и сборки находится в `before` этой папки. Проверка: `tests/test_scheduled_tasks.py` и `deploy/verify_scheduled_tasks.py`; отчёты и журналы рядом с release. Локальные службы по-прежнему не запускаются.

Проверено на рабочем сервере: полный набор до финальных уточнений — 430 тестов, 17 пропущены; окончательные 14 целевых тестов прошли отдельно. Реальная IMAP-проверка завершилась, тестовая задача через модель доставила уведомление Telegram (2468), затем обе тестовые задачи отменены. Рабочая Ива вызвала status/list и подтвердила живой worker без активных задач. Проверка Росэлторга не включалась: это был пример желаемой возможности. Финансовые операции при установке не изменились, SQLite quick_check OK. Отчёт: `data/scheduled-release-20261022/verification.json`.


## Telegram DNS recovery — 23 октября 2026

Telegram polling stopped receiving updates while tinyproxy stayed active and repeatedly failed getaddrinfo(api.telegram.org). Namespace DNS resolved normally; restarting only iva-telegram-proxy restored getMe and polling. No owner command was replayed manually: existing queued Telegram update continued through normal processing.

Installed root-owned `/usr/local/lib/iva/telegram_proxy_health.py` from `deploy/telegram_proxy_health.py` and `iva-telegram-proxy-health.timer` (each minute). The probe uses the private proxy without a bot token. Two consecutive failed checks trigger only a proxy restart, at most once per five minutes. VPN, engine, financial writes and sessions are not restarted or replayed. State: `/run/iva-telegram-proxy-health.json`. Two tests cover recovery, consecutive failures and cooldown; live probe healthy. Unit definitions are in `deploy/systemd`.

Health probe hardened the same day: checks failed/stale idle Telegram consumer after 180 seconds, restarts only idle/failed consumer, never processing/sending owner work. Shared application-deploy flock prevents intervention during releases; intentional inactive/stop states are respected. Proxy attempt cooldown persists before systemctl invocation, including restart failures. Service timeout 90 seconds covers bounded probes/recovery. Seven Linux tests passed (including deployment flock, in-flight financial work, failed restart cooldown); live probe reports healthy=true, consumer_state=ok. No financial command is replayed by this helper.


## OpenCode adapter recovery — 23 октября 2026

The OpenCode service was active and its DeepSeek model answered live probes, while recent primary routing attempts fell back to Codex after roughly 3-second HTTP failures. Session creation/prompt acceptance now have up to 8 seconds within the existing total deadline; reads remain bounded. An uncertain prompt acceptance is reconciled by reading the same session, never resubmitting. Failures store only stage, exception class, elapsed time and optional HTTP status in routing metrics, without provider response text or secrets.

Deployed opencode_service.py, opencode_cli.py and primary_engine.py with backups in data/opencode-repair-20261023/before; Telegram restarted only while no owner turn was processing. Five isolated tests passed. Live real-model probes returned reply for social input (2.52 seconds) and workflow for a financial request (2.41 seconds); the latter was routing-only and created no financial operation. Primary selection remains OpenCode, financial/tender execution unchanged.


## Active VPS migration — 23 октября 2026

Active host is now **5.63.152.247**, Ubuntu 26.04 x86_64, 2 GB RAM + 2 GB swap, 19 GB disk. Canonical app/database paths unchanged. Old 185.182.110.82 has all Iva services/timers and VPN stopped and disabled, with retained data for rollback. It must never run concurrently with the new owner of the bot token/VPN peer.

Copied full /opt/iva, /home/iva (Codex/OpenCode state, Playwright), Node 24.22.0 and global Codex 0.163.0-alpha.10.2, root-owned VPN/Codex/disk helpers, systemd units/drop-ins, sudo rules, VPN config, tinyproxy config and AppArmor local policy. Old and new iva UID/GID both 1000. A first live rsync was followed by a quiesced delta with deletion of stale files. Before runtime start, exact hash/count equality verified for three main databases and 134 document/vault/skill files. Raw password file and temporary pull keys removed. No credential contents printed.

Windows direct port 22 times out; added independent SSH listener iva-management-ssh on port 443, with same host keys and existing login policy. Verified direct key login without old-host relay. Standard port 22 and password login retained. deploy/remote.py selects current host via local deployment metadata and uses pinned target key/private key in ignored data.

441 tests passed (17 skipped). Live target checks: Telegram authorization, OpenCode DeepSeek response, real Iva→financial Sol readback, calendar/skills/schedules reads, Playwright Example Domain HTTP 200, Cartesia Ogg generation and local Russian STT. All working services enabled, Telegram polling healthy. Reports: /opt/iva/data/server-migration/. No live financial data written to Windows snapshot. Rollback requires stopping the target first, reconciling post-cutover data/auth and only then reactivating the old host.


## Детерминированный монитор ресурсов — 23 октября 2026

`iva-resource-monitor.timer` выполняет `iva-resource-monitor.service` каждые три минуты по системному календарю, с Persistent=true и автозагрузкой. Модель не вызывается, токены не расходуются. Скрипт `deploy/resource_monitor.py` установлен root:root 644 в `/usr/local/lib/iva/resource_monitor.py`; служба работает от iva, с NoNewPrivileges, ограничением памяти 64 МБ и read-only filesystem кроме собственных каталогов состояния.

Диск: предупреждение ниже max(10%, 1 ГБ), критический уровень ниже max(5%, 512 МБ). Память: MemAvailable, предупреждение ниже 256 МБ, критический уровень ниже 128 МБ. Swap показывается справочно. Предупреждение немедленно при обнаружении, при ухудшении уровня — новое; для продолжающегося предупреждения интервал 30 минут, для критического — 10 минут. После восстановления одно сообщение. Гистерезис подавляет колебания у порога. Ни удаления файлов, ни остановки процессов нет.

Состояние хранится в `/var/lib/iva-resource-monitor/state.json`, дублируется в `/run/iva-resource-monitor/state.json`: при полном диске уведомление всё ещё отправляется, а runtime-копия подавляет повторы. При неподтверждённой доставке повторяется попытка на следующей проверке. Telegram не имеет idempotency key для sendMessage, поэтому потеря HTTP-подтверждения может дать повторное предупреждение; это не запускает финансовые действия. Секреты не выводятся. Восьми целевым тестам сопутствовала реальная тестовая доставка с явно искусственными значениями (message_id 2558); рабочий замер нормальный, состояние тестом не подменялось. Отчёт `data/resource-monitor-verification.json`.

## MAX на общем сервере — 23 октября 2026

Установлены 37 актуальных runtime-файлов production `/root/max130` сервера 151.248.126.26, SHA256 каждого совпадает с источником. Копия в `/opt/max`, совместимый symlink `/root/max130`. Python 3.12.14 и Playwright 1.48 с Chromium работают в отдельном venv. Источник 151 не изменялся. Профили, авторизации, кампании и ключи источника не копировались: владелец вошёл в новый профиль `/opt/max/state/browser` самостоятельно.

MAX: `max130-web`, `max130-manual`, `max130-worker`, `max130-ads`, `max130-broadcast`, Xvfb/VNC/noVNC/fluxbox и таймер проверки продолжения очереди. Автоматический запуск новых заданий отключён; очередь новая и пустая. Для генерации через RouterAI потребуется собственная настройка ключа в приватной панели. Функциональная отправка сообщений не проверялась и не выполнялась.

`max.slice`: CPUQuota 60%, CPUWeight/IOWeight 20, MemoryHigh 750M, MemoryMax 950M, swap до 256M, TasksMax 192, Nice 10, OOMScoreAdjust 500. Службам Ивы назначены CPUWeight/IOWeight 500, MemoryLow 128M и OOMScoreAdjust -250, сохранены drop-in конфигурации. Приложение Ивы не останавливалось. Жёсткий предел MAX защищает Иву, но тяжёлая страница может завершить MAX по лимиту; замер чтения чатов не гарантирует работу любой массовой кампании.

Production Python-файлы не менялись. Дополнительный `runtime-guard/sitecustomize.py` через PYTHONPATH удерживает системный flock: один контроллер Chromium на профиль и отказ при MemAvailable ниже 384MB (exit 75, без автоматического цикла рестартов). Проверено: запуск второго worker при активном manual отказал с exit 75, без рестартов. Для запуска очереди сначала остановить ручной браузер через панель; блокировку не обходить.

Панель и VNC доступны только через SSH: nginx 127.0.0.1:18080, Flask 127.0.0.1:15000, noVNC 127.0.0.1:16080 и VNC 127.0.0.1:5901. Локальный `python deploy/max_ui_tunnel.py` открывает http://127.0.0.1:15080/ через SSH443. Туннель нужен для управления, но не для работы заданий на сервере. WebSocket RFB и HTTP проверены. Не открывать панель/VNC публично без отдельной защиты.

Установщик: `deploy/install_max_runtime.py`, guard: `deploy/max_browser_guard.py`. Манифест и отчёты `/opt/max-deploy/`; исходный манифест локально `data/max-install-20261023/source-manifest.json`. До входа 120-секундный замер: пик max.slice 739MB, минимум MemAvailable 821MB, CPU в среднем 10.6%, OOM-kill 0. После входа отдельная проверка читает только sidebar и сохраняет агрегаты (без названий чатов) в `/opt/max-deploy/authenticated-load.json`. Проверка отклика Ивы во время этого чтения: 3.35 секунды, ответ получен; отчёт `/opt/iva/data/max-load-response.json`.

Итог после QR-входа: авторизация подтверждена, sidebar прочитан/прокручен, обнаружено 146 чатов; никаких отправок/вступлений. Замер 114.7 секунды: CPU в среднем 23.5% одного ядра, пик max.slice 750MB, минимум MemAvailable 734MB, memory.events oom/oom_kill/max = 0. После проверки ручной браузер восстановлен; службы gateway/engine/telegram/memory/scheduler активны. Эти числа относятся к чтению интерфейса, не к полной кампании.

## Codex для MAX — 23 октября 2026

Владелец выбрал: тексты и просмотр изображений — через Codex, генерация новых изображений — через RouterAI с отдельным новым ключом. OpenCode первичного маршрутизатора Ивы не меняется. Создан отдельный `max-codex.service` (User=iva, max.slice, Nice=10), Unix socket `/run/max-codex/completions.sock`, каталог 0700. MAX обращается к нему через `max_llm.py`; секрет авторизации в MAX не копируется.

Сервис использует существующий `local_finance.codex_cli.Codex(role=orchestrator)` с `/usr/local/bin/iva-codex` и общей безопасной блокировкой обновления авторизации. Текущий профиль — gpt-6-luna, low. Временные контексты не содержат памяти/переписки Ивы; инструменты, shell, MCP, запись файлов и web отключены существующим адаптером, ответ — JSON. Поддержаны только встроенные изображения data:image, нормализация PNG/JPEG/WebP/GIF до PNG, ограничение объёма/размеров. Внешние URL сервер не скачивает. Запросы исполняются последовательно; CLI timeout 100 секунд. Ошибка не переключает на RouterAI/OpenCode. Общие лимиты подписки Codex сохраняются, авторизация общая с Ивой.

Патч установщика `deploy/install_max_codex.py`: текстовые и визуальные вызовы `ad_classification`, `broadcast`, `pin_vision` направлены через явный транспорт; в UI показан Codex и отдельно состояние ключа RouterAI. Исходная точная production-копия теперь имеет документированные изменения в шести файлах. До изменения сохранены проверенные по первоначальным SHA256 файлы и существующие конфигурации в `/opt/max-deploy/before-codex`. `load_routerai_key` и `max_llm.routerai_post` сохраняют отдельный коннектор RouterAI; новый ключ не был предоставлен/скопирован. Реальная генерация изображений в исходной версии не реализована; сохранение ключа ещё не создаёт такой инструмент.

Откат транспорта: остановить max130-web и max-codex, вернуть шесть сохранённых Python-файлов (ad_classification.py, broadcast.py, pin_vision.py, ad_routes.py, ad_ui.py, broadcast_ui.py) и существовавшие конфиги из before-codex, disable max-codex, затем запустить max130-web. Профиль браузера, очереди и данные Ивы не менять. Не запускать исходный install_max_runtime.py поверх адаптера без проверки изменений.

Проверено после подключения: 7 локальных целевых тестов (текст/роли, запрет URL и tool-ролей, нормализация WebP, повреждённое изображение, JSON, отсутствие платного fallback, сохранность коннектора RouterAI). На сервере через реальные функции MAX: rewrite сохранил цену/контакт (5.55с), pin_vision прочитал русские правила и контрольное число 4827 (6.41с), классификация вернула корректный allowed. Веб-кнопка /ads/test также вернула «Codex подключён». Отчёт `/opt/max-deploy/codex-verification.json`; sends=0. Ива и ручной браузер работают; OOM/max events=0. Генерация новых изображений пока не реализована и новым ключом не проверялась.


## Автоотклик AI и совместный HTTPS/SSH — 23 октября 2026

На действующем VPS 5.63.152.247 рядом с Ивой установлена отдельная серверная часть Автоотклик AI: /opt/hh-extens/current, пользователь hhapp, PostgreSQL hh_extens, hh-extens.service (127.0.0.1:18765), hh-extens-backup.timer. Приложение использует RouterAI https://routerai.ru/api/v1 и выбранную владельцем модель deepseek/deepseek-v4.1-flash-1017, прямой маршрут. Приватная конфигурация /etc/hh-extens/backend.env, отдельные сгенерированные JWT/admin keys. Canonical finance Ивы не изменялся, старый VPS не включался.

Порт 443 теперь принимает Nginx stream ssl_preread: TLS направляется на Nginx HTTPS 127.0.0.1:18443, SSH — на 127.0.0.1:22444 (iva-management-ssh). deploy/remote.py продолжает подключаться на 443 без изменений. Дополнительный SSH 22443 (iva-management-ssh-fallback) проверен независимо; 22 сохранён. Не заменять этот listener обычным HTTPS/SSH при следующих релизах. Конфиги: /etc/nginx/streams-enabled/https-ssh.conf, /etc/nginx/sites-available/hh-extens, stream include в /etc/nginx/nginx.conf. До переключения создан /var/backups/hh-extens-network-20261023/rollback.sh; автоматический rollback-таймер отменён после успешного нового SSH-входа.

Для api.aixmode.ru выпущен Let's Encrypt certificate до 21 января 2027, продление через certbot.timer; ACME webroot /var/www/hh-extens-acme на порту 80, reload hook в /etc/letsencrypt/renewal-hooks/deploy/reload-nginx. DNS изменён владельцем в REG.RU. Backend/database/config backups независимы от backup Ивы: /var/backups/hh-extens, ежедневный запуск около 04:10 Екатеринбург. Проверено восстановление dump в отдельную PostgreSQL.

Документация приложения и ограничения: E:/Projects/hh_extens/docs/ARCHITECTURE-DEPLOYMENT.ru.md и /opt/hh-extens/current/docs/ARCHITECTURE-DEPLOYMENT.ru.md. SMTP пока не настроен, OAuth-вход hh.ru/настоящая YooKassa-оплата/настоящая отправка отклика не проверены. Продвижение в Threads записано в docs/THREADS-AUTOOTKLIK.ru.md, не запущено.

## Исправление ложного отказа фоновой почты — 23 октября 2026

Задача 42673be348a54cd4 (каждые 180 минут) возвращала finish «почтовый инструмент недоступен», не вызывая read_tool. IMAP при этом исправен. В scheduled_worker.run_agent добавлено явное разграничение отключённых нативных CLI tools и внешнего JSON-маршрутизатора mail/web/calendar/finance. Первый finish без фактической проверки возвращается модели на один ограниченный повтор; повторный отказ и отсутствие успешных результатов дают ok=false, backoff планировщика и уведомление об ошибке вместо ложного done. В результатах сохраняются checked_tools/tool_calls без содержимого писем. Сброс payload исключает перенос аргументов предыдущего шага при ошибке JSON.

17 целевых тестов прошли, включая восстановление после ложного отказа, отказ без вызовов и неуспешный инструмент. Развёрнут только scheduled_worker.py, перезапущен только iva@scheduler; расписание и финансовые данные не менялись. Живой диагностический запуск с тем же поручением и старым отказом: mail list + два mail read успешно, 51.5с. Письма оставлены непрочитанными; диагностическое уведомление не отправлялось. Отчёт /opt/iva/data/mail-routing-repair-20261023/live-verification.json; исходный worker сохранён там же как scheduled_worker-before.py. Результат теста подтверждает доступ/маршрутизацию/чтение; устойчивый UID-курсор предусмотрен в mail_watch, а эта agent-задача пока сравнивает новые письма с предыдущей текстовой сводкой.


## Защита MAX от блокировки одной группой — 24 октября 2026

Внедрена адаптация broadcast_deferral_20261024 из проекта MAX 151, без замены действующего расписания, Codex rewrite/prepare и кооперативных browser_turn hooks. Изменены шесть модулей: broadcast.py, broadcast_worker.py, broadcast_growth.py, broadcast_autorun.py, broadcast_ui.py, broadcast_join.py. В join дополнительно перенесено распознавание подтверждённого канала без права публикации (локальный пропуск вместо остановки очереди).

Состояние deferred отделено от uncertain/sending и ожидания вступления. Поля deliveries: open_failures INTEGER NOT NULL DEFAULT 0, open_retry_at REAL, open_last_error TEXT. Ошибка открытия при исправном авторизованном MAX откладывает только получателя: повторы через минимум 5 и 30 минут, после третьей ошибки только ручная проверка. Счётчик устойчив к перезапуску/исключению из категории и сбрасывается при успешном подтверждённом открытии. Повтор возвращается в ready с обычным слотом лишь после основной ready/pending очереди. Таймер ждёт срока/рабочего окна без запуска браузера каждую минуту. Глобальная потеря авторизации/загрузки останавливает очередь; auth overlay имеет приоритет над устаревшим sidebar, содержимое сообщений не имитирует login. Sent/uncertain не переигрываются, Request to join не нажимается повторно. UI показывает отдельное «Отложено», причину, попытки и срок/ручную проверку, без новых кнопок верхней панели.

Установщик deploy/install_max_deferral.py проверяет SHA256 текущих шести модулей и стадии, отказывается работать при активном worker, временно останавливает web и autorun timer, добавляет только три поля SQLite и сверяет все прежние значения/кампании/launch markers/состояния browser services. Резервная копия кода и MAX SQLite: /opt/max-deploy/deferral-20261024/before. Манифест/отчёт установки/тестов рядом. На момент установки база MAX: 0 кампаний, 0 получателей; никаких новых кампаний/отправок не создавалось. Финансовая база Ивы не затрагивалась. Откат: остановить MAX workers на безопасной границе, вернуть шесть сохранённых файлов, перезапустить web; добавочные поля совместимы со старым кодом, не откатывать SQLite поверх новых отправок.

Проверки: 23 целевых новых теста, включая подменённый DOM в локальном headless Chromium; на рабочем сервере 19 pure/fake-sender тестов прошли без реального браузера и отправок, JS прошёл node --check. Более широкая сверка: 115 совместимых тестов прошли; 17 старых тестовых фикстур одинаково падают на исходном и новом коде (старые source-selection и main expectations), их несоответствия зафиксированы отдельно, не названы исправленными. Из 13 дополнительных проверок readonly-каналов 12 прошли; одна требует отсутствовавшее до патча сохранение title в cleanup main (не переносилось, main сохранён). Локальные отчёты data/max-deferral-20261024, серверные /opt/max-deploy/deferral-20261024. Ива, max-codex и web активны после установки; браузерные службы не запускались.


## Восстановление Ивы, почты и Threads — 24 октября 2026

Актуальная установка Threads включает непрерывные hourly/development workers, таймеры, мониторинг inbox и суточный отчёт; старые записи выше о неподключённом издателе относятся к историческому этапу. Почта переведена на UID-курсор с чтением тел кодом; подтверждены четыре доставки и повторная проверка без дублей. Добавлены защита сессий engine-generation, безопасные журналы и iva-runtime-monitor.timer каждую минуту. OpenCode в последней живой проверке превысил срок, реальный fallback Codex проверен. Редактор Threads проверен на сервере до отправки; новые публикации считать подтверждёнными только по sent и проверенному permalink. Лимиты и очередь MAX/Threads, VPN/прокси, финансовые операции и HTTPS/SSH не менялись. Подробности, границы проверок и откат: docs/RUNTIME-REPAIR-20261024.ru.md; доказательства /opt/iva/data/repair-20261024/.


## Читаемое оформление рекламы MAX — 25 октября 2026

В действующий broadcast.py перенесены только уточнённый PROMPT и вызов format_body после проверки точного начала и до facts/digest/уникальности. Добавлены broadcast_formatting.py и broadcast_format_repair.py. Codex provider_post, все функции кроме rewrite, график, deferral-политика и browser_turn hooks сохранены. Formatter добавляет только декоративные эмодзи и переносы, защищает контакты/даты/сокращения, идемпотентен и не обрезает длинный текст. Цифровые маркеры не добавляются. Новое оформление сверх лимита возвращает генератору причину для повторной попытки.

repair_prepared допускает только текущую кампанию, только ready/deferred/waiting_join с подготовленным text и sent_at=NULL. Сначала проверяет весь проход, затем CAS-транзакция меняет только text. Digest, факты, персонализация и лимит сохраняются; sent/sending/uncertain/архив защищены. Дополнительно к исходной реализации запретили явный ID архивной кампании и смену текущей кампании между подготовкой и записью. Установщик deploy/install_max_format.py проверяет хеши и безопасную неактивность MAX workers, сохраняет код/SQLite в /opt/max-deploy/format-20261025/before, делает dry_run на копии, сверяет все нетекстовые поля, кампании, launch markers и состояния browser services. Панель/таймер возобновляются в исходном режиме, браузер не запускается.

Рабочая база в момент установки не содержит кампаний и получателей: dry_run considered/changed=0, applied=0. Поэтому здесь не исправлялись 168 сообщений из другого сервера. Новые тексты будут оформляться при генерации. 29 локальных тестов прошли (включая две UI-проверки на подставном DOM и два дополнительных archive/CAS-теста). На установленном сервере 46 pure/fake-sender проверок оформления и deferral прошли, без реального браузера и отправок. Отчёты/манифест/резервная копия: /opt/max-deploy/format-20261025; локальная стадия data/max-format-20261025. Ива, max-codex и панель активны. Финансовая база не менялась. Откат: на безопасной границе вернуть сохранённый broadcast.py, оставить добавленные модули; SQLite поверх более новых отправок не восстанавливать.


## Jev для релевантности Автоотклика — 25 октября 2026

Backend Автоотклика переключён на release `/opt/hh-extens/releases/20261025-jev-072611`. В приватном `/etc/hh-extens/backend.env` добавлено `AI_RELEVANCE_MODEL=typesafe/jev-1.14` для `/decisions`; письма по-прежнему генерирует `AI_MODEL=deepseek/deepseek-v4.1-flash-1017`. В журнале записываются обе модели по этапам. Формула внутренних списаний/схема БД/расширение не менялись. 32 изолированных теста прошли в VPS venv; три настоящих RouterAI примера через временную SQLite подтвердили принятие подходящей вакансии с письмом и отклонение двух неподходящих без письма. Production-балансы и финансовая SQLite Ивы не затрагивались. Перезапущен только hh-extens после проверки отсутствия активных backend-соединений; Ива активна, внешний HTTPS healthz/privacy доступны. Nginx-мультиплексор и SSH сохранены.

Отчёты/закрытый backup env: `/opt/hh-extens/changes/20261025-jev-072611/`; предыдущий release `/opt/hh-extens/releases/20261023-102326` сохранён. Подробности/откат: `E:/Projects/hh_extens/docs/ARCHITECTURE-DEPLOYMENT.ru.md`. Будущие установки должны сохранять отдельную модель релевантности; смена AI_MODEL теперь влияет на письма, а явно заданный AI_RELEVANCE_MODEL имеет приоритет для оценки.

## Claude Haiku для писем Автоотклика — 25 октября 2026

По запросу владельца после установки Jev изменена только настройка `AI_MODEL=anthropic/claude-haiku-5.5` в приватном backend.env; `AI_RELEVANCE_MODEL=typesafe/jev-1.14` сохранена. Реальный контрольный вызов установленного generate_cover с вымышленными документами вернул непустое письмо от указанной Haiku за 3,494 секунды. Рабочая база и HH-отклики не использовались. Перезапущен только hh-extens после проверки отсутствия активных соединений; окружение процесса подтвердило обе модели, внешний HTTPS /healthz вернул ok, службы Ивы активны. Release и расширение прежние. Отчёты и root-only backup env: `/opt/hh-extens/changes/20261025-haiku-55/`. Для отката вернуть только AI_MODEL на DeepSeek и перезапустить hh-extens, не откатывая базу. Подробности в документации hh_extens.

## Threads: старые ветки и внутренние исправления — 25 октября 2026

Сняты ограничения 1/2 часа у обеих кампаний; рекомендации сочетаются с Top/Recent. Тематическая уместность, защита sent/uncertain, прежняя частота и общий браузерный lock сохранены. Технические ошибки/восстановления больше не рассылаются владельцу; подтверждённые инциденты идут в отдельную очередь Codex. Новые DM, предложения и ежедневный отчёт сохранены. Добавлены activity с дедлайном этапа, ограниченные повторы чтения Messages и история причин. Автоматический ремонт двух модулей чтения проходит regression до/после и фиксированные тесты в bwrap; установка только при idle + owner.lock, результат требует последующей живой проверки. Для остальных компонентов хранится diagnosis/review_required. 108 серверных тестов, реальная Codex-проверка на искусственно сломанной копии и успешное чтение трёх живых папок подтверждены. Подробности и откат: docs/THREADS-POLICY-REPAIR-20261025.ru.md; backup/доказательства /opt/iva/data/threads-policy-20261025/. Не возвращать прежний технический Telegram-спам старым установщиком.


## Исследование неудовлетворённого спроса — 25 октября 2026

Задача 2f9611a667cc493a сохранена на ежечасном cron и переведена в demand_research.
Новый native tool status/report/hypotheses/sources показывает реальный корпус
доказательств. Сбор native web + публичный HTTP/PDF работает без Chromium;
анализ отдельный, инструменты отключены. Финансовый Codex-адаптер не менялся.
Контрольные точки, точные цитаты, независимые покупательские подтверждения,
контраргументы и тест-карточки находятся в data/demand-research.sqlite3.
Экспорт идёт в data/demand-research/cards и vault/cards/ideas/demand-research;
изменённые вручную карточки не перезаписываются. Состояние экспорта видно в tool.
Новая БД и карточки включены в deploy/backup.py и проверены восстановлением.

Установка/backup: /opt/iva/data/demand-release-20261025-r2; методика, ограничения,
живые проверки и откат — docs/DEMAND-RESEARCH.ru.md. Не возвращать исследовательскую
задачу к старому generic agent с Bing, не включать shell/MCP в research adapter,
не направлять её записи в Windows финансовый snapshot.

## Хранение исследовательских материалов — 25 октября 2026

Установлена политика 14 дней полных промежуточных материалов и последующего
сохранения компактных семантических архивов. Гипотезы, доказательства, вопросы,
активные контрольные точки и ручные файлы не удаляются. Старые дубли результатов
расписания сокращаются только в scheduled_runs.result, финансовые данные и outbox
сохраняются. Root-owned iva-demand-retention.timer проверяет возможность работы
ежечасно в :25 UTC; фактическая очистка ежедневно, с продолжением ограниченных
партий при накопленном остатке. Нормальная аренда исследователя и общие locks
исключают прерывание исследования и конкуренцию с backup. Новые циклы требуют
1 ГиБ свободного места, текущие не останавливаются. Добавлены проверки свободного
места и атомарности ежедневных резервных копий.

Release `/opt/iva/data/demand-release-20261025-retention`, установка после окончания
цикла в 14:18:12 UTC, первый успешный service run в 14:19:39 UTC. 386 серверных
тестов прошли; проверка на искусственно состаренных копиях настоящих баз сократила
5 циклов, 59 тел источников и 160 результатов расписания на 2 285 839 логических
байт. Гипотезы/доказательства/курсор/финансовые таблицы сохранились, архивные API
и повторный запуск проверены. В production пока нет материалов старше 14 дней:
первый результат no_old_material, удалено 0. Все основные службы активны.

Состояние `data/demand-storage/status.json`, единичный сменяемый rollback-архив
`data/demand-storage/before-retention.tar.gz` (создаётся перед реальной очисткой).
Не включать его в ежедневные backup; никогда не восстанавливать старую финансовую
копию поверх новых записей. Подробные расчёты, правила сохранения и откат:
`docs/DEMAND-STORAGE.ru.md`.

## Разделение направлений и чтение через VPN — 25 октября 2026

Установлен выпуск `/opt/iva/data/demand-release-20261025-scope-network-r2`.
Новые циклы имеют `plan.scope`: ПО отдельно от товаров/услуг для MAX; связанные
углубления разрешены. Отдельная проверка направления действует до допуска
плана, дочерних поручений и гипотез. Неточная цитата возвращается на исправление
с конкретной причиной, максимум два раза; исходный отчёт и завершённые соседние
поручения сохраняются. Исправленная цитата повторно проверяется дословно и по
смыслу, в том числе при итоговом синтезе.

Добавлен `iva-research-fetch.service` с root-owned helper
`/usr/local/lib/iva-research-fetch/server.py`, User=iva и существующим namespace
`iva-codex`. Приватный сокет `/run/iva-research-fetch/fetch.sock`; нет TCP listener.
Точный домен после распознанного геоотказа и успешного повтора сохраняется в
`data/research-network/routes.json` на семь дней. Неудачный повтор имеет часовой
cooldown, максимум 256 доменов. Общие маршруты, SSH/HTTPS, VPN-ключи и службы
Telegram не менялись. Обычный 403/CAPTCHA/429/вход не запускают переключение.

470 серверных тестов прошли; native-проверки подтвердили отклонение старого
смешанного плана, допуск связанного ПО и исправление настоящей переведённой
цитаты после замечания о 27 словах при лимите 25. Живой RPC через VPN и прямое
чтение Хабра успешны. Геоотказ в проверке повтора искусственный, последующие
VPN-запросы реальные; production-кэш не менялся. Reddit дал обычный HTTP 403.
История исследований сохранена; первый полный плановый цикл новой версии ещё
не проверен на момент приёмки 15:36 UTC. Следующий запуск: 16:00 UTC, 21:00
Asia/Yekaterinburg. Все основные службы и таймеры активны.

Отчёт: `docs/DEMAND-DEPLOYMENT-20261025-SCOPE.ru.md`; машинная приёмка
`data/demand-release-20261025-scope-network-r2/final-verification.json`.


## 25.10.2026: сообщения о подтверждённых исправлениях

Установлен `iva-recovery-notices.timer`: раз в минуту читает доказательства
восстановления почты, исследований, Threads и OpenCode. Сообщает владельцу кратко
после положительной проверки, без старой истории и повторов попыток. Собственная
база `data/recovery-notices/notices.sqlite3` включена в резервное копирование.
Неопределённая доставка Telegram не повторяется автоматически. Ива видит
результаты через `browser_control status.recovery_notices`. Описание критериев:
[RECOVERY-NOTICES.ru.md](RECOVERY-NOTICES.ru.md).

Активный выпуск `/opt/iva/data/recovery-notices-release-20261025-r3`, файл
`installation.json` учитывает отдельную поправку доступа к SQLite WAL/SHM.
Основной файл базы ремонтов Threads остаётся read-only. Root-проверка OpenCode
публикует только очищенные подтверждения в отдельный каталог
`/var/lib/iva-opencode-health-events` с правами root:iva0750/0640.

`final-verification.json` подтвердил все 24 файла/служебных unit, работоспособность
сборщиков, gateway, основных служб и реальную доставку сообщения о включении.
Старые события не рассылались; тестовые ошибки на продуктивных задачах не создавались.

При установке выявлена зависимость Telegram от gateway: перезапуск gateway
также ждёт штатного выхода Telegram из длинного опроса. Установщик r3 сначала
приостанавливает Telegram на свободной границе, освобождает краткую блокировку
допуска задач, дожидается выхода, затем перезапускает gateway и восстанавливает
Telegram. Активные исследования не прерывать. Выпуски r1/r2 откатились; их
материалы сохранены для диагностики, не переустанавливать их поверх r3.


## MAX: паузы между партиями 2–5 минут — 26 октября 2026

В /opt/max установлена адаптация broadcast_pacing_20261026. Три файла: broadcast_schedule.py, broadcast.py, broadcast_ui.py. Текущая копия имела policy v2; внедрена v4 с безопасной _migrate_schedule: пересчитываются только неначатые ready-партии, накопленные одиночные объединяются, идентичность/размер/порядок/внутренние интервалы начатых партий сохраняются. ensure_schedule для добавлений и fallback prepare_next_delivery используют общие MIN_BATCH_PAUSE_MINUTES=2 и MAX_BATCH_PAUSE_MINUTES=5; соседние межпартийные паузы различаются. Внутренние короткие интервалы сохраняют прежний диапазон max(MIN_SPACING, delay_seconds*uniform(.6,1.4)). График 09:00–22:00 Asia/Yekaterinburg, партии до 8, дневной лимит сохранены.

В build_plan три бюджетных выражения резервируют PLANNING_OVERHEAD_SECONDS=30 на чат; операционный BROWSER_OVERHEAD_SECONDS=15 используется при retime. Это запас планирования, не обязательная добавленная задержка. 100 стратегий по 500 чатов со старта 09:00 укладываются в окно при расчётной обработке 30 секунд на чат; SQLite runtime на fake clock проверен для 15/30 секунд, включая seed0. Это не гарантия срока при фактических зависаниях, локальных ошибках или повторах.

Codex provider_post, rewrite/format_body, digest, все несвязанные со временем функции, browser_turn hooks и политики deferral/ACK сохранены. OPEN_RETRY_DELAYS=(300,1800) и autorun retry300 не менялись. В текущем UI два существовавших пояснения 5–30 заменены на 2–5; кнопки не добавлялись. Из кода-предка перенесены только планировщик и три пути расчёта расписания, не полный broadcast.py.

Установщик deploy/install_max_pacing.py сверяет свежие хеши, ждёт idle MAX workers, сохраняет код/SQLite в /opt/max-deploy/pacing-20261026/before, предварительно мигрирует отдельную dry-run копию и сверяет все тексты/digest/состояния/историю/launch markers/состав/начатые партии. База в момент установки: campaign=null, phase=empty, replanned_ready=0. Новых кампаний/реальных браузеров/отправок не запускалось; финансовые данные и исследования Ивы не затрагивались. Таймер автопродолжения и web возвращены в исходный режим, остальные browser services не менялись.

65 локальных тестов прошли; один старый deferral-тест адаптирован только в ожидании обычной межпартийной паузы с 300–1800 на 120–300, сохранив проверки 5/30-минутных retry. Дополнительно проверены migration v2→v4 (история/начатая партия и 12 одиночных). На установленном сервере 59 pure/fake-sender тестов прошли, без Chromium и реальных сообщений; JS node --check успешен. Отчёты, манифест, dry-run, backup и server-tests.json: /opt/max-deploy/pacing-20261026; локально data/max-pacing-20261026. Ива, max-codex и панель активны. Откат после появления реальных планов делать на безопасной границе с совместимым reader; не восстанавливать старую SQLite поверх новых отправок.


## MAX: ошибки генерации не блокируют готовую очередь — 26 октября 2026

Установлена адаптация broadcast_generation_20261026 в пять текущих модулей: broadcast.py, broadcast_worker.py, broadcast_autorun.py, broadcast_routes.py, broadcast_ui.py. Сохранён явный Codex provider_post (не RouterAI), format_body, выбранное начало, facts/digest и персонализация. Каждый повтор rewrite получает rejected_text до 4000 символов, retry_reason, конкретную retry_instruction и отличающуюся variation_strategy; после трёх невалидных ответов отдельный TextGenerationError(ProviderError).

Добавлены deliveries.generation_failures DEFAULT0/generation_retry_at/generation_last_error и campaigns.generation_retry_at/generation_error. Неудачный проход оставляет pending; повторы 10/30 минут, после третьего только существующая ручная «Доделать тексты». force применяется явной подготовкой и сбрасывает только pending-блокировки; stopped проверяется до сброса. HTTP/transport/provider error включает общий cooldown5 минут без расходования локальных попыток каждой группы. generation_status сообщает due/waiting/manual и следующую дату/причину. Истёкшие opening retries не блокируются pending, чья генерация сейчас отложена.

refresh_campaign вообще не вызывает генерацию/проверку ключа при наличии ready. Когда ready нет, готовит до 8 доступных pending с continue_on_error. Send route и существующая UI-кнопка допускают частично готовую очередь независимо от auto_max и ключа ИИ. Autorun ждёт только действительно due генерации и сохраняет stop/generation намерения; ожидание cooldown не запускает браузер каждую минуту. В UI добавлены сроки/причины в прежние блоки, без новых кнопок. Исходные worker.send_campaign и все ACK/фактические отправки, pacing v4/2–5 минут, open retry5/30 и браузерные yield-границы не менялись (AST/маркерная сверка).

Установщик deploy/install_max_generation.py проверяет свежие хеши/idle workers, сохраняет пять файлов и MAX SQLite, временно останавливает web/autorun timer, выполняет только additive schema и сверяет все прежние поля всех кампаний/доставок, launch markers и browser service состояния. Резервная копия/манифест/отчёт: /opt/max-deploy/generation-20261026/before и installation.json. На этом сервере phase=empty, 0 кампаний/получателей; очередь 80 ready/283 pending на чужой площадке не относится к этому экземпляру. Новых кампаний и отправок нет. Codex/RouterAI ключи, профили, финансовая база, исследования и настройки Ивы не менялись.

86 локальных тестов прошли: 21 focused/API/UI plus pacing/formatting/deferral, включая 100 стратегий по500 и ручной stop. 79 серверных pure/fake-sender тестов прошли без Chromium, реальных модельных запросов и отправок; node --check успешен. Логи/JSON: /opt/max-deploy/generation-20261026; локально data/max-generation-20261026. MAX web/max-codex и Ива активны. Откат проводить только на безопасной границе с сохранением текущих backoff/stop; не откатывать SQLite поверх более новых отправок.
