"""Read-only interpretation of the unmodified collector's prompts and receipts.

Only narrowly identified interactive prompts enable a key. A mention of ALIGN in
help text is never evidence of an alignment request. Unknown revisions stay unknown.
"""
import json
import hashlib
import re
import time
from pathlib import Path


ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
DEFAULT_PROMPTS = {
    'startup': [r'(?im)^[ \t]*(?:\[[^\]\r\n]{1,50}\][ \t]*)?press[ \t]+(?:\[?enter\]?|return)[ \t]+to[ \t]+(?:continue|start|proceed)[^\r\n]{0,180}[:：>][ \t]*',
                r'(?m)^[ \t]*(?:请|确认[^\r\n]{0,50}后，?)?按(?:下)?[ \t]*(?:Enter|ENTER|回车)[^\r\n]{0,120}(?:继续|启动)[^\r\n]{0,30}[:：>][ \t]*',
                # LIVE-R3 launcher: require the complete consent prompt, not its help banner.
                r'(?im)^[ \t]*确认已单独同意本次[^\r\n]{1,80}夹爪参数[、，, \t]+现场已准备好[，, \t]+按[ \t]*Enter[ \t]*启动[（(]其他输入取消[）)][ \t]*[:：>][ \t]*'],
    'align': [r'(?im)^[ \t]*(?:\[[^\]\r\n]{1,50}\][ \t]*)?(?:type|enter|input)[ \t]+[\'\"\[]?ALIGN[\'\"\]]?[ \t]+(?:to[ \t]+)?(?:confirm|continue|start|execute)[^\r\n]{0,180}[:：>][ \t]*',
              r'(?m)^[ \t]*(?:请)?输入[ \t]*[\'\"\[]?ALIGN[\'\"\]]?[^\r\n]{0,120}(?:确认|对齐|继续)[^\r\n]{0,30}[:：>][ \t]*'],
}


def allowed_actions(session):
    if not session.get('running') or not session.get('controllable', False):
        return []
    phase = session.get('phase')
    if session.get('command_pending'):
        return ['quit'] if session.get('in_main_loop') and phase != 'exiting' else []
    if phase == 'startup_wait':
        return ['startup']
    if phase == 'waiting_align' and session.get('align_prompt'):
        return ['align']
    if phase == 'alignment_review' and session.get('alignment_observed'):
        return ['ready']
    if phase == 'ready' and session.get('aligned'):
        return ['start', 'delete_last', 'quit']
    if phase == 'recording' and session.get('aligned'):
        return ['finish', 'discard', 'quit']
    return ['quit'] if session.get('in_main_loop') and phase != 'exiting' else []


