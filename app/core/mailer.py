"""Best-effort SMTP delivery.

Sending runs in a worker thread (smtplib is blocking) and every failure is
caught and returned as a string, never raised: a notification is already
safely recorded in the database by the time this is called, and an SMTP
outage must not roll back the trip, reservation or defect that triggered
it (zadání 20 asks for notifications, not for mail to be a precondition of
the workflow).

With smtp_host unset the app is fully functional - notifications just stay
in-app only.

Konfigurace se sem **předává**, nečte se tu. Nastavení pošty je od
etapy 7 v administraci (app/core/app_settings.py:get_smtp) s .env jako
záložním zdrojem, a odesílání nemá být místo, které o tom rozhoduje -
jen to, které pošle, co dostane.
"""
import asyncio
import logging
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _send_sync(config, to_email: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(config.host, config.port, timeout=15) as smtp:
        if config.starttls:
            smtp.starttls()
        if config.user:
            smtp.login(config.user, config.password)
        smtp.send_message(message)


async def send_mail(config, to_email: str, subject: str, body: str) -> str | None:
    """Returns None on success, or a short error string to store on the
    notification row. Never raises.

    `config` je app_settings.SmtpConfig."""
    if not config.is_configured:
        return "SMTP není nakonfigurováno"
    if getattr(config, "password_unreadable", False):
        # Uložené heslo nejde rozšifrovat (nejspíš se vyměnil
        # SESSION_SECRET_KEY). Přihlásit se s prázdným heslem by skončilo
        # nesrozumitelnou chybou od serveru.
        return "Uložené heslo k SMTP nelze přečíst – zadejte ho v nastavení znovu"
    if not to_email:
        return "Příjemce nemá e-mailovou adresu"
    try:
        await asyncio.to_thread(_send_sync, config, to_email, subject, body)
        return None
    except Exception as exc:  # noqa: BLE001 - deliberately swallowed, see module docstring
        logger.warning("E-mail notification to %s failed: %s", to_email, exc)
        return f"{type(exc).__name__}: {exc}"[:500]
