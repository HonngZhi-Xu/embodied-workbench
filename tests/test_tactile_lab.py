"""M3025 research records/files and read-only platform asset bridge."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import zarr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'robotics'))
from edge_agent.assets import ExternalAssets
from edge_agent.tactile_lab import LabError, TactileLab, MAX_FILE_BYTES, MAX_RECORD_BYTES


@pytest.fixture
def lab(tmp_path):
    source = tmp_path / 'external'
    source.mkdir()
    assets = ExternalAssets(tmp_path / 'platform')
    assets.configure([str(source)])
    core = SimpleNamespace(data_root=tmp_path / 'platform', assets=assets)
    lab = TactileLab(core)
    lab.source = source
    return lab


def note(**overrides):
    return dict(dict(id='note-1', kind='note', name='研究记录', status='待确认', data={'unknown': None}), **overrides)


def snapshot(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file()}


def make_asset(lab, shape='flat', attributes=True):
    path = lab.source / 'session' / 'manual_task' / 'replay_buffer.zarr'
    root = zarr.open_group(str(path), mode='w')
    data = root.create_group('data')
    meta = root.create_group('meta')
    tactile = np.arange(6 * 231, dtype=np.float32).reshape(6, 231)
    if shape == 'points':
        tactile = tactile.reshape(6, 77, 3)
    data.array('tactile', tactile, chunks=(2,) + tactile.shape[1:])
    data.array('timestamp', np.arange(6) * .05, chunks=(2,))
    data.array('action', np.zeros((6, 7), np.float32), chunks=(2, 7))
    data.array('tactile_resultant', np.ones((6, 3), np.float32) * .2, chunks=(2, 3))
    meta.array('episode_ends', np.array([3, 6], np.int64), chunks=(2,))
    if attributes:
        root.attrs.update(tactile_unit='counts', tactile_resultant_unit='N', timestamp_unit='s', sampling_hz=20)
    return path, root, lab.core.assets.register(path)['id']


def test_records_persist_with_server_versions_and_exact_request_idempotency(lab):
    assert not lab.has_records()
    first = lab.save_record(note())
    assert lab.has_records()
    assert first['version'] == 1 and first['createdAt'].endswith('Z')
    assert lab.save_record(note()) == first
    updated = lab.save_record(note(version=first['version'], data={'unknown': None, 'value': 2}))
    assert updated['version'] == 2
    assert TactileLab(lab.core).get_record(first['id']) == updated
    with pytest.raises(LabError) as error:
        lab.save_record(note(version=1, data={'value': 'stale'}))
    assert error.value.status == 409
    with pytest.raises(LabError, match='version'):
        lab.save_record(note(data={'value': 'unversioned replacement'}))


def test_soft_deleted_workspace_does_not_reseed_fixed_template_ids(lab):
    record = lab.save_record(note())
    lab.delete_record(record['id'])
    assert lab.list_records() == []
    assert lab.has_records()


@pytest.mark.parametrize('change', [
    dict(kind='shell'), dict(kind=['note']), dict(id='../escape'), dict(id='bad/id'), dict(name=''),
    dict(status=''), dict(data=[]), dict(data={'x': float('nan')}), dict(version=True),
])
def test_invalid_records_are_not_persisted(lab, change):
    with pytest.raises(LabError):
        lab.save_record(note(**change))
    assert lab.list_records() == []


def test_limits_count_encoded_bytes_not_characters(lab):
    with pytest.raises(LabError) as error:
        lab.save_record(note(data={'x': '触' * (MAX_RECORD_BYTES // 2)}))
    assert error.value.status == 413
    with pytest.raises(LabError) as error:
        lab.upload_file('oversized.bin', b'0' * (MAX_FILE_BYTES + 1))
    assert error.value.status == 413
    assert not list(lab.files.iterdir())


def test_raw_files_use_opaque_names_and_round_trip_hash(lab):
    raw = b'not executable: \0\xff\r\n, raw data'
    upload = lab.upload_file('../../payload.html', raw, 'text/html')
    record, downloaded = lab.download_file(upload['id'])
    assert downloaded == raw
    assert record['data']['sha256'] == hashlib.sha256(raw).hexdigest()
    assert list(lab.files.iterdir()) == [lab.files / record['data']['key']]
    assert not (lab.root.parent / 'payload.html').exists()


def test_file_metadata_cannot_be_forged_or_retyped(lab):
    with pytest.raises(LabError, match='上传'):
        lab.save_record(note(kind='file', data={'key': '/etc/passwd'}))
    upload = lab.upload_file('samples.csv', b'timestamp,fx\n0,1', 'text/csv')
    changed = dict(upload, name='renamed.csv', data={'key': '../escape', 'size': 1, 'sha256': 'fake', 'mime': 'text/html'})
    saved = lab.save_record(changed)
    for key in ('key', 'size', 'mime', 'sha256'):
        assert saved['data'][key] == upload['data'][key]
    with pytest.raises(LabError, match='类型'):
        lab.save_record(dict(saved, kind='note'))


def test_soft_delete_retains_file_and_restore_preserves_provenance(lab):
    upload = lab.upload_file('evidence.json', b'{"raw":true}')
    episode = lab.save_record(note(kind='episode', data={'fileIds': [upload['id']]}))
    deleted = lab.delete_record(upload['id'])
    assert deleted == {'deleted': upload['id'], 'softDeleted': True}
    assert (lab.files / upload['data']['key']).exists()
    assert len(lab.list_records()) == 1
    assert lab.list_records()[0]['missingFileIds'] == [upload['id']]
    with pytest.raises(LabError) as error:
        lab.download_file(upload['id'])
    assert error.value.status == 404
    restored = lab.restore_record(upload['id'])
    assert restored['version'] > upload['version']
    assert lab.download_file(restored['id'])[1] == b'{"raw":true}'
    assert not any(r.get('missingFileIds') for r in lab.list_records())
    assert lab.get_record(episode['id'])['data']['fileIds'] == [upload['id']]


def test_file_corruption_and_missing_payload_are_visible(lab):
    record = lab.upload_file('data.bin', b'original')
    path = lab.files / record['data']['key']
    path.write_bytes(b'alteredd')
    with pytest.raises(LabError, match='校验'):
        lab.download_file(record['id'])
    path.unlink()
    assert lab.list_records()[0]['fileAvailable'] is False
    with pytest.raises(LabError) as error:
        lab.download_file(record['id'])
    assert error.value.status == 404


@pytest.mark.parametrize('shape', ['flat', 'points'])
def test_bridge_reads_one_episode_without_rewriting_source_or_inventing_force_units(lab, shape):
    path, root, asset_id = make_asset(lab, shape)
    before = snapshot(lab.source)
    imported = lab.import_asset(asset_id, episode_index=1)
    assert imported['sourceUnmodified'] and imported['sampleCount'] == 3
    ds, ep = imported['records']
    assert ep['kind'] == 'episode' and ep['data']['datasetId'] == ds['id']
    assert ep['data']['forceUnit'] == 'counts'
    assert ep['data']['netForceUnit'] == ep['data']['resultantForceUnit'] == 'N'
    assert ep['data']['timestampUnit'] == 's'
    assert ep['data']['samplingHz'] == 20
    assert len(ep['data']['samples'][0]['forces']) == 77
    assert ep['data']['samples'][0]['forces'][0][0] == 3 * 231
    assert ep['data']['samples'][0]['force'][0] == pytest.approx(.2)
    assert ep['data']['unitConfirmationRequired'] is True
    assert snapshot(lab.source) == before
    # Reimport does not overwrite labels curated after the first import.
    curated = lab.save_record(dict(ep, data=dict(ep['data'], split='test', labels=['manual-contact'])))
    repeated = lab.import_asset(asset_id, 1)
    assert repeated['records'][1]['id'] == curated['id']
    assert repeated['records'][1]['data']['split'] == 'test'
    assert len(lab.list_records()) == 2


def test_missing_units_and_nonfinite_measurements_stay_unknown(lab):
    path, root, asset_id = make_asset(lab, attributes=False)
    root['data/tactile'][0, 0] = np.nan
    episode = lab.import_asset(asset_id)['records'][1]['data']
    assert episode['forceUnit'] == '待确认' and episode['netForceUnit'] == '待确认'
    assert episode['timestampUnit'] == '' and episode['samplingHz'] is None
    assert episode['samples'][0]['forces'][0][0] is None
    assert episode['samples'][0]['contact'] is None


def test_active_dataset_refused_before_zarr_is_opened(lab, monkeypatch):
    path, root, asset_id = make_asset(lab)
    lab.core.assets.active_provider = lambda: {'running': True, 'dataset_path': str(path)}
    monkeypatch.setattr(zarr, 'open_group', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Active data opened')))
    with pytest.raises(LabError) as error:
        lab.import_asset(asset_id)
    assert error.value.status == 409
    assert lab.list_records() == []


def test_missing_selected_tactile_chunk_is_not_implicit_zero(lab):
    path, root, asset_id = make_asset(lab)
    key = root['data/tactile']._chunk_key((0, 0))
    del root.store[key]
    with pytest.raises(LabError, match='物理块'):
        lab.import_asset(asset_id)
    assert lab.list_records() == []


def test_bridge_rejects_unregistered_bad_shape_and_out_of_range(lab):
    with pytest.raises(LabError):
        lab.import_asset('/any/path.zarr')
    path, root, asset_id = make_asset(lab)
    with pytest.raises(LabError, match='范围'):
        lab.import_asset(asset_id, 2)
    with pytest.raises(LabError):
        lab.import_asset(asset_id, True)
    del root['data/tactile']
    root['data'].array('tactile', np.ones((6, 6), np.float32))
    with pytest.raises(LabError, match='231'):
        lab.import_asset(asset_id)


def test_deleted_bridge_record_requires_restore_instead_of_duplicate(lab):
    path, root, asset_id = make_asset(lab)
    ep = lab.import_asset(asset_id)['records'][1]
    lab.delete_record(ep['id'])
    with pytest.raises(LabError) as error:
        lab.import_asset(asset_id)
    assert error.value.status == 409
    assert lab.restore_record(ep['id'])['data']['sourceAssetId'] == asset_id
