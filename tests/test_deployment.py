import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient
from backend import main
from backend.database.repository import Repository
from backend.services.demo_access import COOKIE, session_signature


@pytest.fixture
def gated(tmp_path, monkeypatch):
    monkeypatch.setenv('MONITOR_ENABLED', 'false')
    monkeypatch.setenv('DEMO_ACCESS_CODE', 'test-only-demo-code')
    monkeypatch.setenv('SIMULATION_TOKEN', 'test-only-simulation-token')
    monkeypatch.setattr(main, 'repo', Repository(str(tmp_path/'gate.sqlite3')))
    async def forbidden(*args, **kwargs):
        pytest.fail('Access/health checks must not call the agent')
    monkeypatch.setattr(main.engine, 'handle', forbidden)
    with TestClient(main.app, base_url='https://demo.example') as client:
        yield client


def login(client):
    return client.post('/demo/access', data={'access_code':'test-only-demo-code'}, follow_redirects=False)


def test_gate_covers_direct_api_and_health_never_calls_agent(gated):
    assert 'Private competition prototype' in gated.get('/').text
    assert gated.post('/api/episodes', json={}).status_code == 401
    assert gated.post('/api/episodes/unknown/continue').status_code == 401
    assert gated.get('/api/simulation/config', headers={'X-Simulation-Token':'test-only-simulation-token'}).status_code == 401
    assert gated.get('/health').json() == {'status':'ok'}
    assert gated.get('/api/health').json() == {'status':'ok'}
    assert main.repo.all() == []


def test_session_cookie_is_signed_private_and_simulation_gate_is_separate(gated):
    wrong=gated.post('/demo/access', data={'access_code':'wrong'}, follow_redirects=False)
    assert wrong.status_code == 401 and 'test-only-demo-code' not in wrong.text
    result=login(gated)
    assert result.status_code == 303
    cookie=result.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'secure' in cookie and 'samesite=strict' in cookie
    assert 'max-age' not in cookie and 'test-only-demo-code' not in cookie
    assert gated.get('/api/episodes').json() == []
    assert gated.get('/api/simulation/config').status_code == 403
    assert gated.get('/api/simulation/config', headers={'X-Simulation-Token':'test-only-simulation-token'}).status_code == 200
    assert gated.get('/api/episodes').headers['cache-control'] == 'no-store'


def test_forged_expired_and_rotated_sessions_are_denied(gated, monkeypatch):
    gated.cookies.set(COOKIE, 'forged')
    assert gated.get('/api/episodes').status_code == 401
    nonce='x'*43
    message=str(int(time.time())-90000)+'.'+nonce
    gated.cookies.set(COOKIE, message+'.'+session_signature('test-only-demo-code', message))
    assert gated.get('/api/episodes').status_code == 401
    gated.cookies.clear()
    login(gated)
    monkeypatch.setenv('DEMO_ACCESS_CODE','rotated-test-only-code')
    assert gated.get('/api/episodes').status_code == 401


def test_login_rejects_cross_origin_and_limits_guesses(gated):
    assert gated.post('/demo/access', data={'access_code':'test-only-demo-code'}, headers={'Origin':'https://other.example'}).status_code == 403
    assert gated.post('/demo/access', json={'access_code':'test-only-demo-code'}).status_code == 400
    for _ in range(9):
        assert gated.post('/demo/access', data={'access_code':'wrong'}).status_code == 401
    assert gated.post('/demo/access', data={'access_code':'wrong'}).status_code == 429


def test_gate_unconfigured_preserves_local_api(gated, monkeypatch):
    monkeypatch.delenv('DEMO_ACCESS_CODE')
    assert gated.get('/api/episodes').status_code == 200
    assert gated.get('/api/simulation/config').status_code == 403


def test_shared_data_directory_survives_process_restart(tmp_path):
    env={**os.environ, 'DATA_DIR':str(tmp_path), 'DATABASE_PATH':'', 'MONITOR_ENABLED':'false', 'DEMO_ACCESS_CODE':''}
    first="""
from backend import main
from backend.models.domain import Episode, Component
from backend.models.inputs import FileInput
assert main.repo.path == str(main.DATA_DIR/'owedience.sqlite3')
assert main.attachment_store.root == main.DATA_DIR/'attachments'
e=Episode(title='Persistence fixture',payer='A',participants=['A','B'],total=100,components=[Component(description='Shared item',amount=100)])
metadata=main.attachment_store.save(FileInput(filename='receipt.txt',mime_type='text/plain',data_base64='dGVzdA=='))
e.attachments.append(metadata);main.repo.save(e)
"""
    second="""
from backend import main
rows=main.repo.all();assert len(rows)==1 and rows[0].title=='Persistence fixture'
assert main.attachment_store.path(rows[0].attachments[0]['id']).read_bytes()==b'test'
"""
    for code in (first,second):
        result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True)
        assert result.returncode == 0, result.stderr
