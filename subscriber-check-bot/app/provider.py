"""Configuration-driven provider adapter; no invented endpoint or response keys."""
import json, csv, io, httpx, os
from urllib.parse import urlsplit
from . import config

class Contract:
    def __init__(self,d):self.d=d
    @classmethod
    def load(cls):
        if not config.API_URL.startswith('https://') or not config.API_KEY:raise ValueError('실제 HTTPS API 주소 및 인증키가 필요합니다.')
        if os.getenv('TG_API_CONTRACT_VERIFIED') != '1':raise ValueError('실제 API 테스트 전 신청 차단: TG_API_CONTRACT_VERIFIED=1 설정 필요')
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
        if task_id and spec.get('task_id_query'):
            kw['params']={spec['task_id_query']:str(task_id)}
        if phones is not None:
            if spec.get('request_format')=='multipart_file':
                content=('\n'.join(phones)+'\n').encode('utf-8')
                kw['files']={spec.get('file_field','file'):('phones.txt',content,'text/plain')}
                kw['data']={'filter_type':os.getenv('TG_API_FILTER_TYPE','1'), 'conuntry':config.TG_API_COUNTRY}
            else:
                body={spec['phones_field']:phones}
                if spec.get('country_field'):body[spec['country_field']]=config.TG_API_COUNTRY or spec.get('country_value','KR')
                kw['json']=body
        async with httpx.AsyncClient(base_url=config.API_URL,timeout=45,follow_redirects=False) as client:
            r=await client.request(spec['method'].upper(),path,headers=headers,**kw)
            r.raise_for_status()
            if spec.get('response_format')=='csv':
                raw=None
                for encoding in ('utf-8-sig','utf-8','gb18030','cp949'):
                    try:raw=r.content.decode(encoding);break
                    except UnicodeDecodeError:continue
                if raw is None:raise ValueError('결과 CSV 인코딩 오류')
                parsed=list(csv.reader(io.StringIO(raw)))
                return {'rows':parsed}
            return r.json()
    async def create(self,phones):
        result=await self.call('create',phones=phones)
        if int(result.get('code',0))!=1:raise ValueError('제공업체 작업 생성 거절: code='+str(result.get('code')))
        task=self._value(result,self.d['create']['task_id_path'])
        if task is None or str(task)=='':raise ValueError('외부 API 작업 ID 누락')
        return str(task)
    async def status(self,task_id):
        result=await self.call('status',task_id)
        if not isinstance(result,dict):raise ValueError('외부 API 상태 응답 오류')
        total=int(result.get('total') or 0); checked=int(result.get('has_check') or 0)
        if result.get('result_file') or (total>0 and checked>=total):return 'completed'
        state=result.get('status')
        if str(state) in self.d['state_map']:
            mapped=self.d['state_map'][str(state)]
            return 'processing' if mapped=='completed' else mapped
        return 'processing'
    async def results(self,task_id):
        result=await self.call('result',task_id)
        rows=self._value(result,self.d['result']['rows_path'])
        if not isinstance(rows,list):raise ValueError('외부 API 결과 형식 불일치')
        data=[]
        for index,row in enumerate(rows):
            if isinstance(row, list):
                # Phase76 CSV: phone at column 0, UID column 1, username column 2,
                # last-activity day count in final column. Header rows are ignored.
                if len(row)<4:continue
                from .phones import normalize
                phone=normalize(row[0])
                if not phone:continue
                uid=row[1].strip() if row[1] else None
                username=row[2].strip() if row[2] else None
                activity=row[-1].strip()
                try:
                    day=float(activity)
                    bucket='1일 이내' if day<=1 else '3일 이내' if day<=3 else '7일 이내' if day<=7 else '7일 초과'
                    if day<0:bucket='확인 불가'
                except (ValueError,TypeError):
                    bucket=activity if activity in ('1일 이내','3일 이내','7일 이내','7일 초과') else '확인 불가'
                data.append({'phone':phone,'joined':'true','uid':uid if uid!='0' else None,
                    'username':username if username!='0' else None,'last_active':bucket})
                continue
            spec=self.d['result']
            phone=self._value(row,spec['phone_field'])
            joined=self._value(row,spec['joined_field'])
            if not isinstance(joined,(str,bool,int)) and joined is not None:raise ValueError('가입 결과 형식 불일치')
            data.append({'phone':str(phone or ''),'joined':None if joined is None else str(joined),
                'uid':str(self._value(row,spec['uid_field'])) if spec.get('uid_field') and self._value(row,spec['uid_field']) is not None else None,
                'username':str(self._value(row,spec['username_field'])) if spec.get('username_field') and self._value(row,spec['username_field']) is not None else None,
                'last_active':str(self._value(row,spec['last_active_field'])) if spec.get('last_active_field') and self._value(row,spec['last_active_field']) is not None else None})
        return data
