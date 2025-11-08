from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List

class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS: List[int] = Field(default_factory=list)
    DATABASE_URL: str

    # === Глобальная цена
    PRICE_USD: float = 2.0
    USD_TO_UAH: float = 40.0  # курс для конвертации в UAH (можно переопределить в .env)

    # === MonoPay
    MONOPAY_TOKEN: str = ""                 # X-Token мерчанта
    MONOPAY_WEBHOOK_PATH: str = "/monopay"  # путь вебхука
    # Минимальный вывод в «монетах»/поинтах бота
    MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", "10000"))  # 10 000 ~= $50

    # === CryptoBot
    CRYPTO_PAY_TOKEN: str = ""                 # токен из @CryptoBot
    CRYPTO_WEBHOOK_PATH: str = "/cryptobot"    # путь вебхука

    TEST_MODE: bool = False
    WEBHOOK_URL: str | None = None
    WEBHOOK_PATH: str = "/webhook"
    TZ_KYIV: str = "Europe/Kyiv"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
