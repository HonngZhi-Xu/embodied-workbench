"""Site-specific GELLO terminal control and external robodiff training."""
import codecs
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid


DEFAULTS = {
    'capture_script': '/home/zaq/gello_software/experiments/start_live_follow_local.sh',
    'project': '/home/zaq/diffusion_policy',
    'conda_env': 'robodiff',
    'dataset': '/home/zaq/runtime_logs/live_follow_capture/20261111T061114Z_real/manual_task/replay_buffer.zarr',
    'output_root': '/home/zaq/diffusion_policy/data/outputs/dataset_20261111/vision_only_r1_60ep',
    'n_obs_steps': 1, 'resize': [240, 320], 'crop': [216, 288],
    'random_crop': True, 'lr': 5e-5, 'weight_decay': 1e-4,
    'batch_size': 8, 'epochs': 60, 'checkpoint_every': 5,
    'monitor_key': 'train_loss', 'topk': 3, 'exp_name': 'vision_only',
}
OBS_KEYS = ['camera_0', 'camera_1', 'camera_2', 'robot_joint', 'gripper_position']


def training_plan(request):
    values = dict(DEFAULTS, **{k: v for k, v in request.items() if k in DEFAULTS})
    for name in ('project', 'dataset', 'output_root'):
        path = str(values[name])
        if not path.startswith('/') or any(c in path for c in ('\x00', '\n', '\r')):
            raise ValueError(name + ' must be an absolute Linux path.')
        values[name] = path
    for name in ('conda_env', 'exp_name', 'monitor_key'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', str(values[name])):
            raise ValueError('Invalid ' + name)
    for name in ('n_obs_steps', 'batch_size', 'epochs', 'checkpoint_every', 'topk'):
        number = values[name]
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 100000:
            raise ValueError(name + ' must be a positive integer.')
    for name in ('lr', 'weight_decay'):
        number = float(values[name])
        if not math.isfinite(number) or number < 0 or (name == 'lr' and number == 0):
            raise ValueError('Invalid ' + name)
        values[name] = number
    for name in ('resize', 'crop'):
        shape = values[name]
        if not isinstance(shape, list) or len(shape) != 2 or any(type(v) is not int or not 1 <= v <= 4096 for v in shape):
            raise ValueError(name + ' must be [height,width].')
    if any(c > r for c, r in zip(values['crop'], values['resize'])):
        raise ValueError('Crop cannot exceed resize dimensions.')
    if type(values['random_crop']) is not bool:
        raise ValueError('random_crop must be boolean.')
    compact = lambda x: json.dumps(x, separators=(',', ':'))
    overrides = [
        'dataset_path=' + json.dumps(values['dataset']), '~shape_meta.obs.tactile',
        'task.dataset.obs_keys=[' + ','.join(OBS_KEYS) + ']',
        'n_obs_steps=' + str(values['n_obs_steps']),
        'policy.obs_encoder.resize_shape=' + compact(values['resize']),
        'policy.obs_encoder.crop_shape=' + compact(values['crop']),
        'policy.obs_encoder.random_crop=' + str(values['random_crop']).lower(),
        'optimizer.lr=' + str(values['lr']), 'optimizer.weight_decay=' + str(values['weight_decay']),
        'dataloader.batch_size=' + str(values['batch_size']), 'training.num_epochs=' + str(values['epochs']),
        'training.resume=false', 'training.checkpoint_every=' + str(values['checkpoint_every']),
        'checkpoint.topk.monitor_key=' + values['monitor_key'], 'checkpoint.topk.mode=min',
        'checkpoint.topk.k=' + str(values['topk']), 'exp_name=' + values['exp_name'],
    ]
    output_template = values['output_root'].rstrip('/') + '/${now:%Y.%m.%d}/${now:%H.%M.%S}'
    shell_lines = [
        'conda activate ' + shlex.quote(values['conda_env']), 'cd ' + shlex.quote(values['project']),
        'python train.py --config-name=train_gello_live_image_20hz \\\n  ' + ' \\\n  '.join(shlex.quote(v) for v in overrides + ['hydra.run.dir=' + output_template]),
    ]
    return dict(values=values, overrides=overrides, command='\n'.join(shell_lines))


def terminate_group(process):
    if process.poll() is not None:
        return
    if os.name == 'posix':
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()


class URWorkflow:
    def __init__(self, core):
        self.core = core
        self.lock = threading.RLock()
        self.capture = None
        self.capture_process = None
        self.capture_fd = None
        self.training_processes = {}

    def status(self):
        with self.core.lock, self.lock:
            return dict(capture=None if self.capture is None else dict(self.capture),
                        jobs=[dict(j) for j in self.core.jobs.values() if j.get('runner') == 'robodiff'])

    def start_capture(self, script):
        if os.name != 'posix':
            raise ValueError('现场采集终端需要在 Linux 工位运行本地服务。')
        script = Path(script).expanduser().resolve()
        if script.name != 'start_live_follow_local.sh' or not script.is_file():
            raise ValueError('请选择现场已有的 start_live_follow_local.sh。')
        import pty
        with self.core.lock, self.lock:
            if any(j['status'] == 'running' for j in self.core.jobs.values()):
                raise ValueError('先结束当前训练，再启动现场采集。')
            if self.core.capture is not None:
                raise ValueError('先结束平台旁路录制。')
            if self.capture_process is not None and self.capture_process.poll() is None:
                raise ValueError('现场采集进程已经运行。')
            master, slave = pty.openpty()
            try:
                process = subprocess.Popen(['bash', str(script), '--no-tactile'], cwd=script.parent.parent,
                                           stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            except Exception:
                os.close(master)
                raise
            finally:
                os.close(slave)
            session = dict(id='UR-' + uuid.uuid4().hex[:10], running=True, aligned=False,
                           align_prompt=False, phase='waiting_align', log=[], last_command=None)
            self.capture, self.capture_process, self.capture_fd = session, process, master

            def read_terminal():
                decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
                tail = ''
                try:
                    while True:
                        raw = os.read(master, 4096)
                        if not raw:
                            break
                        chunk = decoder.decode(raw)
                        with self.lock:
                            session['log'] = (session['log'] + [chunk])[-100:]
                            tail = (tail + chunk)[-4096:]
                            session['align_prompt'] = session['align_prompt'] or 'ALIGN' in tail
                except OSError:
                    pass
                finally:
                    os.close(master)
                    code = process.wait()
                    with self.lock:
                        session.update(running=False, exit_code=code)
                        if self.capture is session:
                            self.capture_fd = None
            threading.Thread(target=read_terminal, daemon=True).start()
            return dict(session)

    def send_key(self, action, session_id, confirmed=False):
        keys = {'align': b'ALIGN\n', 'start': b's\n', 'finish': b'\n', 'discard': b'd\n', 'next': b'u\n'}
        if action not in keys and action != 'ready':
            raise ValueError('Unknown capture action.')
        with self.lock:
            session = self.capture
            if not session or session['id'] != session_id or not session['running'] or self.capture_fd is None or self.capture_process.poll() is not None:
                raise ValueError('现场终端已结束或会话已变化，请刷新。')
            if action == 'ready':
                if session['phase'] != 'align_sent' or not confirmed:
                    raise ValueError('先发送 ALIGN，再观察终端确认对齐完成。')
                session.update(aligned=True, phase='ready')
                return dict(session)
            if action == 'align':
                if not session['align_prompt'] or session['phase'] != 'waiting_align' or not confirmed:
                    raise ValueError('等待终端 ALIGN 提示，并确认现场姿态。')
            elif not session['aligned']:
                raise ValueError('先等待并手动确认 ALIGN。')
            if action == 'discard' and not confirmed:
                raise ValueError('删除当前条需要确认。')
            if action in ('finish', 'discard') and session['phase'] not in ('recording', 'next_sent'):
                raise ValueError('当前没有已发送的录制命令。')
            if action in ('start', 'next') and session['phase'] == 'recording':
                raise ValueError('先结束当前条。')
            if action == 'next' and session['phase'] != 'between':
                raise ValueError('结束当前条后再进入下一条。')
            os.write(self.capture_fd, keys[action])
            phases = {'align': 'align_sent', 'start': 'recording', 'finish': 'between',
                      'discard': 'between', 'next': 'next_sent'}
            session.update(last_command=action, phase=phases[action])
            return dict(session)

    def stop_capture(self, session_id):
        with self.lock:
            if not self.capture or self.capture['id'] != session_id:
                raise ValueError('会话已变化。')
            process = self.capture_process
        terminate_group(process)
        return self.status()

    def prepare_training(self, request):
        plan = training_plan(request)
        values = plan['values']
        checks = dict(linux=os.name == 'posix', conda=bool(shutil.which('conda')),
                      train_script=(Path(values['project'])/'train.py').is_file(),
                      config=any((Path(values['project'])/'diffusion_policy/config').glob('train_gello_live_image_20hz.y*ml')),
                      dataset=Path(values['dataset']).is_dir())
        plan['checks'] = checks
        plan['ready'] = all(checks.values())
        return plan

    def start_training(self, request):
        with self.core.lock:
            if self.core.capture is not None or (self.capture_process is not None and self.capture_process.poll() is None):
                raise ValueError('请先停止采集并确认数据集保存完成，再启动训练。')
            return self._start_training(request)

    def _start_training(self, request):
        plan = self.prepare_training(request)
        if not plan['ready']:
            raise ValueError('训练环境未就绪：' + ', '.join(k for k,v in plan['checks'].items() if not v))
        from diffusion_policy.common.gello_zarr import inspect_dataset
        report = inspect_dataset(plan['values']['dataset'], check_chunks=True)
        missing = [k for k in OBS_KEYS if k not in report['arrays']]
        if not report['valid'] or missing or report['n_episodes'] < 2:
            raise ValueError('数据检查失败：' + '; '.join(report['errors'] + missing +
                             (['至少两条完整 episode'] if report.get('n_episodes', 0) < 2 else [])))
        for key in OBS_KEYS[:3]:
            info = report['arrays'][key]
            if len(info['shape']) != 4 or info['shape'][-1] != 3 or info['dtype'] != 'uint8':
                raise ValueError(key + ' must be uint8 [T,H,W,3].')
        with self.core.lock:
            if any(j['status'] == 'running' for j in self.core.jobs.values()):
                raise ValueError('已有训练任务运行。')
            identifier = 'UR-DP-' + uuid.uuid4().hex[:10]
            values = plan['values']
            output = Path(values['output_root']) / datetime.now().strftime('%Y.%m.%d/%H.%M.%S') / identifier
            output.mkdir(parents=True, exist_ok=False)
            command = [shutil.which('conda'), 'run', '--no-capture-output', '-n', values['conda_env'],
                       'python', 'train.py', '--config-name=train_gello_live_image_20hz']
            command += plan['overrides'] + ['hydra.run.dir=' + json.dumps(str(output))]
            (output/'platform-request.json').write_text(json.dumps(dict(request=values, argv=command), indent=2), encoding='utf-8')
            process = subprocess.Popen(command, cwd=values['project'], stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                       start_new_session=True)
            job = dict(id=identifier, runner='robodiff', status='running', dataset=values['dataset'],
                       modality='rgb', device='现场配置', log=[], output_dir=str(output), started_at=time.time())
            self.core.jobs[identifier] = job
            self.training_processes[identifier] = process

            def monitor():
                failure = None
                try:
                    with (output/'platform-process.log').open('w', encoding='utf-8') as log:
                        for line in process.stdout:
                            log.write(line)
                            log.flush()
                            with self.core.lock:
                                job['log'] = (job['log'] + [line.rstrip()])[-80:]
                except Exception as exc:
                    failure = str(exc)
                    terminate_group(process)
                code = process.wait()
                with self.core.lock:
                    job.update(exit_code=code, finished_at=time.time(),
                               status='cancelled' if job.get('cancel_requested') else 'completed' if code == 0 and not failure else 'failed')
                    if failure:
                        job['log'].append('Log failure: ' + failure)
                    job['checkpoints'] = [str(p) for p in output.glob('checkpoints/*.ckpt')]
            threading.Thread(target=monitor, daemon=True).start()
            return dict(job)

    def cancel_training(self, identifier):
        with self.core.lock:
            job = self.core.jobs[identifier]
            if job['status'] != 'running':
                return dict(job)
            job['cancel_requested'] = True
        terminate_group(self.training_processes[identifier])
        return dict(job)

    def close(self):
        processes = list(self.training_processes.values())
        if self.capture_process is not None:
            processes.append(self.capture_process)
        for process in processes:
            terminate_group(process)
