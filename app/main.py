import asyncio, sys, logging, os, hmac, hashlib, json, base64, aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import settings
from .db import connect, close, execute, fetchrow
from .schema import ensure_schema, run_stars_migration
from .handlers import start, profile, tasks, withdraw, admin
from aiocryptopay import AioCryptoPay, Networks

# === NEW: для верификации подписи MonoPay
import ecdsa  # pip install ecdsa

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

MONO_BASE = "https://api.monobank.ua"

# Кэш публичного ключа Mono (PEM строки)
_MONO_PUBKEY_PEM: bytes | None = None

async def _fetch_mono_pubkey_pem() -> bytes:
    """
    Тянем base64-строку PEM из /api/merchant/pubkey c X-Token
    и декодим в bytes PEM. Кешируем.
    """
    global _MONO_PUBKEY_PEM
    if _MONO_PUBKEY_PEM:
        return _MONO_PUBKEY_PEM

    headers = {"X-Token": settings.MONOPAY_TOKEN}
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{MONO_BASE}/api/merchant/pubkey", headers=headers, timeout=15) as r:
            if r.status != 200:
                txt = await r.text()
                raise RuntimeError(f"monobank pubkey error {r.status}: {txt}")
            b64 = await r.text()
    try:
        _MONO_PUBKEY_PEM = base64.b64decode(b64.strip())
        return _MONO_PUBKEY_PEM
    except Exception as e:
        raise RuntimeError(f"bad pubkey base64: {e}")

def _verify_mono_xsign(pubkey_pem: bytes, body: bytes, x_sign_b64: str) -> bool:
    """
    Верификация X-Sign (DER) по ECDSA SHA-256 (как в оф. примере).
    Возвращает True/False.
    """
    try:
        signature = base64.b64decode(x_sign_b64)
        vk = ecdsa.VerifyingKey.from_pem(pubkey_pem.decode())
        return vk.verify(signature, body, sigdecode=ecdsa.util.sigdecode_der, hashfunc=hashlib.sha256)
    except Exception:
        return False

async def _ensure_crypto_webhook():
    if not settings.CRYPTO_PAY_TOKEN or not settings.WEBHOOK_URL:
        return
    url = (settings.WEBHOOK_URL or "").rstrip("/") + settings.CRYPTO_WEBHOOK_PATH
    crypto = AioCryptoPay(
        token=settings.CRYPTO_PAY_TOKEN,
        network=Networks.TEST_NET if settings.TEST_MODE else Networks.MAIN_NET
    )
    try:
        await crypto.set_webhook(url)   # регистрируем /cryptobot
    finally:
        await crypto.close()


async def on_startup(bot: Bot):
    await connect()
    await ensure_schema()
    await _ensure_crypto_webhook()
    try:
        await run_stars_migration()
    except Exception:
        pass
    await bot.get_me()
    await bot.set_my_commands([
        BotCommand(command="start", description="Start"),
        BotCommand(command="help", description="Help"),
        BotCommand(command="admin", description="Admin panel"),
    ])
    # На старте заранее подтянем ключ (если токен задан)
    if settings.MONOPAY_TOKEN:
        try:
            await _fetch_mono_pubkey_pem()
            log.info("Mono pubkey cached")
        except Exception as e:
            log.warning("Mono pubkey preload failed: %s", e)

async def on_shutdown(bot: Bot):
    await close()

async def polling():
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(tasks.router)
    dp.include_router(withdraw.router)
    dp.include_router(admin.router)

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    async def startup(_): await on_startup(bot)
    async def shutdown(_): await on_shutdown(bot)

    try:
        await dp.start_polling(
            bot,
            on_startup=startup,
            on_shutdown=shutdown,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()

# ====== Webhook app ======

async def _verify_crypto_signature(req: web.Request, body: bytes) -> bool:
    sig = req.headers.get("crypto-pay-api-signature", "")
    secret = hashlib.sha256(settings.CRYPTO_PAY_TOKEN.encode()).digest()
    calc = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, calc)

