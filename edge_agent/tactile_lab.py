"""Local persistence for the imported M3025 research workbench.

This module never opens a sensor, executes an attachment, or writes an external
dataset. The loopback edge service owns the workspace; no cloud identity or
Cloudflare binding from the supplied standalone Site is reused.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid


MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_EPISODE_FRAMES = 4096
VALID_KINDS = frozenset(('dataset', 'episode', 'preprocess', 'model', 'experiment',
                        'plan', 'conclusion', 'task', 'file', 'report', 'sensor',
                        'evaluation', 'log', 'feature', 'note'))
IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,150}\Z')


class LabError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise LabError('记录标识格式无效。')
    return value


def _encode(value):
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        encoded = text.encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise LabError('记录必须是有限值、可序列化的 JSON。') from None
    if len(encoded) > MAX_RECORD_BYTES:
        raise LabError('记录内容超过 4 MiB，请上传原始文件，或按 Episode／分块导入。', 413)
    return text


class TactileLab:
    def __init__(self, core):
        self.core = core
        self.root = (Path(core.data_root) / 'tactile_lab').resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.files = (self.root / 'files').resolve()
        self.files.mkdir(exist_ok=True)
        self.database = self.root / 'records.sqlite3'
        self.lock = threading.RLock()
        with self._db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS lab_records (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, version INTEGER NOT NULL,
                deleted_at TEXT)''')
            db.execute('CREATE INDEX IF NOT EXISTS lab_record_kind ON lab_records(kind, deleted_at)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.database), timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _record(row):
        if row is None:
            return None
        record = dict(id=row['id'], kind=row['kind'], name=row['name'], status=row['status'],
                      data=json.loads(row['data']), createdAt=row['created_at'],
                      updatedAt=row['updated_at'], version=row['version'])
        if row['deleted_at']:
            record['deletedAt'] = row['deleted_at']
        return record

    def get_record(self, identifier, include_deleted=False):
        identifier = _identifier(identifier)
        with self._db() as db:
            row = db.execute('SELECT * FROM lab_records WHERE id = ?', (identifier,)).fetchone()
        if row is None or (row['deleted_at'] and not include_deleted):
            return None
        return self._record(row)

    def has_records(self):
        """A workspace with deleted templates is still initialized."""
        with self._db() as db:
            return db.execute('SELECT 1 FROM lab_records LIMIT 1').fetchone() is not None

    def _file_path(self, key):
        # Only an upload-generated opaque key can name a local object. The
        # browser's original filename is never used as a filesystem path.
        if not isinstance(key, str) or not re.fullmatch(r'file_[0-9a-f]{32}', key):
            raise LabError('文件存储标识无效。', 404)
        path = (self.files / key).resolve()
        if path.parent != self.files:
            raise LabError('文件路径越界。', 403)
        return path

    def list_records(self, include_deleted=False):
        with self._db() as db:
            rows = db.execute('SELECT * FROM lab_records ' +
                              ('' if include_deleted else 'WHERE deleted_at IS NULL ') +
                              'ORDER BY updated_at DESC, id LIMIT 5000').fetchall()
        records = [self._record(row) for row in rows]
        available = {}
        for record in records:
            if record['kind'] == 'file' and 'deletedAt' not in record:
                try:
                    available[record['id']] = self._file_path(record['data'].get('key')).is_file()
                except LabError:
                    available[record['id']] = False
                record['fileAvailable'] = available[record['id']]
        for record in records:
            referenced = set()
            def walk(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key in ('sourceFileId', 'fileId') and isinstance(child, str):
                            referenced.add(child)
                        elif key == 'fileIds' and isinstance(child, list):
                            referenced.update(x for x in child if isinstance(x, str))
                        else:
                            walk(child)
                elif isinstance(value, list):
                    for child in value:
                        walk(child)
            walk(record['data'])
            missing = sorted(key for key in referenced if not available.get(key))
            if missing:
                record['missingFileIds'] = missing
                record['attachmentWarnings'] = ['原始附件缺失或已移入回收区：' + ', '.join(missing)]
        return records

    def save_record(self, record):
        return self._save(record, trusted_file=False)

    def _save(self, record, trusted_file=False):
        if not isinstance(record, dict):
            raise LabError('请提供有效的记录对象。')
        kind = record.get('kind')
        if not isinstance(kind, str) or kind not in VALID_KINDS:
            raise LabError('不支持的记录类型。')
        name = record.get('name')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 250:
            raise LabError('名称为必填项，且不能超过 250 个字符。')
        status = record.get('status', '待确认')
        if not isinstance(status, str) or not status.strip() or len(status.strip()) > 100:
            raise LabError('状态为必填项，且不能超过 100 个字符。')
        data = record.get('data', {})
        if not isinstance(data, dict):
            raise LabError('data 必须是 JSON 对象。')
        identifier = _identifier(record.get('id') or 'rec_' + uuid.uuid4().hex)
        supplied_version = record.get('version')
        if supplied_version is not None and (type(supplied_version) is not int or supplied_version < 1):
            raise LabError('记录版本格式无效。')
        with self.lock, self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM lab_records WHERE id = ?', (identifier,)).fetchone()
            if row is not None and row['deleted_at']:
                raise LabError('此记录已移入回收区，请先恢复或使用新标识。', 409)
            existing = self._record(row)
            if existing and existing['kind'] != kind:
                raise LabError('已存在记录的类型不能修改。', 409)
            data = dict(data)
            if kind == 'file' and not trusted_file:
                if not existing:
                    raise LabError('请通过文件上传入口创建文件记录。')
                for key in ('key', 'size', 'mime', 'sha256'):
                    data[key] = existing['data'][key]
            encoded = _encode(data)
            same = existing and existing['name'] == name.strip() and existing['status'] == status.strip() and existing['data'] == data
            if existing:
                if supplied_version is not None and supplied_version != existing['version']:
                    raise LabError('该记录已在其他页面更新，请刷新后重试。', 409)
                if same:
                    return existing
                if supplied_version is None:
                    raise LabError('更新已有记录需携带当前 version，请刷新后重试。', 409)
                db.execute('UPDATE lab_records SET name=?, status=?, data=?, updated_at=?, version=version+1 WHERE id=? AND version=?',
                           (name.strip(), status.strip(), encoded, _now(), identifier, supplied_version))
            else:
                now = _now()
                db.execute('INSERT INTO lab_records VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL)',
                           (identifier, kind, name.strip(), status.strip(), encoded, now, now))
            row = db.execute('SELECT * FROM lab_records WHERE id = ?', (identifier,)).fetchone()
            return self._record(row)

    def delete_record(self, identifier):
        identifier = _identifier(identifier)
        with self.lock, self._db() as db:
            row = db.execute('SELECT * FROM lab_records WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise LabError('记录不存在。', 404)
            if not row['deleted_at']:
                now = _now()
                db.execute('UPDATE lab_records SET deleted_at=?,updated_at=?,version=version+1 WHERE id=?', (now, now, identifier))
        # No file is deleted: recoverable removal preserves research provenance.
        return dict(deleted=identifier, softDeleted=True)

    def restore_record(self, identifier):
        identifier = _identifier(identifier)
        with self.lock, self._db() as db:
            row = db.execute('SELECT * FROM lab_records WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise LabError('记录不存在。', 404)
            if row['deleted_at']:
                db.execute('UPDATE lab_records SET deleted_at=NULL,updated_at=?,version=version+1 WHERE id=?', (_now(), identifier))
        return self.get_record(identifier)

    def upload_file(self, filename, content, mime='application/octet-stream', category='data'):
        if not isinstance(content, bytes) or not content:
            raise LabError('不能上传空文件。')
        if len(content) > MAX_FILE_BYTES:
            raise LabError('单个文件不能超过 20 MiB。', 413)
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 250 or any(c in filename for c in '\0\r\n'):
            raise LabError('文件名无效或过长。')
        if not isinstance(mime, str) or len(mime) > 200 or any(c in mime for c in '\0\r\n'):
            raise LabError('文件类型格式无效。')
        if not isinstance(category, str) or len(category) > 100:
            raise LabError('文件类别过长。')
        identifier = 'file_' + uuid.uuid4().hex
        path = self._file_path(identifier)
        try:
            with path.open('xb') as stream:
                stream.write(content)
            record = self._save(dict(id=identifier, kind='file', name=filename.strip(), status='已导入',
                               data=dict(key=identifier, size=len(content), mime=mime or 'application/octet-stream',
                                         sha256=hashlib.sha256(content).hexdigest(), category=category)), trusted_file=True)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return record

    def download_file(self, identifier):
        record = self.get_record(identifier)
        if record is None or record['kind'] != 'file':
            raise LabError('文件不存在或已移入回收区。', 404)
        path = self._file_path(record['data'].get('key'))
        if not path.is_file():
            raise LabError('原始附件内容缺失，请重新上传并核对来源。', 404)
        if path.stat().st_size > MAX_FILE_BYTES or path.stat().st_size != record['data'].get('size'):
            raise LabError('文件大小与上传记录不一致。', 409)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != record['data'].get('sha256'):
            raise LabError('文件校验失败，内容与上传时不一致。', 409)
        return record, content

    def import_asset(self, asset_id, episode_index=0):
        """Copy one bounded M3025 episode into the lab, leaving source untouched."""
        import numpy as np
        import zarr
        if not isinstance(asset_id, str) or not asset_id.startswith('external-'):
            raise LabError('请使用已登记的外部数据资产 ID。')
        if type(episode_index) is not int or episode_index < 0:
            raise LabError('Episode 序号必须为非负整数（从 0 开始）。')
        assets = self.core.assets
        try:
            path = assets.resolve(asset_id)
            if assets.is_active(path):
                raise LabError('原采集程序仍在写入此数据，退出并写盘完成后再导入。', 409)
            assets._check_links(path, full=True)
        except LabError:
            raise
        except (ValueError, OSError) as exc:
            raise LabError(str(exc)) from None
        root = zarr.open_group(str(path), mode='r')
        def read_slice(key, start=None, end=None):
            if key not in root:
                raise LabError('数据缺少字段：' + key)
            array = root[key]
            if array.ndim == 0:
                raise LabError('数据字段不能是标量：' + key)
            for chunk in np.ndindex(*[(s + c - 1) // c for s, c in zip(array.shape, array.chunks)]):
                if start is not None and (chunk[0] * array.chunks[0] >= end or (chunk[0] + 1) * array.chunks[0] <= start):
                    continue
                if array._chunk_key(chunk) not in array.chunk_store:
                    raise LabError('数据物理块缺失，不使用隐式填充值：' + key)
            return array[:] if start is None else array[start:end]
        ends = read_slice('meta/episode_ends')
        if ends.ndim != 1 or not np.issubdtype(ends.dtype, np.integer) or not len(ends) or np.any(np.diff(np.r_[0, ends]) <= 0):
            raise LabError('Episode 边界无效。')
        if episode_index >= len(ends):
            raise LabError('Episode 序号超出范围。')
        start = int(ends[episode_index - 1]) if episode_index else 0
        end = int(ends[episode_index])
        if end - start > MAX_EPISODE_FRAMES:
            raise LabError('此 Episode 超过 4096 帧，请明确分块转换后导入，不会静默截断。', 413)
        if 'data/tactile' not in root:
            raise LabError('此数据集没有 M3025 tactile 字段。')
        shape = root['data/tactile'].shape
        if shape[0] != int(ends[-1]) or shape[1:] not in ((231,), (77, 3)) or not np.issubdtype(root['data/tactile'].dtype, np.number):
            raise LabError('触觉数据应为 [T,231] 或 [T,77,3]，并与 Episode 边界一致。')
        forces = read_slice('data/tactile', start, end).reshape(-1, 77, 3)
        timestamps = read_slice('data/timestamp', start, end) if 'data/timestamp' in root else None
        if timestamps is not None and (timestamps.ndim != 1 or root['data/timestamp'].shape[0] != int(ends[-1])
                                       or not np.issubdtype(timestamps.dtype, np.number)):
            raise LabError('timestamp 长度或维度与触觉数据不一致。')
        resultants = None
        if 'data/tactile_resultant' in root:
            if (root['data/tactile_resultant'].shape != (int(ends[-1]), 3)
                    or not np.issubdtype(root['data/tactile_resultant'].dtype, np.number)):
                raise LabError('tactile_resultant 应为 [T,3]。')
            resultants = read_slice('data/tactile_resultant', start, end)
        def finite(value):
            number = float(value)
            return number if np.isfinite(number) else None
        samples = []
        for index, frame in enumerate(forces):
            sample = dict(timestamp=finite(timestamps[index]) if timestamps is not None else None,
                          forces=[[finite(v) for v in point] for point in frame], contact=None, recovery=None)
            if resultants is not None:
                sample['force'] = [finite(v) for v in resultants[index]]
            samples.append(sample)
        attrs = root.attrs.asdict()
        force_unit = str(attrs.get('tactile_unit', attrs.get('forceUnit', '待确认')))
        timestamp_unit = str(attrs.get('timestamp_unit', attrs.get('timestampUnit', '')))
        resultant_unit = str(attrs.get('tactile_resultant_unit', attrs.get('resultantForceUnit', '待确认')))
        source = dict(sourceAssetId=asset_id, sourceDatasetPath=str(path), sourceEpisodeIndex=episode_index,
                      sourceStartFrame=start, sourceEndFrame=end, sourceFormat='GELLO Zarr v2',
                      forceUnit=force_unit, resultantForceUnit=resultant_unit, timestampUnit=timestamp_unit,
                      netForceUnit=resultant_unit,
                      unitConfirmationRequired=force_unit != 'N', readOnlySource=True)
        fingerprint = hashlib.sha256(_encode(dict(source=source, samples=samples)).encode('utf-8')).hexdigest()
        dataset_id = 'platform_ds_' + hashlib.sha256(asset_id.encode()).hexdigest()[:24]
        episode_id = 'platform_ep_' + fingerprint[:32]
        dataset_input = dict(id=dataset_id, kind='dataset', name=(path.parent.parent.name + ' · 平台触觉数据')[:250], status='未知/待确认',
                             data=dict(source='平台已登记外部数据', sourceAssetId=asset_id, sourceDatasetPath=str(path), split='未划分'))
        data = dict(source, datasetId=dataset_id, samples=samples, sampleCount=len(samples),
                    samplingHz=attrs.get('sampling_hz') if isinstance(attrs.get('sampling_hz'), (int, float)) else None,
                    split='未划分', labels=[], labelIntervals=[], preprocessId='', sourceFingerprint=fingerprint,
                    evidence='只读导入原数据；物理单位、标定、缺失与划分仍需研究人员核对。')
        _encode(data)
        # Recheck before creating records; an active resumed run must not turn a
        # changing dataset into a supposedly stable analysis snapshot.
        if assets.is_active(path):
            raise LabError('导入过程中采集状态已变化，请退出采集后重试。', 409)
        with self.lock:
            existing_dataset = self.get_record(dataset_id, include_deleted=True)
            existing_episode = self.get_record(episode_id, include_deleted=True)
            if any(record and record.get('deletedAt') for record in (existing_dataset, existing_episode)):
                raise LabError('同源研究记录已移入回收区，请先恢复，避免重复创建。', 409)
            dataset = existing_dataset or self.save_record(dataset_input)
            episode = existing_episode or self.save_record(dict(id=episode_id, kind='episode',
                        name=f'{path.parent.parent.name} · Episode {episode_index + 1}'[:250], status='未知/待确认', data=data))
        return dict(records=[dataset, episode], sampleCount=len(samples), assetId=asset_id,
                    episodeIndex=episode_index, sourceUnmodified=True,
                    warning='counts 和未知单位不会转换成 N；三轴测点和独立合力的单位分别保留。')
