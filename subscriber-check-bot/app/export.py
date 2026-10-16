import csv,io
import re
from openpyxl import Workbook
from . import db
from .phones import display
HEAD=['전화번호','가입여부','텔레그램 UID','텔레그램 ID','텔레그램 접속일자']

# Provider uses bounded Korean day buckets, NOT absolute timestamps.
# Unknown / ambiguous provider values must never be guessed into a bucket.
def active_bucket(value):
    if value is None or isinstance(value,bool):return None
    v=str(value).strip().replace(' ', '')
    match=re.fullmatch(r'(?:최근)?(1|3|7)일이내',v)
    return int(match.group(1)) if match else None

def is_registered(value):
    if isinstance(value,bool):return value
    if value is None:return False
    v=str(value).strip().lower()
    return v in {'true','1','yes','y','가입','가입됨','가입자','registered','joined','active'}

def include_result(joined,last_active,days):
    if days=='raw':return True
    if not is_registered(joined):return False
    if days=='all':return True
    bucket=active_bucket(last_active)
    return bucket is not None and bucket<=int(days)

def rows(job_id,days='all'):
    with db.connect() as c:
        j=c.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not j:raise ValueError('작업을 찾을 수 없습니다.')
        data=c.execute("SELECT phone,joined,uid,username,last_active,state FROM phones WHERE job_id=? ORDER BY phone",(job_id,))
        for r in data:
            if r['state']!='done':continue
            if not include_result(r['joined'],r['last_active'],days):continue
            yield [display(r['phone']),r['joined'] or '확인 불가',r['uid'] or '',r['username'] or '',r['last_active'] or '확인 불가']

def export(job_id,fmt='csv',days='all'):
    if days not in ('all','1','3','7','raw'):raise ValueError('필터 오류')
    if fmt=='csv':
        out=io.StringIO();w=csv.writer(out);w.writerow(HEAD);w.writerows(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in row] for row in rows(job_id,days))
        return b'\xef\xbb\xbf'+out.getvalue().encode('utf-8'),'text/csv'
    if fmt=='xlsx':
        wb=Workbook(write_only=True);ws=wb.create_sheet('검수결과');ws.append(HEAD)
        for row in rows(job_id,days):
            ws.append(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in row])
        out=io.BytesIO();wb.save(out)
        return out.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    raise ValueError('지원하지 않는 다운로드 형식')
