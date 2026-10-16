import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
ROOT = Path(__file__).resolve().parent.parent

def env_path(name, default):
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else ROOT / p

DB = env_path('DATABASE_PATH', 'data/checker.sqlite3')
STORAGE = env_path('STORAGE_PATH', 'data/uploads')
CONTRACT = env_path('API_CONTRACT_FILE', 'api_contract.json')
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_TELEGRAM_IDS', '').split(',') if x.strip().isdigit()}
WEB_TOKEN = os.getenv('ADMIN_WEB_TOKEN', '')
API_URL = os.getenv('TELEGRAM_CHECK_API_URL', '')
API_KEY = os.getenv('TELEGRAM_CHECK_API_KEY', '')
SUPPORT = os.getenv('CUSTOMER_SUPPORT', '@support')
MAX_BYTES = int(os.getenv('MAX_UPLOAD_MB', '30')) * 1024 * 1024
POLL_SECONDS = max(5, int(os.getenv('POLL_INTERVAL_SECONDS', '15')))
RETENTION_DAYS = int(os.getenv('RESULT_RETENTION_DAYS', '30'))

TRONGRID_KEY = os.getenv('TRONGRID_API_KEY','')
TRONGRID_URL = os.getenv('TRONGRID_URL','https://api.trongrid.io').rstrip('/')
DEPOSIT_POLL_SECONDS = max(60,int(os.getenv('DEPOSIT_POLL_SECONDS','120')))
BASE_URL=os.getenv('WEB_BASE_URL','').rstrip('/')
