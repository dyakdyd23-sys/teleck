import hashlib, hmac, secrets, re
from datetime import datetime,timezone,timedelta
from . import db

def password_hash(password):
    if len(password)<12 or len(password)>256:raise ValueError('비밀번호는 12~256자로 입력해 주세요.')
    salt=secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1)
    return salt.hex()+':'+digest.hex()
def verify(password,stored):
    try:
        salt,expected=stored.split(':');candidate=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1)
        return hmac.compare_digest(candidate.hex(),expected)
    except Exception:return False

def register(username,password):
    if not re.fullmatch(r'[a-zA-Z0-9_]{4,32}',username):raise ValueError('아이디는 영문·숫자·밑줄 4~32자만 가능합니다.')
    hashed=password_hash(password)
    with db.transaction() as c:
        # Negative IDs avoid collisions with Telegram positive user IDs.
        uid=c.execute('SELECT COALESCE(MIN(user_id),0)-1 FROM users').fetchone()[0]
        c.execute('INSERT INTO users(user_id,created_at) VALUES(?,?)',(uid,db.now()))
        c.execute('INSERT INTO web_accounts(user_id,username,password_hash,created_at) VALUES(?,?,?,?)',(uid,username,hashed,db.now()))
        db.audit(c,str(uid),'register')
    return uid

def login(username,password):
    with db.transaction() as c:
        row=c.execute('SELECT * FROM web_accounts WHERE username=?',(username,)).fetchone()
        if not row or not verify(password,row['password_hash']):raise ValueError('아이디 또는 비밀번호가 일치하지 않습니다.')
        token=secrets.token_urlsafe(32)
        expiration=(datetime.now(timezone.utc)+timedelta(days=7)).isoformat()
        c.execute('INSERT INTO login_sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['user_id'],expiration))
    return token

def user_from_token(token):
    if not token:return None
    with db.connect() as c:
        row=c.execute('SELECT user_id FROM login_sessions WHERE token_hash=? AND expires_at>?',(hashlib.sha256(token.encode()).hexdigest(),db.now())).fetchone()
        return row['user_id'] if row else None

def logout(token):
    with db.transaction() as c:c.execute('DELETE FROM login_sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
