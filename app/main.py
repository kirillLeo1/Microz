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
from aiocryptopay import AioCryptoPay, Networks  # оставляю для совместимости с payments.py

# === NEW: для верификации подписи MonoPay
import ecdsa  # pip install ecdsa

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

MONO_BASE = "https://api.monobank.ua"

# Кэш публичного ключа Mono (в PEM-байтах)
_MONO_PUBKEY_PEM: bytes | None = None


async def _fetch_mono_pubkey_pem() -> bytes:
    """
    Тянем ключ из /api/merchant/pubkey c X-Token и приводим к PEM bytes.
    Поддерживаем: чистый PEM; JSON с полями key/pubkey/data; base64 (с любым паддингом);
    DER → оборачиваем в PEM.
    """
    global _MONO_PUBKEY_PEM
    if _MONO_PUBKEY_PEM:
        return _MONO_PUBKEY_PEM

    headers = {"X-Token": settings.MONOPAY_TOKEN}
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{MONO_BASE}/api/merchant/pubkey", headers=headers, timeout=15) as r:
            txt = await r.text()
            if r.status != 200:
                raise RuntimeError(f"monobank pubkey error {r.status}: {txt}")

    s_txt = txt.strip().strip('"')

    # Попытка распарсить как JSON
    try:
        j = json.loads(s_txt)
        s_txt = str(j.get("key") or j.get("pubkey") or j.get("data") or s_txt).strip()
    except Exception:
        pass

    # Если уже PEM
    if "BEGIN PUBLIC KEY" in s_txt:
        _MONO_PUBKEY_PEM = s_txt.encode()
        return _MONO_PUBKEY_PEM

    # Иначе считаем это base64 (в т.ч. без паддинга). Декодим в DER и оборачиваем в PEM.
    pad = (-len(s_txt)) % 4
    if pad:
        s_txt += "=" * pad
    try:
        der = base64.b64decode(s_txt)
        b64 = base64.encodebytes(der).replace(b"\n\n", b"\n")
        pem = b"-----BEGIN PUBLIC KEY-----\n" + b64 + b"-----END PUBLIC KEY-----\n"
        _MONO_PUBKEY_PEM = pem
        return _MONO_PUBKEY_PEM
    except Exception as e:
        raise RuntimeError(f"bad pubkey format: {e}")


def _verify_mono_xsign(pubkey_pem: bytes, body: bytes, x_sign_b64: str) -> bool:
    """
    Monobank может прислать X-Sign как DER ИЛИ как "raw" 64 байта (r||s).
    Проверяем оба варианта.
    """
    try:
        # base64 с возможным отсутствующим паддингом
        s = x_sign_b64.strip()
        pad = (-len(s)) % 4
        if pad:
            s += "=" * pad
        sig = base64.b64decode(s)
    except Exception:
        return False

    try:
        vk = ecdsa.VerifyingKey.from_pem(pubkey_pem.decode())
    except Exception:
        return False

    # 1) Пытаемся как DER
    try:
        if vk.verify(sig, body, sigdecode=ecdsa.util.sigdecode_der, hashfunc=hashlib.sha256):
            return True
    except Exception:
        pass

    # 2) Пытаемся как "raw" r||s (64 байта)
    try:
        if len(sig) == 64 and vk.verify(sig, body, sigdecode=ecdsa.util.sigdecode_string, hashfunc=hashlib.sha256):
            return True
    except Exception:
        pass

    return False



# --- CryptoPay: «секретный» путь вебхука (по рекомендации доков, и без setWebhook через API)
def _crypto_secret_path() -> str:
    """
    Формирует секретный путь для вебхука Crypto Pay.
    Если в .env задан CRYPTO_WEBHOOK_PATH и он не '/cryptobot' — используем его как есть.
    Иначе строим '/cryptobot/<sha256(token)[:24]>'.
    """
    if settings.CRYPTO_WEBHOOK_PATH and settings.CRYPTO_WEBHOOK_PATH != "/cryptobot":
        return settings.CRYPTO_WEBHOOK_PATH
    slug = hashlib.sha256((settings.CRYPTO_PAY_TOKEN or "no-token").encode()).hexdigest()[:24]
    return f"/cryptobot/{slug}"


async def on_startup(bot: Bot):
    await connect()
    await ensure_schema()
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
    """
    Crypto Pay: подпись — HMAC_SHA256(body, key=SHA256(token)), заголовок: crypto-pay-api-signature
    """
    sig = req.headers.get("crypto-pay-api-signature", "")
    secret = hashlib.sha256((settings.CRYPTO_PAY_TOKEN or "").encode()).digest()
    calc = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, calc)


async def _handle_cryptobot_webhook(request: web.Request):
    body = await request.read()
    # Если есть токен — валидируем подпись (как в доках)
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
    Если не ОК — 403 (monobank ретраит несколько раз).
    После валидации меняем статус платежа и активируем юзера.
    """
    raw = await request.read()
    x_sign = request.headers.get("X-Sign") or request.headers.get("x-sign")
    if not x_sign:
        return web.Response(status=403, text="no signature")

    # 1) верифицируем подпись; при фейле 1 раз обновим ключ и попробуем снова (ротация у банка)
    try:
        pubkey_pem = await _fetch_mono_pubkey_pem()
        ok = _verify_mono_xsign(pubkey_pem, raw, x_sign)
        if not ok:
            global _MONO_PUBKEY_PEM
            _MONO_PUBKEY_PEM = None
            pubkey_pem = await _fetch_mono_pubkey_pem()
            ok = _verify_mono_xsign(pubkey_pem, raw, x_sign)
    except Exception as e:
        log.warning("Mono webhook: pubkey load error: %s", e)
        return web.Response(status=403, text="pubkey error")

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

    # CryptoPay webhook — секретный путь
    CRYPTO_PATH = _crypto_secret_path()
    app.router.add_post(CRYPTO_PATH, _handle_cryptobot_webhook)
    log.info("CryptoPay webhook path: %s", CRYPTO_PATH)

    # MonoPay webhook
    app.router.add_post(settings.MONOPAY_WEBHOOK_PATH, _handle_monopay_webhook)

    async def on_app_start(app_):
        await on_startup(bot)

        # Telegram webhook
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


