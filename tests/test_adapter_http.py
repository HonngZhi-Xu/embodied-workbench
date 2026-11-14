"""Platform API contract tests; no acquisition program or device is started."""
import json
from pathlib import Path
import sys
import threading
from http.server import ThreadingHTTPServer
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge_agent.core import EdgeCore
from edge_agent.server import handler_for

@pytest.fixture
def api(tmp_path):
    core = EdgeCore(ROOT/'robotics', tmp_path)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(core, ROOT/'dist'))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    def call(route, payload=None):
        req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/{route}',
            data=None if payload is None else json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.load(response)
    yield core, call
    server.shutdown(); server.server_close()

def test_capture_api_preserves_mode_resume_confirmation_and_request_id(api):
    core, call = api
    core.ur_workflow.capture_plan = Mock(return_value={'argv': ['bash', '/tmp/script']})
    core.ur_workflow.start_capture = Mock(return_value={'id': 's1'})
    call('ur-workflow/capture/preview', {'script': '/tmp/script', 'capture_mode': 'rgb_only', 'resume_path': '/tmp/run.zarr'})
    core.ur_workflow.capture_plan.assert_called_once_with('/tmp/script', 'rgb_only', '/tmp/run.zarr')
    call('ur-workflow/capture/start', {'script': '/tmp/script', 'capture_mode': 'rgb_tactile',
         'request_id': 'start-once', 'confirmed_external_idle': True})
    core.ur_workflow.start_capture.assert_called_once_with('/tmp/script', 'rgb_tactile', None,
        request_id='start-once', confirmed_external_idle=True)
    core.ur_workflow.send_key = Mock(return_value={'id': 's1'})
    call('ur-workflow/capture/key', {'action': 'delete_last', 'session_id': 's1', 'confirmed': True, 'request_id': 'delete-once'})
    core.ur_workflow.send_key.assert_called_once_with('delete_last', 's1', True, 'delete-once')
    core.ur_workflow.stop_capture = Mock(return_value={})
    call('ur-workflow/capture/stop', {'session_id': 's1', 'method': 'interrupt', 'confirmed': True, 'request_id': 'interrupt-once'})
    core.ur_workflow.stop_capture.assert_called_once_with('s1', method='interrupt', confirmed=True, request_id='interrupt-once')

def test_no_duplicate_device_open_during_script_capture(api):
    core, call = api
    core.ur_workflow.capture = {'id': 's1', 'running': True}
    core.hub.connect = Mock()
    for device in ('rgb', 'tactile'):
        with pytest.raises(urllib.error.HTTPError) as error:
            call('devices/connect', {'device': device, 'config': {}})
        assert error.value.code == 400
    core.hub.connect.assert_not_called()

def test_script_start_rejected_when_platform_sensor_diagnostic_owns_device(api):
    core, call = api
    core.hub.workers['rgb'] = object()
    core.ur_workflow.start_capture = Mock()
    with pytest.raises(urllib.error.HTTPError):
        call('ur-workflow/capture/start', {'script': '/tmp/script', 'confirmed_external_idle': True})
    core.ur_workflow.start_capture.assert_not_called()

def test_external_asset_routes_and_bounded_replay_arguments(api):
    core, call = api
    core.assets.register = Mock(return_value={'id': 'asset-1', 'path': '/data/a.zarr'})
    core.assets.scan = Mock(return_value=[])
    core.assets.frame = Mock(return_value={'image': 'data:image/jpeg;base64,AA=='})
    core.assets.replay = Mock(return_value={'frames': []})
    core.audit = Mock(return_value={'valid': True})
    assert call('datasets/register', {'path': '/data/a.zarr'})['id'] == 'asset-1'
    assert call('datasets/scan', {}) == []
    assert call('datasets/audit', {'dataset': 'asset-1'})['valid']
    call('datasets/frame?id=asset-1&camera=camera_1&index=3')
    core.assets.frame.assert_called_once_with('asset-1', 'camera_1', 3)
    call('datasets/replay?id=asset-1&camera=camera_2&start=4&count=5&stride=2')
    core.assets.replay.assert_called_once_with('asset-1', 'camera_2', 4, 5, 2)
