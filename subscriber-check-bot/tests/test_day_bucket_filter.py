from app.export import active_bucket,is_registered,include_result


def test_provider_day_bucket():
    assert active_bucket('1일 이내')==1
    assert active_bucket('3일 이내')==3
    assert active_bucket('7일 이내')==7
    assert active_bucket('최근 1일 이내')==1
    assert active_bucket('2026-10-15') is None
    assert active_bucket('30일 이내') is None
    assert active_bucket('어제') is None


def test_registered_only_and_nested_buckets():
    for day,expected in [('1', [1]),('3',[1,3]),('7',[1,3,7]),('all',[1,3,7])]:
        actual=[x for x in [1,3,7] if include_result('true',f'{x}일 이내',day)]
        assert actual==expected
    assert include_result('false','1일 이내','all') is False
    assert include_result(None,'1일 이내','all') is False
    assert include_result('true',None,'3') is False
    assert include_result('true',None,'all') is True
    assert include_result('false','1일 이내','raw') is True
    assert not include_result('확인불가','1일 이내','1')
