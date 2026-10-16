"""Read one published Google Sheets cell; treat USD/KRW as the operator-selected USDT proxy."""
import asyncio, csv, io, logging, time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse
import httpx
from . import config, db

log = logging.getLogger(__name__)
_cached_rate = 0
_cached_at = 0.0
MAX_RATE_AGE = 600  # fail closed after 10 minutes

def available_rate():
    return _cached_rate if _cached_rate > 0 and time.monotonic() - _cached_at < MAX_RATE_AGE else 0

async def refresh_once():
    global _cached_rate, _cached_at
    url = config.GOOGLE_SHEET_CSV_URL
    parsed = urlparse(url)
    if not (parsed.scheme == 'https' and parsed.hostname in ('docs.google.com', 'drive.google.com') and parsed.path.startswith('/spreadsheets/')):
        raise ValueError('GOOGLE_SHEET_CSV_URL must be a Google Sheets published CSV URL')
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > 8192:
            raise ValueError('Google Sheets CSV is unexpectedly large')
        rows = list(csv.reader(io.StringIO(response.text.lstrip('\ufeff'))))
    if not rows or not rows[0]:
        raise ValueError('Google Sheets A1 is empty')
    try:
        raw = rows[0][0].strip().replace(',', '')
        price = Decimal(raw)
        if not price.is_finite() or not Decimal('100') <= price <= Decimal('10000'):
            raise ValueError('Invalid USD/KRW cell value')
        rate = int(price.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('Google Sheets A1 must contain a numeric USD/KRW exchange rate') from exc
    # DB is for visibility only; payment authorization always checks fresh in-memory rate.
    with db.transaction() as conn:
        conn.execute("UPDATE settings SET value=? WHERE key='usdt_krw_rate'", (str(rate),))
    _cached_rate, _cached_at = rate, time.monotonic()
    return rate

async def run(stop):
    while not stop.is_set():
        try:
            if config.GOOGLE_SHEET_CSV_URL:
                await refresh_once()
            else:
                log.warning('Google Sheet rate URL is absent; USDT credit paused')
        except Exception as exc:
            log.warning('Google Sheet rate unavailable: %s', type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=120)
        except asyncio.TimeoutError:
            pass
