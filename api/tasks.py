import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _send_expo_push(token: str, title: str, body: str, data: dict | None = None):
    """Send a single push notification via Expo Push API."""
    message = {
        "to": token,
        "sound": "default",
        "title": title,
        "body": body,
    }
    if data:
        message["data"] = data

    try:
        resp = requests.post(
            EXPO_PUSH_URL,
            json=message,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Expo push failed for token %s: %s", token[:20], exc)


def _get_message(lang: str, name: str, days: int) -> tuple[str, str]:
    """Return (title, body) in the user's language."""
    if lang == "tr":
        if days <= 0:
            return (
                "Son kullanma tarihi doldu!",
                f"{name} ürününün tarihi geçti. Hâlâ kullanılabilirse hemen bir tarif oluştur.",
            )
        if days == 1:
            return (
                "Yarın son gün!",
                f"{name} ürününün son kullanma tarihi yarın. Bir tarif oluşturmaya ne dersin?",
            )
        return (
            "SKT yaklaşıyor",
            f"{name} ürününün son kullanma tarihine {days} gün kaldı. İsraf olmasın!",
        )

    if days <= 0:
        return (
            "Expiration date passed!",
            f"{name} has expired. If still usable, create a recipe now.",
        )
    if days == 1:
        return (
            "Expires tomorrow!",
            f"{name} expires tomorrow. How about creating a recipe?",
        )
    return (
        "Expiring soon",
        f"{name} expires in {days} days. Don't let it go to waste!",
    )


@shared_task(name="api.check_expiring_ingredients")
def check_expiring_ingredients():
    """
    Daily task: find ingredients expiring within 3 days
    and send push notifications to users who have a push token.
    """
    from api.models import Ingredient, UserProfile

    today = timezone.now().date()
    threshold = today + timedelta(days=3)

    expiring = (
        Ingredient.objects
        .filter(
            expiration_date__isnull=False,
            expiration_date__lte=threshold,
        )
        .select_related("user")
    )

    user_items: dict[int, list] = {}
    for ing in expiring:
        user_items.setdefault(ing.user_id, []).append(ing)

    if not user_items:
        logger.info("No expiring ingredients found.")
        return "No expiring items."

    profiles = UserProfile.objects.filter(
        user_id__in=user_items.keys(),
        push_token__gt="",
    )
    token_map = {p.user_id: (p.push_token, p.language) for p in profiles}

    sent = 0
    for user_id, items in user_items.items():
        if user_id not in token_map:
            continue

        token, lang = token_map[user_id]

        for ing in items:
            days = (ing.expiration_date - today).days
            title, body = _get_message(lang, ing.name, days)
            _send_expo_push(token, title, body, data={"ingredientId": ing.id})
            sent += 1

    msg = f"Sent {sent} expiry notifications to {len(token_map)} users."
    logger.info(msg)
    return msg
