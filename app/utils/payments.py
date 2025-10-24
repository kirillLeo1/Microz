import aiohttp
import logging
from ..config import settings

BASE_URL = "https://api.cryptocloud.plus"
log = logging.getLogger("payments")

class CryptoCloudError(Exception):
    pass


async def create_invoice(
    amount_usd: float,
    order_id: str,
    description: str = "Activation",
    locale: str = "en",
) -> dict:
    """
    Створює інвойс і ПОВЕРТАЄ УЖЕ нормалізоване:
    {"uuid": "...", "link": "https://pay.cryptocloud.plus/invoice/..." }
    """
    url = f"{BASE_URL}/v2/invoice/create"
    headers = {
        "Authorization": f"Token {settings.CRYPTOCLOUD_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "shop_id": settings.CRYPTOCLOUD_SHOP_ID,          # ← обов'язково
        "amount": f"{amount_usd:.2f}",
        "currency": getattr(settings, "CRYPTOCLOUD_CURRENCY", "USD"),
        "order_id": order_id,
        "description": description,
        "locale": locale,                                  # ← у JSON, не в query
        # опціонально:
        # "success_url": "...",
        # "fail_url": "...",
    }

    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=headers, json=payload, timeout=30) as r:
            data = await r.json()

    log.info("CC create_invoice resp: %s", data)

    # у CC зазвичай: {"status": "success", "result": {...}}
    status = (data.get("status") or "").lower()
    if status not in ("success", "ok", "created"):
        raise CryptoCloudError(
            data.get("message") or data.get("description") or str(data)
        )

    res = data.get("result") or {}
    uuid = res.get("uuid") or res.get("invoice") or res.get("id")
    link = res.get("link") or res.get("pay_url") or res.get("url")
    if not link and uuid:
        # запасний варіант, коли API не повернув link
        link = f"https://pay.cryptocloud.plus/invoice/{uuid}"

    if not uuid or not link:
        raise CryptoCloudError(f"create_invoice: missing uuid/link. raw={data}")

    return {"uuid": uuid, "link": link}


async def get_invoices_info(uuids: list[str]) -> list[dict]:
    """
    Повертає список інвойсів; елементи мають поле 'status' (paid/overpaid/partial/...).
    """
    url = f"{BASE_URL}/v2/invoice/merchant/info"
    headers = {
        "Authorization": f"Token {settings.CRYPTOCLOUD_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"uuids": uuids}

    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=headers, json=payload, timeout=30) as r:
            data = await r.json()

    log.info("CC merchant/info resp: %s", data)

    status = (data.get("status") or "").lower()
    if status not in ("success", "ok"):
        raise CryptoCloudError(
            data.get("message") or data.get("description") or str(data)
        )

    res = data.get("result") or {}
    items = res.get("invoices") if isinstance(res, dict) else res
    return items if isinstance(items, list) else []

