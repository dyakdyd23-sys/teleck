import time
import pytest
from app import db,config,deposits

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DB',tmp_path/'db.sqlite')
    monkeypatch.setattr(config,'STORAGE',tmp_path/'uploads')
    monkeypatch.setattr(config,'TRONGRID_KEY','test-key')
    db.init()
    with db.transaction() as c: db.ensure_user(c,123)
    deposits.set_shared_address('TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t')
    return deposits.create_topup_request(123,'50000',1331)

def test_reject_clears_pending_and_audits(env):
    assert deposits.admin_resolve_topup(env['id'],'reject','송금하지 않아 신청 종료')=='cancelled'
    assert deposits.active_topup(123) is None
    with db.connect() as c:
        assert c.execute('SELECT balance_tenths FROM users WHERE user_id=123').fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM audit WHERE action='admin_reject_topup'").fetchone()[0]==1
    with pytest.raises(ValueError,match='이미 처리된'):
        deposits.admin_resolve_topup(env['id'],'reject','중복 처리를 시도')

def test_approve_only_confirmed_unmatched_and_once(env):
    txid='a'*64
    with pytest.raises(ValueError,match='미매칭'):
        deposits.admin_resolve_topup(env['id'],'approve','입금 확인 완료',txid=txid)
    with db.transaction() as c:
        c.execute('INSERT INTO unmatched_deposits VALUES(?,?,?,?,?,?)',(txid,0,env['address'],env['usdt_milli']*1000,'shared_address_no_unique_invoice',db.now()))
    assert deposits.admin_resolve_topup(env['id'],'approve','TXID 확인 승인',txid=txid)=='completed'
    with db.connect() as c:
        assert c.execute('SELECT balance_tenths FROM users WHERE user_id=123').fetchone()[0]==500000
        assert c.execute('SELECT COUNT(*) FROM deposits WHERE txid=?',(txid,)).fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM unmatched_deposits WHERE txid=?',(txid,)).fetchone()[0]==0
    with pytest.raises(ValueError,match='이미 처리된'):
        deposits.admin_resolve_topup(env['id'],'approve','한번 더 승인',txid=txid)

def test_mismatched_amount_rejected(env):
    txid='b'*64
    with db.transaction() as c:
        c.execute('INSERT INTO unmatched_deposits VALUES(?,?,?,?,?,?)',(txid,0,env['address'],(env['usdt_milli']+2)*1000,'shared_address_no_unique_invoice',db.now()))
    with pytest.raises(ValueError,match='일치하지'):
        deposits.admin_resolve_topup(env['id'],'approve','입금 대조 검사',txid=txid)
    with db.connect() as c:
        assert c.execute('SELECT balance_tenths FROM users WHERE user_id=123').fetchone()[0]==0
