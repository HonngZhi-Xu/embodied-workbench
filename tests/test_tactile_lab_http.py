"""Exercise the embedded research application's actual local API contract."""
import json
from pathlib import Path
import sys
import threading
from http.server import ThreadingHTTPServer
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge_agent.core import EdgeCore
from edge_agent.server import handler_for

@pytest.fixture
def lab_api(tmp_path):
    core = EdgeCore(ROOT/'robotics', tmp_path)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(core, ROOT/'dist'))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    def call(path, value=None, method=None, headers=None, raw=None):
        body = raw if raw is not None else None if value is None else json.dumps(value).encode()
        request = urllib.request.Request(base+'/api/tactile-lab/'+path, data=body, method=method,
            headers=headers or {'Content-Type':'application/json'})
        return urllib.request.urlopen(request, timeout=15)
    yield core, call
    server.shutdown(); server.server_close()

def test_records_persist_version_conflicts_and_recoverable_delete(lab_api):
    core, call = lab_api
    value = dict(id='research-note',kind='note',name='Test evidence',status='待补充实验',data={'evidence':'fixture'})
    with call('records', {'record':value}) as response:
        saved = json.load(response)['record']
    with call('records') as response:
        assert json.load(response)['records'][0]['id'] == value['id']
    with call('records', {'record':dict(saved,name='Updated evidence')}) as response:
        updated = json.load(response)['record']
    assert updated['version'] > saved['version']
    with pytest.raises(urllib.error.HTTPError) as failure:
        call('records', {'record':dict(saved,name='Stale evidence')})
    assert failure.value.code == 409
    with call('records?id=research-note',method='DELETE') as response:
        assert json.load(response)['softDeleted']
    with call('records') as response:
        assert json.load(response)['records'] == []
    with call('restore', {'id':'research-note'}) as response:
        assert json.load(response)['record']['name'] == 'Updated evidence'

def test_multipart_upload_download_is_attachment_and_bytes_unchanged(lab_api):
    _, call = lab_api
    raw = b'{"samples":[],"unknown":null}\n'
    body = (b'--research-boundary\r\nContent-Disposition: form-data; name="file"; filename="episode.json"\r\n'
            b'Content-Type: application/json\r\n\r\n' + raw + b'\r\n--research-boundary--\r\n')
    with call('files',raw=body,headers={'Content-Type':'multipart/form-data; boundary=research-boundary'}) as response:
        record = json.load(response)['record']
    assert record['kind'] == 'file' and record['data']['size'] == len(raw)
    with call('files/'+record['id']) as response:
        assert response.read() == raw
        assert response.headers['Content-Type'] == 'application/octet-stream'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert 'attachment' in response.headers['Content-Disposition']

def test_lab_api_rejects_foreign_origin_and_forged_file_records(lab_api):
    _, call = lab_api
    with pytest.raises(urllib.error.HTTPError) as failure:
        call('records',headers={'Origin':'https://other.example'})
    assert failure.value.code == 403
    with pytest.raises(urllib.error.HTTPError):
        call('records',{'record':dict(id='forged',kind='file',name='forged',data={'key':'../secret'})})
    with pytest.raises(urllib.error.HTTPError) as failure:
        call('import-asset',{'asset_id':'/arbitrary/unregistered.zarr'})
    assert failure.value.code in (400,404)
