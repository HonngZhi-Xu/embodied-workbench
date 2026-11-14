import base64
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
import numpy as np
from edge_agent.devices import DeviceHub
from edge_agent.camera_guard import CameraGuard
from edge_agent.assets import ExternalAssets, DEFAULT_EXTERNAL_ROOTS


class EdgeCore:
    def __init__(self, workspace, data_root):
        self.workspace = Path(workspace).resolve()
        self.data_root = Path(data_root).resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(self.workspace))
        self.hub = DeviceHub()
        self.camera_guard = CameraGuard(self.data_root, self.hub)
        self.lock = threading.RLock()
        self.capture = None
        self.jobs = {}
        self.processes = {}
        self.assets = ExternalAssets(self.data_root, active_provider=lambda: getattr(getattr(self, 'ur_workflow', None), 'capture', None))

    def load_external_config(self, profile=None):
        self.assets.configure((profile or {}).get('external_data_roots', DEFAULT_EXTERNAL_ROOTS))

    def resolve_training_dataset(self, identifier):
        path = self.dataset_path(identifier)
        if self.data_root not in path.parents:
            return self.assets.training_path(identifier)
        if self.capture is not None:
            raise ValueError('平台仍在采集，先保存并退出后再训练。')
        return path

    def dataset_path(self, relative):
        if str(relative).startswith('external-') or Path(relative).is_absolute():
            return self.assets.resolve(relative)
        path = (self.data_root / relative).resolve()
        if self.data_root not in path.parents or not path.is_dir():
            raise ValueError('Dataset must be an existing directory inside the configured data root.')
        return path

    def audit(self, relative):
        from diffusion_policy.common.gello_zarr import inspect_dataset
        path = self.dataset_path(relative)
        if self.data_root not in path.parents:
            return self.assets.audit(relative)
        return inspect_dataset(path, check_chunks=True)

    def datasets(self):
        from diffusion_policy.common.gello_zarr import inspect_dataset
        result = []
        for path in sorted(self.data_root.glob('**/*.zarr'))[:100]:
            if (path / '.zgroup').is_file():
                try:
                    report = inspect_dataset(path, scan_values=False)
                    result.append(dict(id=path.relative_to(self.data_root).as_posix(),
                                       name=path.stem, source='hardware/local', **report))
                except Exception as exc:
                    result.append(dict(id=path.relative_to(self.data_root).as_posix(), valid=False,
                                       name=path.stem, errors=[str(exc)], arrays={}))
        return result + self.assets.list()

    def start_capture(self, tactile=False, require_camera_check=False, expected_cameras=None):
        with self.lock:
            if self.capture is not None:
                raise ValueError('Save or discard the current capture first.')
            preflight = self.camera_guard.preflight(require_camera_check,expected_cameras)
            self.capture = dict(id='CAP-' + uuid.uuid4().hex[:10], tactile=bool(tactile),
                                frames=[], bytes=0, error=None, started_at=time.time(), camera_preflight=preflight)
            return self.capture_status()

    def capture_status(self):
        with self.lock:
            if not self.capture:
                return dict(active=False, frames=0)
            return {k: v for k, v in dict(self.capture, active=True,
                                         frame_count=len(self.capture['frames'])).items() if k != 'frames'}

    def receive_gello(self, payload):
        import cv2
        timestamp = float(payload['timestamp'])
        if abs(time.time() - timestamp) > 5:
            raise ValueError('GELLO timestamp is stale or uses a different clock; use workstation Unix time.')
        joints, action = np.asarray(payload['joints'], dtype=np.float32), np.asarray(payload['action'], dtype=np.float32)
        if joints.shape != (7,) or action.shape != (7,) or not np.isfinite(np.r_[joints, action]).all():
            raise ValueError('GELLO joints and command action must be finite 7D vectors.')
        images = {}
        for key, encoded in payload.get('images', {}).items():
            if key not in ('camera_0', 'camera_1', 'camera_2'):
                raise ValueError('Unexpected camera name.')
            raw = np.frombuffer(base64.b64decode(encoded, validate=True), dtype=np.uint8)
            image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
            if image is None or image.shape != (480, 640, 3):
                raise ValueError('Expected lossless 640x480 PNG camera frames.')
            images[key] = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        if not images:
            raise ValueError('GELLO frames must include RGB observations.')
        identities = payload.get('camera_ids', {})
        intrinsics = payload.get('camera_intrinsics', {})
        for key, image in images.items():
            self.hub.publish_camera(key, image, timestamp, identities.get(key, 'gello-role:'+key), intrinsics.get(key))
        self.hub.publish('gello', dict(timestamp=timestamp, seq=int(payload['seq']),
                                       joints=joints.tolist(), action=action.tolist(), dropped=int(payload.get('dropped', 0))))
        preview = next(iter(images.values()))
        ok, jpeg = cv2.imencode('.jpg', cv2.cvtColor(preview, cv2.COLOR_RGB2BGR))
        if ok:
            self.hub.publish('rgb', dict(timestamp=timestamp, camera_id='GELLO RGB',
                                        image='data:image/jpeg;base64,' + base64.b64encode(jpeg).decode()))
        with self.lock:
            if self.capture is None:
                return dict(recorded=False)
            cap = self.capture
            preflight = cap['camera_preflight']
            if preflight['passed']:
                checked = {c['key']: c for c in preflight['checks']}
                if set(images) != set(checked):
                    raise ValueError('Capture camera set changed after preflight; stop and check every camera again.')
                for key in images:
                    profile = self.camera_guard.profiles[key]
                    if self.hub.camera_frames[key]['identity'] != profile['identity']:
                        raise ValueError('Camera identity changed during capture; recheck device bindings.')
            frame = dict(robot_joint=joints, action=action, timestamp=np.array(timestamp),
                         source_seq=np.array(int(payload['seq'])), **images)
            if cap['tactile']:
                touch = self.hub.nearest_tactile(timestamp)
                frame['tactile'] = np.asarray(touch['points'], dtype=np.float32).reshape(-1)
                frame['tactile_resultant'] = np.asarray(touch['resultant_n'], dtype=np.float32)
                frame['tactile_timestamp'] = np.array(touch['timestamp'])
            frames = cap['frames']
            if frames:
                previous = frames[-1]
                if set(frame) != set(previous) or any(frame[k].shape != previous[k].shape for k in frame):
                    raise ValueError('Modalities or shapes changed within an episode.')
                if timestamp <= float(previous['timestamp']) or int(frame['source_seq']) <= int(previous['source_seq']):
                    raise ValueError('GELLO timestamp/sequence must increase.')
            size = sum(x.nbytes for x in frame.values())
            if cap['bytes'] + size > 256 * 1024 * 1024:
                cap['error'] = 'Capture memory cap reached; save this episode before continuing.'
                raise ValueError(cap['error'])
            frames.append(frame)
            cap['bytes'] += size
            return dict(recorded=True, frame_count=len(frames))

    def save_capture(self):
        import zarr
        from numcodecs import Blosc
        from diffusion_policy.common.gello_zarr import validate_episode, require_valid
        with self.lock:
            cap = self.capture
            if not cap or not cap['frames']:
                raise ValueError('No hardware frames have been recorded.')
            frames = cap['frames']
            episode = {k: np.stack([f[k] for f in frames]) for k in frames[0]}
            validate_episode(episode)
            path = self.data_root / 'captures' / (cap['id'] + '.zarr')
            path.parent.mkdir(exist_ok=True)
            root = zarr.open_group(str(path), mode='w-')
            data = root.create_group('data')
            for key, array in episode.items():
                data.array(key, array, chunks=(1,) + array.shape[1:],
                           compressor=Blosc(cname='lz4', clevel=1))
            root.create_group('meta').array('episode_ends', np.array([len(frames)], dtype=np.int64))
            root.attrs.update(source='GELLO/UR5e', simulated=False, tactile_model='PaXini CP-M3025-Core' if cap['tactile'] else None,
                              tactile_unit='counts', tactile_resultant_unit='N', alignment='latest preceding tactile frame <=200ms',
                              action_semantics='six_joint_position_targets_plus_gripper')
            root.attrs['camera_preflight'] = cap['camera_preflight']
            require_valid(path, check_chunks=True)
            self.capture = None
            return dict(id=path.relative_to(self.data_root).as_posix(), frames=len(frames))

    def combine(self, names):
        """Create a NEW multi-episode training dataset; source stores are read-only."""
        import zarr
        from diffusion_policy.common.gello_zarr import require_valid
        from diffusion_policy.common.replay_buffer import ReplayBuffer
        if len(set(names)) < 2:
            raise ValueError('Select at least two distinct datasets for a train/validation split.')
        paths = [self.resolve_training_dataset(name) for name in names]
        for path in paths:
            require_valid(path, check_chunks=True)
        roots = [zarr.open_group(str(p), mode='r') for p in paths]
        schema = {k: (a.shape[1:], a.dtype) for k, a in roots[0]['data'].arrays()}
        if any({k: (a.shape[1:], a.dtype) for k, a in r['data'].arrays()} != schema for r in roots[1:]):
            raise ValueError('Selected datasets have different modalities, shapes or dtypes.')
        path = self.data_root / ('merged-' + uuid.uuid4().hex[:10] + '.zarr')
        buffer = ReplayBuffer.create_empty_zarr(root=zarr.open_group(str(path), mode='w-'))
        for root in roots:
            start = 0
            for end in root['meta/episode_ends'][:]:
                buffer.add_episode({k: root['data'][k][start:end] for k in schema})
                start = end
        buffer.root.attrs.update(source_datasets=names, simulated=False)
        buffer.root.attrs['camera_preflights'] = [r.attrs.get('camera_preflight') for r in roots]
        require_valid(path, check_chunks=True)
        return dict(id=path.relative_to(self.data_root).as_posix())

    def start_training(self, request):
        from hydra import compose, initialize_config_dir
        from omegaconf import OmegaConf
        training_path = self.resolve_training_dataset(request['dataset'])
        report = self.audit(request['dataset'])
        if not report['valid']:
            raise ValueError('; '.join(report['errors']))
        if report['n_episodes'] < 2:
            raise ValueError('At least two complete episodes are required; combine captures first.')
        modality = request.get('modality', 'rgb')
        if modality not in ('rgb', 'rgb_tactile'):
            raise ValueError('Unsupported DP modality.')
        arrays = report['arrays']
        obs = {}
        for key in ('camera_0', 'camera_1', 'camera_2', 'image0', 'image1'):
            if key in arrays:
                shape = arrays[key]['shape']
                obs[key] = dict(shape=[shape[3], shape[1], shape[2]], type='rgb')
        if not obs:
            raise ValueError('No RGB arrays in dataset.')
        state = 'robot_joint' if 'robot_joint' in arrays else 'agent_pos'
        if state not in arrays or arrays[state]['shape'][1:] != [7]:
            raise ValueError('Missing 7D robot state.')
        obs[state] = dict(shape=[7], type='low_dim')
        if modality == 'rgb_tactile':
            for key, dim in [('tactile', 231), ('tactile_resultant', 3)]:
                if key not in arrays or arrays[key]['shape'][1:] != [dim]:
                    raise ValueError('RGB+tactile training requires measured M3025 231D points and 3D resultant.')
                obs[key] = dict(shape=[dim], type='low_dim')
        device = request.get('device', 'cpu')
        if device not in ('cpu', 'cuda:0'):
            raise ValueError('Only cpu/cuda:0 are supported.')
        epochs = int(request.get('epochs', 100))
        if not 1 <= epochs <= 100000:
            raise ValueError('Invalid epochs.')
        with self.lock:
            if any(j['status'] == 'running' for j in self.jobs.values()):
                raise ValueError('A training job is already running on this edge service.')
            identifier = 'DP-' + uuid.uuid4().hex[:10]
            run = self.data_root / 'runs' / identifier
            run.mkdir(parents=True)
            OmegaConf.register_new_resolver('eval', eval, replace=True)
            with initialize_config_dir(version_base=None, config_dir=str(self.workspace/'diffusion_policy/config')):
                cfg = compose(config_name='train_gello_dp')
            cfg.task.shape_meta = dict(obs=obs, action=dict(shape=[7]))
            cfg.task.dataset.dataset_path = str(training_path)
            cfg.training.device, cfg.training.num_epochs = device, epochs
            cfg.dataloader.num_workers = cfg.val_dataloader.num_workers = 0
            cfg.dataloader.batch_size = cfg.val_dataloader.batch_size = 2
            cfg.logging.mode = 'disabled'
            if request.get('smoke', False):
                cfg.training.num_epochs = 1
                cfg.training.max_train_steps = cfg.training.max_val_steps = 1
                cfg.policy.num_inference_steps = 2
            OmegaConf.save(cfg, run/'job.yaml')
            command = [sys.executable, str(self.workspace/'train.py'), '--config-dir=' + str(run),
                       '--config-name=job', 'hydra.run.dir=' + json.dumps(str(run/'output'))]
            job = dict(id=identifier, status='running', dataset=request['dataset'], modality=modality,
                       device=device, smoke=bool(request.get('smoke')), log=[], started_at=time.time())
            self.jobs[identifier] = job
            env = dict(os.environ)
            if device == 'cpu':
                env.setdefault('OMP_NUM_THREADS', '2')
                env.setdefault('MKL_NUM_THREADS', '2')
            try:
                process = subprocess.Popen(command, cwd=self.workspace, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                           env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except Exception as exc:
                job['status'], job['log'] = 'failed', [str(exc)]
                raise
            self.processes[identifier] = process

            def monitor():
                with (run/'process.log').open('w', encoding='utf-8') as log:
                    for line in process.stdout:
                        log.write(line)
                        log.flush()
                        with self.lock:
                            job['log'] = (job['log'] + [line.rstrip()])[-80:]
                code = process.wait()
                with self.lock:
                    job['exit_code'] = code
                    job['status'] = 'completed' if code == 0 else ('cancelled' if job.get('cancel_requested') else 'failed')
                    job['finished_at'] = time.time()
                    checkpoint = run/'output/checkpoints/latest.ckpt'
                    job['checkpoint'] = str(checkpoint) if checkpoint.exists() else None
            threading.Thread(target=monitor, daemon=True).start()
            return job

    def cancel_training(self, identifier):
        with self.lock:
            job = self.jobs[identifier]
            if job['status'] == 'running':
                job['cancel_requested'] = True
                self.processes[identifier].terminate()
            return job
