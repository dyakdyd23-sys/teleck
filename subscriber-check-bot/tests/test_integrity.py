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
