"""Configuration-driven provider adapter; no invented endpoint or response keys."""
import json, httpx
from urllib.parse import urlsplit
from . import config

class Contract:
    def __init__(self,d):self.d=d
    @classmethod
    def load(cls):
        if not config.API_URL.startswith('https://') or not config.API_KEY:raise ValueError('실제 HTTPS API 주소 및 인증키가 필요합니다.')
        host=urlsplit(config.API_URL)
        if not host.hostname or host.username or host.password:raise ValueError('API URL 오류')
        d=json.loads(config.CONTRACT.read_text(encoding='utf-8'))
        for k in ('auth_header','create','status','result','state_map','max_batch_size','requests_per_minute'):
            if not d.get(k):raise ValueError(f'API 명세 누락: {k}')
        if not 1<=int(d['max_batch_size'])<=1_000_000:raise ValueError('배치 크기 오류')
        if int(d['requests_per_minute'])<1:raise ValueError('API 호출 제한 누락')
        for part,keys in {'create':['method','path','phones_field','task_id_path'],
                          'status':['method','path','state_path'],
                          'result':['method','path','rows_path','phone_field','joined_field']}.items():
            if any(not d[part].get(k) for k in keys):raise ValueError(f'{part} 제공업체 명세 미완료')
        if any(s not in ('pending','processing','completed','failed','cancelled') for s in d['state_map'].values()):raise ValueError('상태 매핑 오류')
        for part in ('create','status','result'):
            path=d[part]['path']
            if not path.startswith('/') or '://' in path or path.startswith('//'):raise ValueError('API 경로는 /로 시작해야 합니다.')
            if d[part]['method'].upper() not in ('GET','POST'):raise ValueError('미지원 HTTP 메서드')
        return cls(d)
    def _value(self,obj,path):
        cur=obj
        for key in path.split('.'):
            if not key:return None
            if isinstance(cur,dict):cur=cur.get(key)
            else:return None
        return cur
    async def call(self,part,task_id=None,phones=None):
        spec=self.d[part];path=spec['path']
        if '{task_id}' in path:
            if not task_id:raise ValueError('필수 작업 ID 누락')
            from urllib.parse import quote
            path=path.replace('{task_id}',quote(str(task_id),safe=''))
        headers={self.d['auth_header']:self.d.get('auth_prefix','')+config.API_KEY}
        kw={}
        if phones is not None:kw['json']={spec['phones_field']:phones}
        async with httpx.AsyncClient(base_url=config.API_URL,timeout=45,follow_redirects=False) as client:
            r=await client.request(spec['method'].upper(),path,headers=headers,**kw)
            r.raise_for_status()
            return r.json()
    async def create(self,phones):
        result=await self.call('create',phones=phones)
        task=self._value(result,self.d['create']['task_id_path'])
        if task is None or str(task)=='':raise ValueError('외부 API 작업 ID 누락')
        return str(task)
    async def status(self,task_id):
        result=await self.call('status',task_id)
        state=self._value(result,self.d['status']['state_path'])
        if str(state) not in self.d['state_map']:raise ValueError('외부 API 상태 매핑 누락')
        return self.d['state_map'][str(state)]
    async def results(self,task_id):
        result=await self.call('result',task_id)
        rows=self._value(result,self.d['result']['rows_path'])
        if not isinstance(rows,list):raise ValueError('외부 API 결과 형식 불일치')
        spec=self.d['result'];data=[]
        for row in rows:
            phone=self._value(row,spec['phone_field'])
            joined=self._value(row,spec['joined_field'])
            if not isinstance(joined,(str,bool,int)) and joined is not None:raise ValueError('가입 결과 형식 불일치')
            data.append({'phone':str(phone or ''),'joined':None if joined is None else str(joined),
                'uid':str(self._value(row,spec['uid_field'])) if spec.get('uid_field') and self._value(row,spec['uid_field']) is not None else None,
                'username':str(self._value(row,spec['username_field'])) if spec.get('username_field') and self._value(row,spec['username_field']) is not None else None,
                'last_active':str(self._value(row,spec['last_active_field'])) if spec.get('last_active_field') and self._value(row,spec['last_active_field']) is not None else None})
        return data
