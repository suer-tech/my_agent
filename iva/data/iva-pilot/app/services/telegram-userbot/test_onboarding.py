"""QR login: the cloud-password answer is caught wherever Telegram returns it."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from telethon import errors

import onboarding


class FakeQr:
    """A login token whose scan never completes inside `wait`."""

    def __init__(self, on_recreate=None):
        self.url = "tg://login?token=dGVzdA"
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=2)
        self.recreated = 0
        self._on_recreate = on_recreate

    async def wait(self, timeout):
        raise asyncio.TimeoutError

    async def recreate(self):
        self.recreated += 1
        if self._on_recreate:
            raise self._on_recreate


class FakeClient:
    def __init__(self, qr):
        self._qr = qr

    async def qr_login(self):
        return self._qr


def run_login(qr):
    with patch.object(onboarding, "_send_qr_to_bot", new=AsyncMock()):
        asyncio.run(onboarding._run_qr_login(FakeClient(qr)))
    return onboarding._state


class QrLoginPasswordTests(unittest.TestCase):
    def test_password_asked_when_refresh_reports_two_factor(self):
        # Alexey, 15.10.2026: scan landed on the refresh boundary; the password answer
        # came from ExportLoginTokenRequest in `recreate()` and went to the `error` phase.
        qr = FakeQr(on_recreate=errors.SessionPasswordNeededError(request=None))
        state = run_login(qr)
        self.assertEqual(state["phase"], "password_needed")
        self.assertEqual(qr.recreated, 1)

    def test_password_asked_when_wait_reports_two_factor(self):
        class ScannedQr(FakeQr):
            async def wait(self, timeout):
                raise errors.SessionPasswordNeededError(request=None)

        state = run_login(ScannedQr())
        self.assertEqual(state["phase"], "password_needed")

    def test_other_refresh_failures_still_end_in_error(self):
        state = run_login(FakeQr(on_recreate=RuntimeError("network down")))
        self.assertEqual(state["phase"], "error")
        self.assertIn("network down", state["detail"])

    def test_token_expiring_every_time_ends_in_expired(self):
        qr = FakeQr()
        state = run_login(qr)
        self.assertEqual(state["phase"], "expired")
        self.assertEqual(qr.recreated, onboarding._QR_MAX_REFRESHES)


if __name__ == "__main__":
    unittest.main()
