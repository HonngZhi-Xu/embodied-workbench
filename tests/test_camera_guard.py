import math
import base64
from pathlib import Path
import sys
import time
import cv2
import numpy as np
import pytest
import zarr

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'robotics'))
from edge_agent.camera_guard import CameraGuard,view_match,homography_metrics,marker_pose,pose_metrics
from edge_agent.devices import DeviceHub
from edge_agent.core import EdgeCore


def texture():
    rng=np.random.default_rng(8)
    image=np.full((480,640,3),220,dtype=np.uint8)
    for _ in range(300):
        x,y=rng.integers([20,20],[620,460]);color=tuple(int(x) for x in rng.integers(0,190,3))
        cv2.circle(image,(int(x),int(y)),int(rng.integers(3,12)),color,-1)
    return image


def source(hub,image,key='camera_1',identity='serial-side-1'):
    hub.publish_camera(key,image,time.time(),identity)


def config(**kwargs):
    return dict(mode='features',mount='fixed',roi=[20,20,600,440],**kwargs)


def test_unchanged_and_shifted_view_with_plane_preview(tmp_path):
    hub=DeviceHub();guard=CameraGuard(tmp_path,hub);image=texture()
    source(hub,image)
    profile=guard.save_baseline('camera_1',config())
    assert guard.check('camera_1')['passed']
    shifted=cv2.warpAffine(image,np.float32([[1,0,20],[0,1,-10]]),(640,480),borderValue=(220,220,220))
    source(hub,shifted)
    result=guard.check('camera_1')
    assert result['status']=='drift'
    assert result['metrics']['shift_x_px']==pytest.approx(20,abs=1)
    assert result['metrics']['shift_y_px']==pytest.approx(-10,abs=1)
    assert 'aligned_preview' in result
    assert 'translation_mm' not in result['metrics']
    assert (guard.root/(profile['id']+'.png')).exists()


def test_unknown_when_blank_stale_or_identity_changed(tmp_path):
    hub=DeviceHub();guard=CameraGuard(tmp_path,hub);source(hub,texture())
    guard.save_baseline('camera_1',config())
    source(hub,np.zeros((480,640,3),dtype=np.uint8))
    assert guard.check('camera_1')['status']=='unknown'
    source(hub,texture(),identity='different-serial')
    assert guard.check('camera_1')['status']=='unknown'
    source(hub,texture());hub.camera_frames['camera_1']['received_at']-=2
    assert guard.check('camera_1')['status']=='unknown'


def test_wrist_requires_same_robot_reference_pose(tmp_path):
    hub=DeviceHub();guard=CameraGuard(tmp_path,hub);source(hub,texture(),key='camera_0')
    with pytest.raises(ValueError,match='fresh robot'):
        guard.save_baseline('camera_0',dict(mode='features',mount='wrist'))
    hub.publish('robot',dict(joints=[0.1]*6,timestamp=time.time()))
    guard.save_baseline('camera_0',dict(mode='features',mount='wrist'))
    assert guard.check('camera_0')['passed']
    hub.publish('robot',dict(joints=[0.5]*6,timestamp=time.time()))
    result=guard.check('camera_0')
    assert result['status']=='unknown' and 'reference joint pose' in result['guidance'][0]


def test_baseline_versions_persist_and_preview_is_diagnostic(tmp_path):
    hub=DeviceHub();guard=CameraGuard(tmp_path,hub);source(hub,texture())
    old=guard.save_baseline('camera_1',config())
    new=guard.save_baseline('camera_1',config())
    assert old['id']!=new['id']
    assert (guard.root/(old['id']+'.png')).exists()
    restored=CameraGuard(tmp_path,hub)
    assert restored.profiles['camera_1']['id']==new['id']
    with pytest.raises(ValueError,match='diagnostic'):
        restored.save_baseline('preview',config())


