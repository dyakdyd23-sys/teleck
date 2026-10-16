import io,logging,html
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup,InputFile
from telegram.ext import Application,CommandHandler,MessageHandler,CallbackQueryHandler,filters,ContextTypes
from . import config,db,service,deposits,sheet_rate
from .export import export
log=logging.getLogger(__name__)

def keyboard():
    return InlineKeyboardMarkup([
      [InlineKeyboardButton('🔍 검수 시작',callback_data='guide'),InlineKeyboardButton('📁 내 작업',callback_data='jobs')],
      [InlineKeyboardButton('💳 잔액 · 충전',callback_data='balance'),InlineKeyboardButton('📘 이용 안내',callback_data='help')],
      [InlineKeyboardButton('💬 고객센터',url='https://t.me/teleck777')]])

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    with db.transaction() as c:
        db.ensure_user(c,uid)
        balance=c.execute('SELECT balance_tenths FROM users WHERE user_id=?',(uid,)).fetchone()[0]
        unit=db.setting(c,'unit_tenths')
        mini=db.setting(c,'min_count');maxi=db.setting(c,'max_count')
    await update.effective_message.reply_text(
        '<b>🔎 텔레그램 가입자 검수</b>\n'
        '<i>대량 전화번호 검수 · 결과 파일 자동 정리</i>\n\n'
        '━━━━━━━━━━━━━━━━━━━━\n'
        '<b>MY DASHBOARD</b>\n\n'
        f'👤 <b>내 계정</b>  <code>{uid}</code>\n'
        f'💳 <b>보유 잔액</b>  {db.money(balance)}\n'
        f'🏷 <b>검수 단가</b>  {db.money(unit)} / 건\n'
        f'📦 <b>신청 수량</b>  {mini:,} ~ {maxi:,}건\n'
        '━━━━━━━━━━━━━━━━━━━━\n\n'
        '파일을 업로드하면 번호를 정리하고 예상 비용을 먼저 안내합니다.\n'
        '📄 <b>Excel · CSV · TXT 지원</b>\n'
        '📥 <b>결과는 Excel(.xlsx)로 제공</b>\n\n'
        '아래에서 원하는 메뉴를 선택하세요.',parse_mode='HTML',reply_markup=keyboard())

async def doc(update:Update,context:ContextTypes.DEFAULT_TYPE):
    document=update.message.document
    if not document.file_name or document.file_name.lower().rsplit('.',1)[-1] not in ('csv','xlsx','xls','txt'):
        return await update.message.reply_text('📂 XLSX / XLS / CSV / TXT 파일을 전송해 주세요.')
    if document.file_size and document.file_size>config.MAX_BYTES:
        return await update.message.reply_text('파일 용량 제한을 초과했습니다.')
    try:
        f=await document.get_file();content=bytes(await f.download_as_bytearray())
        result=service.preview(update.effective_user.id,document.file_name,content)
    except Exception as e:return await update.message.reply_text(f'⚠️ 파일 처리 실패: {e}')
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('✅ 검수 신청',callback_data='submit:'+result['preview_id'])],
                             [InlineKeyboardButton('❌ 취소',callback_data='cancel')]])
    remaining=result['balance_tenths']-result['cost_tenths']
    await update.message.reply_text(
        '<b>📋 파일 분석 결과</b>\n'
        '<i>신청 전 수량과 비용을 확인해 주세요.</i>\n\n'
        '━━━━━━━━━━━━━━━━━━━━\n'
        f'📄 원본 번호  <b>{result["total"]:,}건</b>\n'
        f'✅ 검수 대상  <b>{result["valid"]:,}건</b>\n'
        f'🔁 중복 제외  {result["duplicates"]:,}건\n'
        f'⚠️ 형식 오류  {result["invalid"]:,}건\n'
        '━━━━━━━━━━━━━━━━━━━━\n'
        f'💰 <b>예상 결제</b>  {db.money(result["cost_tenths"])}\n'
        f'💳 현재 잔액  {db.money(result["balance_tenths"])}\n'
        f'📉 결제 후 잔액  {db.money(remaining) if remaining>=0 else "잔액 부족"}\n\n'
        '📥 검수 결과는 <b>Excel(.xlsx)</b> 파일로 제공됩니다.\n'
        '<i>아래 버튼을 누르면 비용이 차감되고 검수가 신청됩니다.</i>',
        parse_mode='HTML',reply_markup=kb)

