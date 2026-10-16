"""TRC-20 USDT incoming transfer confirmation. Does not create wallets or hold keys."""
import asyncio,logging,time,hashlib,uuid
from decimal import Decimal,ROUND_DOWN,ROUND_CEILING,InvalidOperation
from datetime import datetime,timezone
import httpx
from . import db,config,sheet_rate
log=logging.getLogger(__name__)
USDT_CONTRACT='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'

def valid_tron_address(address):
    alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    if not isinstance(address,str) or len(address)!=34 or any(ch not in alphabet for ch in address):return False
    value=0
    for char in address:value=value*58+alphabet.index(char)
    raw=value.to_bytes(25,'big')
    payload,checksum=raw[:-4],raw[-4:]
    return payload[0]==0x41 and hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]==checksum

def assign(user_id,address):
    if not valid_tron_address(address):raise ValueError('TRON 주소 체크섬 검증 실패')
    # Start after assignment: do not attribute historical deposits to a new customer.
    with db.transaction() as c:
        if not c.execute('SELECT 1 FROM users WHERE user_id=?',(user_id,)).fetchone():raise ValueError('사용자가 없습니다.')
        if c.execute('SELECT 1 FROM deposit_addresses WHERE user_id=?',(user_id,)).fetchone():raise ValueError('기존 입금주소 교체는 지원하지 않습니다. 별도 대조가 필요합니다.')
        c.execute('INSERT INTO deposit_addresses VALUES(?,?,?,?,?)',(address,user_id,db.now(),1,int(time.time()*1000)))
        db.audit(c,'admin','assign_deposit_address',detail=f'user={user_id},address={address}')

def add_to_pool(address, actor='admin'):
    """Add an operator-controlled receiving address. No wallet secrets are stored."""
    if not valid_tron_address(address):raise ValueError('TRON 주소 체크섬 검증 실패')
    with db.transaction() as c:
        if c.execute('SELECT 1 FROM deposit_addresses WHERE address=?',(address,)).fetchone():
            raise ValueError('이미 회원에게 배정된 주소입니다.')
        c.execute('INSERT INTO deposit_address_pool VALUES(?,?)',(address,db.now()))
        db.audit(c,actor,'add_deposit_address_pool',detail='address='+address)

def auto_assign(user_id):
    """Atomically assign the next administrator-registered unused address."""
    with db.transaction() as c:
        existing=c.execute('SELECT address FROM deposit_addresses WHERE user_id=? AND active=1',(user_id,)).fetchone()
        if existing:return existing['address']
        if not c.execute('SELECT 1 FROM users WHERE user_id=?',(user_id,)).fetchone():raise ValueError('회원 없음')
        available=c.execute("""SELECT p.address FROM deposit_address_pool p
            LEFT JOIN deposit_addresses a ON a.address=p.address
            WHERE a.address IS NULL ORDER BY p.created_at,p.address LIMIT 1""").fetchone()
        if not available:return None
        address=available['address']
        c.execute('INSERT INTO deposit_addresses VALUES(?,?,?,?,?)',(address,user_id,db.now(),1,int(time.time()*1000)))
        db.audit(c,'system','auto_assign_deposit_address',detail=f'user={user_id},address={address}')
        return address


QUOTE_DURATION_SECONDS=1800

