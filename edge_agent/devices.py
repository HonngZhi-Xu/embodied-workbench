import base64
import threading
import time
from collections import deque
from edge_agent.paxini import PaxiniM3025


class DeviceHub:
    def __init__(self):
        self.lock = threading.RLock()
        self.samples = {}
        self.states = {}
        self.workers = {}
        self.tactile_history = deque(maxlen=100)
        self.camera_frames = {}

    def publish_camera(self, key, image, timestamp, identity, intrinsics=None):
        with self.lock:
            self.camera_frames[key] = dict(image=image.copy(), timestamp=float(timestamp),
                                          received_at=time.time(), identity=str(identity), intrinsics=intrinsics)

    def publish(self, name, payload):
        with self.lock:
            self.samples[name] = dict(payload, received_at=time.time(), source='hardware')
            self.states[name] = dict(status='streaming', error=None)
            if name == 'tactile':
                self.tactile_history.append(self.samples[name])

    def connect(self, name, cfg):
        if name not in ('robot', 'rgb', 'tactile'):
            raise ValueError('Unknown device.')
        self.disconnect(name)
        stop = threading.Event()
        self.states[name] = dict(status='connecting', error=None)

        def work():
            driver = None
            try:
                if name == 'robot':
                    import rtde_receive
                    driver = rtde_receive.RTDEReceiveInterface(cfg['ip'])
                elif name == 'rgb':
                    import pyrealsense2 as rs
                    driver = rs.pipeline()
                    config = rs.config()
                    if cfg.get('serial'):
                        config.enable_device(cfg['serial'])
                    config.enable_stream(rs.stream.color, 640, 480, rs.format.rgb8, 30)
                    active = driver.start(config)
                    camera_serial = active.get_device().get_info(rs.camera_info.serial_number)
                    color_intrinsics = active.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
                    calibration = dict(width=color_intrinsics.width, height=color_intrinsics.height,
                                       fx=color_intrinsics.fx, fy=color_intrinsics.fy,
                                       ppx=color_intrinsics.ppx, ppy=color_intrinsics.ppy,
                                       model=str(color_intrinsics.model), coeffs=list(color_intrinsics.coeffs))
                else:
                    driver = PaxiniM3025(cfg['port'], cfg.get('device_id', 1), cfg.get('baudrate', 921600))
                while not stop.is_set():
                    if name == 'robot':
                        payload = dict(timestamp=time.time(), joints=driver.getActualQ(),
                                       velocities=driver.getActualQd(), tcp=driver.getActualTCPPose())
                    elif name == 'rgb':
                        import cv2
                        import numpy as np
                        frame = driver.wait_for_frames(timeout_ms=1000).get_color_frame()
                        if not frame:
                            continue
                        image = np.asanyarray(frame.get_data())
                        self.publish_camera('preview', image, time.time(), 'realsense:'+camera_serial, calibration)
                        ok, encoded = cv2.imencode('.jpg', cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
                        if not ok:
                            raise ValueError('Could not encode RGB preview.')
                        payload = dict(timestamp=time.time(), device_timestamp_ms=frame.get_timestamp(),
                                       image='data:image/jpeg;base64,' + base64.b64encode(encoded).decode(),
                                       width=image.shape[1], height=image.shape[0], camera_id=cfg.get('serial', 'default'))
                    else:
                        payload = driver.read()
                    self.publish(name, payload)
                    stop.wait(0.03)
            except Exception as exc:
                with self.lock:
                    self.states[name] = dict(status='error', error=f'{type(exc).__name__}: {exc}')
            finally:
                if driver is not None:
                    try:
                        if name == 'rgb':
                            driver.stop()
                        elif name == 'robot':
                            driver.disconnect()
                        else:
                            driver.close()
                    except Exception:
                        pass

        thread = threading.Thread(target=work, daemon=True)
        self.workers[name] = (stop, thread)
        thread.start()

    def disconnect(self, name):
        old = self.workers.pop(name, None)
        if old:
            old[0].set()
            old[1].join(timeout=2)
            if old[1].is_alive():
                self.workers[name] = old
                raise RuntimeError('Previous device connection is still closing; retry shortly.')
        with self.lock:
            self.states[name] = dict(status='disconnected', error=None)
            self.samples.pop(name, None)
            if name == 'tactile':
                self.tactile_history.clear()
            if name == 'rgb':
                self.camera_frames.pop('preview', None)

    def nearest_tactile(self, timestamp, max_age=0.2):
        with self.lock:
            frames = [f for f in self.tactile_history if 0 <= timestamp - f['timestamp'] <= max_age]
            if not frames:
                raise ValueError('No fresh PaXini frame within 200 ms before the GELLO frame.')
            return frames[-1]

    def snapshot(self):
        with self.lock:
            result = {}
            for name in ('robot', 'rgb', 'tactile', 'gello'):
                sample = self.samples.get(name)
                status = dict(self.states.get(name, dict(status='disconnected', error=None)))
                if sample and time.time() - sample['received_at'] > 1.5:
                    status['status'] = 'stale'
                result[name] = dict(**status, sample=sample)
            return result
