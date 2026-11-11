from pathlib import Path
import sys
import json
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge_agent.workstation import WorkstationStore
from edge_agent.ur_workflow import DEFAULTS


def profile():
    return dict(name='UR5e 工位一', robot_ip='192.168.1.10', gripper='二指夹爪',
                camera_0='wrist', camera_1='side1', camera_2='side2', port='',address=1,baud=921600,
                **{k:DEFAULTS[k] for k in ('capture_script','project','conda_env','output_root')})


def test_saved_profile_survives_new_service_instance(tmp_path):
    store=WorkstationStore(tmp_path)
    assert not store.read()['configured']
    result=store.save(profile(), None)
    restored=WorkstationStore(tmp_path).read()
    assert restored==result
    assert restored['profile']['camera_0']=='wrist'
    assert 'connected' not in restored['profile']


def test_stale_revision_cannot_replace_new_profile(tmp_path):
    store=WorkstationStore(tmp_path)
    old=store.save(profile(),None)['profile']
    updated=store.save(dict(profile(),name='工位二'),old['revision'])
    with pytest.raises(ValueError, match='已更新'):
        store.save(profile(),old['revision'])
    assert store.read()==updated


@pytest.mark.parametrize('change', [dict(camera_2='side1'), dict(address=0), dict(baud=True),
                                   dict(name=''), dict(project='relative'),dict(conda_env='a;exit')])
def test_invalid_settings_preserve_saved_profile(tmp_path,change):
    store=WorkstationStore(tmp_path)
    saved=store.save(profile(),None)
    with pytest.raises(ValueError):
        store.save(dict(profile(),**change),saved['profile']['revision'])
    assert store.read()==saved


def test_http_profile_save_read_and_running_capture_guard(tmp_path):
    from edge_agent.core import EdgeCore
    from edge_agent.server import handler_for
    root=Path(__file__).resolve().parents[1]
    core=EdgeCore(root/'robotics',tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(core,root/'dist'))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}/api/workstation'
    def save(expected=None):
        request=urllib.request.Request(url,data=json.dumps(dict(profile=profile(),expected_revision=expected)).encode(),headers={'Content-Type':'application/json'})
        return urllib.request.urlopen(request)
    try:
        with save() as response:
            saved=json.load(response)
        with urllib.request.urlopen(url) as response:
            assert json.load(response)==saved
        core.ur_workflow.capture_process=SimpleNamespace(poll=lambda:None)
        with pytest.raises(urllib.error.HTTPError) as failure:
            save(saved['profile']['revision'])
        assert failure.value.code==400
        assert core.workstation.read()==saved
    finally:
        server.shutdown()
        server.server_close()
