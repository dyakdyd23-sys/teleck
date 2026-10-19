import time
import pytest
from app import db,config,deposits

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DB',tmp_path/'test.sqlite3')
    monkeypatch.setattr(config,'STORAGE',tmp_path/'files')
    monkeypatch.setattr(config,'TRONGRID_KEY','test-only-key')
    db.init()
    address='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
    with db.transaction() as c:
        db.ensure_user(c,111)
        db.ensure_user(c,222)
    deposits.set_shared_address(address)
    return address

def incoming(address,milli,txid,ts):
    return {'to':address,'type':'Transfer','confirmed':True,'token_info':{'address':deposits.USDT_CONTRACT,'decimals':6},'transaction_id':txid,'value':str(milli*1000),'block_timestamp':ts}

def test_shared_invoice_exact_and_duplicate(setup):
    invoice=deposits.create_topup_request(111,'10000',1331)
    assert invoice['address']==setup and invoice['usdt_milli']==7514
    ts=int(time.time()*1000)+1000
    assert not deposits.record_shared(incoming(setup,7513,'a'*64,ts),setup,0)
    assert deposits.record_shared(incoming(setup,7514,'b'*64,ts),setup,0)
    assert not deposits.record_shared(incoming(setup,7514,'b'*64,ts),setup,0)
    with db.connect() as c:
        assert c.execute('SELECT balance_tenths FROM users WHERE user_id=111').fetchone()[0]==100000
        assert c.execute('SELECT COUNT(*) FROM ledger WHERE user_id=111').fetchone()[0]==1

def test_unrequested_payment_never_credited(setup):
    assert not deposits.record_shared(incoming(setup,1000,'c'*64,int(time.time()*1000)+1000),setup,0)
    with db.connect() as c:assert c.execute('SELECT balance_tenths FROM users WHERE user_id=111').fetchone()[0]==0

def test_shared_address_unique_invoice_amounts(setup):
    first=deposits.create_topup_request(111,'10000',1331)
    second=deposits.create_topup_request(222,'10000',1331)
    assert first['address']==second['address']==setup
    assert second['usdt_milli']!=first['usdt_milli']
    with pytest.raises(ValueError,match='진행 중'):deposits.create_topup_request(111,'10000',1331)
    assert deposits.cancel_topup(111)==1
    assert deposits.active_topup(111) is None
    with pytest.raises(ValueError):deposits.create_topup_request(111,'1.123',1331)

def test_cannot_change_shared_address_during_invoice(setup):
    deposits.create_topup_request(111,'10000',1331)
    with pytest.raises(ValueError,match='진행 중'):deposits.set_shared_address('TTLBdjooKRinMZZ4x9KG8bYhJxCGe1MPBr')
