"""Platform-owned PTY/session management; never edits or injects collector code."""
import codecs
import copy
import json
import os
from pathlib import Path
import queue
import re
import shlex
import signal
import subprocess
import threading
import time
import uuid

from edge_agent.capture_observer import CaptureObserver, ReadOnlyReceipts, allowed_actions


def capture_plan(script, capture_mode='rgb_tactile', resume_path=None, assets=None):
    capture_mode = 'rgb_only' if capture_mode == 'rgb' else capture_mode
    if capture_mode not in ('rgb_tactile', 'rgb_only'):
        raise ValueError('采集模式只能选择 rgb_tactile 或 rgb_only。')
    if not isinstance(script, str) or any(c in script for c in ('\0', '\n', '\r')):
        raise ValueError('Invalid capture script path')
    path = Path(script).expanduser().resolve()
    if path.name != 'start_live_follow_local.sh' or not path.is_file():
        raise ValueError('请选择现场已有的 start_live_follow_local.sh。')
    argv = ['bash', str(path)]
    if capture_mode == 'rgb_only':
        argv.append('--no-tactile')
    if resume_path:
        if assets is None:
            raise ValueError('请先配置外部数据目录，再选择接续数据。')
        resumed = assets.check_resume(resume_path, capture_mode)
        argv.extend(['--resume', str(resumed)])
        resume_path = str(resumed)
    return dict(argv=argv, command=shlex.join(argv), cwd=str(path.parent.parent),
                parameters=dict(script=str(path), capture_mode=capture_mode, resume_path=resume_path),
                checks=dict(linux=os.name == 'posix', script_exists=True,
                            display=bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))),
                environment='继承本地服务的 DISPLAY、XAUTHORITY、PATH 和 PYTHONPATH；脚本自身环境保持原样')


def pid_identity(pid):
    try:
        # Start time protects against PID reuse after a service restart on Linux.
        text = Path(f'/proc/{int(pid)}/stat').read_text()
        return text[text.rindex(')') + 2:].split()[19]
    except (OSError, ValueError, IndexError, TypeError):
        return None


