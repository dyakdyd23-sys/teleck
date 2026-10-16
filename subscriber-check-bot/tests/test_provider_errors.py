import httpx
from app.worker import provider_error_summary

def test_status_safe_logging():
    req = httpx.Request('POST', 'https://secret.example/api/tg/addTask?token=private', headers={'token': 'private'})
    resp = httpx.Response(422, request=req, json={'phone_numbers':['01012345678'], 'secret':'private'})
    err = httpx.HTTPStatusError('secret', request=req, response=resp)
    summary = provider_error_summary(err)
    assert summary == 'http_status=422 operation=addTask'
    assert 'private' not in summary and '01012345678' not in summary

def test_network_error_safe_logging():
    req = httpx.Request('GET', 'https://secret.example/api/tg/checkTask?token=private')
    assert provider_error_summary(httpx.ConnectError('secret', request=req)) == 'network_request_error'


def test_provider_rejection_code_visible_without_secrets():
    assert provider_error_summary(ValueError('제공업체 작업 생성 거절: code=2')) == 'provider_rejected code=2'
    assert provider_error_summary(ValueError('제공업체 작업 생성 거절: code=some-secret')) == 'provider_rejected invalid_code'
    assert provider_error_summary(ValueError('외부 API 작업 ID 누락')) == 'provider_missing_task_id'
    assert provider_error_summary(ValueError('API 생성 응답 형식 오류')) == 'provider_create_invalid_response'
    assert provider_error_summary(ValueError('secret 01012345678')) == 'validation_error'
