import asyncio, uuid, logging, hashlib, httpx
from datetime import datetime,timezone,timedelta
from . import db,config
from .provider import Contract
from .phones import normalize
log=logging.getLogger(__name__)

def provider_error_summary(exc):
    """Safe diagnostic metadata; never log credentials, URLs with query, phone lists or response bodies."""
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        path = response.request.url.path if response.request else 'unknown'
        # Path should contain only known API operations, not arbitrary or private URL parts.
        operation = next((x for x in ('addTask', 'checkTask', 'exportPhone') if path.endswith('/'+x)), 'other')
        return f'http_status={response.status_code} operation={operation}'
    if isinstance(exc, httpx.TimeoutException):
        return 'network_timeout'
    if isinstance(exc, httpx.RequestError):
        return 'network_request_error'
    return f'error_type={type(exc).__name__}'

async def cycle():
    try: provider=Contract.load()
    except Exception:return
    # At most one creation and one poll per tick, with conservative rate limiting.
    with db.transaction() as c:
        row=c.execute("SELECT id FROM jobs WHERE status IN ('pending','processing') AND NOT EXISTS (SELECT 1 FROM job_control jc WHERE jc.job_id=jobs.id AND jc.cancel_requested_at IS NOT NULL) AND EXISTS (SELECT 1 FROM phones p WHERE p.job_id=jobs.id AND p.state='pending' AND NOT EXISTS(SELECT 1 FROM batch_phones bp JOIN batches b ON bp.batch_id=b.id WHERE b.job_id=p.job_id AND bp.phone=p.phone)) ORDER BY created_at LIMIT 1").fetchone()
        if row:
            jid=row['id']
            limit=provider.d['max_batch_size']
            phones=[x['phone'] for x in c.execute('''SELECT p.phone FROM phones p WHERE p.job_id=? AND p.state='pending'
                AND NOT EXISTS(SELECT 1 FROM batch_phones bp JOIN batches b ON bp.batch_id=b.id
                WHERE b.job_id=p.job_id AND bp.phone=p.phone) LIMIT ?''',(jid,limit))]
            if phones:
                bid=uuid.uuid4().hex
                c.execute("INSERT INTO batches(id,job_id,state,updated_at) VALUES(?,?,'pending',?)",(bid,jid,db.now()))
                c.executemany('INSERT INTO batch_phones VALUES(?,?)',((bid,p) for p in phones))
                c.execute("UPDATE jobs SET status='processing',updated_at=? WHERE id=?",(db.now(),jid))
            else:
                c.execute("UPDATE jobs SET status='processing' WHERE id=?",(jid,))
    # Reserve one pending batch. Leases protect restart recovery; uncertain create must not auto-repeat.
    with db.transaction() as c:
        b=c.execute("SELECT * FROM batches WHERE state='pending' ORDER BY updated_at LIMIT 1").fetchone()
        if b:
            c.execute("UPDATE batches SET state='creating',attempts=attempts+1,updated_at=? WHERE id=?",(db.now(),b['id']))
            phones=[x[0] for x in c.execute('SELECT phone FROM batch_phones WHERE batch_id=?',(b['id'],))]
        else: phones=[]
    if b:
        try:
            task=await provider.create(phones)
            with db.transaction() as c:
                c.execute("UPDATE batches SET state='processing',api_task_id=?,updated_at=? WHERE id=?",(task,db.now(),b['id']))
        except Exception as e:
            # If create response was lost, retry could charge provider twice: require admin reconciliation.
            with db.transaction() as c:
                c.execute("UPDATE batches SET state='reconcile',error=?,updated_at=? WHERE id=?",(provider_error_summary(e),db.now(),b['id']))
                db.audit(c,'worker','provider_create_uncertain',b['job_id'],f'batch={b["id"]}')
            log.warning('Provider create uncertain for batch %s: %s',b['id'],provider_error_summary(e))
    with db.transaction() as c:
        b=c.execute("SELECT * FROM batches WHERE state='processing' ORDER BY updated_at LIMIT 1").fetchone()
        if b:c.execute('UPDATE batches SET updated_at=? WHERE id=?',(db.now(),b['id']))
    if b:
        try:
            state=await provider.status(b['api_task_id'])
            if state in ('completed','failed','cancelled'):
                data=await provider.results(b['api_task_id']) if state=='completed' else []
                with db.transaction() as c:
                    accepted={x[0] for x in c.execute('SELECT phone FROM batch_phones WHERE batch_id=?',(b['id'],))}
                    seen=set()
                    for r in data:
                        phone=normalize(r['phone'])
                        if not phone or phone not in accepted or phone in seen:continue
                        seen.add(phone)
                        c.execute("UPDATE phones SET state='done',joined=?,uid=?,username=?,last_active=? WHERE job_id=? AND phone=? AND state='pending'",
                                  (r['joined'],r['uid'],r['username'],r['last_active'],b['job_id'],phone))
                    c.execute('UPDATE batches SET state=?,error=NULL,updated_at=? WHERE id=?',(state,db.now(),b['id']))
            elif state=='processing' or state=='pending':pass
            else:raise ValueError('unknown state')
        except Exception as e:
            with db.transaction() as c:
                c.execute('UPDATE batches SET error=? WHERE id=?',(provider_error_summary(e),b['id']))
            log.warning('Provider poll failed for batch %s: %s',b['id'],provider_error_summary(e))
    with db.transaction() as c:
        for j in c.execute("SELECT id FROM jobs WHERE status IN ('pending','processing')").fetchall():
            jid=j['id']
            remaining=c.execute("SELECT COUNT(*) FROM phones p WHERE job_id=? AND state='pending' AND NOT EXISTS(SELECT 1 FROM batch_phones bp WHERE bp.phone=p.phone AND bp.batch_id IN(SELECT id FROM batches WHERE job_id=?))",(jid,jid)).fetchone()[0]
            live=c.execute("SELECT COUNT(*) FROM batches WHERE job_id=? AND state IN('pending','creating','processing','reconcile')",(jid,)).fetchone()[0]
            done=c.execute("SELECT COUNT(*) FROM phones WHERE job_id=? AND state='done'",(jid,)).fetchone()[0]
            all_count=c.execute('SELECT valid_count FROM jobs WHERE id=?',(jid,)).fetchone()[0]
            progress=c.execute('SELECT last_processed FROM job_control WHERE job_id=?',(jid,)).fetchone()
            if progress is None:
                c.execute('INSERT OR IGNORE INTO job_control(job_id,last_progress_at) VALUES(?,?)',(jid,db.now()))
                progress=c.execute('SELECT last_processed FROM job_control WHERE job_id=?',(jid,)).fetchone()
            if done>progress['last_processed']:
                c.execute('UPDATE job_control SET last_processed=?,last_progress_at=? WHERE job_id=?',(done,db.now(),jid))
            cancelled=c.execute('SELECT cancel_requested_at FROM job_control WHERE job_id=?',(jid,)).fetchone()
            should_cancel=bool(cancelled and cancelled['cancel_requested_at'])
            if should_cancel:
                c.execute("UPDATE batches SET state='cancelled',updated_at=? WHERE job_id=? AND state='pending'",(db.now(),jid))
                live=c.execute("SELECT COUNT(*) FROM batches WHERE job_id=? AND state IN('creating','processing','reconcile')",(jid,)).fetchone()[0]
                if not live:remaining=0
            c.execute('UPDATE jobs SET processed_count=?,success_count=?,failed_count=?,updated_at=? WHERE id=?',
                      (all_count-remaining if not live else done,done,max(0,all_count-done) if not live and not remaining else 0,db.now(),jid))
            if not live and not remaining:
                failed=all_count-done
                c.execute('UPDATE jobs SET status=?,updated_at=? WHERE id=?',('cancelled' if should_cancel else ('completed' if done else 'failed'),db.now(),jid))
                db_refund=db_refund_import()
                db_refund(c,jid)
                # Stable canonical digest of the exact stored result rows, independent of export format.
                lines=c.execute('SELECT phone,state,joined,uid,username,last_active FROM phones WHERE job_id=? ORDER BY phone',(jid,)).fetchall()
                digest=hashlib.sha256(('\n'.join('\t'.join('' if val is None else str(val) for val in row) for row in lines)).encode('utf-8')).hexdigest()
                c.execute('INSERT OR IGNORE INTO result_archives(job_id,sha256,created_at) VALUES(?,?,?)',(jid,digest,db.now()))
                db.audit(c,'worker','results_archived',jid,digest)
                job=c.execute('SELECT user_id,status,valid_count,success_count,refunded_tenths FROM jobs WHERE id=?',(jid,)).fetchone()
                db.admin_event(c,'job_finished',job['user_id'],jid,f'✅ 검수 종료\n회원: {job["user_id"]}\n작업: {jid[:12]}\n상태: {job["status"]}\n완료: {job["success_count"]:,}/{job["valid_count"]:,}\n환불: {db.money(job["refunded_tenths"])}')

def db_refund_import():
    from .service import refund_for_unresolved
    return refund_for_unresolved

async def run(stop):
    with db.transaction() as c:
        # Creating requests are ambiguous after crash; never blindly replay.
        c.execute("UPDATE batches SET state='reconcile',error='서버 중단 중 요청 결과 불명: 관리자 확인 필요' WHERE state='creating'")
    while not stop.is_set():
        try:await cycle()
        except Exception:log.exception('Worker error')
        try:await asyncio.wait_for(stop.wait(),timeout=max(config.POLL_SECONDS,60 // max(1,_rate())))
        except asyncio.TimeoutError:pass

def _rate():
    try:return Contract.load().d['requests_per_minute']//2 or 1
    except Exception:return 1