async def _handle_cryptobot_webhook(request: web.Request):
    body = await request.read()
    if settings.CRYPTO_PAY_TOKEN:
        if not await _verify_crypto_signature(request, body):
            return web.Response(status=403, text="bad signature")

    data = json.loads(body.decode("utf-8"))
    if data.get("update_type") == "invoice_paid":
        payload = data.get("payload") or {}
        inv = str(payload.get("invoice_id"))

        # отмечаем платёж и активируем пользователя
        await execute("UPDATE payments SET status='paid' WHERE provider='cryptobot' AND uuid=$1", inv)
        row = await fetchrow("SELECT user_id FROM payments WHERE provider='cryptobot' AND uuid=$1", inv)
        if row:
            await execute("UPDATE users SET status='active' WHERE id=$1", row["user_id"])
        log.info("CryptoBot invoice_paid: %s", inv)

    return web.json_response({"ok": True})

async def _handle_monopay_webhook(request: web.Request):
    """
    Жёсткая проверка подписи X-Sign по ECDSA SHA-256.
    Если не ОК — 403 (monobank попробует повторить до 3 раз). 
    После валидации меняем статус платежа и активируем юзера.
    Документация: X-Sign ECDSA + pubkey endpoint. 
    """
    raw = await request.read()
    x_sign = request.headers.get("X-Sign") or request.headers.get("x-sign")
    if not x_sign:
        return web.Response(status=403, text="no signature")

    # 1) верифицируем подпись; при фейле 1 раз обновим ключ и попробуем снова
    pubkey = await _fetch_mono_pubkey_pem()
    ok = _verify_mono_xsign(pubkey, raw, x_sign)
    if not ok:
        # возможна ротация ключа — пробуем рефреш
        global _MONO_PUBKEY_PEM
        _MONO_PUBKEY_PEM = None
        pubkey = await _fetch_mono_pubkey_pem()
        ok = _verify_mono_xsign(pubkey, raw, x_sign)

    if not ok:
        log.warning("Mono webhook: invalid X-Sign")
        return web.Response(status=403, text="bad signature")

    # 2) подпись валидна — обрабатываем
    data = json.loads(raw.decode("utf-8"))
    status = (data.get("status") or "").lower()
    info = data.get("merchantPaymInfo") or {}
    reference = info.get("reference") or data.get("reference")
    invoice_id = data.get("invoiceId") or data.get("invoice_id")

    if status == "success":
        # помечаем платёж
        await execute("""
            UPDATE payments SET status='paid'
            WHERE provider='monopay' AND (uuid=$1 OR uuid=$2 OR order_id=$3)
        """, str(invoice_id), str(reference), str(reference))

        # активируем пользователя
        row = await fetchrow("""
            SELECT user_id FROM payments
            WHERE provider='monopay' AND (uuid=$1 OR uuid=$2 OR order_id=$3)
            ORDER BY id DESC LIMIT 1
        """, str(invoice_id), str(reference), str(reference))
        if row:
            await execute("UPDATE users SET status='active' WHERE id=$1", row["user_id"])

        log.info("Mono success: invoice=%s ref=%s", invoice_id, reference)

    # всегда отвечаем 200, чтобы банк не ретраил, если всё прошло
    return web.Response(text="ok")

async def webhook():
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(tasks.router)
    dp.include_router(withdraw.router)
    dp.include_router(admin.router)

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    app = web.Application()

    async def handle_tg(request: web.Request):
        if request.path != (settings.WEBHOOK_PATH or "/webhook"):
            return web.Response(text="OK")
        data = await request.json()
        await dp.feed_webhook_update(bot, data)
        return web.Response(text="ok")

    # Telegram webhook
    app.router.add_post(settings.WEBHOOK_PATH, handle_tg)

    # CryptoBot webhook
    app.router.add_post(settings.CRYPTO_WEBHOOK_PATH, _handle_cryptobot_webhook)

    # MonoPay webhook
    app.router.add_post(settings.MONOPAY_WEBHOOK_PATH, _handle_monopay_webhook)

    async def on_app_start(_):
        await on_startup(bot)
        wh_url = (settings.WEBHOOK_URL or "").rstrip("/") + settings.WEBHOOK_PATH
        await bot.delete_webhook(drop_pending_updates=True)
        updates = dp.resolve_used_update_types()
        await bot.set_webhook(wh_url, drop_pending_updates=True, allowed_updates=updates)

    async def on_app_stop(_):
        await on_shutdown(bot)
        await bot.delete_webhook()

    app.on_startup.append(on_app_start)
    app.on_shutdown.append(on_app_stop)

    port = int(os.environ.get("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    if "--polling" in sys.argv:
        asyncio.run(polling())
    else:
        asyncio.run(webhook())
