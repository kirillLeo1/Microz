from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
import hmac

def now_utc() -> datetime:
    return datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))

KYIV_TZ = ZoneInfo("Europe/Kyiv")

def today_kyiv_bounds():
    now_k = datetime.now(KYIV_TZ)
    start = now_k.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end

def timing_safe_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())