async def buttons(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query;await q.answer();uid=q.from_user.id;data=q.data
    try:
        if data=='guide':return await q.message.reply_text('<b>📂 검수 파일 업로드</b>\n<i>아래 형식의 파일을 이 채팅에 보내주세요.</i>\n\n━━━━━━━━━━━━━━━━━━━━\n<b>지원 파일</b>  XLSX · XLS · CSV · TXT\n<b>전화번호 예시</b>\n<code>010-1234-5678</code>\n<code>8210-1234-5678</code>\n<code>+821012345678</code>\n━━━━━━━━━━━━━━━━━━━━\n\n✅ 중복·형식 오류 자동 정리\n✅ 8210 번호를 010으로 변환\n✅ 결과는 Excel(.xlsx)로 전달\n\n<i>파일을 업로드하면 예상 비용을 먼저 보여드립니다.</i>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💬 문의하기',url='https://t.me/teleck777')]]))
        if data=='cancel':return await q.message.reply_text('신청을 취소했습니다. 다른 파일을 올려 주세요.')
        if data=='balance':
            deposits.auto_assign(uid)
            with db.connect() as c:
                row=c.execute('SELECT balance_tenths FROM users WHERE user_id=?',(uid,)).fetchone()
            rate=sheet_rate.available_rate()
            current=deposits.active_topup(uid)
            msg=('💳 <b>USDT · TRC-20 충전 신청</b>\n━━━━━━━━━━━━━━━━━━━━\n'
                f'💰 보유 잔액: <b>{db.money(row[0] if row else 0)}</b>\n'
                + (f'📈 적용 환율: <b>{rate:,}원/USDT</b> (Google USD/KRW 기준)\n\n' if rate else '⚠️ 환율 갱신 대기 중\n\n'))
            if current:
                msg+=('⏳ <b>진행 중인 충전 신청</b>\n'
                      f'신청 금액: <b>{db.money(current["requested_tenths"])}</b>\n'
                      f'송금 수량: <b>{current["usdt_milli"]/1000:.3f} USDT</b>\n'
                      f'입금주소: <code>{html.escape(current["address"])}</code>\n'
                      f'신청번호: <code>{current["id"][:12]}</code>\n\n'
                      '⚠️ 정확히 지정된 USDT 수량만 입금해 주세요.\n'
                      '만료·오입금은 자동 적립되지 않으며 관리자 대조가 필요합니다.')
                controls=[[InlineKeyboardButton('❌ 신청 취소',callback_data='topup_cancel')], [InlineKeyboardButton('💬 충전 문의',url='https://t.me/teleck777')]]
            else:
                msg+=('충전할 <b>원화 금액</b>을 입력하면 필요한 USDT 수량을 계산해 드립니다.\n'
                      '신청 가능: 1,000원 ~ 10,000,000원 (0.1원 단위)\n'
                      '⛔ TRX 입금은 지원하지 않습니다.\n'
                      '⏱ 송금 안내 후 30분 이내 정확히 입금해 주세요.')
                controls=[[InlineKeyboardButton('➕ 충전 금액 입력',callback_data='topup_amount')], [InlineKeyboardButton('💬 충전 문의',url='https://t.me/teleck777')]]
            return await q.message.reply_text(msg,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(controls))
        if data=='topup_amount':
            if deposits.active_topup(uid):return await q.message.reply_text('진행 중인 신청이 있습니다. 먼저 완료하거나 취소해 주세요.')
            context.user_data['awaiting_topup_amount']=True
            return await q.message.reply_text('💰 충전할 원화 금액을 입력해 주세요.\n예: <code>10000</code> 또는 <code>15000.5</code>\n\n⛔ 아직 코인을 보내지 마세요.',parse_mode='HTML')
        if data=='topup_cancel':
            deposits.cancel_topup(uid)
            context.user_data.pop('awaiting_topup_amount',None)
            return await q.message.reply_text('신청이 취소되었습니다. 이미 송금한 금액은 자동 취소·환불되지 않습니다. 송금했다면 고객센터에 문의해 주세요.')
        if data=='jobs':
            jobs=service.list_jobs(uid)
            if not jobs:return await q.message.reply_text('검수 내역이 없습니다.')
            buttons=[[InlineKeyboardButton(f"{j['id'][:8]} | {j['status']} | {j['success_count']:,}/{j['valid_count']:,}",callback_data='job:'+j['id'])] for j in jobs[:10]]
            return await q.message.reply_text('📁 <b>내 검수 작업</b>\n조회할 작업을 선택해 주세요.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(buttons))
        if data=='help':return await q.message.reply_text('📘 <b>서비스 이용 안내</b>\n━━━━━━━━━━━━━━━━━━━━\n<b>01</b>  Excel · CSV · TXT 파일 업로드\n<b>02</b>  번호 정규화·중복 자동 제거\n<b>03</b>  검수 수량·예상 비용 확인\n<b>04</b>  결제 후 작업 진행 상황 확인\n<b>05</b>  검수 완료 시 Excel 결과 수령\n\n📞 <b>고객센터</b>  @teleck777',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💬 고객센터 열기',url='https://t.me/teleck777')]]))
        if data.startswith('submit:'):
            jid=service.submit(uid,data[7:]);return await q.message.reply_text('검수 신청 완료. 작업번호: '+jid[:8]+'\n내 작업 메뉴에서 확인해 주세요.')
        if data.startswith('watch:'):
            jid=data[6:]
            with db.transaction() as c:
                job=c.execute('SELECT id FROM jobs WHERE id=? AND user_id=?',(jid,uid)).fetchone()
                if not job:return await q.message.reply_text('작업 접근 권한이 없습니다.')
                status=c.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()['status']
                if status not in ('pending','processing'):return await q.message.reply_text('이미 종료된 작업입니다.')
                sent=await q.message.reply_text('📡 실시간 검수 진행 상황 연결 중…')
                c.execute('INSERT OR REPLACE INTO job_watches(job_id,user_id,chat_id,message_id) VALUES(?,?,?,?)',
                          (jid,uid,sent.chat_id,sent.message_id))
            return
        if data.startswith('refresh:'):
            data='job:'+data[8:]
        if data.startswith('canceljob:'):
            return await q.message.reply_text(service.cancel_stalled(uid,data[10:]))
        if data.startswith('job:'):
            jid=data[4:];j=next((x for x in service.list_jobs(uid) if x['id']==jid),None)
            if not j:return await q.message.reply_text('작업 접근 권한이 없습니다.')
            if j['status'] in ('completed','failed','cancelled'):
                kb=InlineKeyboardMarkup([[InlineKeyboardButton('📥 가입자 전체 Excel',callback_data='download:'+jid+':xlsx:all')],
                    [InlineKeyboardButton(f'최근 {n}일',callback_data=f'download:{jid}:xlsx:{n}') for n in (1,3,7)]])
            else:
                from datetime import datetime,timezone
                with db.connect() as c:
                    ctrl=c.execute('SELECT * FROM job_control WHERE job_id=?',(jid,)).fetchone()
                    batch=c.execute("SELECT COUNT(*) AS n FROM batches WHERE job_id=? AND state='processing'",(jid,)).fetchone()['n']
                elapsed=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(ctrl['last_progress_at'] if ctrl else j['created_at'])).total_seconds()))
                progress=int(j['success_count']*100/j['valid_count']) if j['valid_count'] else 0
                stalled=elapsed>=300
                kb=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 최신 상태 확인',callback_data='refresh:'+jid),InlineKeyboardButton('📡 자동 갱신',callback_data='watch:'+jid)],
                    [InlineKeyboardButton('⏸ 5분 무진전 · 취소 요청',callback_data='canceljob:'+jid)] ] if stalled and not (ctrl and ctrl['cancel_requested_at']) else
                    [[InlineKeyboardButton('🔄 최신 상태 확인',callback_data='refresh:'+jid),InlineKeyboardButton('📡 자동 갱신',callback_data='watch:'+jid)]])
                return await q.message.reply_text(
                    f'📊 <b>검수 진행 현황</b>\n━━━━━━━━━━━━━━━━\n'
                    f'작업번호: <code>{jid[:12]}</code>\n상태: {"취소 대조 중" if ctrl and ctrl["cancel_requested_at"] else j["status"]}\n'
                    f'검수 완료 확인: <b>{j["success_count"]:,} / {j["valid_count"]:,}</b> ({progress}%)\n'
                    f'API 처리 중 배치: {batch}개\n마지막 결과 진전 후: {elapsed//60}분 {elapsed%60}초\n'
                    f'청구: {db.money(j["charged_tenths"])} / 환불: {db.money(j["refunded_tenths"])}\n\n'
                    '※ 확인된 결과 기준이며 API가 진행량을 제공하지 않으면 실제 처리율과 다를 수 있습니다.',
                    parse_mode='HTML',reply_markup=kb)
            return await q.message.reply_text(f"상태: {j['status']}\n신청: {j['valid_count']:,}\n확인: {j['success_count']:,}\n환불: {db.money(j['refunded_tenths'])}",reply_markup=kb)
        if data.startswith('download:'):
            _,jid,fmt,days=data.split(':')
            if days not in ('all','1','3','7') or fmt!='xlsx':return await q.message.reply_text('허용되지 않는 결과 형식입니다.')
            with db.connect() as c:j=c.execute('SELECT user_id,status FROM jobs WHERE id=?',(jid,)).fetchone()
            if not j or j['user_id']!=uid or j['status'] not in ('completed','failed','cancelled'):return await q.message.reply_text('다운로드 권한 또는 완료 내역이 없습니다.')
            content,_=export(jid,fmt,days)
            return await q.message.reply_document(InputFile(io.BytesIO(content),filename=f'check_{jid[:8]}_{days}.{fmt}'))
    except Exception as e:await q.message.reply_text(str(e))

