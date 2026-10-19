from app.money_input import parse_krw_tenths
from app import db, deposits, service
import pytest

def test_admin_amount_exact_krw():
    assert parse_krw_tenths('50000.0') == 500000
    assert parse_krw_tenths('5000.0') == 50000
    assert parse_krw_tenths('0.1') == 1
    for invalid in ('0','-1','1.11','not a number','NaN'):
        with pytest.raises(ValueError): parse_krw_tenths(invalid)

def test_credit_idempotency_and_exact_amount(tmp_path,monkeypatch):
    from app import config
    monkeypatch.setattr(config,'DB',tmp_path/'audit.sqlite3')
    monkeypatch.setattr(config,'STORAGE',tmp_path/'uploads')
    db.init()
    service.credit(123,parse_krw_tenths('50000.0'),'admin','automatic-example-abc')
    with db.connect() as c:
        assert c.execute('SELECT balance_tenths FROM users WHERE user_id=123').fetchone()[0] == 500000
    with pytest.raises(Exception):service.credit(123,500000,'admin','automatic-example-abc')

def test_replace_address_requires_no_pending_invoice(tmp_path,monkeypatch):
    from app import config
    monkeypatch.setattr(config,'DB',tmp_path/'wallet.sqlite3')
    monkeypatch.setattr(config,'STORAGE',tmp_path/'uploads')
    db.init()
    # two checksummed TRON base58 addresses
    def make_addr(seed):
        import hashlib
        alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
        raw=bytes([0x41])+bytes([seed])*20
        combined=raw+hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
        v=int.from_bytes(combined,'big');out=''
        while v:v,i=divmod(v,58);out=alphabet[i]+out
        return out
    a,b=make_addr(1),make_addr(2)
    with db.transaction() as c: db.ensure_user(c,123)
    deposits.assign(123,a)
    deposits.assign(123,b)
    with db.connect() as c:
        assert c.execute('SELECT address FROM deposit_addresses WHERE user_id=123').fetchone()[0]==b
        assert c.execute('SELECT address FROM retired_deposit_addresses WHERE address=?',(a,)).fetchone() is not None
    with pytest.raises(ValueError):deposits.add_to_pool(a)
