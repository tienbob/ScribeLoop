"""Cloud startup must not require local replay assets or open any devices."""
import sys

import pytest
import uvicorn
from scribeloop_core.api import server


def test_encrypted_cloud_cli_starts_without_public_fixture(tmp_path,monkeypatch,capsys):
    configured={}
    launched={}
    sentinel=object()
    def create_app(**kwargs):
        configured.update(kwargs)
        return sentinel,'synthetic-launch-token'
    def run(app,**kwargs):
        assert app is sentinel
        launched.update(kwargs)
    database=tmp_path/'cloud'/'meeting.sqlcipher'
    monkeypatch.setattr(sys,'argv',['scribeloop','--database',str(database),'--no-discovery'])
    monkeypatch.setattr(server,'create_app',create_app)
    monkeypatch.setattr(uvicorn,'run',run)
    server.main()
    assert configured['storage']=='encrypted' and configured['fixtures']=={}
    assert configured['require_launch_key'] is False
    assert configured['database']==database
    assert launched['host']=='127.0.0.1' and launched['port']==8757
    output=capsys.readouterr().err
    assert 'http://127.0.0.1:8757/' in output and '#token=' not in output
    assert not database.exists(), 'CLI test must not create/open a real database or native source'


def test_public_replay_still_requires_explicit_fixture(tmp_path,monkeypatch):
    database=tmp_path/'not-created'/'demo.sqlite3'
    monkeypatch.setattr(sys,'argv',['scribeloop','--storage','public-demo','--database',str(database)])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code==2 and not database.parent.exists()


def test_launch_key_is_optional_cli_override(tmp_path,monkeypatch,capsys):
    configured={}
    def app(**kwargs):
        configured.update(kwargs)
        return object(),'test-token'
    monkeypatch.setattr(sys,'argv',['scribeloop','--database',str(tmp_path/'test.sqlcipher'),'--require-launch-key'])
    monkeypatch.setattr(server,'create_app',app)
    monkeypatch.setattr(uvicorn,'run',lambda *a,**kw:None)
    server.main()
    assert configured['require_launch_key'] is True
    assert '#token=test-token' in capsys.readouterr().err
