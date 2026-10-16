import json, uuid, os
from pathlib import Path
from . import config, db
from .phones import parse_file

def preview(user_id, filename, content):
    parsed=parse_file(filename,content)
    pid=uuid.uuid4().hex
    suffix=filename.lower().rsplit('.',1)[-1]
    file=config.STORAGE/f'{pid}.{suffix}'
    file.write_bytes(content)
    with db.transaction() as c:
        db.ensure_user(c,user_id)
        c.execute('INSERT INTO previews VALUES(?,?,?,?)',(pid,user_id,str(file),db.now()))
        settings={k:db.setting(c,k) for k in ('unit_tenths','min_count','max_count')}
        balance=c.execute('SELECT balance_tenths FROM users WHERE user_id=?',(user_id,)).fetchone()[0]
    return {**{k:v for k,v in parsed.items() if k!='phones'},'preview_id':pid,'cost_tenths':parsed['valid']*settings['unit_tenths'],'balance_tenths':balance,**settings}

def submit(user_id, preview_id):
    with db.transaction() as c:
        prev=c.execute('SELECT * FROM previews WHERE id=? AND user_id=?',(preview_id,user_id)).fetchone()
        if not prev:raise ValueError('존재하지 않거나 이미 신청된 파일입니다.')
        path=Path(prev['source_file'])
        if not path.exists():raise ValueError('업로드 파일을 찾을 수 없습니다.')
        parsed=parse_file(path.name,path.read_bytes())
        n=parsed['valid'];mini=db.setting(c,'min_count');maxi=db.setting(c,'max_count')
        if not (mini<=n<=maxi):raise ValueError(f'신청 범위는 {mini:,}~{maxi:,}건입니다.')
        rate=db.setting(c,'unit_tenths');amount=n*rate
        if not config.API_URL or not config.API_KEY or not provider_ready():
            raise ValueError('실제 검수 API 연동이 아직 설정되지 않았습니다. 신청과 결제를 차단했습니다.')
        updated=c.execute('UPDATE users SET balance_tenths=balance_tenths-? WHERE user_id=? AND balance_tenths>=?',(amount,user_id,amount)).rowcount
        if updated!=1:raise ValueError('잔액이 부족합니다.')
        jid=uuid.uuid4().hex
        c.execute('INSERT INTO jobs(id,user_id,status,original_count,invalid_count,duplicate_count,valid_count,unit_tenths,charged_tenths,source_file,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (jid,user_id,'pending',parsed['total'],parsed['invalid'],parsed['duplicates'],n,rate,amount,str(path),db.now(),db.now()))
        c.executemany('INSERT INTO phones(job_id,phone) VALUES(?,?)',((jid,p) for p in parsed['phones']))
        c.execute('INSERT INTO ledger(user_id,job_id,kind,amount_tenths,reason,actor,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?)',
                  (user_id,jid,'charge',-amount,'검수 신청','system',f'charge:{jid}',db.now()))
        c.execute('DELETE FROM previews WHERE id=?',(preview_id,))
        c.execute('INSERT INTO job_control(job_id,last_progress_at) VALUES(?,?)',(jid,db.now()))
        db.admin_event(c,'job_submitted',user_id,jid,f'🔍 검수 신청\n회원: {user_id}\n작업: {jid[:12]}\n수량: {n:,}건\n차감: {db.money(amount)}')
        db.audit(c,str(user_id),'submit',jid,f'count={n}')
        return jid

def provider_ready():
    from .provider import Contract
    try:Contract.load();return True
    except Exception:return False

def credit(user_id,amount_tenths,actor,reference):
    if amount_tenths<=0:raise ValueError('충전 금액은 0보다 커야 합니다.')
    if not reference.strip():raise ValueError('중복 방지 참조값이 필요합니다.')
    with db.transaction() as c:
        db.ensure_user(c,user_id)
        c.execute('INSERT INTO ledger(user_id,kind,amount_tenths,reason,actor,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?)',
                  (user_id,'credit',amount_tenths,'관리자 잔액 조정',actor,'credit:'+reference,db.now()))
        c.execute('UPDATE users SET balance_tenths=balance_tenths+? WHERE user_id=?',(amount_tenths,user_id))
        db.audit(c,actor,'credit',detail=f'user={user_id},amount_tenths={amount_tenths},reference={reference}')

def refund_for_unresolved(c,jid,actor='system'):
    """Refund only proven unprocessed input numbers, not missing/matched result rows.

    `reconcile`, `creating`, and `processing` batches are ambiguous: never refund
    them before external confirmation. `completed` batches are billable regardless
    of how many joined accounts were returned in the export.
    """
    j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    if not j:raise ValueError('작업이 없습니다.')
    # A successful provider batch counts as processed even if export lists only
    # registered users. This count also handles existing historical pending rows.
    refundable=c.execute("""SELECT COUNT(*) FROM phones p WHERE p.job_id=? AND p.state!='done'
      AND NOT EXISTS (
       SELECT 1 FROM batch_phones bp JOIN batches b ON b.id=bp.batch_id
       WHERE b.job_id=p.job_id AND bp.phone=p.phone
       AND b.state IN ('completed','creating','processing','reconcile','pending')
      )""",(jid,)).fetchone()[0]
    amount=min(refundable*j['unit_tenths'],j['charged_tenths']-j['refunded_tenths'])
    if amount>0:
        key=f'refund:unprocessed:{jid}'
        inserted=c.execute('INSERT OR IGNORE INTO ledger(user_id,job_id,kind,amount_tenths,reason,actor,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (j['user_id'],jid,'refund',amount,'검수 미접수·실패 확정 건 환불',actor,key,db.now())).rowcount
        if inserted:
            c.execute('UPDATE users SET balance_tenths=balance_tenths+? WHERE user_id=?',(amount,j['user_id']))
            c.execute('UPDATE jobs SET refunded_tenths=refunded_tenths+? WHERE id=?',(amount,jid))
            db.audit(c,actor,'refund',jid,f'count={refundable},amount_tenths={amount}')
    return amount

def list_jobs(user_id=None):
    with db.connect() as c:
        if user_id is None:return [dict(x) for x in c.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100')]
        return [dict(x) for x in c.execute('SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 50',(user_id,))]


def cancel_stalled(user_id,job_id):
    """Stop new batches, but do not refund while provider batches are unresolved."""
    from datetime import datetime,timezone
    with db.transaction() as c:
        j=c.execute('SELECT * FROM jobs WHERE id=? AND user_id=?',(job_id,user_id)).fetchone()
        if not j:raise ValueError('해당 작업을 찾을 수 없습니다.')
        if j['status'] not in ('pending','processing'):raise ValueError('이미 종료된 작업입니다.')
        x=c.execute('SELECT * FROM job_control WHERE job_id=?',(job_id,)).fetchone()
        last=(x['last_progress_at'] if x else j['created_at'])
        stalled=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()))
        if stalled<300:raise ValueError(f'5분 이상 진행이 없을 때 취소할 수 있습니다. {300-stalled}초 남았습니다.')
        if x and x['cancel_requested_at']:return '이미 취소 요청되었습니다. 외부 작업 확인 후 정산됩니다.'
        if not x:c.execute('INSERT INTO job_control(job_id,last_progress_at) VALUES(?,?)',(job_id,last))
        c.execute('UPDATE job_control SET cancel_requested_at=? WHERE job_id=?',(db.now(),job_id))
        db.audit(c,str(user_id),'cancel_requested',job_id,'stalled_seconds='+str(stalled))
        db.admin_event(c,'cancel_requested',user_id,job_id,f'⏸ 검수 취소 요청\n회원: {user_id}\n작업: {job_id[:12]}\n무진전: {stalled}초')
        return '취소 요청을 접수했습니다. 아직 접수하지 않은 건만 정산 대상이며, 이미 접수된 검수는 처리 여부를 확인합니다.'
