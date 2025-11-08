import asyncio, sys, logging, hmac, hashlib, json, base64, os, aiohttp
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
from aiocryptopay import AioCryptoPay, Networks  # лишаю для payments.py

# cryptography — надійна валідація MonoPay (DER/RAW + urlsafe b64) і парс PEM/DER ключа/сертифіката
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509 import load_pem_x509_certificate, load_der_x509_certificate
from cryptography.exceptions import InvalidSignature

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

MONO_BASE = "https://api.monobank.ua"

# Кеш публічного ключа Mono
_MONO_PUBKEY_PEM: bytes | None = None
_MONO_PUBKEY_OBJ = None  # ec.EllipticCurvePublicKey


# ===================== Mono: робота з публічним ключем =====================

def _try_parse_pubkey_from_text(text: str) -> bytes | None:
    """
    Принимает: PEM ключ, PEM сертификат, JSON с полем ключа,
    base64/url-safe base64 DER (ключ/сертификат),
    И ТАКЖЕ base64 от PEM (твой кейс).
    Возвращает PEM публичного ключа (bytes) или None.
    """
    s = (text or "").strip().strip('"')

    # поддержка "одной строки" PEM с \n
    if "\\n" in s and "BEGIN " in s:
        s = s.replace("\\n", "\n")

    # 1) JSON-обёртка
    try:
        j = json.loads(s)
        s = str(j.get("key") or j.get("pubkey") or j.get("data") or s).strip()
    except Exception:
        pass

    # 2) Уже PEM public key?
    if "BEGIN PUBLIC KEY" in s:
        return s.encode()

    # 3) PEM сертификат -> достаём ключ
    if "BEGIN CERTIFICATE" in s:
        try:
            from cryptography.x509 import load_pem_x509_certificate
            from cryptography.hazmat.primitives import serialization
            cert = load_pem_x509_certificate(s.encode())
            pk = cert.public_key()
            return pk.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        except Exception:
            return None

    # 4) Пробуем как base64/url-safe base64
    def _b64decode_any(b64s: str) -> bytes | None:
        b64s = "".join(b64s.split())  # убираем пробелы/переносы
        try:
            pad = (-len(b64s)) % 4
            return base64.b64decode(b64s + ("=" * pad if pad else ""))
        except Exception:
            try:
                pad = (-len(b64s)) % 4
                b64s2 = b64s.replace("-", "+").replace("_", "/") + ("=" * pad if pad else "")
                return base64.b64decode(b64s2)
            except Exception:
                return None

    raw = _b64decode_any(s)
    if not raw:
        return None

    # 4a) вдруг это base64-от-PEM (декод получился текстом с -----BEGIN ...)
    try:
        txt = raw.decode("utf-8", "ignore")
        if "BEGIN PUBLIC KEY" in txt:
            return txt.encode()
        if "BEGIN CERTIFICATE" in txt:
            from cryptography.x509 import load_pem_x509_certificate
            from cryptography.hazmat.primitives import serialization
            cert = load_pem_x509_certificate(txt.encode())
            pk = cert.public_key()
            return pk.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
    except Exception:
        pass

    # 4b) DER public key
    try:
        from cryptography.hazmat.primitives import serialization
        pk = serialization.load_der_public_key(raw)
        return pk.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except Exception:
        pass

    # 4c) DER certificate
    try:
        from cryptography.x509 import load_der_x509_certificate
        from cryptography.hazmat.primitives import serialization
        cert = load_der_x509_certificate(raw)
        pk = cert.public_key()
        return pk.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except Exception:
        pass

    return None


async def _fetch_mono_pubkey_pem() -> bytes:
    """
    Джерела в порядку пріоритету:
      1) MONOPAY_PUBKEY або MONOPAY_PUBKEY_PEM з .env (будь-який формат, див. _try_parse_pubkey_from_text)
      2) GET /api/merchant/pubkey (X-Token), з подальшим розбором як вище
    На виході — PEM публічного ключа (bytes).
    """
    global _MONO_PUBKEY_PEM
    if _MONO_PUBKEY_PEM:
        return _MONO_PUBKEY_PEM

    # 1) .env
    env_val = os.getenv("MONOPAY_PUBKEY") or os.getenv("MONOPAY_PUBKEY_PEM")
    if env_val:
        pem = _try_parse_pubkey_from_text(env_val)
        if not pem:
            raise RuntimeError("MONOPAY_PUBKEY env is set but can't be parsed")
        _MONO_PUBKEY_PEM = pem
        return _MONO_PUBKEY_PEM

    # 2) API
    headers = {"X-Token": settings.MONOPAY_TOKEN}
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{MONO_BASE}/api/merchant/pubkey", headers=headers, timeout=15) as r:
            txt = await r.text()
            if r.status != 200:
                raise RuntimeError(f"monobank pubkey error {r.status}: {txt}")

    pem = _try_parse_pubkey_from_text(txt)
    if not pem:
        raise RuntimeError("monobank pubkey: can't parse format")
    _MONO_PUBKEY_PEM = pem
    return _MONO_PUBKEY_PEM


def _load_mono_pubkey_obj() -> None:
    """Ініціалізуємо cryptography-об’єкт публічного ключа з PEM і логнемо відбиток."""
    global _MONO_PUBKEY_OBJ
    if _MONO_PUBKEY_OBJ is not None:
        return
    if not _MONO_PUBKEY_PEM:
        raise RuntimeError("mono pubkey pem not loaded")
    _MONO_PUBKEY_OBJ = serialization.load_pem_public_key(_MONO_PUBKEY_PEM)
    fp = hashlib.sha256(
        _MONO_PUBKEY_OBJ.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).hexdigest()[:16]
    log.info("Mono pubkey fingerprint: %s", fp)


