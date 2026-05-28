import logging

import requests
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger("api")


def send_app_email(
    *,
    to: list[str],
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> bool:
    """Send transactional email via SMTP2GO HTTP API (preferred) or SMTP fallback."""
    if not to:
        return False

    api_key = getattr(settings, "SMTP2GO_API_KEY", "")
    if api_key:
        return _send_via_smtp2go_api(
            api_key=api_key,
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )

    return _send_via_smtp(to=to, subject=subject, text_body=text_body, html_body=html_body)


def _send_via_smtp2go_api(
    *,
    api_key: str,
    to: list[str],
    subject: str,
    text_body: str,
    html_body: str | None,
) -> bool:
    sender = settings.DEFAULT_FROM_EMAIL
    payload: dict = {
        "sender": f"MealsMind <{sender}>",
        "to": to,
        "subject": subject,
        "text_body": text_body,
        "fastaccept": True,
    }
    if html_body:
        payload["html_body"] = html_body

    try:
        resp = requests.post(
            "https://api.smtp2go.com/v3/email/send",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Smtp2go-Api-Key": api_key,
            },
            timeout=15,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("data", {}).get("email_id"):
            return True
        logger.error("SMTP2GO API rejected email: status=%s body=%s", resp.status_code, data)
        return False
    except Exception:
        logger.exception("SMTP2GO API request failed")
        return False


def _send_via_smtp(
    *,
    to: list[str],
    subject: str,
    text_body: str,
    html_body: str | None,
) -> bool:
    if not getattr(settings, "EMAIL_HOST_USER", ""):
        logger.error("Email not configured — set SMTP2GO_API_KEY or SMTP credentials")
        return False
    try:
        send_mail(
            subject=subject,
            message=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=to,
            html_message=html_body,
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("SMTP send failed for %s", to)
        return False
