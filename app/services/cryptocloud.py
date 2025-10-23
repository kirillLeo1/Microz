import httpx
import jwt
from typing import Optional
from ..config import settings

BASE_URL = "https://api.cryptocloud.plus/v2"
AUTH_HEADER = {"Authorization": f"Token {settings.CRYPTOCLOUD_API_KEY}"}

async def create_invoice(amount_usd: float, order_id: str, email: Optional[str] = None, locale: str | None = None) -> dict:
    payload = {
        "shop_id": settings.CRYPTOCLOUD_SHOP_ID,
        "amount": round(float(amount_usd), 2),  # поле amount — USD (docs v2)
        "currency": "USD",
        "order_id": order_id,
    }
    if email:
        payload["email"] = email
    params = {"locale": locale} if locale else None
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{BASE_URL}/invoice/create", json=payload, headers=AUTH_HEADER, params=params)
        r.raise_for_status()
        return r.json()

async def get_invoice_info(uuids: list[str]) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{BASE_URL}/invoice/merchant/info", json={"uuids": uuids}, headers=AUTH_HEADER)
        r.raise_for_status()
        return r.json()

# POSTBACK (webhook) — перевірка JWT HS256 (token у JSON)
# Token підписаний секретом із налаштувань проекту; дійсний 5 хв; містить UUID інвойсу.

def verify_postback_jwt(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.CRYPTOCLOUD_POSTBACK_SECRET, algorithms=["HS256"])  # повертає dict
        return payload
    except Exception:
        return None