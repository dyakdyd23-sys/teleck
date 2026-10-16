import asyncio, html, secrets, base64, os, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from . import config,db,service
from .bot import run as bot_run
from .worker import run as worker_run
from .deposits import run as deposit_run
from .notify import run as notify_run

log=logging.getLogger(__name__)
security=HTTPBasic(auto_error=False)
stop=asyncio.Event()

@asynccontextmanager
async def lifespan(app):
    db.init();stop.clear()
    tasks=[asyncio.create_task(worker_run(stop)),asyncio.create_task(bot_run(stop)),asyncio.create_task(deposit_run(stop)),asyncio.create_task(notify_run(stop))]
    yield
    stop.set()
    await asyncio.gather(*tasks,return_exceptions=True)

app=FastAPI(title='가입자 검수봇',docs_url=None,redoc_url=None,lifespan=lifespan)
@app.middleware('http')
async def secure_headers(request, call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    response.headers['Cache-Control']='no-store'
    return response


def admin(credentials:HTTPBasicCredentials=Depends(security)):
    if not config.WEB_TOKEN:raise HTTPException(503,'ADMIN_WEB_TOKEN 설정 필요')
    if credentials is None or not secrets.compare_digest(credentials.username,'admin') or not secrets.compare_digest(credentials.password,config.WEB_TOKEN):
        raise HTTPException(401,'관리자 인증 필요',headers={'WWW-Authenticate':'Basic'})
    return 'admin'

def guard_origin(request:Request):
    origin=request.headers.get('origin')
    from urllib.parse import urlsplit
    referer=request.headers.get('referer')
    supplied=origin or referer
    if supplied:
        parsed=urlsplit(supplied)
        if parsed.netloc!=request.headers.get('host') or parsed.scheme not in ('http','https'):
            raise HTTPException(403,'잘못된 요청 출처')
    elif request.cookies.get('checker_session') or request.headers.get('authorization'):
        raise HTTPException(403,'요청 출처 확인 불가')

def esc(x):return html.escape(str(x or ''))
def wrap(body):
    return HTMLResponse('''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>가입자 검수봇 관리자</title><style>
    :root{color-scheme:dark}body{font-family:system-ui,Arial;background:#0b1220;color:#eaf1ff;max-width:1180px;margin:auto;padding:30px}h1{font-size:24px}h2{font-size:18px;margin-top:32px}p{color:#a5b5d0}main{background:#142036;padding:22px;border-radius:16px;margin:20px 0;overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:11px;text-align:left;border-bottom:1px solid #344057;white-space:nowrap}th{color:#91a9ca}input,button{background:#202d45;color:white;border:1px solid #53637b;border-radius:8px;padding:10px;margin:4px}button{background:#3873e0;border:0;cursor:pointer}a{color:#7eb0ff}small{color:#b8c9e4}</style></head><body><h1>가입자 검수봇 <small>관리자</small></h1>'''+body+'</body></html>')

@app.get('/health')
def health():return {'service':'subscriber-check-bot','status':'running','provider_configured':service.provider_ready()}

@app.get('/admin',response_class=HTMLResponse)
def dashboard(user=Depends(admin)):
    with db.connect() as c:
        jobs=c.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 50').fetchall()
        users=c.execute('SELECT * FROM users ORDER BY created_at DESC LIMIT 30').fetchall()
        logs=c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 20').fetchall()
        settings={k:db.setting(c,k) for k in ('unit_tenths','min_count','max_count')}
        issues=c.execute("SELECT * FROM batches WHERE state='reconcile' ORDER BY updated_at DESC LIMIT 30").fetchall()
    body=f'<p>외부 API 상태: <b>{"설정 완료 (연결 성공 여부는 별도 검증 필요)" if service.provider_ready() else "미설정 · 신청 차단"}</b></p>'
    body+='<main><h2>요금 및 수량 설정</h2><form method="post" action="/admin/settings">'
    for key,label in [('unit_tenths','건당 요금(0.1원 단위)'),('min_count','최소 수량'),('max_count','최대 수량')]:
        body+=f'<label>{label} <input type="number" min="1" name="{key}" value="{settings[key]}" required></label>'
    body+='<button>설정 저장</button></form></main>'
    body+='<main><h2>관리자 잔액 충전 (운영자 전용)</h2><form method="post" action="/admin/credit"><input type="number" name="user_id" placeholder="Telegram 사용자 ID" required><input type="number" name="amount_tenths" placeholder="0.1원 단위 충전액" min="1" required><input name="reference" placeholder="고유 거래 참조값" required><button>충전</button></form></main>'
    body+='<main><h2>TRC-20 USDT 자동충전</h2><p>회원별로 실제 통제 중인 고유 입금주소를 배정하세요. 설정 환율 0원인 경우 자동 지급은 중지됩니다.</p><form method="post" action="/admin/deposit-address"><input name="user_id" type="number" placeholder="사용자 ID" required><input name="address" placeholder="전용 TRON 주소" required><button>주소 등록</button></form><form method="post" action="/admin/usdt-rate"><input type="number" min="0" name="rate" placeholder="1 USDT 환산 원화 금액" required><button>환율 설정</button></form></main>'
    body+='<main><h2>작업 목록</h2><table><tr><th>작업번호</th><th>사용자</th><th>상태</th><th>검수</th><th>완료</th><th>청구</th><th>환불</th></tr>'
    for j in jobs:body+=f'<tr><td><a href="/admin/job/{esc(j["id"])}">{esc(j["id"][:12])}</a></td><td>{j["user_id"]}</td><td>{esc(j["status"])}</td><td>{j["valid_count"]:,}</td><td>{j["success_count"]:,}</td><td>{db.money(j["charged_tenths"])}</td><td>{db.money(j["refunded_tenths"])}</td></tr>'
    body+='</table></main><main><h2>사용자별 잔액</h2><table><tr><th>Telegram ID</th><th>잔액</th></tr>'
    for u in users:body+=f'<tr><td>{u["user_id"]}</td><td>{db.money(u["balance_tenths"])}</td></tr>'
    body+='</table></main><main><h2>외부 요청 결과 불명 · 수동 대조 필요</h2><table><tr><th>배치 ID</th><th>작업</th><th>오류</th></tr>'
    for b in issues:body+=f'<tr><td>{esc(b["id"][:12])}</td><td>{esc(b["job_id"][:12])}</td><td>{esc(b["error"])}</td></tr>'
    body+='</table><p>제공업체에서 외부 작업 ID 확인 시 연결하여 상태 조회를 재개할 수 있습니다. 작업 생성이 실패한 것이 확인된 경우에만 실패로 확정하세요. 실패 확정 시 미처리 건은 자동 환불 대상입니다.</p>'
    body+='<form method="post" action="/admin/reconcile"><input name="batch_id" placeholder="전체 배치 ID" required><input name="api_task_id" placeholder="확인된 외부 작업 ID (재개 시 필수)"><select name="resolution"><option value="resume">외부 작업 ID 연결·재개</option><option value="fail">외부 작업 없음 확인·실패 확정</option></select><input name="reason" placeholder="대조 사유/확인 근거" minlength="8" required><button>대조 기록</button></form></main><main><h2>감사 로그</h2><table><tr><th>시각</th><th>실행자</th><th>작업</th><th>메모</th></tr>'
    for a in logs:body+=f'<tr><td>{esc(a["created_at"][:19])}</td><td>{esc(a["actor"])}</td><td>{esc(a["action"])}</td><td>{esc(a["detail"])}</td></tr>'
    body+='</table></main>'
    return wrap(body)

@app.post('/admin/settings')
def settings(request:Request,unit_tenths:int=Form(...),min_count:int=Form(...),max_count:int=Form(...),user=Depends(admin)):
    guard_origin(request)
    if not (1<=unit_tenths<=100000 and 1<=min_count<=max_count<=1000000):raise HTTPException(400,'설정값 범위 오류')
    with db.transaction() as c:
        for k,v in [('unit_tenths',unit_tenths),('min_count',min_count),('max_count',max_count)]:c.execute('UPDATE settings SET value=? WHERE key=?',(str(v),k))
        db.audit(c,user,'change_settings',detail=f'rate={unit_tenths},range={min_count}-{max_count}')
    return HTMLResponse('<meta http-equiv="refresh" content="0;url=/admin">설정 완료')

@app.post('/admin/credit')
def credit(request:Request,user_id:int=Form(...),amount_tenths:int=Form(...),reference:str=Form(...),user=Depends(admin)):
    guard_origin(request)
    try:service.credit(user_id,amount_tenths,user,reference)
    except Exception as e:raise HTTPException(400,str(e))
    return HTMLResponse('<meta http-equiv="refresh" content="0;url=/admin">충전 완료')

@app.get('/admin/job/{jid}',response_class=HTMLResponse)
def detail(jid:str,user=Depends(admin)):
    with db.connect() as c:
        j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not j:raise HTTPException(404)
        batches=c.execute('SELECT * FROM batches WHERE job_id=?',(jid,)).fetchall()
        ledger=c.execute('SELECT * FROM ledger WHERE job_id=? ORDER BY id',(jid,)).fetchall()
    body=f'<a href="/admin">← 목록</a><main><h2>작업 {esc(jid)}</h2><p>상태: {esc(j["status"])} | 성공 {j["success_count"]:,}건 / {j["valid_count"]:,}건</p><p>청구: {db.money(j["charged_tenths"])} | 환불: {db.money(j["refunded_tenths"])}</p>'
    body+='<h2>배치</h2><table><tr><th>ID</th><th>상태</th><th>시도</th><th>오류</th></tr>'
    for b in batches:body+=f'<tr><td>{esc(b["id"][:12])}</td><td>{esc(b["state"])}</td><td>{b["attempts"]}</td><td>{esc(b["error"])}</td></tr>'
    body+='</table><h2>정산 원장</h2><table><tr><th>유형</th><th>금액(0.1원 단위)</th><th>사유</th></tr>'
    for x in ledger:body+=f'<tr><td>{esc(x["kind"])}</td><td>{x["amount_tenths"]}</td><td>{esc(x["reason"])}</td></tr>'
    body+=f'<p>관리자 원본 결과: <a href="/admin/download/{esc(jid)}?fmt=csv">CSV 다운로드</a> · <a href="/admin/download/{esc(jid)}?fmt=xlsx">Excel 다운로드</a></p>' if j['status'] in ('completed','failed') else ''
    body+='</table></main><p>요청 결과 불명 배치는 자동 재전송하지 않습니다. 제공업체와 작업 ID를 대조한 후 별도 복구 작업을 수행해야 합니다.</p>'
    return wrap(body)

# Standalone web customer interface; cookie sessions are HttpOnly and SameSite=Strict.
from fastapi import UploadFile, File
from fastapi.responses import RedirectResponse, Response
from . import accounts,deposits
from .export import export

def customer(request:Request):
    uid=accounts.user_from_token(request.cookies.get('checker_session',''))
    if uid is None:raise HTTPException(401,'로그인이 필요합니다.')
    return uid

def customer_form(title,action,fields):
    f=''.join(f'<label>{label} <input name="{name}" type="{typ}" required></label><p></p>' for label,name,typ in fields)
    return wrap(f'<main><h2>{title}</h2><form method="post" action="{action}">{f}<button>확인</button></form><a href="/">홈</a></main>')

@app.get('/')
def home(request:Request):
    uid=accounts.user_from_token(request.cookies.get('checker_session',''))
    if uid is None:return wrap('<main><h2>가입자 검수봇</h2><p>검수 신청 및 결과 확인을 위한 회원 페이지입니다.</p><a href="/register">회원가입</a> · <a href="/login">로그인</a></main>')
    with db.connect() as c:
        balance=c.execute('SELECT balance_tenths FROM users WHERE user_id=?',(uid,)).fetchone()[0]
        row=c.execute('SELECT address FROM deposit_addresses WHERE user_id=?',(uid,)).fetchone()
        rate=db.setting(c,'usdt_krw_rate')
        jobs=c.execute('SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 50',(uid,)).fetchall()
        deposits_rows=c.execute('SELECT txid,usdt_micro,krw_tenths FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT 15',(uid,)).fetchall()
    address=esc(row['address']) if row else '관리자 주소 배정 대기 중 · 입금하지 마세요'
    body=f'<main><h2>내 잔액: {db.money(balance)}</h2><p>USDT 충전: TRON(TRC-20)만 지원 · 입금주소: <strong>{address}</strong></p><p>자동 충전 적용 환율: {rate:,}원/USDT (입금 확인 시점의 설정 환율, 0이면 자동 반영 중지)</p><p>다른 네트워크 송금 금지. 체인 확인 후 원화 잔액으로 반영됩니다.</p></main>'
    body+='<main><h2>검수 파일</h2><form method="post" action="/customer/preview" enctype="multipart/form-data"><input type="file" name="file" accept=".csv,.xlsx,.xls" required><button>파일 분석</button></form></main>'
    body+='<main><h2>검수 작업</h2><table><tr><th>작업</th><th>상태</th><th>처리</th><th>결과</th></tr>'
    for j in jobs:
        downloads=(f'<a href="/customer/download/{j["id"]}?fmt=csv">CSV</a> · <a href="/customer/download/{j["id"]}?fmt=xlsx">Excel</a>' if j['status'] in ('completed','failed') else '-')
        body+=f'<tr><td>{esc(j["id"][:12])}</td><td>{esc(j["status"])}</td><td>{j["success_count"]:,}/{j["valid_count"]:,}</td><td>{downloads}</td></tr>'
    body+='</table></main><main><h2>자동충전 기록</h2><table><tr><th>TXID</th><th>USDT</th><th>반영 금액</th></tr>'
    for d in deposits_rows:body+=f'<tr><td>{esc(d["txid"][:18])}…</td><td>{d["usdt_micro"]/1000000:.6f}</td><td>{db.money(d["krw_tenths"])}</td></tr>'
    body+='</table></main><form method="post" action="/logout"><button>로그아웃</button></form>'
    return wrap(body)

@app.get('/register')
def register_form():return customer_form('회원가입','/register',[('아이디','username','text'),('비밀번호 (12자 이상)','password','password')])
@app.post('/register')
def register_post(request:Request,username:str=Form(...),password:str=Form(...)):
    guard_origin(request)
    try:accounts.register(username,password)
    except ValueError as e:raise HTTPException(400,str(e))
    return RedirectResponse('/login',303)
@app.get('/login')
def login_form():return customer_form('로그인','/login',[('아이디','username','text'),('비밀번호','password','password')])
@app.post('/login')
def login_post(request:Request,username:str=Form(...),password:str=Form(...)):
    guard_origin(request)
    try:token=accounts.login(username,password)
    except Exception:raise HTTPException(401,'아이디 또는 비밀번호가 올바르지 않습니다.')
    response=RedirectResponse('/',303)
    response.set_cookie('checker_session',token,httponly=True,secure=(request.url.scheme=='https' or config.BASE_URL.startswith('https://')),samesite='strict',max_age=604800)
    return response
@app.post('/logout')
def logout(request:Request):
    guard_origin(request);accounts.logout(request.cookies.get('checker_session',''))
    response=RedirectResponse('/',303);response.delete_cookie('checker_session');return response

@app.post('/customer/preview')
async def customer_preview(request:Request,file:UploadFile=File(...),uid:int=Depends(customer)):
    guard_origin(request)
    data=await file.read(config.MAX_BYTES+1)
    if len(data)>config.MAX_BYTES:raise HTTPException(413,'업로드 크기 제한 초과')
    try:p=service.preview(uid,file.filename or '',data)
    except Exception as e:raise HTTPException(400,str(e))
    return wrap(f'''<main><h2>검수 신청 확인</h2><p>원본: {p['total']:,}건 / 정상: {p['valid']:,}건 / 중복: {p['duplicates']:,}건 / 형식 오류: {p['invalid']:,}건</p><p>예상 금액: {db.money(p['cost_tenths'])} / 현재 잔액: {db.money(p['balance_tenths'])}</p><form action="/customer/submit" method="post"><input type="hidden" name="preview_id" value="{p['preview_id']}"><button>검수 신청 및 결제</button></form><a href="/">취소</a></main>''')
@app.post('/customer/submit')
def customer_submit(request:Request,preview_id:str=Form(...),uid:int=Depends(customer)):
    guard_origin(request)
    try:jid=service.submit(uid,preview_id)
    except Exception as e:raise HTTPException(400,str(e))
    return wrap(f'<main>작업 신청 완료: {esc(jid)} <a href="/">내 작업</a></main>')
@app.get('/customer/download/{jid}')
def customer_download(jid:str,fmt:str='csv',days:str='all',uid:int=Depends(customer)):
    with db.connect() as c:
        j=c.execute('SELECT user_id,status FROM jobs WHERE id=?',(jid,)).fetchone()
        if not j or j['user_id']!=uid or j['status'] not in ('completed','failed'):raise HTTPException(404)
    try:content,mime=export(jid,fmt,days)
    except Exception as e:raise HTTPException(400,str(e))
    with db.transaction() as c:c.execute('UPDATE result_archives SET delivered_to_user_at=COALESCE(delivered_to_user_at,?) WHERE job_id=?',(db.now(),jid))
    return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="check_{jid[:12]}.{fmt}"','Cache-Control':'no-store'})

@app.post('/admin/deposit-address')
def admin_deposit_address(request:Request,user_id:int=Form(...),address:str=Form(...),user=Depends(admin)):
    guard_origin(request)
    try:deposits.assign(user_id,address.strip())
    except Exception as e:raise HTTPException(400,str(e))
    return RedirectResponse('/admin',303)
@app.post('/admin/usdt-rate')
def admin_usdt_rate(request:Request,rate:int=Form(...),user=Depends(admin)):
    guard_origin(request)
    if rate<0 or rate>100000:raise HTTPException(400,'환율 범위 오류')
    with db.transaction() as c:
        c.execute("UPDATE settings SET value=? WHERE key='usdt_krw_rate'",(str(rate),))
        db.audit(c,user,'change_usdt_rate',detail=f'rate_krw={rate}')
    return RedirectResponse('/admin',303)
@app.get('/admin/download/{jid}')
def admin_download(jid:str,fmt:str='csv',days:str='all',user=Depends(admin)):
    with db.connect() as c:
        j=c.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()
        if not j or j['status'] not in ('completed','failed'):raise HTTPException(404)
    try:data,mime=export(jid,fmt,days)
    except Exception as e:raise HTTPException(400,str(e))
    with db.transaction() as c:
        c.execute('UPDATE result_archives SET delivered_to_admin_at=COALESCE(delivered_to_admin_at,?) WHERE job_id=?',(db.now(),jid))
        db.audit(c,user,'admin_download_results',jid)
    return Response(data,media_type=mime,headers={'Content-Disposition':f'attachment; filename="admin_check_{jid[:12]}.{fmt}"','Cache-Control':'no-store'})

@app.post('/admin/reconcile')
def admin_reconcile(request:Request,batch_id:str=Form(...),resolution:str=Form(...),reason:str=Form(...),api_task_id:str=Form(''),user=Depends(admin)):
    guard_origin(request)
    if len(reason.strip())<8 or len(reason)>1000:raise HTTPException(400,'대조 근거는 8~1000자로 입력하세요.')
    if resolution not in ('resume','fail'):raise HTTPException(400,'대조 유형 오류')
    if resolution=='resume' and not api_task_id.strip():raise HTTPException(400,'외부 작업 ID 필요')
    with db.transaction() as c:
        b=c.execute('SELECT * FROM batches WHERE id=?',(batch_id,)).fetchone()
        if not b or b['state']!='reconcile':raise HTTPException(409,'대조 대상 배치가 아닙니다.')
        if resolution=='resume':
            if c.execute('SELECT 1 FROM batches WHERE api_task_id=?',(api_task_id,)).fetchone():raise HTTPException(409,'이미 등록된 외부 작업 ID')
            c.execute("UPDATE batches SET state='processing',api_task_id=?,error=NULL,updated_at=? WHERE id=?",(api_task_id.strip(),db.now(),batch_id))
        else:
            c.execute("UPDATE batches SET state='failed',error=?,updated_at=? WHERE id=?",('관리자 대조로 실패 확정',db.now(),batch_id))
        db.audit(c,user,'reconcile_'+resolution,b['job_id'],f'batch={batch_id};reason={reason[:500]}')
    return RedirectResponse('/admin',303)
