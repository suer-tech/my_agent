"""QR-login onboarding tools for the telegram-userbot proxy.

Lets a non-technical iva user connect their Telegram account by scanning a QR
code sent straight into their bot chat — no CLI, no session strings. The login
token (`tg://login?token=…`) is account-takeover-grade, so it is rendered and
delivered ON THE BOX and never returned into the model context.

Tools (registered by `register_onboarding_tools`):
  qr_login_start     — begin QR login; render + send the QR to the owner's bot chat
  qr_login_status    — poll: starting | waiting | password_needed | authorized | expired | error
  qr_login_password  — supply the 2FA password when status == password_needed
  login_status       — is the userbot connected? (agent checks before other tools)

The Telethon client persists the session automatically on success (file session),
so no manual save and no restart: the same live client just becomes authorized.
"""
import asyncio
import io
import os
import sys
from datetime import datetime, timezone

import httpx
import qrcode
from qrcode.image.pure import PyPNGImage
from telethon import errors

_QR_MAX_REFRESHES = 3  # ~30s per QR ⇒ ~90s total before we give up
_QR_CAPTION = (
    "Отсканируй этот QR в приложении Telegram того аккаунта, который подключаешь:\n"
    "Настройки → Устройства → Подключить устройство.\n"
    "Код обновляется автоматически. Это подключение на твой страх и риск."
)

# Single-user login state machine. phase ∈
#   idle | starting | waiting | password_needed | authorized | expired | error
_state = {"phase": "idle", "detail": ""}
_lock = asyncio.Lock()
_task: "asyncio.Task | None" = None


def _first_id(csv: "str | None") -> "str | None":
    if not csv:
        return None
    for part in csv.replace(",", " ").split():
        part = part.strip()
        if part:
            return part
    return None


def _owner_chat_id() -> "str | None":
    return os.getenv("TELEGRAM_USERBOT_QR_CHAT_ID") or _first_id(
        os.getenv("TELEGRAM_ALLOWED_USER_IDS")
    )


def _render_qr_png(url: str) -> bytes:
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(image_factory=PyPNGImage).save(buf)
    return buf.getvalue()


async def _send_qr_to_bot(png: bytes, caption: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = _owner_chat_id()
    if not token or not chat_id:
        raise RuntimeError(
            "нужны TELEGRAM_BOT_TOKEN и chat владельца "
            "(TELEGRAM_USERBOT_QR_CHAT_ID или TELEGRAM_ALLOWED_USER_IDS) для доставки QR"
        )
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("login-qr.png", png, "image/png")},
        )
        resp.raise_for_status()


def _seconds_until_expiry(qr) -> float:
    expires = qr.expires
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return max(1.0, (expires - datetime.now(timezone.utc)).total_seconds() - 1.0)


async def _run_qr_login(client) -> None:
    global _state
    try:
        qr = await client.qr_login()
        for _ in range(_QR_MAX_REFRESHES):
            await _send_qr_to_bot(_render_qr_png(qr.url), _QR_CAPTION)
            _state = {"phase": "waiting", "detail": "QR отправлен в чат с ботом, жду скан"}
            try:
                await qr.wait(timeout=_seconds_until_expiry(qr))
            except asyncio.TimeoutError:
                await qr.recreate()
                continue
            _state = {"phase": "authorized", "detail": "Аккаунт подключён"}
            return
        _state = {"phase": "expired", "detail": "QR истёк слишком много раз — начни заново"}
    except errors.SessionPasswordNeededError:
        # Telegram asks for the cloud password as soon as the scanned token is accepted.
        # With the scan landing on the refresh boundary that answer comes back from
        # `recreate()` (ExportLoginTokenRequest), not from `wait()` — so it is caught for
        # the whole login, and the owner is asked for the password in every case.
        _state = {
            "phase": "password_needed",
            "detail": "Включена двухфакторная защита — пришли пароль",
        }
    except Exception as exc:  # noqa: BLE001
        _state = {"phase": "error", "detail": str(exc)}
        print(f"telegram-userbot: qr login failed: {exc}", file=sys.stderr)


def register_onboarding_tools(mcp, client) -> None:
    from mcp.types import ToolAnnotations

    async def qr_login_start() -> str:
        """Подключить Telegram-аккаунт владельца через QR. Рендерит QR-код и отправляет его
        картинкой в чат владельца с ботом. Затем опрашивай qr_login_status."""
        global _task
        async with _lock:
            if await client.is_user_authorized():
                _state.update(phase="authorized", detail="уже подключён")
                return "Telegram уже подключён — новый QR не нужен."
            if _task and not _task.done():
                return "Логин уже идёт — проверь свой чат с ботом, там QR-код."
            _state.update(phase="starting", detail="")
            _task = asyncio.create_task(_run_qr_login(client))
        return (
            "Отправляю QR-код в твой чат с ботом. Открой в приложении Telegram того "
            "аккаунта: Настройки → Устройства → Подключить устройство — и отсканируй. "
            "Потом я проверю статус."
        )

    async def qr_login_status() -> str:
        """Проверить статус QR-логина: starting | waiting | password_needed | authorized | expired | error."""
        if await client.is_user_authorized():
            return "authorized: аккаунт подключён."
        return f"{_state['phase']}: {_state['detail']}"

    async def qr_login_password(password: str) -> str:
        """Завершить логин двухфакторным паролем, когда статус == password_needed."""
        async with _lock:
            if _state.get("phase") != "password_needed":
                return f"Пароль сейчас не требуется (статус: {_state.get('phase')})."
            try:
                await client.sign_in(password=password)
                _state.update(phase="authorized", detail="Аккаунт подключён")
                return "Готово — аккаунт подключён."
            except Exception as exc:  # noqa: BLE001
                _state.update(phase="error", detail=str(exc))
                return f"Не удалось войти: {exc}"

    async def login_status() -> str:
        """Подключён ли userbot к Telegram? Зови перед использованием остальных тулов."""
        if await client.is_user_authorized():
            return "connected"
        return f"not_connected (phase={_state['phase']}: {_state['detail']})"

    read_only = ToolAnnotations(readOnlyHint=True)
    for fn in (qr_login_start, qr_login_status, qr_login_password, login_status):
        mcp.add_tool(fn, name=fn.__name__, description=fn.__doc__, annotations=read_only)
