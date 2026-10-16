import io,logging
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup,InputFile
from telegram.ext import Application,CommandHandler,MessageHandler,CallbackQueryHandler,filters,ContextTypes
from . import config,db,service
from .export import export
log=logging.getLogger(__name__)

def keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton('내 잔액',callback_data='balance'),InlineKeyboardButton('내 작업',callback_data='jobs')],
      [InlineKeyboardButton('이용 안내',callback_data='help')]])

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    with db.transaction() as c:db.ensure_user(c,update.effective_user.id)
    await update.effective_message.reply_text('가입자 검수봇\n\nCSV / XLSX / XLS 파일을 이 대화에 전송해 주세요. 번호 정규화와 중복 제거 후 예상 비용을 안내합니다.\n신청 전 실제 API 연동 여부를 확인합니다.',reply_markup=keyboard())

async def doc(update:Update,context:ContextTypes.DEFAULT_TYPE):
    document=update.message.document
    if not document.file_name or document.file_name.lower().rsplit('.',1)[-1] not in ('csv','xlsx','xls'):
        return await update.message.reply_text('CSV/XLSX/XLS 파일을 전송해 주세요.')
    if document.file_size and document.file_size>config.MAX_BYTES:
        return await update.message.reply_text('파일 용량 제한을 초과했습니다.')
    try:
        f=await document.get_file();content=bytes(await f.download_as_bytearray())
        result=service.preview(update.effective_user.id,document.file_name,content)
    except Exception as e:return await update.message.reply_text(f'파일 처리 실패: {e}')
    keyboard=InlineKeyboardMarkup([[InlineKeyboardButton('이 파일 검수 신청',callback_data='submit:'+result['preview_id'])]])
    await update.message.reply_text(f"원본 행: {result['total']:,}건\n정상·중복제거: {result['valid']:,}건\n중복: {result['duplicates']:,}건\n형식 오류: {result['invalid']:,}건\n예상 비용: {db.money(result['cost_tenths'])}\n현재 잔액: {db.money(result['balance_tenths'])}\n신청 제한: {result['min_count']:,}~{result['max_count']:,}건",reply_markup=keyboard)

async def buttons(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query;await q.answer();uid=q.from_user.id;data=q.data
    try:
        if data=='balance':
            with db.connect() as c:row=c.execute('SELECT balance_tenths FROM users WHERE user_id=?',(uid,)).fetchone()
            return await q.message.reply_text('현재 잔액: '+db.money(row[0] if row else 0))
        if data=='jobs':
            jobs=service.list_jobs(uid)
            if not jobs:return await q.message.reply_text('검수 내역이 없습니다.')
            buttons=[[InlineKeyboardButton(f"{j['id'][:8]} | {j['status']} | {j['success_count']:,}/{j['valid_count']:,}",callback_data='job:'+j['id'])] for j in jobs[:10]]
            return await q.message.reply_text('내 검수 작업',reply_markup=InlineKeyboardMarkup(buttons))
        if data=='help':return await q.message.reply_text(f'파일 전송 → 예상 비용 확인 → 검수 신청 → 완료 후 결과 다운로드\n고객센터: {config.SUPPORT}')
        if data.startswith('submit:'):
            jid=service.submit(uid,data[7:]);return await q.message.reply_text('검수 신청 완료. 작업번호: '+jid[:8]+'\n내 작업 메뉴에서 확인해 주세요.')
        if data.startswith('job:'):
            jid=data[4:];j=next((x for x in service.list_jobs(uid) if x['id']==jid),None)
            if not j:return await q.message.reply_text('작업 접근 권한이 없습니다.')
            kb=InlineKeyboardMarkup([[InlineKeyboardButton('CSV 전체',callback_data='download:'+jid+':csv:all'),InlineKeyboardButton('Excel 전체',callback_data='download:'+jid+':xlsx:all')],
              [InlineKeyboardButton(f'{n}일',callback_data=f'download:{jid}:csv:{n}') for n in (1,3,7)]]) if j['status'] in ('completed','failed') else None
            return await q.message.reply_text(f"상태: {j['status']}\n신청: {j['valid_count']:,}\n확인: {j['success_count']:,}\n미처리: {j['failed_count']:,}\n청구: {db.money(j['charged_tenths'])}\n환불: {db.money(j['refunded_tenths'])}",reply_markup=kb)
        if data.startswith('download:'):
            _,jid,fmt,days=data.split(':')
            with db.connect() as c:j=c.execute('SELECT user_id,status FROM jobs WHERE id=?',(jid,)).fetchone()
            if not j or j['user_id']!=uid or j['status'] not in ('completed','failed'):return await q.message.reply_text('다운로드 권한 또는 완료 내역이 없습니다.')
            content,_=export(jid,fmt,days)
            return await q.message.reply_document(InputFile(io.BytesIO(content),filename=f'check_{jid[:8]}_{days}.{fmt}'))
    except Exception as e:await q.message.reply_text(str(e))

def build():
    app=Application.builder().token(config.TOKEN).build()
    app.add_handler(CommandHandler('start',start));app.add_handler(CommandHandler('menu',start))
    app.add_handler(MessageHandler(filters.Document.ALL,doc))
    app.add_handler(CallbackQueryHandler(buttons))
    return app

async def run(stop):
    if not config.TOKEN:
        log.warning('Telegram token absent; bot disabled');return
    app=build()
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=False)
        await stop.wait()
        await app.updater.stop()
        await app.stop()
