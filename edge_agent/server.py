"""Serve the same workbench and hardware APIs on loopback; no cloud CORS bridge."""
import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from edge_agent.core import EdgeCore


def handler_for(core, static_dir):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_dir), **kwargs)

        def allowed(self):
            expected = {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            if host not in expected or (origin and origin not in {'http://' + h for h in expected}):
                self.reply(403, dict(error='Use the workbench served by this local edge service.'))
                return False
            return True

        def reply(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.allowed():
                return
            url = urlsplit(self.path)
            if not url.path.startswith('/api/'):
                return super().do_GET()
            try:
                if url.path == '/api/health':
                    response = dict(service='embodied-edge', version=2, hardware_commands='read-only',
                                    data_root=str(core.data_root))
                elif url.path == '/api/status':
                    response = dict(devices=core.hub.snapshot(), capture=core.capture_status(),
                                    jobs=list(core.jobs.values()))
                elif url.path == '/api/camera-guard/status':
                    response = core.camera_guard.summary()
                elif url.path == '/api/camera-guard/check':
                    response = core.camera_guard.check(parse_qs(url.query)['camera'][0])
                elif url.path == '/api/camera-guard/frame':
                    response = core.camera_guard.preview(parse_qs(url.query)['camera'][0])
                elif url.path == '/api/datasets':
                    response = core.datasets()
                elif url.path == '/api/datasets/audit':
                    response = core.audit(parse_qs(url.query)['id'][0])
                elif url.path == '/api/ports':
                    try:
                        from serial.tools import list_ports
                        response = dict(ports=[dict(port=p.device, description=p.description) for p in list_ports.comports()])
                    except ImportError:
                        response = dict(ports=[], error='Install pyserial in the edge service environment.')
                elif url.path == '/api/cameras':
                    try:
                        import pyrealsense2 as rs
                        response = dict(cameras=[dict(serial=d.get_info(rs.camera_info.serial_number),
                                                     name=d.get_info(rs.camera_info.name)) for d in rs.context().devices])
                    except ImportError:
                        response = dict(cameras=[], error='Install pyrealsense2 in the edge service environment.')
                else:
                    return self.reply(404, dict(error='Unknown API route.'))
                self.reply(200, response)
            except Exception as exc:
                self.reply(400, dict(error=f'{type(exc).__name__}: {exc}'))

        def do_POST(self):
            if not self.allowed():
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8 * 1024 * 1024:
                    return self.reply(413, dict(error='Expected JSON body up to 8 MiB.'))
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Expected JSON object.')
                route = urlsplit(self.path).path
                if route == '/api/devices/connect':
                    core.hub.connect(body['device'], body['config'])
                    response = dict(accepted=True)
                elif route == '/api/devices/disconnect':
                    core.hub.disconnect(body['device'])
                    response = dict(disconnected=True)
                elif route == '/api/capture/start':
                    response = core.start_capture(body.get('tactile', False), body.get('require_camera_check', False),body.get('expected_cameras'))
                elif route == '/api/camera-guard/baseline':
                    with core.lock:
                        if core.capture is not None:
                            raise ValueError('Finish or discard the active capture before changing a camera baseline.')
                        response = core.camera_guard.save_baseline(body['camera'], body['config'])
                elif route == '/api/capture/save':
                    response = core.save_capture()
                elif route == '/api/capture/discard':
                    with core.lock:
                        core.capture = None
                    response = dict(discarded=True)
                elif route == '/api/gello/frame':
                    try:
                        response = core.receive_gello(body)
                    except Exception as exc:
                        if core.capture:
                            core.capture['error'] = str(exc)
                        raise
                elif route == '/api/datasets/combine':
                    response = core.combine(body['datasets'])
                elif route == '/api/training/start':
                    response = core.start_training(body)
                elif route == '/api/training/cancel':
                    response = core.cancel_training(body['id'])
                else:
                    return self.reply(404, dict(error='Unknown API route.'))
                self.reply(200, response)
            except Exception as exc:
                self.reply(400, dict(error=f'{type(exc).__name__}: {exc}'))
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--workspace', type=Path, default=root/'robotics')
    parser.add_argument('--data-root', type=Path, default=root/'edge_data')
    args = parser.parse_args()
    core = EdgeCore(args.workspace, args.data_root)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(core, root/'dist'))
    print(f'Embodied Studio local workbench: http://127.0.0.1:{args.port}/#ur5e', flush=True)
    print('Devices are disconnected. Connect only through the local workbench when ready.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for name in list(core.hub.workers):
            core.hub.disconnect(name)
        for process in core.processes.values():
            if process.poll() is None:
                process.terminate()


if __name__ == '__main__':
    main()