class CaptureObserver:
    def __init__(self, session, exact_prompts=None):
        self.session = session
        self.buffer = ''
        self.base = 0
        self.seen_prompt_end = -1
        self.terminal_line_end = 0
        self.event_signatures = set()
        self.patterns = {k: [re.compile(v) for v in values] for k, values in DEFAULT_PROMPTS.items()}
        # Optional platform-owned literal prompts, not injected collector code.
        for kind, value in (exact_prompts or {}).items():
            if kind not in self.patterns or not isinstance(value, str) or not 3 <= len(value) <= 512 or '\n' in value:
                raise ValueError('Invalid literal capture prompt configuration')
            self.patterns[kind] = [re.compile(r'(?m)^' + re.escape(value))]

    def terminal(self, chunk):
        clean = ANSI.sub('', chunk).replace('\r', '\n')
        self.buffer += clean
        if len(self.buffer) > 32768:
            excess = len(self.buffer) - 32768
            self.base += excess
            self.buffer = self.buffer[excess:]
        events = []
        for kind, patterns in self.patterns.items():
            for pattern in patterns:
                for match in pattern.finditer(self.buffer):
                    # A trailing space delivered in the next PTY chunk is still
                    # the same prompt, even after its Enter was already sent.
                    absolute_end = self.base + match.start() + len(match.group(0).rstrip(' \t'))
                    if absolute_end > self.seen_prompt_end:
                        events.append((absolute_end, kind, match.group(0)))
        for match in re.finditer(r'(?m)^\{[^\r\n]+\}[ \t]*\n', self.buffer):
            absolute_end = self.base + match.end()
            if absolute_end > self.terminal_line_end:
                events.append((absolute_end, 'receipt', match.group(0)))
        # Interleaved receipts and prompts in one PTY read retain their byte order.
        # In particular a rejected ALIGN followed by a new prompt must end waiting.
        for end, kind, text in sorted(events):
            if kind == 'receipt':
                if end > self.terminal_line_end:
                    self.terminal_line_end = end
                    try:
                        self.event(json.loads(text))
                    except ValueError:
                        pass
                continue
            if end <= self.seen_prompt_end:
                continue
            self.seen_prompt_end = end
            s = self.session
            if kind == 'startup' and s.get('phase') in ('initializing', 'unknown', 'startup_wait'):
                s.update(phase='startup_wait', startup_prompt=True, prompt_text=text, command_pending=None)
            elif kind == 'align' and s.get('startup_confirmed') and not s.get('in_main_loop'):
                s.update(phase='waiting_align', align_prompt=True, startup_prompt=False,
                         alignment_observed=False, aligned=False, prompt_text=text, command_pending=None)
        # Paths must be explicitly named by the collector; never synthesize timestamp directories.
        for match in re.finditer(r'(?im)^[ \t]*(?:\[[^\]\r\n]*\][ \t]*)?(output(?:[_ ](?:dir|directory|root))?|run[_ ](?:dir|directory)|session[_ ](?:dir|directory)|日志目录|输出目录)[ \t]*[:=：][ \t]*[\'\"]?(/[^\r\n\'\"]+)', self.buffer):
            self.session['output_dir'] = match.group(2).strip()
        for match in re.finditer(r'(?im)^[ \t]*(?:dataset(?:[_ ]path)?|zarr(?:[_ ]path)?)[ \t]*[:=：][ \t]*[\'\"]?(/[^\r\n\'\"]+\.zarr)', self.buffer):
            self.session['dataset_path'] = match.group(1).strip()

    def event(self, event):
        if not isinstance(event, dict):
            return
        s = self.session
        name = event.get('event')
        relevant = {'alignment_confirmation_rejected', 'alignment_rejected', 'alignment_completed', 'recording_started',
                    'episode_finished', 'episode_deleted', 'observation_gap', 'writer_error', 'save_failed', 'sensor_error', 'error', 'finished'}
        if name not in relevant:
            return
        signature = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).digest()
        if signature in self.event_signatures:
            return
        if len(self.event_signatures) > 4096:
            self.event_signatures.clear()
        self.event_signatures.add(signature)
        s['last_event'] = name
        s['last_receipt_at'] = time.time()
        if name in ('alignment_confirmation_rejected', 'alignment_rejected'):
            s.update(aligned=False, alignment_observed=False, align_prompt=False,
                     phase='unknown', command_pending=None, message='对齐确认被拒绝，等待新的实际 ALIGN 提示')
        elif name == 'alignment_completed':
            if not s.get('alignment_request_sent'):
                return
            s.update(alignment_observed=True, align_prompt=False, phase='alignment_review', command_pending=None,
                     message='脚本报告对齐完成；等待操作员现场确认')
        elif name == 'recording_started':
            s.update(phase='recording', command_pending=None, recording_observed=True,
                     current_episode=event.get('episode_id'), message='录制中，原程序持续跟随')
        elif name == 'episode_finished':
            reason = event.get('reason', 'unknown')
            queued = event.get('queued') is True
            state = 'queued' if queued else 'not_saved' if reason in ('too_short', 'operator_discard', 'timing_quality', 'observation_gap', 'sensor_fault') or event.get('saved') is False else 'unconfirmed'
            s.update(phase='ready' if s.get('aligned') else 'unknown', command_pending=None,
                     recording_observed=False, save_state=state,
                     episode_result={k: event.get(k) for k in ('episode_id', 'dataset_episode', 'rows', 'reason', 'operator_complete', 'queued', 'saved')},
                     message='本条已提交保存队列，尚未确认落盘；原程序持续跟随' if queued else '本条结束；保存结果待核对，原程序持续跟随')
        elif name == 'episode_deleted':
            s.update(phase='ready' if s.get('aligned') else 'unknown', command_pending=None,
                     save_state='deletion_reported', message='脚本报告删除完成；原程序持续跟随',
                     delete_result={k: event.get(k) for k in ('operation', 'dataset_episode', 'rows', 'remaining')})
        elif name == 'observation_gap':
            s.update(quality_warning='observation_gap', save_state='unconfirmed', message='检测到观测间断，片段完整性待核对')
        elif name in ('writer_error', 'save_failed', 'sensor_error', 'error'):
            s.update(save_state='failed', quality_warning=str(name), message='采集或保存异常，请检查原程序日志')
        elif name == 'finished' and isinstance(event.get('result'), dict):
            self.result(event['result'])
        for key in ('output_dir', 'dataset_path'):
            if isinstance(event.get(key), str) and event[key].startswith('/'):
                s[key] = event[key]
        if s.get('quit_requested') and s.get('running'):
            s['phase'] = 'exiting'

    def result(self, result):
        s = self.session
        # Keep bounded summaries; full source result remains untouched in its directory.
        keys = ('status', 'fault', 'saved_rows', 'saved_episodes', 'writer_stopped', 'dataset',
                'dataset_sha256', 'operator_confirmation_pending', 'production_training_qualified')
        s['result'] = {k: result[k] for k in keys if k in result}
        if isinstance(result.get('dataset'), str):
            s['dataset_path'] = result['dataset']
        s['save_state'] = 'writer_stopped_pending_audit' if result.get('writer_stopped') is True and isinstance(result.get('saved_rows'), int) else 'unconfirmed'
        s['message'] = '原程序结果已读取，仍需数据完整性检查；不能仅凭返回码判定有效'
        s['command_pending'] = None


