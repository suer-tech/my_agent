import smtplib
import tempfile
import unittest
from dataclasses import replace
from email import message_from_bytes
from email.policy import default
from pathlib import Path

from mailer.config import DispatcherConfig, MailConfig
from mailer.transport import compose, enhance_hr_letter, is_hr_letter, send_one

HR_BODY = (
    "Здравствуйте!\n\n"
    "На вашем сайте описан подбор специалистов и руководителей: от разбора требований до интервью.\n\n"
    "Предлагаю попробовать наш работающий сервис на основе искусственного интеллекта: он анализирует"
    " резюме и интервью, сопоставляя их с требованиями конкретной вакансии. Критерии удобно настраиваются"
    " в отдельном интерфейсе.\n\n"
    "В нашем рабочем сценарии ручной разбор одного кандидата занимает от 30 минут до часа. Сервис выполняет"
    " обработку автоматически за 10–15 минут, без участия рекрутера в самом анализе. Рекрутер получает готовый"
    " отчёт о кандидате: соответствие требованиям вакансии, подтверждения из материалов и итоговую рекомендацию"
    " — «Рекомендовать», «Не рекомендовать» или «Недостаточно данных». Окончательное решение остаётся за человеком.\n\n"
    "Кому в вашей команде можно показать такой пример и обсудить применимость?\n\n"
    "Если это не к вам или неактуально, сообщите — больше не буду беспокоить.\n\nAI Studio"
)

HR_ENHANCED = HR_BODY.replace("Предлагаю попробовать наш работающий сервис", "Предлагаю вам попробовать наш работающий сервис")
def approving_client(messages, config):
    return "APPROVED" if "независимый проверяющий" in messages[0]["content"] else HR_ENHANCED

OCR_BODY = (
    "Здравствуйте!\n\nПредлагаю внедрить ИИ для автоматического извлечения данных из судебных документов:"
    " например, номеров дел, сторон, сумм, сроков и условий. Данные извлекаются вместо ручного ввода;"
    " результат можно проверить перед использованием.\n\nПо нашему бенчмарку время обработки одного"
    " документа сократилось с 30 минут до 1 минуты.\n\nЕсли это не к вам, сообщите.\n\nAI Studio"
)

REPLY_BODY = "Здравствуйте! Спасибо за письмо, интересно попробовать. Когда вам удобно созвониться?"


class FakeSMTP:
    def __init__(self, failure="", response=250):
        self.failure, self.response, self.payload = failure, response, b""

    def ehlo(self):
        return 250, b"ok"

    def login(self, *args):
        pass

    def mail(self, *args):
        return 250, b"ok"

    def rcpt(self, *args):
        return 250, b"ok"

    def docmd(self, *args):
        return 354, b"ok"

    def send(self, payload):
        self.payload = payload

    def getreply(self):
        return self.response, b"private response"

    def quit(self):
        if self.failure == "quit":
            raise OSError("private response")

    def close(self):
        pass


def fail_client(*args, **kwargs):
    raise AssertionError("llm_client must not be called")


def mime_text(payload: bytes) -> str:
    message = message_from_bytes(payload, policy=default)
    body = message.get_body(preferencelist=("plain", "html"))
    return body.get_content() if body is not None else ""


class HrDetectionTests(unittest.TestCase):
    def test_hr_letter_recognised(self):
        self.assertTrue(is_hr_letter(HR_BODY))

    def test_ocr_and_reply_never_marked_hr(self):
        self.assertFalse(is_hr_letter(OCR_BODY))
        self.assertFalse(is_hr_letter(REPLY_BODY))
        self.assertFalse(is_hr_letter(""))


