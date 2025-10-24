from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from contextlib import asynccontextmanager
from sqlalchemy import select, update
from .config import settings
from .db import AsyncSessionLocal
from .middlewares import DBSessionMiddleware
from .routers.user import user_router
from .routers.admin import admin_router
from .models import Users, Payments, UserStatus
from .services.cryptocloud import verify_postback_jwt
from .services.referrals import grant_referral_bonus
from .db import engine, Base
bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())
dp.message.middleware(DBSessionMiddleware())
dp.callback_query.middleware(DBSessionMiddleware())
dp.include_router(user_router)
dp.include_router(admin_router)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1) створимо таблиці, якщо їх ще нема (це не заміна Alembic, але розблокує запуск)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2) ставимо вебхук
    await bot.set_webhook(
        url=f"{settings.PUBLIC_URL}/tg/{settings.WEBHOOK_SECRET}",
        secret_token=settings.WEBHOOK_SECRET,
        drop_pending_updates=True,
        allowed_updates=["message","callback_query"]
    )
    yield


app = FastAPI(lifespan=lifespan)

@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"

@app.post("/tg/{secret}")
async def tg_webhook(secret: str, request: Request):
    if secret != settings.WEBHOOK_SECRET:
        raise HTTPException(403, "bad secret path")
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != settings.WEBHOOK_SECRET:
        raise HTTPException(403, "bad secret header")
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

# CryptoCloud POSTBACK — перевірка JWT + активація
@app.post("/cc/callback")
async def cc_callback(request: Request):
    payload = await request.json()
    token = payload.get("token")
    if not token:
        raise HTTPException(400, "missing token")
    decoded = verify_postback_jwt(token)
    if not decoded:
        raise HTTPException(403, "invalid token")
    inv_info = payload.get("invoice_info") or {}
    uuid = inv_info.get("uuid") or payload.get("invoice_id")
    status = (inv_info.get("status") or "").lower()  # created/paid/partial/overpaid/canceled
    if not uuid:
        raise HTTPException(400, "no uuid")
    # Ідемпотентно оновлюємо в БД
    async with AsyncSessionLocal() as sess:
        # знайдемо платіж
        res = await sess.execute(select(Payments).where(Payments.uuid == uuid))
        p = res.scalar()
        if not p:
            # Може бути, що інвойс створений не через кнопку 'Я оплатив', але ми все одно відмітимо
            # Створимо запис і спробуємо знайти user за order_id, якщо передавався
            p = Payments(user_id=None, uuid=uuid, amount_usd=float(inv_info.get("amount_usd") or 0), status=status)
            sess.add(p)
        else:
            p.status = status
        # Якщо оплачено — активуємо користувача (через order_id u<user_id> у create_invoice)
        order_id = payload.get("order_id") or inv_info.get("project", {}).get("name") or None
        # ми передавали order_id=f"u{user.id}" при створенні
        ref_user_id = None
        if order_id and str(order_id).startswith("u"):
            try:
                ref_user_id = int(str(order_id)[1:])
            except Exception:
                ref_user_id = None
        if status in {"paid", "overpaid", "partial", "success"} and ref_user_id:
            await sess.execute(update(Users).where(Users.id == ref_user_id).values(status=UserStatus.active))
            # рефералка
            await grant_referral_bonus(sess, referee_id=ref_user_id)
        await sess.commit()
    return {"ok": True}
