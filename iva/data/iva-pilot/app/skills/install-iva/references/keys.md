# Step 5 — Keys (only what is missing)

Ask only for what the installer cannot know. Each answer goes into a file with mode 600 (`references/secrets.md`).

| Key | How the person gets it | Check before use |
|---|---|---|
| Model | Ask what they already pay for. ChatGPT Plus/Pro or Claude Pro/Max subscription → sign in on the server before passing the env file (`iva login` / `claude auth login`, a link and a code — pass both to the person). An API key (OpenCode Go, Ollama Cloud, OpenRouter) → the key and the model name | API key: a one-token request to the provider's `/models` or chat endpoint returns 200 |
| Bot token | Telegram → @BotFather → `/newbot` → a name → a username ending in `bot` → copy the token | `getMe` returns `ok: true`; note the username |
| Person's Telegram ID | "Send your new bot any message." Then read it once: `getUpdates` → `message.from.id` and first name; confirm the name with the person. Do this before Iva starts — after that Iva owns the updates | a number, the confirmed name matches |
| Timezone | from the person's computer (pre-flight), confirm | IANA name, e.g. `Asia/Tashkent` |
| Language | the language the person writes to you in | `ru` or `en` |

Voice (Deepgram) and web search (Tavily and others) are optional: tell the person they can add them later in the bot
with `/menu`. Do not ask for them now.

## The minimal env file

```
AGENT_LANGUAGE=ru
MODEL_PROVIDER=opencode
OPENCODE_API_KEY=<key>
OPENCODE_MODEL=<model>
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_ALLOWED_USER_IDS=<id>
ASSISTANT_TIMEZONE=<tz>
```

Provider variable names: `opencode` → `OPENCODE_API_KEY`/`OPENCODE_MODEL`; `ollama` → `OLLAMA_API_KEY`/`OLLAMA_MODEL`;
`openrouter` → `OPENROUTER_API_KEY`/`OPENROUTER_MODEL`; `codex` (ChatGPT) → `CODEX_MODEL` + sign-in; `claude` →
`CLAUDE_MODEL` + sign-in. Everything else the installer fills in.

For a subscription on a fresh machine, first run the installer with `--non-interactive` and no env file to prepare
the CLI and dependencies. Complete the sign-in, then repeat the installer with `--env-file` using the collected answers.
Preparation leaves `autostart=false` in `.iva-installed`, so the next run continues the first installation and configures
autostart. The subscription must be signed in before the env file can pass validation.

A bot already connected to another service (a webhook is set — `getWebhookInfo` shows a URL) answers nobody through
Iva: ask the person before `deleteWebhook`.
