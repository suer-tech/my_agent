"""Deterministic delivery of one approved recipient/letter package.

Production input comes from the CRM's atomic approved-outreach claim, after
independent model review and the CRM's contact/suppression/pacing checks.
No model, recipient selection, rewriting, CRM claim, or SMTP retry occurs here.
The caller holds DispatchStore.lock(); each attempt is committed before SMTP.
"""
from dataclasses import dataclass
import sqlite3

from .transport import SendResult, compose, send_one


@dataclass(frozen=True)
class DispatchOutcome:
    attempted: bool
    result: SendResult | None = None
    reason: str = ""


def receipt_payload(message, result):
    return {"claim_token": message["claim_token"], "outcome": result.outcome,
            "provider_message_id": result.provider_message_id,
            "receipt": result.receipt or result.error or "No transport acknowledgement"}


def dispatch(message, config, store, *, sender=send_one):
    """Send the exact claimed to/subject/body once, with durable receipt.

    `message` contains id, claim_token, message_id, from, to, subject, body.
    No unapproved model output should call this interface; production caller
    is mailer.runner after CRM claim. The owner test uses a separate journal.
    A recorded duplicate or interrupted attempt is never sent again.
    """
    if not config.enabled or not config.send_enabled:
        return DispatchOutcome(False, reason="sending_disabled")
    if (not isinstance(message, dict) or not isinstance(message.get("id"), str)
            or not 1 <= len(message["id"]) <= 100
            or not isinstance(message.get("claim_token"), str)
            or not 20 <= len(message["claim_token"]) <= 100
            or not isinstance(message.get("message_id"), str)):
        return DispatchOutcome(False, reason="invalid_claim_message")
    # Keep a private copy so the exact approved envelope is used throughout.
    message = dict(message)
    try:
        store.register(message)
    except sqlite3.IntegrityError:
        return DispatchOutcome(False, reason="duplicate_claim_no_resend")
    try:
        compose(message, config)
    except (TypeError, ValueError):
        result = SendResult("failed_before_send", error="invalid_claim_payload")
    else:
        try:
            result = sender(message, config)
        except Exception:
            result = SendResult("uncertain", error="transport_interrupted_no_resend")
    if not isinstance(result, SendResult) or result.outcome not in {
            "accepted", "failed_before_send", "rejected", "uncertain"}:
        result = SendResult("uncertain", error="invalid_transport_result")
    # A crash while saving still leaves `started`: recovery reconciles it as
    # uncertain instead of resending a possibly delivered email.
    store.result(message["id"], receipt_payload(message, result))
    return DispatchOutcome(True, result)