def test_capture_preflight_required_all_cameras_and_raw_data_preserved(tmp_path):
    core=EdgeCore(ROOT/'robotics',tmp_path)
    with pytest.raises(ValueError,match='No baseline'):
        core.start_capture(require_camera_check=True)
    source(core.hub,texture(),key='camera_0',identity='serial-wrist')
    core.hub.publish('robot',dict(joints=[0.1]*6,timestamp=time.time()))
    core.camera_guard.save_baseline('camera_0',dict(mode='features',mount='wrist'))
    source(core.hub,texture())
    core.camera_guard.save_baseline('camera_1',config())
    source(core.hub,texture(),key='camera_2',identity='serial-side-2')
    with pytest.raises(ValueError,match='camera_2'):
        core.start_capture(require_camera_check=True)
    core.camera_guard.save_baseline('camera_2',config())
    core.start_capture(require_camera_check=True)
    assert core.capture['camera_preflight']['passed']
    image=texture()
    ok,encoded=cv2.imencode('.png',cv2.cvtColor(image,cv2.COLOR_RGB2BGR))
    assert ok
    core.receive_gello(dict(timestamp=time.time(),seq=1,joints=[.1]*7,action=[.2]*7,
                            images={k:base64.b64encode(encoded).decode() for k in ('camera_0','camera_1','camera_2')},
                            camera_ids={'camera_0':'serial-wrist','camera_1':'serial-side-1','camera_2':'serial-side-2'}))
    saved=core.save_capture()
    dataset=zarr.open_group(str(tmp_path/saved['id']),mode='r')
    assert np.array_equal(dataset['data/camera_1'][0],image)
    report=dataset.attrs['camera_preflight']
    assert report['passed'] and len(report['checks'])==3
    assert all(c['baseline_id'].startswith('CB-') for c in report['checks'])
    moved=cv2.warpAffine(texture(),np.float32([[1,0,18],[0,1,0]]),(640,480))
    source(core.hub,moved)
    # Required baselines enforce checks even if an older caller omits the flag.
    with pytest.raises(ValueError,match='blocked capture'):
        core.start_capture()


def test_three_camera_layout_does_not_accept_only_one_camera(tmp_path):
    core=EdgeCore(ROOT/'robotics',tmp_path)
    source(core.hub,texture())
    core.camera_guard.save_baseline('camera_1',config())
    with pytest.raises(ValueError,match='camera_0'):
        core.start_capture(require_camera_check=True)
    assert core.capture is None
    # An explicitly configured one-camera workcell remains supported by the API.
    core.start_capture(require_camera_check=True,expected_cameras=['camera_1'])
    assert core.capture['camera_preflight']['passed']


def intrinsics():
    return dict(width=640,height=480,fx=600,fy=600,ppx=320,ppy=240,model='none',coeffs=[0]*5)


def marker_image(translation=(0,0,.5)):
    side=.08
    obj=np.array([[-side/2,side/2,0],[side/2,side/2,0],[side/2,-side/2,0],[-side/2,-side/2,0]])
    K=np.array([[600.,0,320],[0,600,240],[0,0,1]])
    corners=cv2.projectPoints(obj,np.array([math.pi-.35,.05,.05]),np.array(translation,dtype=float),K,np.zeros(5))[0].reshape(4,2)
    marker=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),0,240)
    # Supersample edges: binary nearest-neighbour rasterization at 96px gives
    # unrealistic corner quantization that dominates the planar pose estimate.
    H=cv2.getPerspectiveTransform(np.float32([[0,0],[239,0],[239,239],[0,239]]),corners.astype(np.float32)*4)
    image=cv2.warpPerspective(marker,H,(2560,1920),flags=cv2.INTER_NEAREST,borderValue=255)
    image=cv2.resize(image,(640,480),interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(image,cv2.COLOR_GRAY2RGB)


def test_aruco_metric_estimation_and_missing_intrinsics(tmp_path):
    cfg=dict(mode='aruco',mount='fixed',marker_id=0,marker_size_m=.08)
    reference=marker_pose(marker_image(),cfg,intrinsics())
    current=marker_pose(marker_image((.02,0,.5)),cfg,intrinsics())
    metrics=pose_metrics(reference,current)
    assert metrics['translation_mm']==pytest.approx(20,abs=5)
    hub=DeviceHub();guard=CameraGuard(tmp_path,hub);source(hub,marker_image())
    with pytest.raises(ValueError,match='intrinsics'):
        guard.save_baseline('camera_1',cfg)
    guard.save_baseline('camera_1',dict(cfg,intrinsics=intrinsics()))
    assert guard.check('camera_1')['status']=='pass'
    source(hub,marker_image((.02,0,.5)))
    assert guard.check('camera_1')['status']=='drift'


def test_bad_roi_resolution_and_thresholds_are_rejected(tmp_path):
    hub=DeviceHub();guard=CameraGuard(tmp_path,hub);source(hub,texture())
    with pytest.raises(ValueError,match='ROI'):
        guard.save_baseline('camera_1',dict(mode='features',roi=[-1,0,100,100]))
    with pytest.raises(ValueError,match='finite'):
        guard.save_baseline('camera_1',config(shift_px=float('nan')))
    guard.save_baseline('camera_1',config())
    source(hub,np.zeros((240,320,3),dtype=np.uint8))
    assert guard.check('camera_1')['status']=='unknown'
