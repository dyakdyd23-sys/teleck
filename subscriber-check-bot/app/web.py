import asyncio, html, secrets, base64, os, logging, uuid
from .money_input import parse_krw_tenths
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from . import config,db,service,sheet_rate
from .bot import run as bot_run
from .worker import run as worker_run
from .deposits import run as deposit_run
from .notify import run as notify_run
from .sheet_rate import run as rate_run

log=logging.getLogger(__name__)
security=HTTPBasic(auto_error=False)
stop=asyncio.Event()

@asynccontextmanager
async def lifespan(app):
    db.init();stop.clear()
    tasks=[asyncio.create_task(worker_run(stop)),asyncio.create_task(bot_run(stop)),asyncio.create_task(rate_run(stop)),asyncio.create_task(deposit_run(stop)),asyncio.create_task(notify_run(stop))]
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
    :root{color-scheme:dark;--bg:#090f1c;--panel:#111b2e;--line:#273650;--ink:#f1f6ff;--muted:#9caec9;--accent:#60a5fa}
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif;background:radial-gradient(ellipse at 75% -10%,#182d52 0,transparent 42%),var(--bg);color:var(--ink);max-width:1240px;margin:0 auto;padding:26px 24px 85px;line-height:1.55}
    body>h1{letter-spacing:-.7px;font-size:25px;margin:6px 0 20px;padding:13px 0 22px;border-bottom:1px solid var(--line)}body>h1:before{content:'◈';color:#6bb7ff;margin-right:11px;font-size:23px}h1 small{font-weight:500;font-size:12px;letter-spacing:2px;text-transform:uppercase;border:1px solid #304669;border-radius:999px;padding:5px 10px;vertical-align:middle;margin-left:8px;color:#a9c7ee}
    h2{font-size:17px;font-weight:730;letter-spacing:-.4px;margin:0 0 18px;color:#f4f8ff}p{color:var(--muted);line-height:1.7}main{background:linear-gradient(160deg,#16233a,#111a2c 70%);padding:25px 26px;border:1px solid #283b59;border-radius:17px;margin:18px 0;overflow-x:auto;box-shadow:0 13px 34px #00000024}main:hover{border-color:#355070}
    table{width:100%;border-collapse:collapse;font-size:13px;text-align:left}td,th{padding:13px 12px;text-align:left;border-bottom:1px solid #2a3850;white-space:nowrap}tr:last-child td{border-bottom:0}tbody tr:hover{background:#20304a66}th{color:#9bb7dc;font-weight:650;font-size:12px;letter-spacing:.2px;background:#1c2a403d}td{color:#e3edff}
    input,select,button{font:inherit;font-size:13px;border-radius:9px;padding:11px 13px;margin:5px 5px 5px 0}input,select{background:#0c1729;color:white;border:1px solid #3c506b;min-height:42px}input::placeholder{color:#778ca8}input:focus,select:focus{outline:2px solid #5996ff;outline-offset:1px}label{color:#b4c6df;font-size:13px;margin-right:10px}button{background:linear-gradient(135deg,#3285f5,#2563dd);border:1px solid #4591f7;min-height:42px;cursor:pointer;font-weight:700;color:white;box-shadow:0 4px 13px #0f57cd3b;transition:.15s ease}button:hover{filter:brightness(1.15);transform:translateY(-1px)}a{color:#87bbff;text-decoration:none;font-weight:550}a:hover{text-decoration:underline;color:#b5d7ff}.stat{display:inline-block;background:linear-gradient(140deg,#203858,#192a44);border:1px solid #385377;border-radius:13px;padding:17px 21px;margin:5px 8px 8px 0;min-width:145px}.stat strong{display:block;font-size:21px;margin-top:5px;color:#f8fbff}.tag{font-size:12px;color:#93c6ff}small{color:#b8c9e4}form{margin:10px 0}main>form+form{border-top:1px solid #2a3c56;margin-top:20px;padding-top:20px}
    @media(max-width:650px){body{padding:14px 13px 50px}body>h1{font-size:20px}main{padding:19px 16px;margin:13px 0;border-radius:14px}h2{font-size:16px}input,select{max-width:100%;width:100%}button{width:100%;margin:7px 0}.stat{width:calc(50% - 12px);min-width:0;font-size:12px;padding:12px}td,th{padding:10px 8px}table{font-size:12px}}
</style></head><body><h1>가입자 검수봇 <small>관리자</small></h1>'''+body+'</body></html>')

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
    body+='<main><h2>관리자 잔액 충전 (운영자 전용)</h2><form method="post" action="/admin/credit"><input type="number" name="user_id" placeholder="Telegram 사용자 ID" required><input type="number" name="amount_krw" placeholder="충전 금액(원) 예: 50000.0" min="0.1" step="0.1" required><span class="tag">거래 참조번호 자동 발급</span><button>충전</button></form></main>'
    current_shared=deposits.shared_address() or '미설정'
    body+=f'<main><h2>USDT · TRC-20 공용 충전주소</h2><p>현재 공용 주소: <code>{esc(current_shared)}</code></p><form method="post" action="/admin/shared-deposit-address"><input name="address" placeholder="공용 TRON USDT 수신주소" required><button>공용 주소 저장 / 변경</button></form><p>주소를 변경하면 신규 신청부터 적용합니다. 기존 입금·신청·원장은 그대로 보존합니다. 진행 중인 충전 신청이 있으면 변경할 수 없습니다.</p></main>'
    with db.connect() as c:
        invoices=c.execute("SELECT r.*,u.balance_tenths FROM topup_requests r JOIN users u ON u.user_id=r.user_id ORDER BY r.created_at DESC LIMIT 50").fetchall()
        unmatched=c.execute("SELECT * FROM unmatched_deposits ORDER BY seen_at DESC LIMIT 50").fetchall()
    body+='<main><h2>USDT 충전 신청 내역</h2><p>송금 요청액·정확한 수량·상태 및 거래 식별자를 조회합니다. 미일치 입금은 자동 적립되지 않습니다.</p><table><tr><th>신청번호</th><th>회원</th><th>신청액</th><th>USDT</th><th>환율</th><th>상태</th><th>TXID</th><th>관리자 처리</th></tr>'
    for r in invoices:
        actions='—'
        if r['status'] in ('pending','expired'):
            actions=(f'<form method="post" action="/admin/topup/resolve">'
                     f'<input type="hidden" name="request_id" value="{esc(r["id"])}">'
                     f'<input name="reason" placeholder="처리 사유 (필수)" minlength="5" required>'
                     f'<input name="txid" placeholder="승인 시 미매칭 입금 TXID 64자리">'
                     f'<button name="action" value="approve">입금 확인 후 승인</button>'
                     f'<button name="action" value="reject" style="background:#92353c" onclick="return confirm(&quot;입금이 진행됐다면 거절 후 자동 적립되지 않습니다. 거절할까요?&quot;)">신청 거절</button></form>')
        body+=f'<tr><td>{esc(r["id"][:12])}</td><td>{r["user_id"]}</td><td>{db.money(r["requested_tenths"])}</td><td>{r["usdt_milli"]/1000:.3f}</td><td>{r["rate_krw"]:,}</td><td>{esc(r["status"])}</td><td>{esc(r["txid"] or "-")}</td><td>{actions}</td></tr>'
    body+='</table></main><main><h2>자동 반영 보류 입금 · 관리자 대조 필요</h2><p>입금 확인은 됐으나 유효한 신청과 일치하지 않은 거래입니다. 거래내역을 직접 확인한 뒤 기존 수동 잔액 조정 기능으로 별도 정산하세요. 중복 지급에 주의하세요.</p><table><tr><th>회원</th><th>USDT</th><th>TXID</th><th>사유</th></tr>'
    for r in unmatched:
        body+=f'<tr><td>{r["user_id"]}</td><td>{r["usdt_micro"]/1000000:.6f}</td><td>{esc(r["txid"])}</td><td>{esc(r["reason"])}</td></tr>'
    body+='</table></main>'
    body+='<main><h2>작업 목록</h2><table><tr><th>작업번호</th><th>사용자</th><th>상태</th><th>검수</th><th>완료</th><th>청구</th><th>환불</th></tr>'
    for j in jobs:body+=f'<tr><td><a href="/admin/job/{esc(j["id"])}">{esc(j["id"][:12])}</a></td><td>{j["user_id"]}</td><td>{esc(j["status"])}</td><td>{j["valid_count"]:,}</td><td>{j["success_count"]:,}</td><td>{db.money(j["charged_tenths"])}</td><td>{db.money(j["refunded_tenths"])}</td></tr>'
    body+='</table></main><main><h2>사용자별 잔액</h2><table><tr><th>Telegram ID</th><th>잔액</th></tr>'
    for u in users:body+=f'<tr><td><a href="/admin/member/{u["user_id"]}">{u["user_id"]}</a></td><td>{db.money(u["balance_tenths"])}</td></tr>'
    body+='</table><p><a href="/admin/members">회원 전체 조회 · 검색 · 정산 관리 →</a></p></main><main><h2>외부 요청 결과 불명 · 수동 대조 필요</h2><table><tr><th>배치 ID</th><th>작업</th><th>오류</th></tr>'
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
def credit(request:Request,user_id:int=Form(...),amount_krw:str=Form(...),user=Depends(admin)):
    guard_origin(request)
    try:
        amount_tenths=parse_krw_tenths(amount_krw)
        service.credit(user_id,amount_tenths,user,uuid.uuid4().hex)
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
    address=esc(deposits.shared_address() or '관리자 공용 충전주소 대기 중 · 입금하지 마세요')
    active_rate=sheet_rate.available_rate()
    body=f'<main><h2>내 잔액: {db.money(balance)}</h2><p><b>USDT · TRC-20 전용 자동충전</b></p><p>전용 입금주소: <strong>{address if active_rate and config.TRONGRID_KEY else "준비 중 · 입금하지 마세요"}</strong></p><p>구글 스프레드시트 USD/KRW 기준 환산: {active_rate:,}원/USDT</p><p><b>TRX는 충전 지원하지 않습니다. TRON 체인의 USDT(TRC-20)만 전송하세요.</b></p></main>'
    body+='<main><h2>검수 파일</h2><form method="post" action="/customer/preview" enctype="multipart/form-data"><input type="file" name="file" accept=".csv,.xlsx,.xls,.txt" required><button>파일 분석</button></form></main>'
    body+='<main><h2>검수 작업</h2><table><tr><th>작업</th><th>상태</th><th>처리</th><th>결과</th></tr>'
    for j in jobs:
        downloads=(f'<a href="/customer/download/{j["id"]}?fmt=xlsx">Excel 다운로드</a>' if j['status'] in ('completed','failed') else '-')
        body+=f'<tr><td>{esc(j["id"][:12])}</td><td>{esc(j["status"])}</td><td>{j["success_count"]:,}/{j["valid_count"]:,}</td><td>{downloads}</td></tr>'
    body+='</table></main><main><h2>자동충전 기록</h2><table><tr><th>TXID</th><th>USDT</th><th>반영 금액</th></tr>'
    for d in deposits_rows:body+=f'<tr><td>{esc(d["txid"][:18])}…</td><td>{d["usdt_micro"]/1000000:.3f}</td><td>{db.money(d["krw_tenths"])}</td></tr>'
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
def customer_download(jid:str,fmt:str='xlsx',days:str='all',uid:int=Depends(customer)):
    with db.connect() as c:
        j=c.execute('SELECT user_id,status FROM jobs WHERE id=?',(jid,)).fetchone()
        if not j or j['user_id']!=uid or j['status'] not in ('completed','failed'):raise HTTPException(404)
    if days=='raw':raise HTTPException(403,'관리자 전용 원본 결과')
    try:content,mime=export(jid,fmt,days)
    except Exception as e:raise HTTPException(400,str(e))
    with db.transaction() as c:c.execute('UPDATE result_archives SET delivered_to_user_at=COALESCE(delivered_to_user_at,?) WHERE job_id=?',(db.now(),jid))
    return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="check_{jid[:12]}.{fmt}"','Cache-Control':'no-store'})

@app.post('/admin/topup/resolve')
def admin_topup_resolve(request:Request,request_id:str=Form(...),action:str=Form(...),reason:str=Form(...),txid:str=Form(''),user=Depends(admin)):
    guard_origin(request)
    try:
        deposits.admin_resolve_topup(request_id,action,reason,actor=user,txid=txid)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    return RedirectResponse('/admin',303)

@app.post('/admin/shared-deposit-address')
def admin_shared_deposit_address(request:Request,address:str=Form(...),user=Depends(admin)):
    guard_origin(request)
    try:deposits.set_shared_address(address.strip(),actor=user)
    except ValueError as e:raise HTTPException(400,str(e))
    return RedirectResponse('/admin',303)

# Deprecated individual-address endpoints: public UI uses the one shared address.
@app.post('/admin/deposit-pool')
def admin_deposit_pool(request:Request,user=Depends(admin)):
    guard_origin(request)
    raise HTTPException(410,'공용 주소 설정만 지원합니다. /admin에서 공용 지갑주소를 설정하세요.')

@app.post('/admin/deposit-address')
def admin_deposit_address(request:Request,user=Depends(admin)):
    guard_origin(request)
    raise HTTPException(410,'회원별 주소 지정은 비활성화되었습니다. 공용 지갑주소만 사용하세요.')

@app.post('/admin/usdt-rate')
def admin_usdt_rate(request:Request,rate:int=Form(...),user=Depends(admin)):
    guard_origin(request)
    raise HTTPException(403,'Google Sheets 자동 환율만 지원합니다.')
    if rate<0 or rate>100000:raise HTTPException(400,'환율 범위 오류')
    with db.transaction() as c:
        c.execute("UPDATE settings SET value=? WHERE key='usdt_krw_rate'",(str(rate),))
        db.audit(c,user,'change_usdt_rate',detail=f'rate_krw={rate}')
    return RedirectResponse('/admin',303)
@app.post('/admin/trx-rate')
def admin_trx_rate(request:Request,rate:int=Form(...),user=Depends(admin)):
    guard_origin(request)
    raise HTTPException(403,'TRX 자동충전은 지원하지 않습니다.')
    if not 0<=rate<=100000:raise HTTPException(400,'환율 범위 오류')
    with db.transaction() as c:
        c.execute("UPDATE settings SET value=? WHERE key='trx_krw_rate'",(str(rate),))
        db.audit(c,user,'change_trx_rate',detail=f'rate_krw={rate}')
    return RedirectResponse('/admin',303)

@app.get('/admin/download/{jid}')
def admin_download(jid:str,fmt:str='xlsx',days:str='all',user=Depends(admin)):
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


@app.get('/admin/members',response_class=HTMLResponse)
def admin_members(q:str='',page:int=1,user=Depends(admin)):
    page=max(1,min(page,100000));q=q.strip()[:100]
    with db.connect() as c:
        where="WHERE CAST(u.user_id AS TEXT) LIKE ? OR a.username LIKE ?" if q else ''
        params=(f'%{q}%',f'%{q}%') if q else ()
        users=c.execute(f"""SELECT u.user_id,u.balance_tenths,u.created_at,a.username,
            (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.user_id) job_count,
            (SELECT COUNT(*) FROM deposits d WHERE d.user_id=u.user_id)+(SELECT COUNT(*) FROM trx_deposits t WHERE t.user_id=u.user_id) deposit_count
            FROM users u LEFT JOIN web_accounts a ON a.user_id=u.user_id {where}
            ORDER BY u.created_at DESC LIMIT 50 OFFSET ?""",(*params,(page-1)*50)).fetchall()
        count=c.execute(f'SELECT COUNT(*) FROM users u LEFT JOIN web_accounts a ON a.user_id=u.user_id {where}',params).fetchone()[0]
    body='<main><h2>회원 관리</h2><form method="get"><input name="q" placeholder="회원 ID 또는 아이디 검색" value="'+esc(q)+'"><button>검색</button></form><p>검색 결과 '+format(count,',')+'명 · 50명씩 표시</p><table><tr><th>회원 ID</th><th>아이디</th><th>잔액</th><th>검수 작업</th><th>충전 건수</th><th>가입일</th></tr>'
    for x in users:
        body+=f'<tr><td><a href="/admin/member/{x["user_id"]}">{x["user_id"]}</a></td><td>{esc(x["username"] or "Telegram")}</td><td>{db.money(x["balance_tenths"])}</td><td>{x["job_count"]:,}</td><td>{x["deposit_count"]:,}</td><td>{esc(x["created_at"][:10])}</td></tr>'
    body+='</table><p>'
    if page>1:body+=f'<a href="/admin/members?q={__import__("urllib.parse",fromlist=["quote"]).quote(q)}&page={page-1}">← 이전</a> '
    if page*50<count:body+=f'<a href="/admin/members?q={__import__("urllib.parse",fromlist=["quote"]).quote(q)}&page={page+1}">다음 →</a>'
    return wrap(body+'</p><a href="/admin">관리자 홈</a></main>')

@app.get('/admin/member/{uid}',response_class=HTMLResponse)
def admin_member(uid:int,user=Depends(admin)):
    with db.connect() as c:
        m=c.execute('SELECT u.*,a.username FROM users u LEFT JOIN web_accounts a ON a.user_id=u.user_id WHERE u.user_id=?',(uid,)).fetchone()
        if not m:raise HTTPException(404,'회원을 찾을 수 없습니다.')
        jobs=c.execute('SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 100',(uid,)).fetchall()
        ledger=c.execute('SELECT * FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 100',(uid,)).fetchall()
        deposits=c.execute('SELECT * FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT 50',(uid,)).fetchall()
        trx_deposits=c.execute('SELECT * FROM trx_deposits WHERE user_id=? ORDER BY id DESC LIMIT 50',(uid,)).fetchall()
        events=c.execute('SELECT * FROM audit WHERE actor=? OR detail LIKE ? ORDER BY id DESC LIMIT 100',(str(uid),f'%user={uid},%')).fetchall()
        address=c.execute('SELECT address FROM deposit_addresses WHERE user_id=?',(uid,)).fetchone()
    body=f'<a href="/admin/members">← 회원 목록</a><main><h2>회원 상세 · {uid}</h2><p>아이디: {esc(m["username"] or "텔레그램 계정")} · 가입: {esc(m["created_at"])}</p><div class="stat">현재 잔액<br><strong>{db.money(m["balance_tenths"])}</strong></div><div class="stat">검수 작업<br><strong>{len(jobs)}건 (최근 100건)</strong></div><p>공용 입금 주소: {esc(deposits.shared_address() or "미등록")}</p></main>'
    body+=f"""<main><h2>관리자 수동 잔액 조정</h2><p>조정 사유를 남기며 거래 참조번호는 자동 생성됩니다. 차감은 보유잔액 범위 내에서만 가능합니다.</p>
    <form method="post" action="/admin/member/{uid}/adjust"><label>유형 <select name="direction"><option value="credit">충전 (+)</option><option value="debit">차감 (-)</option></select></label>
    <label>금액(원) <input type="number" name="amount_krw" min="1" max="1000000000" required></label>
    <label>사유 <input name="reason" minlength="5" maxlength="300" required></label>
    <button>잔액 조정 기록</button></form></main>"""
    body+='<main><h2>회원 검수 진행 로그</h2><table><tr><th>작업 ID</th><th>일시</th><th>상태</th><th>진행률</th><th>성공/실패</th><th>결제/환불</th></tr>'
    for j in jobs:
        progress=min(100,round(j['processed_count']*100/max(j['valid_count'],1),1))
        body+=f'<tr><td><a href="/admin/job/{esc(j["id"])}">{esc(j["id"][:12])}</a></td><td>{esc(j["created_at"][:19])}</td><td>{esc(j["status"])}</td><td>{progress}%</td><td>{j["success_count"]:,}/{j["failed_count"]:,}</td><td>{db.money(j["charged_tenths"])} / {db.money(j["refunded_tenths"])}</td></tr>'
    body+='</table></main><main><h2>잔액 변동 및 정산 원장</h2><table><tr><th>일시</th><th>종류</th><th>금액</th><th>사유</th><th>처리자</th><th>참조값</th></tr>'
    for x in ledger:body+=f'<tr><td>{esc(x["created_at"][:19])}</td><td>{esc(x["kind"])}</td><td>{db.money(x["amount_tenths"]) if x["amount_tenths"]>=0 else "-"+db.money(-x["amount_tenths"])}</td><td>{esc(x["reason"])}</td><td>{esc(x["actor"])}</td><td>{esc(x["idempotency_key"])}</td></tr>'
    body+='</table></main><main><h2>USDT 충전 이력</h2><table><tr><th>일시</th><th>TXID</th><th>USDT</th><th>지급 원화</th></tr>'
    for x in deposits:body+=f'<tr><td>{esc(x["confirmed_at"][:19])}</td><td>{esc(x["txid"])}</td><td>{x["usdt_micro"]/1000000:.3f}</td><td>{db.money(x["krw_tenths"])}</td></tr>'
    body+='</table></main><main><h2>과거 TRX 충전 이력 (현재 신규 충전 중단)</h2><table><tr><th>일시</th><th>TXID</th><th>TRX</th><th>지급 원화</th></tr>'
    for x in trx_deposits:body+=f'<tr><td>{esc(x["confirmed_at"][:19])}</td><td>{esc(x["txid"])}</td><td>{x["amount_sun"]/1000000:.3f}</td><td>{db.money(x["krw_tenths"])}</td></tr>' 
    body+='</table></main><main><h2>회원 활동 감사 로그</h2><table><tr><th>시각</th><th>활동</th><th>작업</th><th>내용</th></tr>'
    for x in events:body+=f'<tr><td>{esc(x["created_at"][:19])}</td><td>{esc(x["action"])}</td><td>{esc(x["job_id"] or "-")}</td><td>{esc(x["detail"])}</td></tr>'
    return wrap(body+'</table></main>')

@app.post('/admin/member/{uid}/adjust')
def admin_adjust(request:Request,uid:int,direction:str=Form(...),amount_krw:int=Form(...),reason:str=Form(...),user=Depends(admin)):
    guard_origin(request)
    if direction not in ('credit','debit') or not (1<=amount_krw<=1000000000):raise HTTPException(400,'조정값 오류')
    if not 5<=len(reason.strip())<=300:raise HTTPException(400,'사유 길이 오류')
    reference=uuid.uuid4().hex
    amount=amount_krw*10 * (1 if direction=='credit' else -1)
    with db.transaction() as c:
        if not c.execute('SELECT 1 FROM users WHERE user_id=?',(uid,)).fetchone():raise HTTPException(404,'회원 없음')
        inserted=c.execute('UPDATE users SET balance_tenths=balance_tenths+? WHERE user_id=? AND balance_tenths+?>=0',(amount,uid,amount)).rowcount
        if not inserted:raise HTTPException(409,'잔액 부족')
        try:c.execute('INSERT INTO ledger(user_id,kind,amount_tenths,reason,actor,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?)',
            (uid,'admin_'+direction,amount,reason.strip(),user,'admin_adjust:'+reference.strip(),db.now()))
        except __import__('sqlite3').IntegrityError:raise HTTPException(409,'이미 사용한 고유 참조값')
        db.audit(c,user,'admin_'+direction,detail=f'user={uid},amount_tenths={amount},reason={reason.strip()},reference={reference.strip()}')
    return RedirectResponse(f'/admin/member/{uid}',303)