class ReadOnlyReceipts:
    """Incremental bounded JSONL reads and sidecar summaries; called off the PTY reader."""
    def __init__(self, observer, roots):
        self.observer = observer
        self.roots = tuple(Path(p).resolve() for p in roots)
        self.offset = 0
        self.partial = b''
        self.source = None
        self.result_stamp = None
        self.manifest_stamp = None

    def allowed(self, path):
        path = Path(path).expanduser().resolve()
        if not any(path == root or root in path.parents for root in self.roots):
            raise ValueError('采集输出不在已配置外部数据根目录内')
        return path

    def poll(self):
        raw = self.observer.session.get('output_dir')
        if not raw:
            return
        directory = self.allowed(raw)
        manifest_file = self.allowed(directory / 'run_manifest.json')
        if manifest_file.is_file():
            stat = manifest_file.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if stamp != self.manifest_stamp and stat.st_size <= 1024 * 1024 and stat.st_mtime >= self.observer.session.get('started_at', 0):
                try:
                    manifest = json.loads(manifest_file.read_text('utf-8'))
                    if isinstance(manifest, dict):
                        keys = ('revision', 'entry_script', 'protocol', 'backend', 'interpreter', 'display',
                                'preview_enabled', 'resume_dataset', 'session_id', 'camera_serials', 'robot_ip')
                        self.observer.session['run_manifest'] = {k: manifest[k] for k in keys if k in manifest}
                        self.manifest_stamp = stamp
                except (ValueError, UnicodeDecodeError):
                    pass
        source = self.allowed(directory / 'capture.jsonl')
        if source != self.source:
            self.source, self.partial = source, b''
            self.offset = self.observer.session.get('prior_receipt_sizes', {}).get(str(source), 0)
        if source.is_file():
            if source.stat().st_size < self.offset:
                self.offset, self.partial = 0, b''
                self.observer.session['receipt_warning'] = 'capture.jsonl was truncated; state may be incomplete'
            with source.open('rb') as stream:
                stream.seek(self.offset)
                block = stream.read(1024 * 1024)
                self.offset = stream.tell()
            lines = (self.partial + block).split(b'\n')
            self.partial = lines.pop()
            if len(self.partial) > 8 * 1024 * 1024:
                self.partial = b''
                self.observer.session['receipt_warning'] = 'oversized receipt line skipped'
            for line in lines:
                try:
                    self.observer.event(json.loads(line))
                except (ValueError, UnicodeDecodeError):
                    self.observer.session['receipt_warning'] = '部分日志不可解析；请核对最终 result.json'
        result_file = self.allowed(directory / 'result.json')
        if result_file.is_file():
            stat = result_file.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if stamp != self.result_stamp and stat.st_size <= 8 * 1024 * 1024 and stat.st_mtime >= self.observer.session.get('started_at', 0):
                try:
                    result = json.loads(result_file.read_text('utf-8'))
                    if isinstance(result, dict):
                        self.observer.result(result)
                        self.result_stamp = stamp
                except (ValueError, UnicodeDecodeError):
                    pass  # File may be mid-write. Never repair or rewrite it.
        dataset = self.observer.session.get('dataset_path')
        if dataset:
            self.allowed(dataset)
