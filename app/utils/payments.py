import os
import json
import time
import aiohttp
import logging
from dataclasses import dataclass

from aiocryptopay import AioCryptoPay, Networks

from ..config import settings

log = logging.getLogger("payments")

MONO_BASE = "https://api.monobank.ua"


# ===== Helpers

def usd_to_uah_cop(usd: float) -> int:
    """
    Конвертация USD -> UAH (копейки) с настраиваемым курсом.
    PRICE_USD * USD_TO_UAH * 100  -> int
    """
    rate = float(os.getenv("USD_TO_UAH", settings.USD_TO_UAH))
    return int(round(usd * rate * 100))


@dataclass
class Invoice:
    provider: str
    invoice_id: str
    pay_url: str
    extra: dict | None = None


# ===== MonoPay

async def create_monopay_invoice(order_id: str, description: str = "Activation") -> Invoice:
    """
    Создаёт счёт в MonoPay и отдаёт ссылку для оплаты.
    Сумма считается из PRICE_USD -> копейки UAH.
    """
    amount_cop = usd_to_uah_cop(settings.PRICE_USD)

    headers = {"X-Token": settings.MONOPAY_TOKEN}
    payload = {
        "amount": amount_cop,     # копейки
        "ccy": 980,
        "merchantPaymInfo": {
            "reference": order_id,
            "basketOrder": [{"name": description, "qty": 1, "sum": amount_cop}],
            "webHookUrl": (settings.WEBHOOK_URL or "").rstrip("/") + settings.MONOPAY_WEBHOOK_PATH,
        },
        "paymentType": "debit",
        "validityDuration": 86400,  # 24h
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{MONO_BASE}/api/merchant/invoice/create", json=payload, headers=headers, timeout=30) as r:
            data = await r.json()
    log.info("Mono create_invoice resp: %s", data)

    invoice_id = data.get("invoiceId") or data.get("invoice_id", "")
    pay_url = data.get("pageUrl") or data.get("invoiceUrl") or data.get("payUrl", "")
    if not invoice_id or not pay_url:
        raise RuntimeError(f"Mono create_invoice failed: {data}")

    return Invoice("monopay", invoice_id, pay_url, extra=data)


# ===== CryptoBot (Crypto Pay API)

def _crypto_network():
    return Networks.MAIN_NET if not settings.TEST_MODE else Networks.TEST_NET

async def create_cryptobot_invoice(order_id: str, description: str = "Activation") -> Invoice:
    """
    Создаёт инвойс в CryptoBot в фиате USD.
    Возвращает bot_invoice_url.
    """
    crypto = AioCryptoPay(token=settings.CRYPTO_PAY_TOKEN, network=_crypto_network())
    inv = await crypto.create_invoice(
        currency_type="fiat",
        fiat="USD",
        amount=float(settings.PRICE_USD),
        description=description,
        payload=order_id,
    )
    await crypto.close()
    return Invoice("cryptobot", str(inv.invoice_id), inv.bot_invoice_url, extra={"status": inv.status})

async def get_cryptobot_invoice(invoice_id: str):
    """
    Получить инфо по инвойсу CryptoBot (по id).
    """
    crypto = AioCryptoPay(token=settings.CRYPTO_PAY_TOKEN, network=_crypto_network())
    items = await crypto.get_invoices(invoice_ids=[int(invoice_id)])
    await crypto.close()
    return items[0] if items else None

