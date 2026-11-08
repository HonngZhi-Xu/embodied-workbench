"""Optional non-blocking GELLO tap. The existing robot control path stays in charge."""
import base64
import json
import queue
import threading
import time
import urllib.request
import os
from urllib.parse import urlsplit


class PlatformPublisher:
    def __init__(self, url='http://127.0.0.1:8787', hz=10):
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost'):
            raise ValueError('PlatformPublisher requires a local loopback edge service URL.')
        self.url = url.rstrip('/') + '/api/gello/frame'
        self.hz = hz
        self.queue = queue.Queue(maxsize=1)
        self.last = 0.0
        self.seq = self.dropped = 0
        # Stable serial-to-role bindings and calibrated intrinsics supplied by the workstation.
        self.camera_ids = json.loads(os.environ.get('EMBODIED_CAMERA_IDS', '{}'))
        self.camera_intrinsics = json.loads(os.environ.get('EMBODIED_CAMERA_INTRINSICS', '{}'))
        self.thread = threading.Thread(target=self._send_loop, daemon=True)
        self.thread.start()

    def submit(self, obs, action):
        now = time.monotonic()
        if now - self.last < 1 / self.hz:
            return
        self.last = now
        self.seq += 1
        if self.queue.full():
            self.dropped += 1
            return
        # Snapshot memory before the producer reuses camera arrays.
        snap = {k: v.copy() if hasattr(v, 'copy') else v for k, v in obs.items()}
        self.queue.put_nowait((time.time(), self.seq, snap, action.copy()))

    def _send_loop(self):
        import cv2
        mapping = {'image_wrist': 'camera_0', 'image_base': 'camera_1', 'image_camera_2': 'camera_2'}
        while True:
            timestamp, seq, obs, action = self.queue.get()
            try:
                images = {}
                for source, target in mapping.items():
                    if source in obs:
                        ok, encoded = cv2.imencode('.png', cv2.cvtColor(obs[source], cv2.COLOR_RGB2BGR))
                        if not ok:
                            raise ValueError('PNG encoding failed.')
                        images[target] = base64.b64encode(encoded).decode()
                payload = dict(timestamp=timestamp, seq=seq, joints=obs['joints'].tolist(),
                               action=action.tolist(), images=images, dropped=self.dropped,
                               camera_ids=self.camera_ids, camera_intrinsics=self.camera_intrinsics)
                request = urllib.request.Request(self.url, json.dumps(payload).encode(),
                                                 headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=2) as response:
                    response.read()
            except Exception as exc:
                self.dropped += 1
                print(f'[PlatformPublisher] Frame not recorded: {exc}')
            finally:
                self.queue.task_done()
