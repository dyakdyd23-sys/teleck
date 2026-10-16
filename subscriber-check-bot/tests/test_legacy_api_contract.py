import asyncio,json
import httpx
from app.provider import Contract
from app import config

def test_phase76_contract(monkeypatch):
    spec=json.loads((config.ROOT/'api_contract.legacy-template.json').read_text())
    provider=Contract(spec)
    class Client:
        def __init__(self,*a,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def request(self,method,path,headers=None,**kw):
            assert headers['token']=='test-token'
            if path=='/addTask':
                assert kw['data']=={'filter_type':'1','conuntry':'KR'}
                assert kw['files']['file'][0]=='phones.txt'
                assert kw['files']['file'][1]==b'821012345678\n'
                return httpx.Response(200,json={'code':1,'id':'some-task'},request=httpx.Request(method,'https://example.org'+path))
            if path=='/checkTask':
                assert kw['params']=={'id':'some-task'}
                return httpx.Response(200,json={'total':2,'has_check':2,'result_file':'a.csv'},request=httpx.Request(method,'https://example.org'+path))
            assert path=='/exportPhone'
            return httpx.Response(200,content='01012345678,123456789,example,1\n01087654321,987654321,other,3\n'.encode(),request=httpx.Request(method,'https://example.org'+path))
    monkeypatch.setattr(httpx,'AsyncClient',Client)
    monkeypatch.setattr(config,'API_KEY','test-token')
    monkeypatch.setattr(config,'API_URL','https://example.org')
    assert asyncio.run(provider.create(['01012345678']))=='some-task'
    assert asyncio.run(provider.status('some-task'))=='completed'
    rows=asyncio.run(provider.results('some-task'))
    assert len(rows)==2 and rows[0]['last_active']=='1일 이내' and rows[1]['last_active']=='3일 이내'
