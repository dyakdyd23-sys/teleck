import time
import pytest
from app import db,config,deposits

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DB',tmp_path/'test.sqlite3')
    monkeypatch.setattr(config,'STORAGE',tmp_path/'files')
    monkeypatch.setattr(config,'TRONGRID_KEY','test-only-key')
    db.init()
    addr='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
    with db.transaction() as c:db.ensure_user(c,111)
    deposits.add_to_pool(addr)
    return addr

def incoming(addr,milli,txid,ts):
    return {'to':addr,'type':'Transfer','confirmed':True,'token_info':{'address':deposits.USDT_CONTRACT,'decimals':6},
            'transaction_id':txid,'value':str(milli*1000),'block_timestamp':ts}

def test_invoice_credit_exact_only_once(setup):
    inv=deposits.create_topup_request(111,'10000',1331)
    assert inv['usdt_milli']==7514  # ceil(10000/1331 * 1000)
    with db.connect() as c:assigned=c.execute('SELECT scan_after_ms FROM deposit_addresses WHERE user_id=111').fetchone()[0]
    ts=max(assigned+1,int(time.time()*1000)+1)
    assert deposits.record(incoming(setup,7500,'a'*64,ts),setup,111,assigned) is False
    assert deposits.record(incoming(setup,7514,'b'*64,ts),setup,111,assigned) is True
    assert deposits.record(incoming(setup,7514,'b'*64,ts),setup,111,assigned) is False
    with db.connect() as c:
        assert c.execute('SELECT balance_tenths FROM users WHERE user_id=111').fetchone()[0]==100000
        assert c.execute('SELECT COUNT(*) FROM ledger WHERE user_id=111').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM unmatched_deposits').fetchone()[0]==1

def test_unauthorized_unsolicited_rejected(setup):
    deposits.auto_assign(111)
    with db.connect() as c:assigned=c.execute('SELECT scan_after_ms FROM deposit_addresses WHERE user_id=111').fetchone()[0]
    assert deposits.record(incoming(setup,1000,'c'*64,assigned+1000),setup,111,assigned) is False
    with db.connect() as c:assert c.execute('SELECT balance_tenths FROM users WHERE user_id=111').fetchone()[0]==0

def test_invoice_cannot_duplicate_or_underpay(setup):
    deposits.create_topup_request(111,'1000',1331)
    with pytest.raises(ValueError,match='진행 중'):deposits.create_topup_request(111,'1000',1331)
    assert deposits.cancel_topup(111)==1
    assert deposits.active_topup(111) is None
    with pytest.raises(ValueError):deposits.create_topup_request(111,'1.123',1331)