def _reset_mono_pubkey_cache():
    """Скинути кеші ключа Mono."""
    global _MONO_PUBKEY_PEM, _MONO_PUBKEY_OBJ
    _MONO_PUBKEY_PEM = None
    _MONO_PUBKEY_OBJ = None


def _decode_b64_maybe_urlsafe(s: str) -> bytes:
    s = s.strip()
    try:
        pad = (-len(s)) % 4
        return base64.b64decode(s + ("=" * pad if pad else ""))
    except Exception:
        pad = (-len(s)) % 4
        s2 = s.replace("-", "+").replace("_", "/") + ("=" * pad if pad else "")
        return base64.b64decode(s2)


def _verify_mono_xsign(body: bytes, x_sign_b64: str) -> bool:
    """
    Валідація X-Sign: спочатку DER, якщо ні — raw r||s (64 байти).
    """
    if not _MONO_PUBKEY_OBJ:
        raise RuntimeError("mono pubkey obj not initialized")
    try:
        sig = _decode_b64_maybe_urlsafe(x_sign_b64)
    except Exception:
        return False

    # 1) DER
    try:
        _MONO_PUBKEY_OBJ.verify(sig, body, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        pass
    except Exception:
        pass

    # 2) RAW r||s -> DER
    try:
        if len(sig) == 64:
            r = int.from_bytes(sig[:32], "big")
            s = int.from_bytes(sig[32:], "big")
            sig_der = utils.encode_dss_signature(r, s)
            _MONO_PUBKEY_OBJ.verify(sig_der, body, ec.ECDSA(hashes.SHA256()))
            return True
    except InvalidSignature:
        pass
    except Exception:
        pass

    return False


# ===================== CryptoPay: секретний шлях вебхука =====================

def _crypto_secret_path() -> str:
    if settings.CRYPTO_WEBHOOK_PATH and settings.CRYPTO_WEBHOOK_PATH != "/cryptobot":
        return settings.CRYPTO_WEBHOOK_PATH
    slug = hashlib.sha256((settings.CRYPTO_PAY_TOKEN or "no-token").encode()).hexdigest()[:24]
    return f"/cryptobot/{slug}"


# ===================== Надійне підключення до БД з ретраями =====================

async def _connect_db_with_retry(max_tries: int = 8) -> None:
    delay = 0.5
    last_err = None
    for i in range(1, max_tries + 1):
        try:
            await connect()
            if i > 1:
                log.info("DB connected on try %s", i)
            return
        except Exception as e:
            last_err = e
            log.warning("DB connect failed (try %s/%s): %s", i, max_tries, e)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)
    raise last_err


async def on_startup(bot: Bot):
    await _connect_db_with_retry()
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
    # Підвантажимо і проініціалізуємо ключ Mono
    if settings.MONOPAY_TOKEN:
        try:
            await _fetch_mono_pubkey_pem()
            _load_mono_pubkey_obj()
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


# ===================== Webhook app =====================

async def _verify_crypto_signature(req: web.Request, body: bytes) -> bool:
    """Crypto Pay: HMAC_SHA256(body, key=SHA256(token)), header: crypto-pay-api-signature"""
    sig = req.headers.get("crypto-pay-api-signature", "")
    secret = hashlib.sha256((settings.CRYPTO_PAY_TOKEN or "").encode()).digest()
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

        await execute("UPDATE payments SET status='paid' WHERE provider='cryptobot' AND uuid=$1", inv)
        row = await fetchrow("SELECT user_id FROM payments WHERE provider='cryptobot' AND uuid=$1", inv)
        if row:
            await execute("UPDATE users SET status='active' WHERE id=$1", row["user_id"])
        log.info("CryptoBot invoice_paid: %s", inv)

    return web.json_response({"ok": True})


async def _handle_monopay_webhook(request: web.Request):
    """
    X-Sign: перевіряємо DER і RAW r||s; підтримуємо звич./urlsafe base64.
    На success — помічаємо платіж і активуємо користувача.
    """
    raw = await request.read()
    x_sign = request.headers.get("X-Sign") or request.headers.get("x-sign")
    if not x_sign:
        return web.Response(status=403, text="no signature")

    # Перевірка підпису; при фейлі один раз оновимо ключ (можлива ротація)
    try:
        if _MONO_PUBKEY_PEM is None or _MONO_PUBKEY_OBJ is None:
            await _fetch_mono_pubkey_pem()
            _load_mono_pubkey_obj()
        ok = _verify_mono_xsign(raw, x_sign)
        if not ok:
            _reset_mono_pubkey_cache()
            await _fetch_mono_pubkey_pem()
            _load_mono_pubkey_obj()
            ok = _verify_mono_xsign(raw, x_sign)
    except Exception as e:
        log.warning("Mono webhook: pubkey load error: %s", e)
        return web.Response(status=403, text="pubkey error")

    if not ok:
        log.warning("Mono webhook: invalid X-Sign")
        return web.Response(status=403, text="bad signature")

    data = json.loads(raw.decode("utf-8"))
    status = (data.get("status") or "").lower()
    info = data.get("merchantPaymInfo") or {}
    reference = info.get("reference") or data.get("reference")
    invoice_id = data.get("invoiceId") or data.get("invoice_id")

    if status == "success":
        await execute("""
            UPDATE payments SET status='paid'
            WHERE provider='monopay' AND (uuid=$1 OR uuid=$2 OR order_id=$3)
        """, str(invoice_id), str(reference), str(reference))

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

    # CryptoPay webhook — секретний шлях
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


