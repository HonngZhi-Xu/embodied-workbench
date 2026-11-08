"""Pre-capture camera view checks. Never edits raw data or moves hardware.

ORB + planar homography is a view-consistency test, not a 3-D camera localizer.
ArUco + calibrated intrinsics estimates pose relative to a FIXED reference marker.
"""
import base64
import json
import math
from pathlib import Path
import re
import time
import uuid
import cv2
import numpy as np


def jpeg(image):
    ok, data = cv2.imencode('.jpg', cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ValueError('Preview encoding failed.')
    return 'data:image/jpeg;base64,' + base64.b64encode(data).decode()


def mask_for(image, roi):
    h,w=image.shape[:2]
    x,y,rw,rh=[int(v) for v in roi]
    if min(x,y)<0 or min(rw,rh)<40 or x+rw>w or y+rh>h:
        raise ValueError('ROI must lie inside the image and be at least 40 x 40 pixels.')
    mask=np.zeros((h,w),dtype=np.uint8);mask[y:y+rh,x:x+rw]=255
    return mask


def view_match(reference,current,roi):
    if reference.shape!=current.shape:
        raise ValueError('Image resolution changed; re-establish the camera baseline.')
    mask=mask_for(reference,roi)
    orb=cv2.ORB_create(nfeatures=2200,fastThreshold=10)
    key0,des0=orb.detectAndCompute(cv2.cvtColor(reference,cv2.COLOR_RGB2GRAY),mask)
    # The reference features may move outside the baseline ROI in the current view.
    key1,des1=orb.detectAndCompute(cv2.cvtColor(current,cv2.COLOR_RGB2GRAY),None)
    if des0 is None or des1 is None or len(des0)<30 or len(des1)<30:
        raise ValueError('Too few static features; choose a textured fixed ROI or use ArUco.')
    pairs=cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(des0,des1,k=2)
    candidates=[p[0] for p in pairs if len(p)==2 and p[0].distance<.7*p[1].distance]
    # Do not allow many query points to vote for one current keypoint.
    unique={}
    for m in sorted(candidates,key=lambda m:m.distance):
        unique.setdefault(m.trainIdx,m)
    matches=list(unique.values())
    if len(matches)<25:
        raise ValueError('Insufficient reliable matches; scene/lighting/occlusion may have changed.')
    src=np.float32([key0[m.queryIdx].pt for m in matches])
    dst=np.float32([key1[m.trainIdx].pt for m in matches])
    H,inliers=cv2.findHomography(src,dst,cv2.RANSAC,2.5)
    count=int(inliers.sum()) if inliers is not None else 0
    if H is None or not np.isfinite(H).all() or abs(np.linalg.det(H))<1e-8 or count<20 or count/len(matches)<.55:
        raise ValueError('Homography is unreliable; do not infer camera movement from this scene.')
    area=cv2.contourArea(cv2.convexHull(src[inliers.ravel().astype(bool)]))
    if area/(roi[2]*roi[3])<.08:
        raise ValueError('Matched features are too localized; expand the fixed-reference ROI.')
    return H,dict(matches=len(matches),inliers=count,inlier_ratio=count/len(matches),reference_coverage=area/(roi[2]*roi[3]))


def homography_metrics(H,shape,roi):
    x,y,w,h=roi
    corners=np.float32([[x,y],[x+w,y],[x+w,y+h],[x,y+h]])
    projected=cv2.perspectiveTransform(corners[None],H)[0]
    if not np.isfinite(projected).all() or not cv2.isContourConvex(projected.astype(np.float32)):
        raise ValueError('Degenerate or folded plane projection.')
    center=np.float32([[[x+w/2,y+h/2],[x+w/2+30,y+h/2],[x+w/2,y+h/2+30]]])
    p=cv2.perspectiveTransform(center,H)[0]
    dx,dy=(p[0]-center[0,0]).tolist()
    J=np.stack([(p[1]-p[0])/30,(p[2]-p[0])/30],axis=1)
    if np.linalg.det(J)<=0:
        raise ValueError('Mirrored image projection; check camera orientation/processing.')
    scale=math.sqrt(float(np.linalg.det(J)))
    return dict(shift_x_px=dx,shift_y_px=dy,shift_px=math.hypot(dx,dy),
                angle_deg=math.degrees(math.atan2(J[1,0],J[0,0])),scale=scale,
                corner_rms_px=float(np.sqrt(np.mean(np.sum((projected-corners)**2,axis=1)))))


def calibration(intrinsics,shape):
    if not intrinsics:
        raise ValueError('Metric ArUco mode requires calibrated intrinsics for this camera/resolution.')
    if intrinsics.get('model','none') not in ('none','brown_conrady','distortion.none','distortion.brown_conrady'):
        raise ValueError('Unsupported distortion model; provide OpenCV-compatible calibration.')
    h,w=shape[:2]
    if (int(intrinsics['width']),int(intrinsics['height']))!=(w,h):
        raise ValueError('Calibration resolution does not match the camera image.')
    K=np.array([[intrinsics['fx'],0,intrinsics['ppx']],[0,intrinsics['fy'],intrinsics['ppy']],[0,0,1]],dtype=float)
    distortion=np.asarray(intrinsics.get('coeffs',[0]*5),dtype=float)
    if not np.isfinite(K).all() or min(K[0,0],K[1,1])<=0 or distortion.size not in (4,5,8,12,14) or not np.isfinite(distortion).all():
        raise ValueError('Invalid intrinsic matrix/distortion coefficients.')
    return K,distortion


def marker_pose(image,config,intrinsics):
    K,D=calibration(intrinsics,image.shape)
    dictionary=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    parameters=cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod=cv2.aruco.CORNER_REFINE_SUBPIX
    corners,ids,_=cv2.aruco.ArucoDetector(dictionary,parameters).detectMarkers(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY))
    target=int(config['marker_id'])
    found=[] if ids is None else np.flatnonzero(ids.ravel()==target)
    if len(found)!=1:
        raise ValueError(f'Expected one fixed ArUco 4x4_50 marker with ID {target}; keep it fully visible.')
    points=corners[int(found[0])].reshape(4,2).astype(np.float64)
    if min(np.linalg.norm(np.roll(points,-1,axis=0)-points,axis=1))<40:
        raise ValueError('Marker is too small in the image for a reliable pose check.')
    side=float(config['marker_size_m'])
    if not .005<=side<=2:
        raise ValueError('Measure the printed marker outer black square; size must be in metres.')
    obj=np.array([[-side/2,side/2,0],[side/2,side/2,0],[side/2,-side/2,0],[-side/2,-side/2,0]])
    ok,rvecs,tvecs,errors=cv2.solvePnPGeneric(obj,points,K,D,flags=cv2.SOLVEPNP_IPPE_SQUARE)
    solutions=[]
    for r,t in zip(rvecs,tvecs):
        r,t=cv2.solvePnPRefineLM(obj,points,K,D,r.copy(),t.copy())
        R=cv2.Rodrigues(r)[0]
        if np.min((R@obj.T+t).T[:,2])<=0:
            continue
        project=cv2.projectPoints(obj,r,t,K,D)[0].reshape(-1,2)
        error=float(np.sqrt(np.mean(np.sum((project-points)**2,axis=1))))
        solutions.append((error,R,t.reshape(3)))
    solutions.sort(key=lambda v:v[0])
    if not ok or not solutions or solutions[0][0]>2:
        raise ValueError('Pose reprojection error is too high.')
    if len(solutions)>1:
        rotation=cv2.Rodrigues(solutions[0][1]@solutions[1][1].T)[0]
        if solutions[1][0]<solutions[0][0]*1.3+.05 and np.linalg.norm(rotation)>math.radians(8):
            raise ValueError('Planar marker pose is ambiguous; use a clearer oblique view or a larger calibrated board.')
    error,R,t=solutions[0]
    return dict(rotation=R.tolist(),translation=t.tolist(),camera_center=(-R.T@t).tolist(),reprojection_px=error,corners=points.tolist())


