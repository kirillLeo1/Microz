import re
from urllib.parse import urlparse, unquote

TELEGRAM_HOSTS = ("t.me", "telegram.me")

def normalize_url(u: str) -> str:
    if not u:
        return u
    u = u.strip()

    # @channel → https://t.me/channel
    if u.startswith("@"):
        return f"https://t.me/{u[1:]}"

    # без схеми → https://
    if any(u.startswith(h + "/") for h in TELEGRAM_HOSTS):
        return "https://" + u
    if not re.match(r"^https?://|^tg://", u, flags=re.I):
        return "https://" + u
    return u


def to_tg_deeplink(u: str) -> str:
    """
    Спроба конвертувати https://t.me/... у tg://... (відкривається надійніше).
    Якщо не вдається — повертаємо оригінал.
    """
    u = normalize_url(u)
    p = urlparse(u)
    if p.scheme in ("http", "https") and p.netloc in TELEGRAM_HOSTS:
        path = unquote(p.path or "/").strip("/")
        parts = path.split("/") if path else []

        # https://t.me/+invitecode → tg://join?invite=invitecode
        if parts and parts[0].startswith("+"):
            return f"tg://join?invite={parts[0][1:]}"

        # https://t.me/username
        if len(parts) == 1 and parts[0] and parts[0] not in ("c",):
            return f"tg://resolve?domain={parts[0]}"

        # https://t.me/username/123 → конкретний пост
        if len(parts) >= 2 and parts[0] and parts[1].isdigit():
            return f"tg://resolve?domain={parts[0]}&post={parts[1]}"

        # інші випадки (c/... приватні чати) – лишаємо як є
    return u


def is_clickable(u: str) -> bool:
    try:
        p = urlparse(u)
        if p.scheme == "tg":
            return True
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False
