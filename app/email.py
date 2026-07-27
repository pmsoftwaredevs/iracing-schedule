"""SMTP sending. Call sites: signup confirmation (private manage link), season-rollover
recap, and lost-link recovery. Credentials come from Settings (env vars); if unset,
sends are logged instead of attempted so local dev doesn't need real SMTP creds.
"""

import logging
import smtplib
from email.message import EmailMessage

from app.config import Settings

logger = logging.getLogger(__name__)


def send_email(settings: Settings, to_address: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        logger.info("SMTP not configured; would have sent to %s: %s\n%s", to_address, subject, body)
        return

    message = EmailMessage()
    message["From"] = settings.smtp_from_address
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def send_signup_email(settings: Settings, to_address: str, name: str, manage_url: str) -> None:
    send_email(
        settings,
        to_address,
        subject="Your iRacing calendar is ready",
        body=(
            f"Hi {name},\n\n"
            f"Your iRacing calendar feed is ready. Manage your selections or subscribe your "
            f"calendar app here:\n\n{manage_url}\n\n"
            f"Keep this link private — it's how you manage your feed, no password needed."
        ),
    )


def send_rollover_email(settings: Settings, to_address: str, name: str, manage_url: str, summary: str) -> None:
    send_email(
        settings,
        to_address,
        subject="Your iRacing calendar was updated for the new season",
        body=(
            f"Hi {name},\n\n"
            f"A new season just started, so we re-matched your championships:\n\n{summary}\n\n"
            f"Review or fix anything here:\n{manage_url}"
        ),
    )


def send_recovery_email(settings: Settings, to_address: str, links: list[tuple[str, str]]) -> None:
    """links is a list of (name, manage_url) — usually one, but a single email can
    have signed up more than once, so all of them are listed in one email."""
    lines = "\n".join(f"- {name}: {manage_url}" for name, manage_url in links)
    send_email(
        settings,
        to_address,
        subject="Your iRacing calendar links",
        body=(
            f"Here are the iRacing calendar link(s) associated with this email address:\n\n"
            f"{lines}\n\n"
            f"Keep these private — they're how you manage your feed, no password needed."
        ),
    )
