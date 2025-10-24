import aiohttp
from typing import Any, Dict, List
from ..config import settings

BASE_URL = "https://api.cryptocloud.plus"

class CryptoCloudError(Exception):
    pass

async def create_invoice(amount_usd: float, order_id: str, description: str = "Activation", locale: str = "en") -> dict:
    url = f"{BASE_URL}/v2/invoice/create?locale={locale}"
    headers = {"Authorization": f"Token {settings.CRYPTOCLOUD_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "amount": str(round(amount_usd, 2)),
        "shop_id": None,  # optional if API key is project-level; leave None
        "order_id": order_id,
        "description": description
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=headers, json=payload, timeout=30) as r:
            data = await r.json()
            if r.status >= 400 or data.get("status") not in ("success","created","ok"):
                raise CryptoCloudError(f"Create invoice failed: {r.status} {data}")
            return data

async def get_invoices_info(uuids: List[str]) -> dict:
    url = f"{BASE_URL}/v2/invoice/merchant/info"
    headers = {"Authorization": f"Token {settings.CRYPTOCLOUD_API_KEY}", "Content-Type": "application/json"}
    payload = {"uuids": uuids}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=headers, json=payload, timeout=30) as r:
            data = await r.json()
            if r.status >= 400:
                raise CryptoCloudError(f"Invoice info failed: {r.status} {data}")
            return data
