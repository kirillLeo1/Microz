from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List

class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS: List[int] = Field(default_factory=list)
    DATABASE_URL: str
    CRYPTOCLOUD_API_KEY: str = ""
    CRYPTOCLOUD_PRICE_USD: float = 1.00
    TEST_MODE: bool = False
    WEBHOOK_URL: str | None = None
    WEBHOOK_PATH: str = "/webhook"
    TZ_KYIV: str = "Europe/Kyiv"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
