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

def shared_address():
    with db.connect() as c:
        r=c.execute("SELECT value FROM settings WHERE key='shared_usdt_address'").fetchone()
        return r['value'] if r and r['value'] else None

def set_shared_address(address,actor='admin'):
    """Change shared deposit destination; do not rewrite historic invoices."""
    if not valid_tron_address(address):raise ValueError('TRON 주소 체크섬 검증 실패')
    with db.transaction() as c:
        current=c.execute("SELECT value FROM settings WHERE key='shared_usdt_address'").fetchone()
        if current and current['value']==address:return address
        if c.execute("SELECT 1 FROM topup_requests WHERE status='pending' AND expires_at_ms>? LIMIT 1",(int(time.time()*1000),)).fetchone():
            raise ValueError('진행 중인 충전 신청이 있습니다. 만료 또는 정산 후 주소를 바꿔 주세요.')
        # Only transfers after the switch are attributed to new invoices.
        c.execute("INSERT INTO settings(key,value) VALUES('shared_usdt_address',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(address,))
        c.execute("INSERT INTO settings(key,value) VALUES('shared_usdt_scan_after_ms',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(int(time.time()*1000)),))
        db.audit(c,actor,'change_shared_usdt_address',detail=f'old={current["value"] if current else ""},new={address}')
    return address

def assign(user_id,address,actor='admin'):
    """Assign or safely replace an address. Never reuse a retired address."""
    if not valid_tron_address(address):raise ValueError('TRON 주소 체크섬 검증 실패')
    with db.transaction() as c:
        if not c.execute('SELECT 1 FROM users WHERE user_id=?',(user_id,)).fetchone():raise ValueError('사용자가 없습니다.')
        current=c.execute('SELECT address FROM deposit_addresses WHERE user_id=?',(user_id,)).fetchone()
        if current and current['address']==address:return address
        owner=c.execute('SELECT user_id FROM deposit_addresses WHERE address=?',(address,)).fetchone()
        if owner:raise ValueError('이미 다른 회원에게 배정된 주소입니다.')
        if c.execute('SELECT 1 FROM retired_deposit_addresses WHERE address=?',(address,)).fetchone():raise ValueError('과거 사용된 주소는 재배정할 수 없습니다.')
        # Block even expired invoices that are still marked pending until state reconciliation.
        pending=c.execute("SELECT 1 FROM topup_requests WHERE user_id=? AND status='pending' LIMIT 1",(user_id,)).fetchone()
        if pending:raise ValueError('미완료 충전 신청이 있습니다. 신청을 종료하고 주소를 변경해 주세요.')
        # An address may still receive late funds. Retire it permanently from allocation pool.
        if current:
            old=current['address']
            c.execute('DELETE FROM deposit_address_pool WHERE address=?',(old,))
            c.execute('INSERT OR IGNORE INTO retired_deposit_addresses VALUES(?,?,?)',(old,user_id,db.now()))
            c.execute('UPDATE deposit_addresses SET address=?,assigned_at=?,active=1,scan_after_ms=? WHERE user_id=?',(address,db.now(),int(time.time()*1000),user_id))
            db.audit(c,actor,'replace_deposit_address',detail=f'user={user_id},old={old},new={address}')
        else:
            c.execute('INSERT INTO deposit_addresses VALUES(?,?,?,?,?)',(address,user_id,db.now(),1,int(time.time()*1000)))
            db.audit(c,actor,'assign_deposit_address',detail=f'user={user_id},address={address}')
        c.execute('DELETE FROM deposit_address_pool WHERE address=?',(address,))
        return address

def add_to_pool(address, actor='admin'):
    """Add an operator-controlled receiving address. No wallet secrets are stored."""
    if not valid_tron_address(address):raise ValueError('TRON 주소 체크섬 검증 실패')
    with db.transaction() as c:
        if c.execute('SELECT 1 FROM retired_deposit_addresses WHERE address=?',(address,)).fetchone() or c.execute('SELECT 1 FROM deposit_addresses WHERE address=?',(address,)).fetchone() or c.execute('SELECT 1 FROM topup_requests WHERE address=?',(address,)).fetchone() or c.execute('SELECT 1 FROM deposits WHERE address=?',(address,)).fetchone():
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
            WHERE a.address IS NULL
            AND NOT EXISTS(SELECT 1 FROM topup_requests t WHERE t.address=p.address)
            AND NOT EXISTS(SELECT 1 FROM deposits d WHERE d.address=p.address)
            AND NOT EXISTS(SELECT 1 FROM retired_deposit_addresses r WHERE r.address=p.address)
            ORDER BY p.created_at,p.address LIMIT 1""").fetchone()
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
    address=shared_address()
    if not address:raise ValueError('관리자 공용 충전주소가 등록되지 않았습니다.')
    if not config.TRONGRID_KEY:raise ValueError('입금 확인 서비스가 준비되지 않았습니다.')
    with db.transaction() as c:
        now_ms=int(time.time()*1000)
        c.execute("UPDATE topup_requests SET status='expired' WHERE status='pending' AND expires_at_ms<=?",(now_ms,))
        existing=c.execute("SELECT 1 FROM topup_requests WHERE user_id=? AND status='pending'",(user_id,)).fetchone()
        if existing:raise ValueError('진행 중인 충전 신청이 있습니다. 완료하거나 만료된 후 다시 신청해 주세요.')
        # Shared address: give each simultaneous invoice a distinct thousandth-USDT amount.
        # A historical same-amount transfer is never matched outside its invoice's time window.
        base_milli=milli
        for offset in range(0,1000):
            candidate=base_milli+offset
            occupied=c.execute("SELECT 1 FROM topup_requests WHERE address=? AND usdt_milli=? AND status='pending' AND expires_at_ms>? LIMIT 1",(address,candidate,now_ms)).fetchone()
            if not occupied:break
        else:raise ValueError('현재 충전 신청이 많습니다. 잠시 후 다시 시도해 주세요.')
        milli=candidate
        rid=uuid.uuid4().hex
        expiry=now_ms+QUOTE_DURATION_SECONDS*1000
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

def admin_resolve_topup(request_id, action, reason, actor='admin', txid=''):
    """Resolve a top-up without inventing blockchain evidence.

    Approval requires a confirmed TRC20 USDT deposit already captured in the
    unmatched-deposit suspense table. All updates use one DB transaction.
    """
    reason=(reason or '').strip()
    if len(reason)<5:raise ValueError('처리 사유를 5자 이상 입력해 주세요.')
    if action not in ('approve','reject'):raise ValueError('처리 유형이 올바르지 않습니다.')
    with db.transaction() as c:
        request=c.execute('SELECT * FROM topup_requests WHERE id=?',(request_id,)).fetchone()
        if not request:raise ValueError('충전 신청을 찾을 수 없습니다.')
        if request['status'] not in ('pending','expired'):
            raise ValueError('이미 처리된 신청입니다. 중복 승인·거절할 수 없습니다.')
        if action=='reject':
            c.execute("UPDATE topup_requests SET status='cancelled' WHERE id=? AND status IN ('pending','expired')",(request_id,))
            db.audit(c,actor,'admin_reject_topup',detail=f'invoice={request_id};user={request["user_id"]};reason={reason}')
            db.admin_event(c,'topup_rejected',request['user_id'],None,f'충전 신청 거절\n회원: {request["user_id"]}\n신청: {request_id[:12]}')
            return 'cancelled'
        txid=(txid or '').strip().lower()
        if len(txid)!=64 or any(ch not in '0123456789abcdef' for ch in txid):
            raise ValueError('입금 확인된 거래의 64자리 TXID를 입력해 주세요.')
        if c.execute('SELECT 1 FROM deposits WHERE txid=?',(txid,)).fetchone():
            raise ValueError('이미 충전 처리된 TXID입니다.')
        if c.execute('SELECT 1 FROM topup_requests WHERE txid=?',(txid,)).fetchone():
            raise ValueError('다른 신청에서 처리된 TXID입니다.')
        pending=c.execute('SELECT * FROM unmatched_deposits WHERE txid=?',(txid,)).fetchone()
        if not pending:raise ValueError('TronGrid 입금 감시에서 확인된 미매칭 거래가 아닙니다. 승인할 수 없습니다.')
        if pending['address']!=request['address'] or pending['usdt_micro']!=request['usdt_milli']*1000:
            raise ValueError('입금 주소 또는 USDT 수량이 신청과 일치하지 않습니다.')
        uid=request['user_id'];amount=request['requested_tenths']
        c.execute('INSERT INTO deposits(user_id,txid,event_key,address,usdt_micro,krw_tenths,rate_krw,confirmed_at) VALUES(?,?,?,?,?,?,?,?)',
                  (uid,txid,txid,request['address'],pending['usdt_micro'],amount,request['rate_krw'],db.now()))
        c.execute('INSERT INTO ledger(user_id,kind,amount_tenths,reason,actor,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?)',
                  (uid,'usdt_deposit',amount,f'Admin verified TRC20 invoice {request_id}: {reason}',actor,'trx:'+txid,db.now()))
        c.execute('UPDATE users SET balance_tenths=balance_tenths+? WHERE user_id=?',(amount,uid))
        c.execute("UPDATE topup_requests SET status='completed',txid=?,completed_at=? WHERE id=?",(txid,db.now(),request_id))
        c.execute('DELETE FROM unmatched_deposits WHERE txid=?',(txid,))
        db.audit(c,actor,'admin_approve_topup',detail=f'invoice={request_id};user={uid};txid={txid};krw_tenths={amount};reason={reason}')
        db.admin_event(c,'topup_completed',uid,None,f'✅ 관리자 승인 충전\n회원: {uid}\n금액: {db.money(amount)}\nTXID: {txid}')
        return 'completed'

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

def record_shared(transfer,address,scan_after):
    """Match each confirmed transfer to exactly one pending shared-address invoice.

    Never assign a transfer by sender guesswork or by a duplicated/ambiguous amount.
    """
    if transfer.get('to')!=address or transfer.get('type')!='Transfer':return False
    if transfer.get('confirmed') is False or transfer.get('contractRet') not in (None,'SUCCESS'):return False
    token=transfer.get('token_info') or {}
    if token.get('address')!=USDT_CONTRACT or str(token.get('decimals'))!='6':return False
    txid=transfer.get('transaction_id')
    if not isinstance(txid,str) or len(txid)!=64 or any(x not in '0123456789abcdefABCDEF' for x in txid):return False
    try:micro=int(transfer.get('value','0'));ts=int(transfer.get('block_timestamp',0))
    except (ValueError,TypeError):return False
    if micro<=0 or ts<scan_after:return False
    with db.transaction() as c:
        if c.execute('SELECT 1 FROM deposits WHERE txid=?',(txid,)).fetchone():return False
        if c.execute('SELECT 1 FROM unmatched_deposits WHERE txid=?',(txid,)).fetchone():return False
        candidates=c.execute("""SELECT * FROM topup_requests WHERE address=? AND status='pending'
            AND usdt_milli=? AND expires_at_ms>=?""",(address,micro//1000,ts)).fetchall() if micro%1000==0 else []
        candidates=[x for x in candidates if int(datetime.fromisoformat(x['created_at']).timestamp()*1000)<=ts]
        if len(candidates)==1:
            invoice=candidates[0];uid=invoice['user_id']
            c.execute('INSERT INTO deposits(user_id,txid,event_key,address,usdt_micro,krw_tenths,rate_krw,confirmed_at) VALUES(?,?,?,?,?,?,?,?)',(uid,txid,txid,address,micro,invoice['requested_tenths'],invoice['rate_krw'],db.now()))
            c.execute('INSERT INTO ledger(user_id,kind,amount_tenths,reason,actor,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?)',(uid,'usdt_deposit',invoice['requested_tenths'],f'Shared TRC20 invoice {invoice["id"]}', 'chain','trx:'+txid,db.now()))
            c.execute('UPDATE users SET balance_tenths=balance_tenths+? WHERE user_id=?',(invoice['requested_tenths'],uid))
            c.execute("UPDATE topup_requests SET status='completed',txid=?,completed_at=? WHERE id=?",(txid,db.now(),invoice['id']))
            db.admin_event(c,'topup_completed',uid,None,f'✅ 충전 완료\n회원: {uid}\n충전: {db.money(invoice["requested_tenths"])}\n입금: {micro/1000000:.3f} USDT\nTXID: {txid}')
            db.audit(c,'chain','shared_usdt_invoice_paid',detail=f'user={uid},invoice={invoice["id"]},txid={txid}')
            return True
        # user_id=0 is the system suspense account, not a customer's balance.
        c.execute('INSERT OR IGNORE INTO unmatched_deposits(txid,user_id,address,usdt_micro,reason,seen_at) VALUES(?,?,?,?,?,?)',(txid,0,address,micro,'shared_address_no_unique_invoice',db.now()))
        db.admin_event(c,'topup_unmatched',None,None,f'⚠️ 미확인 USDT 입금\nTXID: {txid}\n수량: {micro/1000000:.6f} USDT\n관리자 대조 필요')
    return False

async def scan_once():
    if not config.TRONGRID_KEY:return 0
    address=shared_address()
    if not address:return 0
    with db.connect() as c:
        scan_from=c.execute("SELECT value FROM settings WHERE key='shared_usdt_scan_after_ms'").fetchone()
        scan_after=int(scan_from['value']) if scan_from else 0
    params={'only_confirmed':'true','only_to':'true','limit':200,'min_timestamp':scan_after,'contract_address':USDT_CONTRACT}
    path=f'/v1/accounts/{address}/transactions/trc20'
    count=0;fingerprint=None;transfers=[];exhausted=False
    async with httpx.AsyncClient(timeout=25) as client:
        for _ in range(100):
            if fingerprint:params['fingerprint']=fingerprint
            r=await client.get(config.TRONGRID_URL+path,params=params,headers={'TRON-PRO-API-KEY':config.TRONGRID_KEY})
            r.raise_for_status();body=r.json()
            if not body.get('success',True):raise ValueError('TronGrid unavailable')
            transfers.extend(body.get('data') or [])
            nxt=(body.get('meta') or {}).get('fingerprint')
            if not nxt or nxt==fingerprint:exhausted=True;break
            fingerprint=nxt
    if not exhausted:
        log.error('Shared USDT deposit pagination incomplete; processing deferred');return 0
    for transfer in transfers:
        if record_shared(transfer,address,scan_after):count+=1
    return count

async def run(stop):
    while not stop.is_set():
        try:await scan_once()
        except Exception:log.exception('Deposit scan error; deposits will be retried')
        try:await asyncio.wait_for(stop.wait(),timeout=config.DEPOSIT_POLL_SECONDS)
        except asyncio.TimeoutError:pass
