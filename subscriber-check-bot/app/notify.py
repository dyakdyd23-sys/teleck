"""Admin-only result notifications. Sends customer-equivalent full CSV, if within Telegram limit."""
import asyncio,io,logging
from telegram import Bot,InputFile
from . import config,db
from .export import export
log=logging.getLogger(__name__)

async def send_pending():
    if not config.TOKEN or not config.ADMIN_IDS:return
    with db.connect() as c:
        jobs=c.execute('''SELECT j.id,j.user_id,j.status,j.valid_count,j.success_count,j.refunded_tenths
            FROM jobs j JOIN result_archives a ON a.job_id=j.id
            WHERE a.delivered_to_admin_at IS NULL ORDER BY a.created_at LIMIT 5''').fetchall()
    if not jobs:return
    async with Bot(config.TOKEN) as bot:
        for j in jobs:
            try:
                content,_=export(j['id'],'xlsx','all')
                for admin in sorted(config.ADMIN_IDS):
                    with db.connect() as c:
                        if c.execute('SELECT 1 FROM admin_deliveries WHERE job_id=? AND admin_id=?',(j['id'],admin)).fetchone():continue
                    msg=(f"가입자 검수 결과 보관 알림\n작업: {j['id']}\n구매자 ID: {j['user_id']}\n"
                         f"상태: {j['status']}\n신청: {j['valid_count']} / 성공: {j['success_count']}\n"
                         f"환불: {db.money(j['refunded_tenths'])}")
                    await bot.send_message(chat_id=admin,text=msg)
                    document_sent=int(len(content)<=45*1024*1024)
                    if document_sent:
                        await bot.send_document(chat_id=admin,document=InputFile(io.BytesIO(content),filename=f'results_{j["id"][:12]}.xlsx'))
                    else:
                        await bot.send_message(chat_id=admin,text='결과 파일이 대용량이므로 관리자 페이지에서 내려받으세요.')
                    with db.transaction() as c:
                        c.execute('INSERT OR IGNORE INTO admin_deliveries(job_id,admin_id,delivered_at,document_sent) VALUES(?,?,?,?)',(j['id'],admin,db.now(),document_sent))
                with db.transaction() as c:
                    c.execute('UPDATE result_archives SET delivered_to_admin_at=COALESCE(delivered_to_admin_at,?) WHERE job_id=?',(db.now(),j['id']))
                    db.audit(c,'system','admin_result_sent',j['id'],f'admin_count={len(config.ADMIN_IDS)}')
            except Exception:
                log.exception('Admin result delivery failed; scheduled to retry')
                break

async def run(stop):
    while not stop.is_set():
        try:await send_pending()
        except Exception:log.exception('Admin notification loop error')
        try:await asyncio.wait_for(stop.wait(),timeout=45)
        except asyncio.TimeoutError:pass
