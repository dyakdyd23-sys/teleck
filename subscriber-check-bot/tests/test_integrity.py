import pytest
from app import config,db,service,accounts,deposits

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'DB',tmp_path/'test.sqlite3')
    monkeypatch.setattr(config,'STORAGE',tmp_path/'uploads')
    db.init()
    return tmp_path

def test_register_login_and_sessions(store):
    uid=accounts.register('buyer_123','longenough-password-123')
    assert uid<0
    with pytest.raises(Exception):accounts.register('buyer_123','longenough-password-123')
    assert accounts.user_from_token(accounts.login('buyer_123','longenough-password-123'))==uid
    with pytest.raises(ValueError):accounts.login('buyer_123','bad-password')

def test_credit_idempotency(store):
    uid=accounts.register('buyer_456','longenough-password-456')
    service.credit(uid,100,'admin','invoice-1')
    with pytest.raises(Exception):service.credit(uid,100,'admin','invoice-1')
    with db.connect() as c:
        assert c.execute('SELECT balance_tenths FROM users WHERE user_id=?',(uid,)).fetchone()[0]==100
        assert c.execute('SELECT COUNT(*) FROM ledger WHERE user_id=?',(uid,)).fetchone()[0]==1

def test_address_checksum(store):
    assert deposits.valid_tron_address('TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t')
    assert not deposits.valid_tron_address('TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6u')

def test_unconfigured_provider_prevents_charge(store,monkeypatch):
    uid=accounts.register('buyer_789','longenough-password-789')
    service.credit(uid,100000,'admin','invoice-2')
    monkeypatch.setattr(config,'API_KEY','')
    content=('전화번호\n'+''.join(f'010{n:08d}\n' for n in range(5000))).encode()
    prev=service.preview(uid,'numbers.csv',content)
    assert prev['valid']==5000
    with pytest.raises(ValueError,match='설정되지'):
        service.submit(uid,prev['preview_id'])
    with db.connect() as c:
        assert c.execute('SELECT balance_tenths FROM users WHERE user_id=?',(uid,)).fetchone()[0]==100000
        assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0

def test_refund_once(store):
    uid=accounts.register('buyer_444','longenough-password-444')
    with db.transaction() as c:
        c.execute('UPDATE users SET balance_tenths=500 WHERE user_id=?',(uid,))
        c.execute("INSERT INTO jobs(id,user_id,status,original_count,invalid_count,duplicate_count,valid_count,unit_tenths,charged_tenths,source_file,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",('x',uid,'failed',2,0,0,2,6,12,'none',db.now(),db.now()))
        c.execute("INSERT INTO phones(job_id,phone,state) VALUES('x','01012345678','done')")
        c.execute("INSERT INTO phones(job_id,phone,state) VALUES('x','01098765432','pending')")
        assert service.refund_for_unresolved(c,'x')==6
        assert service.refund_for_unresolved(c,'x')==6
    with db.connect() as c:
        assert c.execute('SELECT balance_tenths FROM users WHERE user_id=?',(uid,)).fetchone()[0]==506
        assert c.execute("SELECT COUNT(*) FROM ledger WHERE kind='refund'").fetchone()[0]==1

def test_trx_incoming_confirmed_once(store):
    uid=accounts.register('trx_buyer','longenough-password-123')
    address='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
    deposits.assign(uid,address)
    with db.connect() as c: assigned=c.execute('SELECT scan_after_ms FROM deposit_addresses WHERE user_id=?',(uid,)).fetchone()[0]
    alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    n=0
    for x in address:n=n*58+alphabet.index(x)
    to_hex=n.to_bytes(25,'big')[:-4].hex()
    transfer={'txID':'a'*64,'ret':[{'contractRet':'SUCCESS'}],'raw_data':{'timestamp':assigned+1,'contract':[{'type':'TransferContract','parameter':{'value':{'amount':1234567,'to_address':to_hex}}}]}}
    assert not deposits.record_trx(transfer,address,uid,assigned,1000)
    assert not deposits.record_trx(transfer,address,uid,assigned,1000)
    with db.connect() as c:
        assert c.execute('SELECT balance_tenths FROM users WHERE user_id=?',(uid,)).fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM trx_deposits').fetchone()[0]==0
    assert deposits.coin_display(1234567)=='1.235'

def test_trx_reject_failed_and_contract_calls(store):
    uid=accounts.register('trx_fail','longenough-password-123')
    address='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
    deposits.assign(uid,address)
    raw={'txID':'b'*64,'ret':[{'contractRet':'REVERT'}],'raw_data':{'timestamp':9999999999999,'contract':[{'type':'TriggerSmartContract','parameter':{'value':{'amount':1000000}}}]}}
    assert not deposits.record_trx(raw,address,uid,0,1000)

def test_automatic_address_pool_never_reuses(store,monkeypatch):
    a='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
    b=deposits.tron_hex_to_base58('41'+'ab'*20)
    deposits.add_to_pool(a);deposits.add_to_pool(b)
    u1=accounts.register('auto_buyer_1','longenough-password-123')
    u2=accounts.register('auto_buyer_2','longenough-password-123')
    u3=accounts.register('auto_buyer_3','longenough-password-123')
    assert deposits.auto_assign(u1)==a
    assert deposits.auto_assign(u1)==a
    assert deposits.auto_assign(u2)==b
    assert deposits.auto_assign(u3) is None
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM deposit_addresses').fetchone()[0]==2

def test_invalid_address_pool_rejected(store,monkeypatch):
    pass
    uid=accounts.register('auto_buyer_bad','longenough-password-123')
    with pytest.raises(ValueError,match='주소'):deposits.add_to_pool('invalid_address')
