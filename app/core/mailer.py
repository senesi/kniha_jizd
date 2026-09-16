"""Best-effort SMTP delivery.

Sending runs in a worker thread (smtplib is blocking) and every failure is
caught and returned as a string, never raised: a notification is already
safely recorded in the database by the time this is called, and an SMTP
outage must not roll back the trip, reservation or defect that triggered
it (zadání 20 asks for notifications, not for mail to be a precondition of
the workflow).

With smtp_host unset the app is fully functional - notifications just stay
in-app only.
"""
import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(get_settings().smtp_host)


def _send_sync(to_email: str, subject: str, body: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


async def send_mail(to_email: str, subject: str, body: str) -> str | None:
    """Returns None on success, or a short error string to store on the
    notification row. Never raises."""
    if not is_configured():
        return "SMTP není nakonfigurováno"
    if not to_email:
        return "Příjemce nemá e-mailovou adresu"
    try:
        await asyncio.to_thread(_send_sync, to_email, subject, body)
        return None
    except Exception as exc:  # noqa: BLE001 - deliberately swallowed, see module docstring
        logger.warning("E-mail notification to %s failed: %s", to_email, exc)
        return f"{type(exc).__name__}: {exc}"[:500]