def create_topup_request(user_id, amount, rate):
    """Lock the rate for a 30-minute invoice. USD/KRW is the operator-selected USDT proxy."""
    try:
        krw=Decimal(str(amount))
        if not krw.is_finite() or krw.as_tuple().exponent < -1 or krw < 1000 or krw > 10000000:
            raise ValueError('충전 신청은 1,000원~10,000,000원, 최대 0.1원 단위로 입력해 주세요.')
        tenths=int(krw*10)
        if rate<=0:raise ValueError('환율을 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.')
        milli=int((krw*Decimal(1000)/Decimal(rate)).to_integral_value(rounding=ROUND_CEILING))
        if milli<=0:raise ValueError('충전 수량 계산 오류')
    except (InvalidOperation,TypeError) as exc:raise ValueError('충전 금액 형식이 올바르지 않습니다.') from exc
    address=auto_assign(user_id)
    if not address:raise ValueError('충전주소가 준비되지 않았습니다. 고객센터에 문의해 주세요.')
    if not config.TRONGRID_KEY:raise ValueError('입금 확인 서비스가 준비되지 않았습니다.')
    with db.transaction() as c:
        c.execute("UPDATE topup_requests SET status='expired' WHERE user_id=? AND status='pending' AND expires_at_ms<=?",(user_id,int(time.time()*1000)))
        existing=c.execute("SELECT 1 FROM topup_requests WHERE user_id=? AND status='pending'",(user_id,)).fetchone()
        if existing:raise ValueError('진행 중인 충전 신청이 있습니다. 완료하거나 만료된 후 다시 신청해 주세요.')
        rid=uuid.uuid4().hex
        expiry=int(time.time()*1000)+QUOTE_DURATION_SECONDS*1000
        c.execute("INSERT INTO topup_requests(id,user_id,address,requested_tenths,usdt_milli,rate_krw,status,created_at,expires_at_ms) VALUES(?,?,?,?,?,?,'pending',?,?)",(rid,user_id,address,tenths,milli,int(rate),db.now(),expiry))
        db.audit(c,str(user_id),'topup_requested',detail=f'id={rid};krw_tenths={tenths};usdt_milli={milli};rate={rate}')
        db.admin_event(c,'topup_requested',user_id,None,f'💳 충전 신청\n회원: {user_id}\n신청: {db.money(tenths)}\n송금 예정: {milli/1000:.3f} USDT\n신청번호: {rid[:12]}')
    return {'id':rid,'address':address,'requested_tenths':tenths,'usdt_milli':milli,'rate':int(rate),'expires_at_ms':expiry}

def active_topup(user_id):
    with db.transaction() as c:
        c.execute("UPDATE topup_requests SET status='expired' WHERE user_id=? AND status='pending' AND expires_at_ms<=?",(user_id,int(time.time()*1000)))
        row=c.execute("SELECT * FROM topup_requests WHERE user_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",(user_id,)).fetchone()
    return dict(row) if row else None

def cancel_topup(user_id):
    with db.transaction() as c:
        count=c.execute("UPDATE topup_requests SET status='cancelled' WHERE user_id=? AND status='pending'",(user_id,)).rowcount
        if count:db.audit(c,str(user_id),'topup_cancelled')
    return count

def coin_display(micro):
    return f'{Decimal(micro)/Decimal(1_000_000):,.3f}'

def record_trx(transfer,address,user_id,assigned_after,rate):
    """TRX credit intentionally disabled. Kept as a compatibility stub."""
    return False

def tron_hex_to_base58(value):
    raw=bytes.fromhex(value)
    if len(raw)!=21 or raw[0]!=0x41:raise ValueError('Invalid TRON hex address')
    checked=raw+hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    number=int.from_bytes(checked,'big');out=''
    while number:number,r=divmod(number,58);out=alphabet[r]+out
    return out

