import asyncio, sys, logging, os
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from .config import settings
from .db import connect, close
from .schema import ensure_schema
from .handlers import start, profile, tasks, withdraw, admin
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
logging.basicConfig(level=logging.INFO)

def main_kb(lang: str):
    if lang=="uk":
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🎯 Завдання")],
                      [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="💸 Вивід коштів")]],
            resize_keyboard=True
        )
    if lang=="ru":
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🎯 Задания")],
                      [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💸 Вывод средств")]],
            resize_keyboard=True
        )
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🎯 Tasks")],
                  [KeyboardButton(text="👤 Profile"), KeyboardButton(text="💸 Withdraw")]],
        resize_keyboard=True
    )

async def on_startup(bot: Bot):
    await connect()
    await ensure_schema()
    await bot.set_my_commands([
        BotCommand(command="start", description="Start"),
        BotCommand(command="help", description="Help"),
        BotCommand(command="admin", description="Admin panel"),
    ])

async def on_shutdown(bot: Bot):
    await close()

async def polling():
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(tasks.router)
    dp.include_router(withdraw.router)
    dp.include_router(admin.router)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    async def startup(_):
        await on_startup(bot)

    async def shutdown(_):
        await on_shutdown(bot)

    try:
        await dp.start_polling(bot, on_startup=startup, on_shutdown=shutdown)
    finally:
        await bot.session.close()

async def webhook():
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(tasks.router)
    dp.include_router(withdraw.router)
    dp.include_router(admin.router)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    app = web.Application()

    async def handle(request: web.Request):
        bot_path = settings.WEBHOOK_PATH or "/webhook"
        if request.path != bot_path:
            return web.Response(text="OK")
        data = await request.json()
        await dp.feed_webhook_update(bot, data)
        return web.Response(text="ok")

    app.router.add_post(settings.WEBHOOK_PATH, handle)

    async def on_app_start(app_):
        await on_startup(bot)
        wh_url = (settings.WEBHOOK_URL or "").rstrip("/") + settings.WEBHOOK_PATH
        await bot.set_webhook(wh_url, drop_pending_updates=True)

    async def on_app_stop(app_):
        await on_shutdown(bot)
        await bot.delete_webhook()

    app.on_startup.append(on_app_start)
    app.on_shutdown.append(on_app_stop)
    port = int(os.environ.get("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    # Keep alive
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    if "--polling" in sys.argv:
        asyncio.run(polling())
    else:
        asyncio.run(webhook())
