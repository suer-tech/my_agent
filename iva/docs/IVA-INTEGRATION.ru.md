# Проверка интеграции Ива

Обновление: для текущего локального Windows-запуска выбран именно Codex CLI. Штатный `codex` в Ива — OAuth Responses API, не CLI. Поэтому реализован отдельный совместимый адаптер, использующий прежнее финансовое ядро. См. `LOCAL-WINDOWS.ru.md`; исходный плагин сохранён для полноценного Ива позднее.

Исходники прочитаны из официального [репозитория smixs/iva-agent](https://github.com/smixs/iva-agent), локальная исследовательская копия `upstream/iva-agent` (исключена из проекта через `.gitignore`). Проверенный commit: `a9dba20c46bf97b1eee4b292bddc6407e1e5da3b`, версия package.json `0.5.4`. Описание продукта: [iva-agent.com](https://iva-agent.com/).

| Возможность | Проверенный исходник | Вывод для модуля |
|---|---|---|
| Голосовой ввод | `agent/transcribe.ts`, `agent/lib/telegram-media.ts`, `agent/channels/telegram.ts` | Deepgram nova-3; язык из `DEEPGRAM_LANGUAGE`, ключ `DEEPGRAM_API_KEY`; по умолчанию multi. Ввод — транскрипция, а не доказанная сумма операции. |
| Фото | `agent/vision.ts`, Telegram media pipeline | Vision использует модель провайдера. Подходящий вход для кандидата чека; точность реальных чеков ещё не испытана. |
| Расширения | `docs/plugins.md`, `agent/lib/plugin-reader.ts`, `plugin-skills.ts` | `plugin.json` + `skills/<name>/SKILL.md` + `mcp.json`; stdio обслуживает systemd proxy Ива с локальным bearer. |
| Постоянные данные | `docs/plugins.md` | `${PLUGIN_DATA}` соответствует `data/plugin-data/family-finance/`; база переживает обновление/удаление плагина. |
| Telegram | `docs/deploy.md`, `scripts/telegram-poll.mjs` | Единственный poller Ива; worker модуля выполняет только исходящие sendMessage/sendVoice. |
| Авторизация | `.env.example` | `TELEGRAM_ALLOWED_USER_IDS` задаёт разрешённого пользователя. Для данного этапа использовать только один приватный диалог с ботом; отключить добавление бота в группы в BotFather. |
| Напоминания | `docs/plugins.md`, `docs/deploy.md` | У плагина нет собственных eve schedules; финансовый worker запускается независимым systemd timer. Не дублировать его платежи обычными напоминаниями Ива. |
| Голосовой ответ | Поиск `sendVoice`, `sendAudio`, TTS в авторском коде | Готового исходящего TTS-контура не найдено. Есть отдельный адаптер проекта; движок и автоматический вызов из turn lifecycle ещё предстоит подключить. |

`iva plugin add` копирует плагин в свой store. Для MCP требуется доверие владельца; Ива сама выполняет сборку соединений и restart. Во время работы агента установка через `iva plugin propose` требует подтверждения владельца в Telegram — это поведение upstream, а не запрос на разрешение локальной разработки. Здесь Ива не устанавливалась и реальные сервисы не запускались.

Проверка настоящим reader выполнена: `node scripts/validate-iva-plugin.mjs` (Node 24.14.0), manifest принят, skill найден, MCP ledger принят, `diagnostics=[]`. Для воспроизведения нужна локальная копия upstream в `upstream/iva-agent` либо путь первым аргументом скрипта; Node должен поддерживать запуск TypeScript. Это проверка загрузки компонентов, не end-to-end проверка установки на Linux.

Финансовый MCP не получает Telegram/LLM ключи. Worker напоминаний получает токен из отдельного EnvironmentFile. На будущее специализированным агентам можно дать тот же локальный контракт; права записи пока общие, разделение read-only/write потребует отдельного шлюза. Базу нескольким процессам защищают транзакции SQLite и UNIQUE-ограничения, но это не многопользовательский сервер.