class CaptureSessions:
    """Mixin used by URWorkflow; existing training methods stay independent."""
    def initialize_capture(self):
        self.capture = None
        self.capture_process = None
        self.capture_fd = None
        self.capture_observer = None
        self.start_requests = {}
        self.session_path = Path(self.core.data_root) / 'ur_sessions' / 'latest.json' if hasattr(self.core, 'data_root') else None
        if self.session_path and self.session_path.is_file():
            try:
                saved = json.loads(self.session_path.read_text('utf-8'))
                if isinstance(saved, dict) and saved.get('id'):
                    identity = pid_identity(saved.get('pid'))
                    alive = identity is not None and identity == saved.get('pid_identity')
                    saved.update(running=alive, controllable=False, managed=False,
                                 phase='uncontrolled' if alive else 'unknown', allowed_actions=[],
                                 message='服务已重启，旧 PTY 控制通道不可恢复。请在现场核对进程，平台仅只读展示。')
                    self.capture = saved
                    if saved.get('start_request_id'):
                        self.start_requests[saved['start_request_id']] = saved['id']
            except (OSError, ValueError, TypeError):
                pass

    def capture_plan(self, script, capture_mode='rgb_tactile', resume_path=None):
        return capture_plan(script, capture_mode, resume_path, getattr(self.core, 'assets', None))

    def _snapshot(self):
        if self.capture is None:
            return None
        snapshot = copy.deepcopy(self.capture)
        snapshot['allowed_actions'] = allowed_actions(snapshot)
        return snapshot

    def _persist_capture(self):
        if not self.session_path:
            return
        with self.lock:
            snapshot = self._snapshot()
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.session_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.session_path)

    @staticmethod
    def _request_id(request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', request_id):
            raise ValueError('控制请求需要唯一 request_id；请刷新页面后重试。')
        return request_id

    def start_capture(self, script, capture_mode='rgb_tactile', resume_path=None, request_id=None, confirmed_external_idle=False):
        request_id = self._request_id(request_id)
        if confirmed_external_idle is not True:
            raise ValueError('请先现场确认没有外部采集进程占用设备；平台不能接管外部终端。')
        if os.name != 'posix':
            raise ValueError('现场采集终端需要在 Linux 工位运行本地服务。')
        plan = self.capture_plan(script, capture_mode, resume_path)
        capture_mode = plan['parameters']['capture_mode']
        import pty
        with self.core.lock, self.lock:
            if request_id in self.start_requests:
                if self.capture and self.capture['id'] == self.start_requests[request_id]:
                    return self._snapshot()
                raise ValueError('旧启动请求不能启动新会话，请刷新。')
            if any(j['status'] == 'running' for j in self.core.jobs.values()):
                raise ValueError('先结束当前训练，再启动现场采集。')
            if getattr(self.core, 'capture', None) is not None:
                raise ValueError('先结束平台旁路录制。')
            if self.capture and self.capture.get('running'):
                if not self.capture.get('controllable'):
                    identity = pid_identity(self.capture.get('pid'))
                    if identity is not None and identity == self.capture.get('pid_identity'):
                        raise ValueError('旧采集进程仍在运行但不可控制，请先到现场处理，禁止重复启动。')
                else:
                    raise ValueError('平台托管采集会话已经运行。')
            if getattr(getattr(self.core, 'hub', None), 'workers', {}):
                raise ValueError('先断开平台独立设备连接，避免与原采集程序争用相机、机械臂或串口。')
            exact_prompts = {}
            prompt_path = Path(self.core.data_root) / 'capture-prompts.json' if hasattr(self.core, 'data_root') else None
            if prompt_path and prompt_path.is_file():
                exact_prompts = json.loads(prompt_path.read_text('utf-8'))
                if not isinstance(exact_prompts, dict):
                    raise ValueError('capture-prompts.json 必须是 startup/align 字面提示对象')
            session = dict(id='UR-' + uuid.uuid4().hex[:10], start_request_id=request_id, running=True, managed=True, controllable=True,
                           aligned=False, startup_prompt=False, startup_confirmed=False, align_prompt=False,
                           alignment_observed=False, phase='initializing', command_pending=None,
                           log=[], last_command=None, request_ids={}, started_at=time.time(),
                           capture_mode=capture_mode, parameters=plan['parameters'], argv=plan['argv'], command=plan['command'],
                           cwd=plan['cwd'], output_dir=None, dataset_path=None, save_state='unconfirmed',
                           message='等待脚本实际交互提示；未识别时保持状态未知，不自动发送按键。')
            # Existing receipt prefixes in resume mode belong to the old run.
            prior_sizes = {}
            if plan['parameters']['resume_path']:
                resumed = Path(plan['parameters']['resume_path'])
                for ancestor in list(resumed.parents)[:4]:
                    candidate = ancestor / 'capture.jsonl'
                    if candidate.is_file():
                        prior_sizes[str(candidate.resolve())] = candidate.stat().st_size
            session['prior_receipt_sizes'] = prior_sizes
            observer = CaptureObserver(session, exact_prompts)
            master, slave = pty.openpty()
            try:
                process = subprocess.Popen(plan['argv'], cwd=plan['cwd'], stdin=slave, stdout=slave,
                                           stderr=slave, start_new_session=True)
            except Exception:
                os.close(master)
                raise
            finally:
                os.close(slave)
            session.update(pid=process.pid, pid_identity=pid_identity(process.pid))
            self.capture, self.capture_process, self.capture_fd, self.capture_observer = session, process, master, observer
            self.start_requests[request_id] = session['id']
            chunks = queue.Queue(maxsize=512)
            reader_finished = threading.Event()
            self._persist_capture()

        def read_terminal():
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            try:
                while True:
                    raw = os.read(master, 4096)
                    if not raw:
                        break
                    chunk = decoder.decode(raw)
                    try:
                        chunks.put_nowait(chunk)
                    except queue.Full:
                        session['terminal_dropped_chunks'] = session.get('terminal_dropped_chunks', 0) + 1
            except OSError:
                pass
            finally:
                reader_finished.set()
                try:
                    os.close(master)
                except OSError:
                    pass

        def observe():
            roots = getattr(getattr(self.core, 'assets', None), 'roots', ())
            receipts = ReadOnlyReceipts(observer, roots)
            last_persist = 0
            last_asset_stamp = None
            log = None
            try:
                if self.session_path:
                    log = (self.session_path.parent / (session['id'] + '.console.log')).open('a', encoding='utf-8')
                while True:
                    for _ in range(256):
                        try:
                            chunk = chunks.get_nowait()
                        except queue.Empty:
                            break
                        if log:
                            log.write(chunk)
                        with self.lock:
                            session['log'] = (session['log'] + [chunk])[-100:]
                            observer.terminal(chunk)
                    if log:
                        log.flush()
                    try:
                        receipts.poll()
                        path = session.get('dataset_path')
                        asset_stamp = (path, session.get('running'), session.get('phase'), session.get('save_state'))
                        if path and hasattr(self.core, 'assets') and asset_stamp != last_asset_stamp:
                            self.core.assets.register(path, session=copy.deepcopy(session))
                            last_asset_stamp = asset_stamp
                    except (OSError, ValueError, KeyError) as exc:
                        session['receipt_warning'] = str(exc)
                    if reader_finished.is_set() and chunks.empty() and process.poll() is not None:
                        with self.lock:
                            code = process.poll()
                            session.update(running=False, controllable=False, exit_code=code, command_pending=None,
                                           finished_at=time.time(), phase='exited' if code == 0 else 'error',
                                           message='采集进程已退出，保存结果以原始回执及数据检查为准。')
                            if self.capture is session:
                                self.capture_fd = None
                        if path and hasattr(self.core, 'assets'):
                            self.core.assets.register(path, session=copy.deepcopy(session))
                        self._persist_capture()
                        break
                    if time.time() - last_persist >= 1:
                        self._persist_capture()
                        last_persist = time.time()
                    time.sleep(.1)
            except Exception as exc:
                with self.lock:
                    session.update(receipt_warning='平台只读观察异常：' + str(exc), phase='unknown')
            finally:
                if log:
                    log.close()
        threading.Thread(target=read_terminal, name='ur-pty-reader', daemon=True).start()
        threading.Thread(target=observe, name='ur-readonly-observer', daemon=True).start()
        return self._snapshot()

    def _controlled(self, session_id):
        session = self.capture
        if not session or session['id'] != session_id:
            raise ValueError('会话已变化，请刷新；旧页面不能操作新会话。')
        if not session.get('running') or not session.get('controllable') or self.capture_fd is None or self.capture_process is None or self.capture_process.poll() is not None:
            raise ValueError('现场终端已结束或 PTY 不可用；平台仅可只读查看。')
        return session

    def send_key(self, action, session_id, confirmed=False, request_id=None):
        request_id = self._request_id(request_id)
        aliases = {'startup_confirm': 'startup', 'next': 'start', 'save_exit': 'quit'}
        action = aliases.get(action, action)
        keys = {'startup': b'\n', 'align': b'ALIGN\n', 'start': b's\n', 'finish': b'\n',
                'discard': b'd\n', 'delete_last': b'u\n', 'quit': b'q\n'}
        with self.lock:
            session = self._controlled(session_id)
            requests = session.setdefault('request_ids', {})
            if request_id in requests:
                if requests[request_id] != action:
                    raise ValueError('request_id 已用于其他操作。')
                return self._snapshot()  # An HTTP retry never sends a second key.
            if action not in allowed_actions(session):
                raise ValueError('当前脚本状态不允许该操作；请等待原程序回执并刷新。')
            if action in ('startup', 'align', 'ready', 'discard', 'delete_last', 'quit') and confirmed is not True:
                raise ValueError('此操作需要现场或删除确认。')
            if action == 'ready':
                session.update(aligned=True, in_main_loop=True, phase='ready', command_pending=None,
                               message='操作员已确认；原程序持续跟随，当前未录制。')
            else:
                os.write(self.capture_fd, keys[action])
                phase = {'startup': 'initializing', 'align': 'aligning', 'quit': 'exiting'}.get(action, 'awaiting_receipt')
                session.update(last_command=action, phase=phase,
                               command_pending=dict(request_id=request_id, action=action, sent_at=time.time(), delivery='sent'),
                               message='按键已发送，等待原程序接受与执行回执；不能据此判定保存成功。')
                if action == 'startup':
                    session.update(startup_prompt=False, startup_confirmed=True)
                elif action == 'align':
                    session.update(align_prompt=False, alignment_request_sent=True)
                elif action == 'quit':
                    session['quit_requested'] = True
            requests[request_id] = action
            return self._snapshot()

    def stop_capture(self, session_id, method='interrupt', confirmed=False, request_id=None):
        request_id = self._request_id(request_id)
        if method not in ('interrupt', 'terminate', 'kill') or confirmed is not True:
            raise ValueError('请选择并明确确认中断、终止或强制终止；正常退出应发送 q。')
        with self.lock:
            session = self._controlled(session_id)
            key = 'signal:' + method
            if request_id in session['request_ids']:
                if session['request_ids'][request_id] != key:
                    raise ValueError('request_id 已用于其他操作。')
            else:
                sig = {'interrupt': signal.SIGINT, 'terminate': signal.SIGTERM, 'kill': getattr(signal, 'SIGKILL', 9)}[method]
                os.killpg(self.capture_process.pid, sig)
                session['request_ids'][request_id] = key
                session.update(phase='exiting', interruption=method,
                               command_pending=dict(request_id=request_id, action=key, sent_at=time.time(), delivery='signal_sent'),
                               message='信号已发送，等待原程序退出；未保存片段可能丢弃，不会自动升级为强制终止。')
        return self.status()

    def detach_capture(self):
        # Do not turn service shutdown into q, SIGTERM or SIGKILL. The old PTY is
        # not claimed as recoverable after restart; persisted PID identity is read-only.
        with self.lock:
            if self.capture and self.capture.get('running'):
                self.capture.update(controllable=False, managed=False, phase='uncontrolled',
                                    message='平台服务正在关闭，未发送保存或终止指令；需到现场核对原采集进程。')
                self._persist_capture()