def pose_metrics(reference,current):
    R0,R1=np.asarray(reference['rotation']),np.asarray(current['rotation'])
    correction=np.asarray(reference['camera_center'])-np.asarray(current['camera_center'])
    rotation=cv2.Rodrigues(R1@R0.T)[0].reshape(3)
    return dict(translation_mm=float(np.linalg.norm(correction)*1000),
                angle_deg=float(np.linalg.norm(rotation)*180/math.pi),
                correction_marker_xyz_mm=(correction*1000).tolist(),
                correction_camera_rotvec_deg=(rotation*180/math.pi).tolist(),
                reprojection_px=current['reprojection_px'])


class CameraGuard:
    def __init__(self,root,hub):
        self.root=Path(root)/'camera_guard';self.root.mkdir(exist_ok=True)
        self.hub=hub
        self.index=self.root/'profiles.json'
        self.profiles=json.loads(self.index.read_text('utf-8')) if self.index.exists() else {}

    def frame(self,key):
        with self.hub.lock:
            frame=self.hub.camera_frames.get(key)
            if not frame or time.time()-frame['received_at']>1.5 or abs(time.time()-frame['timestamp'])>1.5:
                raise ValueError(f'{key}: no fresh RGB frame. Connect the actual acquisition source first.')
            return dict(frame,image=frame['image'].copy())

    def summary(self):
        with self.hub.lock:
            sources=[dict(key=k,identity=f['identity'],width=f['image'].shape[1],height=f['image'].shape[0],
                          timestamp=f['timestamp'],fresh=time.time()-f['received_at']<=1.5,
                          intrinsics=f.get('intrinsics')) for k,f in self.hub.camera_frames.items()]
        return dict(profiles=self.profiles,sources=sources)

    def preview(self,key):
        frame=self.frame(key)
        return dict(key=key,identity=frame['identity'],timestamp=frame['timestamp'],
                    width=frame['image'].shape[1],height=frame['image'].shape[0],
                    intrinsics=frame.get('intrinsics'),image=jpeg(frame['image']))

    def robot_pose(self):
        snapshot=self.hub.snapshot()
        for key in ('robot','gello'):
            if snapshot[key]['status']=='streaming':
                joints=snapshot[key]['sample'].get('joints',[])[:6]
                if len(joints)==6 and np.isfinite(joints).all():
                    return joints
        raise ValueError('Wrist camera check requires a fresh robot joint state at the reference pose.')

    def save_baseline(self,key,config):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}',key):
            raise ValueError('Invalid camera key.')
        cfg=dict(mode=config.get('mode','features'),mount=config.get('mount','fixed'),
                 required=bool(config.get('required',True)),roi=config.get('roi'),
                 shift_px=float(config.get('shift_px',6)),angle_deg=float(config.get('angle_deg',2)),
                 scale_ratio=float(config.get('scale_ratio',.03)),translation_mm=float(config.get('translation_mm',5)),
                 marker_id=int(config.get('marker_id',0)),marker_size_m=float(config.get('marker_size_m',.05)))
        if cfg['mode'] not in ('features','aruco') or cfg['mount'] not in ('fixed','wrist'):
            raise ValueError('Unsupported check mode or camera mount.')
        for name in ('shift_px','angle_deg','scale_ratio','translation_mm'):
            if not math.isfinite(cfg[name]) or cfg[name]<=0:
                raise ValueError('Thresholds must be finite and positive.')
        if not 0<=cfg['marker_id']<50:
            raise ValueError('Marker ID must be 0..49 for DICT_4X4_50.')
        if key=='preview' and cfg['required']:
            raise ValueError('Independent preview is diagnostic only. Save required baselines from GELLO camera_0/1/2.')
        frame=self.frame(key);image=frame['image']
        reference_joints=self.robot_pose() if cfg['mount']=='wrist' else None
        cfg['roi']=cfg['roi'] or [0,0,image.shape[1],image.shape[0]]
        mask_for(image,cfg['roi'])
        intrinsics=config.get('intrinsics') or frame.get('intrinsics')
        extra={}
        if cfg['mode']=='features':
            view_match(image,image,cfg['roi'])
        else:
            extra['pose']=marker_pose(image,cfg,intrinsics)
        identifier='CB-'+uuid.uuid4().hex[:12]
        filename=identifier+'.png'
        if not cv2.imwrite(str(self.root/filename),cv2.cvtColor(image,cv2.COLOR_RGB2BGR)):
            raise IOError('Could not save baseline PNG.')
        profile=dict(id=identifier,key=key,created_at=time.time(),identity=frame['identity'],config=cfg,
                     intrinsics=intrinsics,image_file=filename,shape=list(image.shape),
                     reference_joints=reference_joints,**extra)
        # Baseline files remain immutable; updating a profile creates a new version.
        (self.root/(identifier+'.json')).write_text(json.dumps(profile,indent=2),encoding='utf-8')
        self.profiles[key]=profile
        temporary=self.index.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.profiles,indent=2),encoding='utf-8');temporary.replace(self.index)
        return profile

    def check(self,key,preview=True):
        profile=self.profiles.get(key)
        result=dict(key=key,status='unknown',passed=False,checked_at=time.time(),guidance=[],baseline_id=profile['id'] if profile else None)
        try:
            if not profile:
                raise ValueError('No baseline registered for this acquisition camera.')
            frame=self.frame(key);cfg=profile['config'];current=frame['image']
            if frame['identity']!=profile['identity'] or list(current.shape)!=profile['shape']:
                raise ValueError('Camera identity or resolution changed; verify the device/role binding first.')
            if cfg['mount']=='wrist':
                delta=np.asarray(self.robot_pose())-profile['reference_joints']
                delta=np.arctan2(np.sin(delta),np.cos(delta))
                if np.max(np.abs(delta))>.03:
                    raise ValueError('Robot is not at the stored reference joint pose; wrist-camera motion cannot be judged yet.')
            reference=cv2.cvtColor(cv2.imread(str(self.root/profile['image_file'])),cv2.COLOR_BGR2RGB)
            if cfg['mode']=='features':
                H,quality=view_match(reference,current,cfg['roi'])
                metrics=homography_metrics(H,current.shape,cfg['roi'])
                result.update(metrics=metrics,quality=quality,scope='planar-view-consistency; no metric 3D pose')
                passed=metrics['corner_rms_px']<=cfg['shift_px'] and abs(metrics['angle_deg'])<=cfg['angle_deg'] and abs(metrics['scale']-1)<=cfg['scale_ratio']
                result['guidance']=[f"固定参照在图像中偏移 X={metrics['shift_x_px']:+.1f}px、Y={metrics['shift_y_px']:+.1f}px；调整相机后再次检查。",
                                    f"图像平面旋转 {metrics['angle_deg']:+.2f}°，局部尺度 {metrics['scale']:.3f}。这些量不是物理移动距离或云台角度。",
                                    '确认固定 ROI 没有被机械臂、木块或工具遮挡；布帘与任务物体不能作为固定参照。']
                if preview:
                    aligned=cv2.warpPerspective(current,np.linalg.inv(H),(current.shape[1],current.shape[0]))
                    result['aligned_preview']=jpeg(cv2.addWeighted(reference,.5,aligned,.5,0))
                    result['preview_note']='平面配准叠加预览；不改变原图，不保证三维场景/机械臂对齐。'
            else:
                if frame.get('intrinsics') is not None and frame['intrinsics']!=profile['intrinsics']:
                    raise ValueError('Intrinsics changed; verify calibration before pose comparison.')
                pose=marker_pose(current,cfg,profile['intrinsics'])
                metrics=pose_metrics(profile['pose'],pose)
                result.update(metrics=metrics,scope='camera pose relative to the fixed marker; not robot-base calibration')
                passed=metrics['translation_mm']<=cfg['translation_mm'] and metrics['angle_deg']<=cfg['angle_deg']
                xyz=metrics['correction_marker_xyz_mm']
                result['guidance']=[f"恢复基准所需的相机中心位移（标记坐标系）：X {xyz[0]:+.1f}mm，Y {xyz[1]:+.1f}mm，Z {xyz[2]:+.1f}mm。",
                                    f"相对姿态差 {metrics['angle_deg']:.2f}°；确认标记板本身没有移动，逐步调整并复检。",
                                    'X/Y/Z 以固定标记坐标为准；此结果不是机械臂基坐标指令，平台不会驱动相机或机械臂。']
                if preview:
                    annotated=cv2.cvtColor(current,cv2.COLOR_RGB2BGR)
                    cv2.polylines(annotated,[np.asarray(pose['corners'],dtype=np.int32)],True,(0,210,120),2)
                    result['aligned_preview']=jpeg(cv2.cvtColor(annotated,cv2.COLOR_BGR2RGB))
                    result['preview_note']='已识别的固定标记；三维视角变化不自动用图像扭曲替代。'
            result.update(status='pass' if passed else 'drift',passed=bool(passed),mode=cfg['mode'])
            if preview:
                result['reference_preview']=jpeg(reference);result['current_preview']=jpeg(current)
        except (ValueError,cv2.error,TypeError,KeyError) as exc:
            result['guidance']=[str(exc),'当前无法可靠判断；不会把检测失败判为位置正常。']
        return result

    def preflight(self,require=False,expected=None):
        keys={k for k,p in self.profiles.items() if p['config']['required']}
        if not require and not keys:
            return dict(status='not_configured',passed=False,checks=[])
        with self.hub.lock:
            keys.update(k for k in self.hub.camera_frames if k.startswith('camera_'))
        if require:
            expected=['camera_0','camera_1','camera_2'] if expected is None else expected
            if not isinstance(expected,list) or not expected or any(k not in ('camera_0','camera_1','camera_2') for k in expected):
                raise ValueError('Expected cameras must be a nonempty list of camera_0/1/2.')
            keys.update(expected)
        if not keys:
            raise ValueError('Camera preflight is required. Register a baseline for each GELLO RGB camera first.')
        checks=[self.check(k,preview=False) for k in sorted(keys)]
        if not all(c['passed'] for c in checks):
            message='; '.join(c['key']+': '+c['guidance'][0] for c in checks if not c['passed'])
            raise ValueError('Camera preflight blocked capture: '+message)
        return dict(status='pass',passed=True,checks=checks)
