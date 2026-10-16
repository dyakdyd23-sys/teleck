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
