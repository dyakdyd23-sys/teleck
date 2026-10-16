"""Admin-only result notifications. Sends customer-equivalent full CSV, if within Telegram limit."""
import asyncio,io,logging
from datetime import datetime,timezone
from telegram import Bot,InputFile,InlineKeyboardButton,InlineKeyboardMarkup
from . import config,db
from .export import export
log=logging.getLogger(__name__)

async def send_events():
    if not config.TOKEN or not config.ADMIN_IDS:return
    with db.connect() as c:
        events=c.execute('SELECT * FROM admin_events WHERE delivered_at IS NULL ORDER BY id LIMIT 20').fetchall()
    if not events:return
    async with Bot(config.TOKEN) as bot:
        for event in events:
            try:
                for admin in sorted(config.ADMIN_IDS):
                    await bot.send_message(chat_id=admin,text=event['message'])
                with db.transaction() as c:
                    c.execute('UPDATE admin_events SET delivered_at=? WHERE id=? AND delivered_at IS NULL',(db.now(),event['id']))
            except Exception:
                log.exception('Admin event delivery failed; will retry')
                return

async def refresh_watches():
    if not config.TOKEN:return
    with db.connect() as c:
        watches=c.execute("""SELECT w.*,j.status,j.valid_count,j.success_count,j.charged_tenths,j.refunded_tenths,
                     j.created_at,ctl.last_progress_at,ctl.cancel_requested_at
                     FROM job_watches w JOIN jobs j ON j.id=w.job_id
                     LEFT JOIN job_control ctl ON ctl.job_id=w.job_id LIMIT 100""").fetchall()
    if not watches:return
    async with Bot(config.TOKEN) as bot:
        for w in watches:
            try:
                elapsed=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(w['last_progress_at'] or w['created_at'])).total_seconds()))
                terminal=w['status'] in ('completed','failed','cancelled')
                percent=int(w['success_count']*100/w['valid_count']) if w['valid_count'] else 0
                display=('취소 대조 중' if w['cancel_requested_at'] and not terminal else w['status'])
                txt=(f'📡 검수 실시간 현황\n작업: {w["job_id"][:12]}\n상태: {display}\n'
                     f'검수 처리: {w["success_count"]:,} / {w["valid_count"]:,} ({percent}%)\n'
                     f'최근 진행: {elapsed//60}분 전\n'
                     f'청구: {db.money(w["charged_tenths"])} / 환불: {db.money(w["refunded_tenths"])}\n'
                     '\n'
                     + ('✅ 작업 종료 · 내 작업에서 결과 확인' if terminal else '🔄 약 45초마다 자동 갱신'))
                if txt!=w['last_text']:
                    controls=[]
                    if terminal:
                        controls=[[InlineKeyboardButton('📁 내 작업 / 결과 확인',callback_data='job:'+w['job_id'])]]
                    elif w['cancel_requested_at']:
                        controls=[[InlineKeyboardButton('🔄 상태 확인',callback_data='refresh:'+w['job_id'])]]
                    elif elapsed>=300:
                        controls=[[InlineKeyboardButton('⏸ 5분 무진전 · 취소 요청',callback_data='canceljob:'+w['job_id'])],
                                  [InlineKeyboardButton('🔄 상태 확인',callback_data='refresh:'+w['job_id'])]]
                    else:
                        controls=[[InlineKeyboardButton('🔄 지금 확인',callback_data='refresh:'+w['job_id'])]]
                    await bot.edit_message_text(chat_id=w['chat_id'],message_id=w['message_id'],text=txt,
                                                reply_markup=InlineKeyboardMarkup(controls))
                    with db.transaction() as c:c.execute('UPDATE job_watches SET last_text=? WHERE job_id=? AND user_id=?',(txt,w['job_id'],w['user_id']))
                if terminal:
                    with db.transaction() as c:c.execute('DELETE FROM job_watches WHERE job_id=? AND user_id=?',(w['job_id'],w['user_id']))
            except Exception:
                log.exception('Progress message update failed')
                # Keep retrying transient Telegram/network failures.

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
                content,_=export(j['id'],'xlsx','raw')
                for admin in sorted(config.ADMIN_IDS):
                    with db.connect() as c:
                        if c.execute('SELECT 1 FROM admin_deliveries WHERE job_id=? AND admin_id=?',(j['id'],admin)).fetchone():continue
                    msg=(f"가입자 검수 결과 보관 알림\n작업: {j['id']}\n구매자 ID: {j['user_id']}\n"
                         f"상태: {j['status']}\n신청: {j['valid_count']} / 성공: {j['success_count']}\n"
                         f"환불: {db.money(j['refunded_tenths'])}")
                    await bot.send_message(chat_id=admin,text=msg)
                    document_sent=int(len(content)<=45*1024*1024)
                    if document_sent:
                        await bot.send_document(chat_id=admin,document=InputFile(io.BytesIO(content),filename=f'results_raw_{j["id"][:12]}.xlsx'))
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
        try:
            await refresh_watches()
            await send_events()
            await send_pending()
        except Exception:log.exception('Admin notification loop error')
        try:await asyncio.wait_for(stop.wait(),timeout=45)
        except asyncio.TimeoutError:pass
