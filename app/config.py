from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMINS: str
    DATABASE_URL: str
    CRYPTOCLOUD_API_KEY: str
    CRYPTOCLOUD_SHOP_ID: str
    CRYPTOCLOUD_POSTBACK_SECRET: str
    ENTRY_AMOUNT_USD: float = 1.0
    PUBLIC_URL: str
    WEBHOOK_SECRET: str
    DAILY_TASK_LIMIT: int = 10
    RESET_TZ: str = "Europe/Kyiv"
    TECH_CHAT_ID: int | None = None

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
ADMINS_LIST: List[int] = [int(x) for x in settings.ADMINS.split(",") if x.strip()]