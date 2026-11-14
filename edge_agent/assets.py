"""Platform-owned index of read-only external GELLO datasets.

Registration never modifies original Zarr stores, manifests or receipts. A listed
asset is not a training approval: explicit inspection and final-run evidence are
required before a caller can obtain a training path.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import numpy as np

DEFAULT_EXTERNAL_ROOTS = ['/home/zaq/runtime_logs/live_follow_capture']
CAPTURE_MODES = ('rgb_tactile', 'rgb_only')


def _json_file(path, limit=8 * 1024 * 1024):
    """Bound reads of source evidence; never follow it outside the dataset root."""
    if not path.is_file() or path.stat().st_size > limit:
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (ValueError, OSError):
        return None


class ExternalAssets:
    def __init__(self, data_root, active_provider=None):
        self.data_root = Path(data_root).resolve()
        self.path = self.data_root / 'assets-index.json'
        self.lock = threading.RLock()
        self.roots = ()
        self.active_provider = active_provider or (lambda: None)
        self.entries = {}
        if self.path.is_file():
            saved = _json_file(self.path)
            if saved and isinstance(saved.get('assets'), dict):
                self.entries = saved['assets']
        self.configure(DEFAULT_EXTERNAL_ROOTS)

    def configure(self, roots):
        if not isinstance(roots, list) or not 1 <= len(roots) <= 16:
            raise ValueError('请配置 1–16 个外部数据根目录。')
        resolved = []
        for raw in roots:
            if not isinstance(raw, str) or len(raw) > 2048 or any(c in raw for c in '\0\n\r'):
                raise ValueError('Invalid external data root.')
            path = Path(raw).expanduser()
            if not path.is_absolute() and not raw.startswith('/'):
                raise ValueError('外部数据根目录必须使用绝对路径。')
            path = path.resolve()
            if path == Path(path.anchor):
                raise ValueError('不能把整个磁盘或系统根目录配置为数据根目录。')
            resolved.append(path)
        with self.lock:
            self.roots = tuple(dict.fromkeys(resolved))

    def _allowed(self, raw, require_store=True):
        path = Path(raw).expanduser().resolve()
        if not any(root in path.parents or path == root for root in self.roots):
            raise ValueError('数据路径超出已配置的外部根目录。')
        if require_store and (not path.is_dir() or not (path / '.zgroup').is_file() or path.suffix != '.zarr'):
            raise ValueError('请选择存在的 Zarr v2 replay_buffer.zarr 目录。')
        return path

    def _session(self):
        try:
            session = self.active_provider()
            return dict(session) if isinstance(session, dict) else {}
        except Exception:
            return {'running': True, 'observation_error': True}

    def is_active(self, path):
        session = self._session()
        if not session.get('running'):
            return False
        actual = session.get('dataset_path')
        output = session.get('output_dir')
        if actual:
            return Path(actual).expanduser().resolve() == Path(path).resolve()
        if output:
            out = Path(output).expanduser().resolve()
            return out == path or out in Path(path).resolve().parents
        # Until a live session reveals its actual output, no external training
        # or replay is allowed; the source may be a resumed dataset.
        return True

    @staticmethod
    def _identifier(path):
        return 'external-' + hashlib.sha256(str(path).encode()).hexdigest()[:20]

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'version': 1, 'assets': self.entries}, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def _check_links(self, path, full=False, camera=None):
        """Reject source links escaping configured roots before any Zarr read.

        Lightweight checks touch group/array metadata only. The explicit offline
        audit checks all file links before the inspector opens image chunks.
        """
        for group in (path, path / 'data', path / 'meta'):
            self._allowed(group, require_store=False)
            for name in ('.zgroup', '.zattrs'):
                self._allowed(group / name, require_store=False)
        if full:
            for directory, names, files in os.walk(path, followlinks=False):
                for name in names + files:
                    entry = Path(directory) / name
                    if entry.is_symlink():
                        self._allowed(entry, require_store=False)
        else:
            groups = [path / 'data' / camera] if camera else [path / 'data', path / 'meta']
            for group in groups:
                self._allowed(group, require_store=False)
                if camera:
                    candidates = [group / '.zarray', group / '.zattrs']
                else:
                    candidates = list(group.glob('*/.zarray')) + list(group.glob('*/.zattrs'))
                for item in candidates:
                    self._allowed(item, require_store=False)

    def _fingerprint(self, path):
        """Metadata freshness only, not a claim of a content hash verification."""
        self._check_links(path)
        values = []
        evidence = [path / '.zattrs', path.parent / 'result.json', path.parent / 'dataset_manifest.json',
                    path.parent.parent / 'result.json', path.parent.parent / 'dataset_manifest.json']
        evidence.extend(path.glob('meta/*/.zarray'))
        evidence.extend(path.glob('data/*/.zarray'))
        for item in sorted(set(evidence)):
            try:
                self._allowed(item, require_store=False)
            except ValueError:
                continue
            if item.is_file():
                stat = item.stat()
                values.append((str(item.relative_to(path.parent.parent)), stat.st_size, stat.st_mtime_ns))
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()

    def _metadata(self, path):
        self._check_links(path)
        arrays = {}
        for item in sorted((path / 'data').glob('*/.zarray')):
            meta = _json_file(item, 64 * 1024)
            if meta:
                arrays[item.parent.name] = {'shape': meta.get('shape', []), 'dtype': meta.get('dtype', '')}
        ends = _json_file(path / 'meta/episode_ends/.zarray', 64 * 1024) or {}
        count = (ends.get('shape') or [0])[0]
        action = arrays.get('action', {}).get('shape') or [0]
        return dict(arrays=arrays, n_episodes=int(count), n_frames=int(action[0]), n_steps=int(action[0]),
                    mode='rgb_tactile' if 'tactile' in arrays else 'rgb_only')

    def register(self, path, session=None):
        path = self._allowed(path)
        identifier = self._identifier(path)
        metadata = self._metadata(path)
        with self.lock:
            prior = dict(self.entries.get(identifier, {}))
            fingerprint = self._fingerprint(path)
            changed = prior.get('metadata_fingerprint') != fingerprint
            prior.update(id=identifier, name=path.parent.parent.name + '/' + path.parent.name,
                         path=str(path), source='external/gello', registered_at=prior.get('registered_at', time.time()),
                         metadata_fingerprint=fingerprint, **metadata)
            if changed or self.is_active(path):
                prior.update(training_ready=False, valid=False, integrity='collecting' if self.is_active(path) else 'unchecked',
                             errors=['采集中：只展示轻量元信息。'] if self.is_active(path) else ['尚未完成只读数据检查。'])
                prior.pop('checked_at', None)
            if session:
                prior['session_id'] = session.get('id')
                prior['capture_mode'] = session.get('capture_mode')
            self.entries[identifier] = prior
            self._save()
        return self.get(identifier)

    def get(self, identifier):
        with self.lock:
            item = dict(self.entries.get(identifier) or {})
        if not item:
            raise ValueError('外部数据尚未登记，请先扫描或登记。')
        path = self._allowed(item['path'])
        active = self.is_active(path)
        item['active'] = active
        if active:
            item.update(training_ready=False, integrity='collecting')
        elif item.get('checked_at') and item.get('metadata_fingerprint') != self._fingerprint(path):
            item.update(training_ready=False, integrity='changed', errors=['数据或结果已变化，请重新检查。'])
        return item

    def list(self):
        with self.lock:
            keys = list(self.entries)
        result = []
        for key in keys:
            try:
                result.append(self.get(key))
            except (ValueError, OSError):
                pass
        return sorted(result, key=lambda x: x['registered_at'], reverse=True)

    def scan(self):
        if self._session().get('running'):
            return self.list()
        found = 0
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in root.rglob('*.zarr'):
                if found >= 200:
                    return self.list()
                if (path / '.zgroup').is_file():
                    try:
                        self.register(path)
                        found += 1
                    except (ValueError, OSError):
                        continue
        return self.list()

    def resolve(self, identifier_or_path):
        with self.lock:
            entry = self.entries.get(str(identifier_or_path))
            if entry:
                return self._allowed(entry['path'])
            candidate = Path(identifier_or_path).expanduser().resolve()
            entry = next((item for item in self.entries.values() if item['path'] == str(candidate)), None)
        if entry is None:
            raise ValueError('数据未登记，不能直接访问任意绝对路径。')
        return self._allowed(entry['path'])

    def check_resume(self, path, mode):
        if mode not in CAPTURE_MODES:
            raise ValueError('Invalid capture mode.')
        path = self._allowed(path)
        if self.is_active(path):
            raise ValueError('接续数据仍被活跃会话写入。')
        metadata = self._metadata(path)
        if metadata['mode'] != mode:
            raise ValueError('接续数据的触觉模式与本次采集不兼容。')
        if not all(k in metadata['arrays'] for k in ('camera_0', 'camera_1', 'camera_2', 'action')):
            raise ValueError('接续数据缺少三路相机或 action 字段。')
        return path

    def _evidence(self, path, name):
        for parent in (path.parent, path.parent.parent):
            candidate = parent / name
            # Resolve the evidence itself to reject a link to an arbitrary file.
            resolved = candidate.resolve()
            if not any(root == resolved or root in resolved.parents for root in self.roots):
                continue
            value = _json_file(resolved)
            if value is not None:
                return value
        return None

    @staticmethod
    def _flags(root, key, ends):
        n = int(ends[-1])
        for prefix in ('meta/', 'data/'):
            if prefix + key in root:
                array = root[prefix + key]
                if len(array.shape) != 1:
                    return None
                for index in range((len(array) + array.chunks[0] - 1) // array.chunks[0]):
                    if array._chunk_key((index,)) not in array.chunk_store:
                        return None
                values = array[:]
                if len(values) == len(ends):
                    return [np.asarray([value]) for value in values]
                if len(values) == n:
                    starts = np.r_[0, ends[:-1]]
                    return [values[int(start):int(end)] for start, end in zip(starts, ends)]
        return None

    def audit(self, identifier):
        import zarr
        path = self.resolve(identifier)
        asset_id = self._identifier(path)
        if self.is_active(path):
            raise ValueError('数据正在采集中，禁止全量检查、回放和训练。')
        self._check_links(path, full=True)
        from diffusion_policy.common.gello_zarr import inspect_dataset
        before = self._fingerprint(path)
        root = zarr.open_group(str(path), mode='r')
        if 'meta/episode_ends' in root:
            boundaries = root['meta/episode_ends']
            for chunk_index in np.ndindex(*[(s + c - 1) // c for s, c in zip(boundaries.shape, boundaries.chunks)]):
                if boundaries._chunk_key(chunk_index) not in boundaries.chunk_store:
                    raise ValueError('meta/episode_ends 物理块缺失，禁止使用隐式填充值。')
        report = inspect_dataset(path, check_chunks=True)
        errors = list(report.get('errors', []))
        result = self._evidence(path, 'result.json')
        reason_names = root.attrs.get('episode_end_reason_codes', {})
        code_names = {}
        if isinstance(reason_names, dict):
            for reason_key, value in reason_names.items():
                if isinstance(value, int):
                    code_names[value] = str(reason_key)
                elif str(reason_key).lstrip('-').isdigit() and isinstance(value, str):
                    code_names[int(reason_key)] = value
        safe_reasons = {'operator_save', 'operator_quit', 'operator_stop'}
        result_episodes = {}
        if result and isinstance(result.get('episodes'), list):
            for item in result['episodes']:
                if isinstance(item, dict) and type(item.get('dataset_episode')) is int:
                    result_episodes[item['dataset_episode'] - 1] = item
        episodes = []
        ends = np.asarray(report.get('episode_ends', []), dtype=np.int64)
        complete = self._flags(root, 'episode_operator_complete', ends) if len(ends) else None
        timing = self._flags(root, 'episode_timing_quality_pass', ends) if len(ends) else None
        reason = self._flags(root, 'episode_end_reason_code', ends) if len(ends) else None
        for index in range(len(ends)):
            status = 'unknown'
            if complete is not None:
                status = 'complete' if np.all(complete[index] == 1) else 'incomplete'
            if timing is not None and not np.all(timing[index] == 1):
                status = 'incomplete'
            codes = np.unique(reason[index]).tolist() if reason is not None else []
            evidence = result_episodes.get(index, {})
            named_reasons = [code_names.get(code) for code in codes]
            reason_known = (bool(codes) and all(name in safe_reasons for name in named_reasons))
            # Some upstream stores expose numeric reason codes only. The final
            # result's explicit per-episode reason can identify their meaning;
            # queue acceptance alone is never considered a disk-write receipt.
            if (len(codes) == 1 and evidence.get('reason') in safe_reasons
                    and evidence.get('operator_complete') is True):
                reason_known = True
                named_reasons = [evidence['reason']]
            if not reason_known:
                status = 'unknown'
            episodes.append(dict(index=index, complete=status, end_reason_codes=codes,
                                 end_reasons=named_reasons, reason_known=reason_known,
                                 timing_quality='passed' if timing is not None and np.all(timing[index] == 1) else 'failed' if timing is not None else 'unknown'))
        if not episodes or any(e['complete'] != 'complete' for e in episodes):
            errors.append('包含不完整片段或缺少 episode_operator_complete / episode_end_reason_code，默认不用于训练。')
        if timing is None:
            errors.append('缺少 episode_timing_quality_pass，时序质量未知。')
        summary = {}
        if result:
            keys = ('status', 'production_training_qualified', 'operator_confirmation_pending', 'writer_stopped',
                    'saved_rows', 'saved_episodes', 'fault', 'dataset', 'dataset_sha256')
            summary = {k: result[k] for k in keys if k in result and not isinstance(result[k], (dict, list))}
            if result.get('writer_stopped') is not True:
                errors.append('原程序尚未确认写盘线程已结束。')
            if result.get('operator_confirmation_pending') is not False:
                errors.append('原程序结果仍待人工审核或审核状态未知。')
            if result.get('production_training_qualified') is not True:
                errors.append('原程序未批准 production_training_qualified，平台不绕过原回执。')
            if result.get('saved_rows') != report.get('n_steps') or result.get('saved_episodes') != report.get('n_episodes'):
                errors.append('最终结果的条数/帧数与实际 Zarr 不一致。')
            if result.get('fault') not in (None, '', 'none', False):
                errors.append('原程序报告故障：' + str(result.get('fault')))
            stated = result.get('dataset')
            if stated and Path(stated).resolve() != path:
                errors.append('result.json 对应的数据路径与当前数据集不一致。')
            if any(term in str(result.get('status', '')).lower() for term in ('pending', 'fail', 'error', 'fault')):
                errors.append('最终保存状态尚未完成或异常。')
            elif not result.get('status'):
                errors.append('最终保存状态未知。')
        else:
            errors.append('未发现可读取的最终 result.json，保存结果未知。')
        if self.is_active(path) or before != self._fingerprint(path):
            raise ValueError('检查期间数据或采集状态已变化，请在退出后重新检查。')
        item = self.register(path)
        item.update(report)
        item.update(n_frames=report.get('n_steps', 0), episodes=episodes,
                    complete_episodes=sum(e['complete'] == 'complete' for e in episodes),
                    incomplete_episodes=sum(e['complete'] == 'incomplete' for e in episodes),
                    unknown_episodes=sum(e['complete'] == 'unknown' for e in episodes),
                    final_result=summary, checked_at=time.time(), errors=errors,
                    training_ready=not errors, valid=not errors, integrity='passed' if not errors else 'blocked',
                    metadata_fingerprint=before)
        # Do not overwrite a stable asset identity with an inspector's path.
        item.update(id=asset_id, path=str(path), active=False)
        with self.lock:
            self.entries[asset_id] = dict(item)
            self._save()
        return item

    def training_path(self, identifier):
        path = self.resolve(identifier)
        report = self.audit(self._identifier(path))
        if not report['training_ready']:
            raise ValueError('数据未通过训练检查：' + '; '.join(report['errors']))
        return path

    def frame(self, identifier, camera='camera_0', index=0):
        import cv2
        import zarr
        if camera not in ('camera_0', 'camera_1', 'camera_2'):
            raise ValueError('Unknown camera key.')
        if type(index) is not int or index < 0:
            raise ValueError('帧序号必须为非负整数。')
        path = self.resolve(identifier)
        if self.is_active(path):
            raise ValueError('采集中仅使用原程序本机预览，不读取正在变化的图像。')
        self._check_links(path, camera=camera)
        root = zarr.open_group(str(path), mode='r')
        key = 'data/' + camera
        if key not in root:
            raise ValueError('该数据集没有此相机。')
        array = root[key]
        if array.ndim != 4 or array.shape[-1] != 3 or array.dtype != np.uint8 or index >= len(array):
            raise ValueError('相机帧格式或帧序号无效。')
        # Reject absent chunks rather than displaying zero-valued fill frames.
        counts = [(s + c - 1) // c for s, c in zip(array.shape[1:], array.chunks[1:])]
        for suffix in np.ndindex(*counts):
            chunk_key = array._chunk_key((index // array.chunks[0],) + suffix)
            self._allowed(path / chunk_key, require_store=False)
            if chunk_key not in array.chunk_store:
                raise ValueError('图像物理块缺失，无法回放。')
        rgb = array[index]
        scale = min(1., 480 / max(rgb.shape[:2]))
        if scale < 1:
            rgb = cv2.resize(rgb, (round(rgb.shape[1] * scale), round(rgb.shape[0] * scale)))
        ok, encoded = cv2.imencode('.jpg', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            raise ValueError('JPEG encoding failed.')
        return dict(id=self._identifier(path), camera=camera, index=index, total_frames=len(array),
                    cameras=[k for k in ('camera_0', 'camera_1', 'camera_2') if 'data/' + k in root],
                    image='data:image/jpeg;base64,' + base64.b64encode(encoded).decode())

    def replay(self, identifier, camera='camera_0', start=0, count=8, stride=1):
        if any(type(v) is not int for v in (start, count, stride)) or start < 0 or not 1 <= count <= 12 or not 1 <= stride <= 1000:
            raise ValueError('回放范围无效（最多 12 帧）。')
        first = self.frame(identifier, camera, start)
        frames = [dict(index=first['index'], image=first['image'])]
        for index in range(start + stride, min(first['total_frames'], start + count * stride), stride):
            frame = self.frame(identifier, camera, index)
            frames.append(dict(index=index, image=frame['image']))
        return dict(id=first['id'], camera=camera, total_frames=first['total_frames'], frames=frames)
