"""TRC-20 USDT incoming transfer confirmation. Does not create wallets or hold keys."""
import asyncio,logging,time,hashlib
from decimal import Decimal,ROUND_DOWN
from datetime import datetime,timezone
import httpx
from . import db,config
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

def record(transfer,address,user_id,assigned_after,rate):
    if transfer.get('to')!=address or transfer.get('type')!='Transfer':return False
    token=transfer.get('token_info') or {}
    if token.get('address')!=USDT_CONTRACT or str(token.get('decimals'))!='6':return False
    txid=transfer.get('transaction_id')
    if not isinstance(txid,str) or len(txid)!=64 or any(x not in '0123456789abcdefABCDEF' for x in txid):return False
    micro=int(transfer.get('value','0'))
    ts=int(transfer.get('block_timestamp',0))
    if ts<assigned_after or micro<=0 or rate<=0:return False
    # Different transfers within same tx cannot be safely differentiated by this endpoint.
    # One deposit per txid is accepted; ambiguous additional events must be reconciled.
    tenths=int((Decimal(micro)*Decimal(rate)*10/Decimal(1_000_000)).to_integral_value(rounding=ROUND_DOWN))
    if tenths<=0:return False
    with db.transaction() as c:
        if c.execute('SELECT 1 FROM deposits WHERE txid=?',(txid,)).fetchone():return False
        c.execute('INSERT INTO deposits(user_id,txid,event_key,address,usdt_micro,krw_tenths,rate_krw,confirmed_at) VALUES(?,?,?,?,?,?,?,?)',(user_id,txid,txid,address,micro,tenths,rate,db.now()))
        c.execute('INSERT INTO ledger(user_id,kind,amount_tenths,reason,actor,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?)',(user_id,'usdt_deposit',tenths,f'TRC20 USDT confirmed {micro}/1000000 at {rate} KRW','chain','trx:'+txid,db.now()))
        c.execute('UPDATE users SET balance_tenths=balance_tenths+? WHERE user_id=?',(tenths,user_id))
        db.audit(c,'chain','usdt_deposit',detail=f'user={user_id},txid={txid},micro={micro},rate={rate}')
    return True

async def scan_once():
    if not config.TRONGRID_KEY:return 0
    with db.connect() as c:
        rate=db.setting(c,'usdt_krw_rate')
        addresses=[dict(x) for x in c.execute('SELECT * FROM deposit_addresses WHERE active=1')]
    if rate<=0:return 0
    count=0
    async with httpx.AsyncClient(timeout=20) as client:
        for row in addresses:
            params={'only_confirmed':'true','only_to':'true','contract_address':USDT_CONTRACT,'limit':200,'min_timestamp':row['scan_after_ms']}
            fingerprint=None
            collected=[]
            exhausted=False
            for page in range(30):
                if fingerprint:params['fingerprint']=fingerprint
                r=await client.get(f"{config.TRONGRID_URL}/v1/accounts/{row['address']}/transactions/trc20",params=params,headers={'TRON-PRO-API-KEY':config.TRONGRID_KEY})
                r.raise_for_status();payload=r.json()
                if not payload.get('success',True):raise ValueError('TRON provider response unsuccessful')
                collected.extend(payload.get('data',[]))
                next_fp=(payload.get('meta') or {}).get('fingerprint')
                if not next_fp or next_fp==fingerprint:
                    exhausted=True
                    break
                fingerprint=next_fp
            if not exhausted:
                log.error('Deposit pagination limit reached for address %s; no credits applied until complete reconciliation',row['address'])
                continue
            for t in collected:
                if record(t,row['address'],row['user_id'],row['scan_after_ms'],rate):count+=1
    return count

async def run(stop):
    while not stop.is_set():
        try:await scan_once()
        except Exception:log.exception('Deposit scan error; deposits will be retried')
        try:await asyncio.wait_for(stop.wait(),timeout=config.DEPOSIT_POLL_SECONDS)
        except asyncio.TimeoutError:pass