async def topup_amount_message(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_topup_amount'):return
    context.user_data.pop('awaiting_topup_amount',None)
    uid=update.effective_user.id
    try:
        rate=sheet_rate.available_rate()
        invoice=deposits.create_topup_request(uid,update.effective_message.text.strip().replace(',',''),rate)
    except ValueError as exc:
        return await update.message.reply_text(f'⚠️ {exc}\n충전 메뉴에서 다시 신청해 주세요.')
    await update.message.reply_text(
        '<b>✅ USDT 충전 신청서</b>\n━━━━━━━━━━━━━━━━━━━━\n'
        f'💰 충전 금액: <b>{db.money(invoice["requested_tenths"])}</b>\n'
        f'📈 적용 환율: {invoice["rate"]:,}원/USDT\n'
        f'🪙 보내야 할 수량: <b>{invoice["usdt_milli"]/1000:.3f} USDT</b>\n\n'
        '📥 <b>USDT TRC-20 전용 입금주소</b>\n'
        f'<code>{html.escape(invoice["address"])}</code>\n\n'
        f'🧾 신청번호: <code>{invoice["id"][:12]}</code>\n'
        '⏱ 유효시간: 신청 후 30분\n'
        '⚠️ 수량이 다르거나 늦게 들어온 송금은 자동 충전되지 않고 관리자 확인이 필요합니다.\n'
        '⛔ TRX 및 타 네트워크 입금 금지\n'
        '✅ 블록체인 확정 후 자동 적립됩니다.',parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💬 입금 문의',url='https://t.me/teleck777')]]))

def build():
    app=Application.builder().token(config.TOKEN).build()
    app.add_handler(CommandHandler('start',start));app.add_handler(CommandHandler('menu',start))
    app.add_handler(MessageHandler(filters.Document.ALL,doc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,topup_amount_message))
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