class EnhanceTests(unittest.TestCase):
    def setUp(self):
        self.config = MailConfig(llm_enabled=True)

    def test_enhancement_inserts_capability_and_preserves_letter(self):
        body, changed = enhance_hr_letter(
            HR_BODY, self.config, client=approving_client)
        self.assertTrue(changed)
        self.assertEqual(body, HR_ENHANCED)
        self.assertNotIn("эмоциональный портрет", body)
        self.assertIn("Предлагаю вам попробовать", body)
        self.assertTrue(body.startswith("Здравствуйте!"))
        self.assertTrue(body.endswith("AI Studio"))

    def test_independent_review_rejection_keeps_original(self):
        def client(messages, config):
            return "REJECTED" if "независимый проверяющий" in messages[0]["content"] else HR_ENHANCED
        body, changed = enhance_hr_letter(HR_BODY, self.config, client=client)
        self.assertFalse(changed)
        self.assertEqual(body, HR_BODY)

    def test_changed_numbers_or_profiling_never_reach_smtp(self):
        for candidate in [HR_ENHANCED.replace("10–15", "1–2"), HR_ENHANCED.replace("готовый отчёт", "эмоциональный портрет и готовый отчёт")]:
            with self.subTest(candidate=candidate):
                body, changed = enhance_hr_letter(HR_BODY, self.config, client=lambda *_: candidate)
                self.assertFalse(changed)
                self.assertEqual(body, HR_BODY)

    def test_non_hr_letter_never_touched(self):
        body, changed = enhance_hr_letter(OCR_BODY, self.config, client=fail_client)
        self.assertFalse(changed)
        self.assertEqual(body, OCR_BODY)

    def test_model_error_keeps_approved_body(self):
        def boom(messages, config):
            raise RuntimeError("provider unavailable")
        body, changed = enhance_hr_letter(HR_BODY, self.config, client=boom)
        self.assertFalse(changed)
        self.assertEqual(body, HR_BODY)

    def test_output_without_addition_rejected(self):
        body, changed = enhance_hr_letter(
            HR_BODY, self.config, client=lambda messages, config: HR_BODY)
        self.assertFalse(changed)
        self.assertEqual(body, HR_BODY)

    def test_disabled_config_never_calls_model(self):
        body, changed = enhance_hr_letter(
            HR_BODY, MailConfig(llm_enabled=False), client=fail_client)
        self.assertFalse(changed)
        self.assertEqual(body, HR_BODY)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_llm_key_reads_private_file(self):
        secret = Path(self.temp.name) / "llm-key"
        secret.write_text("sk-private-test-key-1234567890", encoding="utf-8")
        secret.chmod(0o600)
        config = MailConfig(llm_enabled=True, llm_api_key_file=str(secret))
        self.assertEqual(config.llm_key(), "sk-private-test-key-1234567890")

    def test_llm_key_missing_raises(self):
        with self.assertRaises(ValueError):
            MailConfig(llm_enabled=True).llm_key()

    def test_validate_requires_https_routerai_origin(self):
        for url in ("http://routerai.ru/api/v1", "https://routerai.ru/foo", "https://user:pw@routerai.ru/api/v1"):
            with self.subTest(url=url):
                config = DispatcherConfig(mail=MailConfig(llm_enabled=True, llm_base_url=url))
                with self.assertRaises(ValueError):
                    config.validate()

    def test_validate_llm_timeout_range(self):
        config = DispatcherConfig(mail=MailConfig(llm_enabled=True, llm_timeout=120))
        with self.assertRaises(ValueError):
            config.validate()


class SendOneIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        secret = Path(self.temp.name) / "secret"
        secret.write_text("not-a-real-password", encoding="utf-8")
        secret.chmod(0o600)
        self.base = MailConfig(enabled=True, send_enabled=True, llm_enabled=True,
                               password_file=str(secret))

    def draft(self, body, subject="Сводка резюме и интервью по требованиям вакансии"):
        return {"from": self.base.sender, "to": "buyer@example.org", "subject": subject,
                "body": body, "message_id": "<draft-7@lead-flow.invalid>"}

    def send(self, body, client=None, llm_enabled=True):
        smtp = FakeSMTP()
        config = replace(self.base, llm_enabled=llm_enabled)
        result = send_one(self.draft(body), config,
                          smtp_factory=lambda *a, **k: smtp, llm_client=client)
        self.assertNotIn("private response", str(result))
        return result, smtp

    def test_hr_letter_enhanced_by_model(self):
        result, smtp = self.send(HR_BODY, client=approving_client)
        self.assertEqual(result.outcome, "accepted")
        self.assertTrue(result.enhanced)
        self.assertIn("body_enhanced_by_model", result.receipt)
        text = mime_text(smtp.payload)
        self.assertNotIn("эмоциональный портрет", text)
        self.assertTrue(text.startswith("Здравствуйте!"))

    def test_non_hr_letter_not_enhanced(self):
        result, _ = self.send(OCR_BODY, client=fail_client)
        self.assertEqual(result.outcome, "accepted")
        self.assertFalse(result.enhanced)
        self.assertNotIn("body_enhanced_by_model", result.receipt)
        self.assertNotIn("эмоциональный портрет", result.receipt)

    def test_failed_enhancement_is_fail_open(self):
        def boom(messages, config):
            raise RuntimeError("offline")
        result, smtp = self.send(HR_BODY, client=boom)
        self.assertEqual(result.outcome, "accepted")
        self.assertFalse(result.enhanced)
        self.assertIn("hr_enhancement_skipped", result.receipt)
        self.assertNotIn("эмоциональный портрет", mime_text(smtp.payload))

    def test_llm_disabled_never_calls_model(self):
        result, _ = self.send(HR_BODY, client=fail_client, llm_enabled=False)
        self.assertEqual(result.outcome, "accepted")
        self.assertFalse(result.enhanced)

    def test_compose_of_enhanced_body_remains_valid(self):
        config = replace(self.base, password_file="")
        mime = compose(self.draft(HR_ENHANCED), config)
        self.assertNotIn("эмоциональный портрет", mime_text(mime))


if __name__ == "__main__":
    unittest.main()