import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from . import config

def now(): return datetime.now(timezone.utc).isoformat()

def connect():
    config.DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

@contextmanager
def transaction():
    con = connect()
    try:
        con.execute('BEGIN IMMEDIATE')
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def init():
    config.STORAGE.mkdir(parents=True, exist_ok=True)
    with transaction() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, balance_tenths INTEGER NOT NULL DEFAULT 0 CHECK(balance_tenths>=0), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(user_id),status TEXT NOT NULL,
          original_count INTEGER NOT NULL,invalid_count INTEGER NOT NULL,duplicate_count INTEGER NOT NULL,
          valid_count INTEGER NOT NULL,unit_tenths INTEGER NOT NULL,charged_tenths INTEGER NOT NULL DEFAULT 0,
          refunded_tenths INTEGER NOT NULL DEFAULT 0,processed_count INTEGER NOT NULL DEFAULT 0,
          success_count INTEGER NOT NULL DEFAULT 0,failed_count INTEGER NOT NULL DEFAULT 0,
          source_file TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,error TEXT,
          CHECK(status IN ('pending','processing','completed','failed','cancelled')));
        CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id,created_at);
        CREATE TABLE IF NOT EXISTS phones(job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,phone TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', joined TEXT, uid TEXT, username TEXT, last_active TEXT,
          PRIMARY KEY(job_id,phone));
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          state TEXT NOT NULL DEFAULT 'pending',api_task_id TEXT,attempts INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL,error TEXT,lease_until TEXT,UNIQUE(api_task_id));
        CREATE INDEX IF NOT EXISTS idx_batches_state ON batches(state,updated_at);
        CREATE TABLE IF NOT EXISTS batch_phones(batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
          phone TEXT NOT NULL, PRIMARY KEY(batch_id,phone));
        CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,
          job_id TEXT,kind TEXT NOT NULL,amount_tenths INTEGER NOT NULL,reason TEXT,actor TEXT,
          idempotency_key TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT NOT NULL,
          action TEXT NOT NULL,job_id TEXT,detail TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,source_file TEXT NOT NULL,
          created_at TEXT NOT NULL);
        ''')
        c.executescript("""
        CREATE TABLE IF NOT EXISTS web_accounts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE REFERENCES users(user_id),username TEXT NOT NULL UNIQUE COLLATE NOCASE,password_hash TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS login_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(user_id),expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deposit_address_pool(address TEXT PRIMARY KEY,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deposit_addresses(address TEXT PRIMARY KEY,user_id INTEGER NOT NULL UNIQUE REFERENCES users(user_id),assigned_at TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,scan_after_ms INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(user_id), txid TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE, address TEXT NOT NULL,usdt_micro INTEGER NOT NULL,krw_tenths INTEGER NOT NULL,rate_krw INTEGER NOT NULL,confirmed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_deposits_user ON deposits(user_id,confirmed_at);
        CREATE TABLE IF NOT EXISTS trx_deposits(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(user_id),txid TEXT NOT NULL UNIQUE,address TEXT NOT NULL,amount_sun INTEGER NOT NULL,krw_tenths INTEGER NOT NULL,rate_krw INTEGER NOT NULL,confirmed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_trx_deposits_user ON trx_deposits(user_id,confirmed_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_deposits_txid ON deposits(txid);
        CREATE TABLE IF NOT EXISTS result_archives(job_id TEXT PRIMARY KEY REFERENCES jobs(id), sha256 TEXT NOT NULL, delivered_to_user_at TEXT, delivered_to_admin_at TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS admin_deliveries(job_id TEXT NOT NULL REFERENCES jobs(id), admin_id INTEGER NOT NULL, delivered_at TEXT NOT NULL, document_sent INTEGER NOT NULL, PRIMARY KEY(job_id,admin_id));
        """)
        c.execute("INSERT OR IGNORE INTO settings VALUES('usdt_krw_rate','0')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('trx_krw_rate','0')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('unit_tenths','6')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('min_count','5000')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('max_count','1000000')")

def setting(c,key): return int(c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()['value'])
def ensure_user(c,user_id):
    c.execute('INSERT OR IGNORE INTO users(user_id,created_at) VALUES(?,?)',(user_id,now()))

def money(tenths):
    return f'{tenths//10:,}.{tenths%10}원'

def audit(c,actor,action,job_id=None,detail=None):
    c.execute('INSERT INTO audit(actor,action,job_id,detail,created_at) VALUES(?,?,?,?,?)',(actor,action,job_id,detail,now()))