def record(transfer,address,user_id,assigned_after,rate=None):
    """Credit only an exact, confirmed USDT transfer matching one unexpired invoice.

    Unsolicited, early, late and mismatched payments are logged for manual review,
    never automatically posted to the customer's balance.
    """
    if transfer.get('to')!=address or transfer.get('type')!='Transfer':return False
    if transfer.get('confirmed') is False or transfer.get('contractRet') not in (None,'SUCCESS'):return False
    token=transfer.get('token_info') or {}
    if token.get('address')!=USDT_CONTRACT or str(token.get('decimals'))!='6':return False
    txid=transfer.get('transaction_id')
    if not isinstance(txid,str) or len(txid)!=64 or any(x not in '0123456789abcdefABCDEF' for x in txid):return False
    try:micro=int(transfer.get('value','0'));ts=int(transfer.get('block_timestamp',0))
    except (TypeError,ValueError):return False
    if ts<assigned_after or micro<=0:return False
    with db.transaction() as c:
        if c.execute('SELECT 1 FROM deposits WHERE txid=?',(txid,)).fetchone():return False
        if c.execute('SELECT 1 FROM unmatched_deposits WHERE txid=?',(txid,)).fetchone():return False
        invoice=c.execute("""SELECT * FROM topup_requests WHERE user_id=? AND address=? AND status='pending'
          AND expires_at_ms>=? ORDER BY created_at LIMIT 1""",(user_id,address,ts)).fetchone()
        # Prevent matching transfers made before invoice issuance. ISO8601 UTC creation timestamp.
        from datetime import datetime,timezone
        created_ms=int(datetime.fromisoformat(invoice['created_at']).timestamp()*1000) if invoice else 0
        if invoice and micro==invoice['usdt_milli']*1000 and created_ms<=ts<=invoice['expires_at_ms']:
            c.execute('INSERT INTO deposits(user_id,txid,event_key,address,usdt_micro,krw_tenths,rate_krw,confirmed_at) VALUES(?,?,?,?,?,?,?,?)',(user_id,txid,txid,address,micro,invoice['requested_tenths'],invoice['rate_krw'],db.now()))
            c.execute('INSERT INTO ledger(user_id,kind,amount_tenths,reason,actor,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?)',(user_id,'usdt_deposit',invoice['requested_tenths'],f'TRC20 invoice {invoice["id"]} exact match', 'chain','trx:'+txid,db.now()))
            c.execute('UPDATE users SET balance_tenths=balance_tenths+? WHERE user_id=?',(invoice['requested_tenths'],user_id))
            c.execute("UPDATE topup_requests SET status='completed',txid=?,completed_at=? WHERE id=?",(txid,db.now(),invoice['id']))
            db.admin_event(c,'topup_completed',user_id,None,f'✅ 충전 완료\n회원: {user_id}\n충전: {db.money(invoice["requested_tenths"])}\n입금: {micro/1000000:.3f} USDT\nTXID: {txid}')
            db.audit(c,'chain','usdt_invoice_paid',detail=f'user={user_id},invoice={invoice["id"]},txid={txid}')
            return True
        reason='no_matching_active_invoice_or_amount_or_timestamp'
        c.execute('INSERT OR IGNORE INTO unmatched_deposits(txid,user_id,address,usdt_micro,reason,seen_at) VALUES(?,?,?,?,?,?)',(txid,user_id,address,micro,reason,db.now()))
        db.audit(c,'chain','unmatched_usdt_received',detail=f'user={user_id},txid={txid},micro={micro}')
    return False

async def scan_once():
    if not config.TRONGRID_KEY:return 0
    # Invoices lock their exchange rates at creation; scanning must run even if current rate is stale.
    with db.connect() as c:
        addresses=[dict(x) for x in c.execute('SELECT * FROM deposit_addresses WHERE active=1')]
    count=0
    async with httpx.AsyncClient(timeout=25) as client:
        for row in addresses:
            params={'only_confirmed':'true','only_to':'true','limit':200,
                    'min_timestamp':row['scan_after_ms'],'contract_address':USDT_CONTRACT}
            path=f"/v1/accounts/{row['address']}/transactions/trc20"
            fingerprint=None;collected=[];exhausted=False
            for _ in range(100):
                if fingerprint:params['fingerprint']=fingerprint
                response=await client.get(config.TRONGRID_URL+path,params=params,
                    headers={'TRON-PRO-API-KEY':config.TRONGRID_KEY})
                response.raise_for_status();payload=response.json()
                if not payload.get('success',True):raise ValueError('TronGrid unavailable')
                collected.extend(payload.get('data') or [])
                next_fp=(payload.get('meta') or {}).get('fingerprint')
                if not next_fp or next_fp==fingerprint:exhausted=True;break
                fingerprint=next_fp
            if not exhausted:
                log.error('USDT pagination incomplete at %s; skipped',row['address']);continue
            for transfer in collected:
                if record(transfer,row['address'],row['user_id'],row['scan_after_ms']):count+=1
    return count

async def run(stop):
    while not stop.is_set():
        try:await scan_once()
        except Exception:log.exception('Deposit scan error; deposits will be retried')
        try:await asyncio.wait_for(stop.wait(),timeout=config.DEPOSIT_POLL_SECONDS)
        except asyncio.TimeoutError:pass
