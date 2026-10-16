from datetime import datetime,timezone,timedelta
from app import db,config,service

def test_stalled_cancel_stops_new_work_and_audits(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DB',tmp_path/'data.sqlite3')
    monkeypatch.setattr(config,'STORAGE',tmp_path/'uploads')
    db.init()
    past=(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()
    with db.transaction() as c:
        db.ensure_user(c,10001)
        c.execute("INSERT INTO jobs(id,user_id,status,original_count,invalid_count,duplicate_count,valid_count,unit_tenths,charged_tenths,source_file,created_at,updated_at) VALUES('job1',10001,'processing',5,0,0,5,6,30,'x',?,?)",(past,past))
        c.execute("INSERT INTO job_control(job_id,last_progress_at) VALUES('job1',?)",(past,))
    assert '취소 요청' in service.cancel_stalled(10001,'job1')
    assert '이미 취소' in service.cancel_stalled(10001,'job1')
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM admin_events WHERE kind='cancel_requested'").fetchone()[0]==1
        assert c.execute("SELECT cancel_requested_at FROM job_control WHERE job_id='job1'").fetchone()[0]

def test_cancel_requires_five_minutes(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DB',tmp_path/'data.sqlite3')
    monkeypatch.setattr(config,'STORAGE',tmp_path/'uploads')
    db.init()
    with db.transaction() as c:
        db.ensure_user(c,10001)
        now=db.now()
        c.execute("INSERT INTO jobs(id,user_id,status,original_count,invalid_count,duplicate_count,valid_count,unit_tenths,charged_tenths,source_file,created_at,updated_at) VALUES('job2',10001,'processing',5,0,0,5,6,30,'x',?,?)",(now,now))
    try:service.cancel_stalled(10001,'job2')
    except ValueError as e:assert '5분' in str(e)
    else:assert False
