"""Bill validated submitted numbers, not matches in provider export."""
import asyncio
from app import db, config, service, worker


def setup_job(tmp_path,monkeypatch,number=10):
    monkeypatch.setattr(config,'DB',tmp_path/'billing.sqlite3')
    monkeypatch.setattr(config,'STORAGE',tmp_path/'uploads')
    db.init()
    with db.transaction() as c:
        db.ensure_user(c,1234)
        c.execute('UPDATE users SET balance_tenths=10000 WHERE user_id=1234')
        now=db.now()
        c.execute('''INSERT INTO jobs(id,user_id,status,original_count,invalid_count,duplicate_count,valid_count,unit_tenths,charged_tenths,source_file,created_at,updated_at)
                     VALUES('j',1234,'processing',?,?,?,?,?,?,'file',?,?)''',(0,0,number,number,6,number*6,now,now))
        c.executemany("INSERT INTO phones(job_id,phone) VALUES('j',?)",[(f'010{n:08d}',) for n in range(number)])
        c.execute("INSERT INTO job_control(job_id,last_progress_at) VALUES('j',?)",(now,))
        c.execute("INSERT INTO batches(id,job_id,state,api_task_id,updated_at) VALUES('b','j','processing','ext',?)",(now,))
        c.executemany("INSERT INTO batch_phones(batch_id,phone) VALUES('b',?)",[(f'010{n:08d}',) for n in range(number)])


def test_completed_provider_with_fewer_matches_does_not_refund(tmp_path,monkeypatch):
    setup_job(tmp_path,monkeypatch)
    class FakeProvider:
        d={'max_batch_size':50000,'requests_per_minute':10}
        async def status(self,task_id):return 'completed'
        async def results(self,task_id):
            return [{'phone':f'010{n:08d}','joined':'true','uid':str(n),'username':None,'last_active':'1일 이내'} for n in range(8)]
    monkeypatch.setattr(worker.Contract,'load',classmethod(lambda cls:FakeProvider()))
    asyncio.run(worker.cycle())
    with db.connect() as c:
        j=c.execute("SELECT * FROM jobs WHERE id='j'").fetchone()
        assert j['status']=='completed'
        assert j['processed_count']==10
        assert j['success_count']==10
        assert j['refunded_tenths']==0
        assert c.execute("SELECT COUNT(*) FROM phones WHERE state='done'").fetchone()[0]==10
        assert c.execute("SELECT COUNT(*) FROM phones WHERE joined='true'").fetchone()[0]==8
        assert c.execute("SELECT COUNT(*) FROM ledger WHERE kind='refund'").fetchone()[0]==0


def test_ambiguous_batch_not_refunded(tmp_path,monkeypatch):
    setup_job(tmp_path,monkeypatch)
    with db.transaction() as c:
        c.execute("UPDATE batches SET state='reconcile' WHERE id='b'")
        assert service.refund_for_unresolved(c,'j')==0


def test_cancelled_before_submission_refunds_only_unsubmitted(tmp_path,monkeypatch):
    setup_job(tmp_path,monkeypatch)
    with db.transaction() as c:
        c.execute("DELETE FROM batch_phones WHERE phone IN (SELECT phone FROM phones ORDER BY phone DESC LIMIT 2)")
        c.execute("UPDATE batches SET state='completed' WHERE id='b'")
        assert service.refund_for_unresolved(c,'j')==12
        assert service.refund_for_unresolved(c,'j')==12
    with db.connect() as c:
        assert c.execute("SELECT refunded_tenths FROM jobs WHERE id='j'").fetchone()[0]==12
