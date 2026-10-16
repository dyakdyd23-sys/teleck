import asyncio
import httpx
from app import config, db, sheet_rate, deposits

def test_no_rate_no_credit(monkeypatch):
    monkeypatch.setattr(sheet_rate,'_cached_rate',0)
    assert sheet_rate.available_rate()==0
    assert not deposits.record_trx({},'x',1,0,1000)

def test_sheet_rate_parsing(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"DB",tmp_path/"test.sqlite3")
    monkeypatch.setattr(config,"STORAGE",tmp_path/"uploads")
    db.init()
    monkeypatch.setattr(config,'GOOGLE_SHEET_CSV_URL','https://docs.google.com/spreadsheets/d/test/pub?output=csv')
    async def fake_get(self,url):
        return httpx.Response(200,text='1423.72\n',request=httpx.Request('GET',url))
    monkeypatch.setattr(httpx.AsyncClient,'get',fake_get)
    assert asyncio.run(sheet_rate.refresh_once())==1424
    assert sheet_rate.available_rate()==1424
    with db.connect() as c:assert db.setting(c,'usdt_krw_rate')==1424
