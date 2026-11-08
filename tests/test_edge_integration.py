import base64
import copy
import json
from pathlib import Path
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import cv2
import numpy as np
import pytest
import zarr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'robotics'))
from edge_agent.paxini import read_request, decode_response, xyz_counts, PaxiniM3025
from edge_agent.core import EdgeCore
from edge_agent.server import handler_for
from http.server import ThreadingHTTPServer


def response(address, payload, device=1, status=0):
    frame = b'\xaa\x55' + struct.pack('<HBBB I H', 10+len(payload), device, 0, 0xfb, address, len(payload))
    frame += bytes([status])+payload
    return frame+bytes([(-sum(frame)) & 255])


def test_uart_request_matches_manual_example():
    assert read_request(1038, 32, 1).hex(' ').upper() == '55 AA 09 00 01 00 FB 0E 04 00 00 20 00 CA'
    assert read_request(1038, 32, 2)[-1] == 0xc9
    assert xyz_counts(bytes([255, 128, 255, 127, 0, 0])) == [[-1, -128, 255], [127, 0, 0]]


@pytest.mark.parametrize('corruption', ['lrc', 'device', 'length', 'status'])
def test_uart_rejects_bad_responses(corruption):
    frame = response(1008, b'\x00\x00\x0a', device=2 if corruption=='device' else 1,
                     status=1 if corruption=='status' else 0)
    if corruption=='lrc': frame=frame[:-1]+bytes([frame[-1]^1])
    if corruption=='length': frame=frame[:-1]
    with pytest.raises(ValueError):
        decode_response(frame, 1008, 3)


def test_uart_fragmented_transport_and_scaling():
    class Transport:
        def __init__(self): self.pending=b''
        def write(self, req):
            address=int.from_bytes(req[7:11], 'little')
            payload=b'\xff\x02\x0a' if address==1008 else b'\x00\xff\x03'*77
            self.pending=b'noise'+response(address, payload)
        @property
        def in_waiting(self): return len(self.pending)
        def read(self, n):
            result=self.pending[:min(n,3)];self.pending=self.pending[len(result):];return result
        def close(self): pass
    sensor=PaxiniM3025('fixture', transport=Transport())
    frame=sensor.read()
    assert len(frame['points'])==77
    assert frame['resultant_n']==[-.1,.2,1.0]
    assert frame['points'][0]==[0,-1,3]


def frame_payload(seq, timestamp):
    image=np.full((480,640,3),123,dtype=np.uint8)
    ok,data=cv2.imencode('.png',image)
    assert ok
    return dict(seq=seq,timestamp=timestamp,joints=[.1]*7,action=[.2]*7,
                images={'camera_0':base64.b64encode(data).decode()})


def touch(timestamp):
    return dict(timestamp=timestamp,seq=1,points=[[0,1,2]]*77,resultant_n=[.1,.2,1.0],unit='counts')


def test_multimodal_record_combine_and_real_training(tmp_path):
    core=EdgeCore(ROOT/'robotics',tmp_path)
    names=[]
    base=time.time()-3
    for ep in range(2):
        core.start_capture(tactile=True)
        for i in range(12):
            stamp=base+ep*1.4+i*.1
            core.hub.publish('tactile',touch(stamp-.01))
            core.receive_gello(frame_payload(ep*12+i,stamp))
        names.append(core.save_capture()['id'])
    with pytest.raises(ValueError,match='two complete episodes'):
        core.start_training(dict(dataset=names[0]))
    merged=core.combine(names)['id']
    report=core.audit(merged)
    assert report['valid'] and report['n_episodes']==2
    assert report['arrays']['tactile']['shape']==[24,231]
    source=zarr.open_group(str(tmp_path/names[0]),mode='r')
    assert source['meta/episode_ends'][:].tolist()==[12]
    job=core.start_training(dict(dataset=merged,modality='rgb_tactile',device='cpu',epochs=1,smoke=True))
    deadline=time.time()+120
    while job['status']=='running' and time.time()<deadline:
        time.sleep(.2)
    if job['status']=='running':
        core.cancel_training(job['id'])
        pytest.fail('Smoke subprocess did not finish in 120s')
    assert job['status']=='completed', '\n'.join(job['log'])
    assert Path(job['checkpoint']).is_file()
    saved=json.loads(json.dumps(core.datasets()))
    assert len(saved)==3


def test_stale_tactile_missing_camera_and_path_guard(tmp_path):
    core=EdgeCore(ROOT/'robotics',tmp_path)
    core.start_capture(tactile=True)
    core.hub.publish('tactile',touch(time.time()-1))
    with pytest.raises(ValueError,match='fresh PaXini'):
        core.receive_gello(frame_payload(1,time.time()))
    assert core.capture_status()['frame_count']==0
    payload=frame_payload(2,time.time())
    payload['images']={}
    with pytest.raises(ValueError,match='include RGB'):
        core.receive_gello(payload)
    with pytest.raises(ValueError,match='inside'):
        core.dataset_path('../')


def test_local_http_routes_origin_and_read_only_robot_contract(tmp_path):
    core=EdgeCore(ROOT/'robotics',tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(core,ROOT/'dist'))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(url+'/api/health') as res:
            assert json.load(res)['hardware_commands']=='read-only'
        with urllib.request.urlopen(url+'/api/camera-guard/check?camera=camera_0') as res:
            assert json.load(res)['status']=='unknown'
        with urllib.request.urlopen(url+'/api/camera-guard/status') as res:
            assert json.load(res)['profiles']=={}
        req=urllib.request.Request(url+'/api/capture/start',data=b'{"require_camera_check":true}',headers={'Content-Type':'application/json'})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        assert error.value.code==400 and core.capture is None
        req=urllib.request.Request(url+'/api/capture/start',data=b'{}',headers={'Content-Type':'application/json','Origin':'https://untrusted.invalid'})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        assert error.value.code==403
        req=urllib.request.Request(url+'/api/robot/move',data=b'{}',headers={'Content-Type':'application/json'})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        assert error.value.code==404
        with urllib.request.urlopen(url+'/') as res:
            assert b'platform.js' in res.read()
    finally:
        server.shutdown();server.server_close()


def test_checkpoint_failure_preserves_previous_model(tmp_path, monkeypatch):
    import torch
    from omegaconf import OmegaConf
    from diffusion_policy.workspace.base_workspace import BaseWorkspace
    workspace=BaseWorkspace(OmegaConf.create({}),output_dir=str(tmp_path))
    destination=tmp_path/'model.ckpt'
    destination.write_bytes(b'previous-valid-model')
    def fail(*args,**kwargs):
        raise OSError('simulated disk full')
    monkeypatch.setattr(torch,'save',fail)
    workspace.save_checkpoint(destination)
    with pytest.raises(RuntimeError,match='Checkpoint save failed'):
        workspace.wait_for_saving()
    assert destination.read_bytes()==b'previous-valid-model'
    assert not list(tmp_path.glob('*.tmp'))
