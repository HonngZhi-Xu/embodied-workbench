"""Read-only asset registration, actual Zarr quality gates and replay limits."""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest
import zarr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'robotics'))
from edge_agent.assets import ExternalAssets
from edge_agent.core import EdgeCore
from edge_agent.workstation import WorkstationStore


def snapshot(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file()}


def store(path, mode='rgb_tactile', complete=True, reviewed=True, field_group='data'):
    path.mkdir(parents=True)
    root = zarr.open_group(str(path), mode='w')
    data = root.create_group('data')
    meta = root.create_group('meta')
    for camera in range(3):
        data.array('camera_' + str(camera), np.full((6, 8, 10, 3), 45 + camera * 40, np.uint8), chunks=(1, 8, 10, 3))
    data.array('action', np.zeros((6, 7), np.float32), chunks=(3, 7))
    data.array('robot_joint', np.zeros((6, 6), np.float32), chunks=(3, 6))
    data.array('gripper_position', np.ones((6, 1), np.float32), chunks=(3, 1))
    data.array('timestamp', np.array([0., .05, .1, 0., .05, .1]), chunks=(3,))
    if mode == 'rgb_tactile':
        data.array('tactile', np.zeros((6, 6), np.float32), chunks=(3, 6))
    meta.array('episode_ends', np.array([3, 6], np.int64), chunks=(2,))
    flag = root[field_group]
    size = 6 if field_group == 'data' else 2
    flag.array('episode_operator_complete', np.full(size, int(complete), np.int64), chunks=(size,))
    flag.array('episode_end_reason_code', np.ones(size, np.int64), chunks=(size,))
    flag.array('episode_timing_quality_pass', np.ones(size, np.int64), chunks=(size,))
    root.attrs['episode_end_reason_codes'] = {'operator_save': 1, 'operator_quit': 2, 'sensor_failure': 3}
    result = dict(status='completed' if reviewed else 'software_complete_pending_review', writer_stopped=True,
                  production_training_qualified=reviewed, operator_confirmation_pending=not reviewed,
                  saved_rows=6, saved_episodes=2, dataset=str(path.resolve()), fault='none')
    (path.parent / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    return root


@pytest.fixture
def assets(tmp_path):
    source = tmp_path / 'external'
    source.mkdir()
    index = ExternalAssets(tmp_path / 'platform')
    index.configure([str(source)])
    return index, source


def test_register_and_audit_are_read_only_and_persistent(assets):
    index, source = assets
    path = source / 'session' / 'manual_task' / 'replay_buffer.zarr'
    store(path)
    before = snapshot(source)
    item = index.register(path)
    assert item['id'].startswith('external-') and not item['training_ready']
    report = index.audit(item['id'])
    assert report['id'] == item['id'] and list(index.entries) == [item['id']]
    assert report['valid'] and report['training_ready'] and report['complete_episodes'] == 2
    assert report['n_frames'] == 6 and report['mode'] == 'rgb_tactile'
    assert index.training_path(item['id']) == path.resolve()
    assert snapshot(source) == before
    restored = ExternalAssets(index.data_root)
    restored.configure([str(source)])
    assert restored.resolve(item['id']) == path.resolve()


def test_scans_history_and_missing_default_root_is_empty(assets, tmp_path):
    index, source = assets
    path = source / 'historic' / 'manual_task' / 'replay_buffer.zarr'
    store(path)
    assert index.scan()[0]['path'] == str(path.resolve())
    empty = ExternalAssets(tmp_path / 'empty-platform')
    empty.configure([str(tmp_path / 'missing')])
    assert empty.scan() == []


def test_active_capture_blocks_audit_frames_and_training_without_touching_arrays(assets, monkeypatch):
    index, source = assets
    path = source / 'active' / 'replay_buffer.zarr'
    store(path)
    session = dict(running=True, dataset_path=str(path))
    index.active_provider = lambda: session
    monkeypatch.setattr(zarr, 'open_group', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Active arrays opened')))
    item = index.register(path)
    assert item['active'] and not item['training_ready']
    for operation in (lambda: index.audit(item['id']), lambda: index.frame(item['id']), lambda: index.training_path(item['id'])):
        with pytest.raises(ValueError, match='采集|变化'):
            operation()
    assert index.scan()[0]['active']


@pytest.mark.parametrize('field_group', ['data', 'meta'])
def test_unknown_and_incomplete_episodes_never_train(assets, field_group):
    index, source = assets
    path = source / field_group / 'replay_buffer.zarr'
    root = store(path, complete=False, field_group=field_group)
    item = index.register(path)
    report = index.audit(item['id'])
    assert not report['training_ready'] and report['incomplete_episodes'] == 2
    del root[field_group + '/episode_operator_complete']
    assert index.audit(item['id'])['unknown_episodes'] == 2
    with pytest.raises(ValueError, match='未通过'):
        index.training_path(item['id'])


def test_queued_or_unreviewed_final_result_is_not_training_approval(assets):
    index, source = assets
    path = source / 'pending' / 'replay_buffer.zarr'
    store(path, reviewed=False)
    item = index.register(path)
    report = index.audit(item['id'])
    assert report['complete_episodes'] == 2 and not report['training_ready']
    assert any('production_training_qualified' in e for e in report['errors'])
    result = path.parent / 'result.json'
    payload = json.loads(result.read_text())
    payload.update(status='completed', production_training_qualified=True, operator_confirmation_pending=False, writer_stopped=False)
    result.write_text(json.dumps(payload))
    assert any('写盘' in e for e in index.audit(item['id'])['errors'])


def test_chunk_loss_and_result_mismatch_block_training(assets):
    index, source = assets
    path = source / 'loss' / 'replay_buffer.zarr'
    root = store(path)
    item = index.register(path)
    array = root['data/camera_0']
    del array.chunk_store[array._chunk_key((0, 0, 0, 0))]
    report = index.audit(item['id'])
    assert not report['training_ready'] and any('missing physical chunks' in e for e in report['errors'])
    with pytest.raises(ValueError, match='物理块缺失'):
        index.frame(item['id'])


def test_scope_and_unregistered_paths_are_rejected(assets, tmp_path):
    index, source = assets
    outside = tmp_path / 'outside' / 'replay_buffer.zarr'
    store(outside)
    with pytest.raises(ValueError, match='根目录'):
        index.register(outside)
    inside = source / 'inside' / 'replay_buffer.zarr'
    store(inside)
    with pytest.raises(ValueError, match='未登记'):
        index.resolve(inside)
    alias = source / 'linked.zarr'
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    with pytest.raises(ValueError, match='根目录'):
        index.register(alias)


def test_resume_mode_and_frame_replay_limits(assets):
    index, source = assets
    path = source / 'rgb' / 'replay_buffer.zarr'
    store(path, mode='rgb_only')
    assert index.check_resume(path, 'rgb_only') == path.resolve()
    with pytest.raises(ValueError, match='不兼容'):
        index.check_resume(path, 'rgb_tactile')
    item = index.register(path)
    frame = index.frame(item['id'], 'camera_1', 2)
    assert frame['image'].startswith('data:image/jpeg;base64,') and frame['total_frames'] == 6
    replay = index.replay(item['id'], 'camera_0', count=3, stride=2)
    assert [f['index'] for f in replay['frames']] == [0, 2, 4]
    with pytest.raises(ValueError): index.replay(item['id'], count=100)


def test_core_resolves_registered_asset_and_rechecks_active_state(assets, tmp_path):
    _, source = assets
    path = source / 'ready' / 'replay_buffer.zarr'
    store(path)
    project = Path(__file__).resolve().parents[1]
    core = EdgeCore(project / 'robotics', tmp_path / 'core-platform')
    core.load_external_config(dict(external_data_roots=[str(source)]))
    item = core.assets.register(path)
    assert core.dataset_path(item['id']) == path.resolve()
    assert core.resolve_training_dataset(item['id']) == path.resolve()
    assert any(r['id'] == item['id'] for r in core.datasets())
    core.assets.active_provider = lambda: dict(running=True)
    with pytest.raises(ValueError): core.resolve_training_dataset(item['id'])


def test_stale_audit_and_unknown_reason_codes_block_training(assets):
    index, source = assets
    path = source / 'changing' / 'replay_buffer.zarr'
    root = store(path)
    item = index.register(path)
    assert index.audit(item['id'])['training_ready']
    root['data/episode_end_reason_code'][:] = 99
    assert index.audit(item['id'])['unknown_episodes'] == 2
    result = path.parent / 'result.json'
    payload = json.loads(result.read_text())
    payload['saved_rows'] = 7
    result.write_text(json.dumps(payload))
    assert not index.get(item['id'])['training_ready']


def test_training_reaudits_chunks_even_when_metadata_did_not_change(assets):
    index, source = assets
    path = source / 'chunk-change' / 'replay_buffer.zarr'
    root = store(path)
    item = index.register(path)
    assert index.audit(item['id'])['training_ready']
    before = index._fingerprint(path)
    # Only mutate a physically stored data chunk. Cached inspection metadata is
    # unchanged, but the training path must run a fresh full inspection.
    root['data/action'][0, 0] = np.nan
    assert index._fingerprint(path) == before
    with pytest.raises(ValueError, match='nonfinite'):
        index.training_path(item['id'])


def test_missing_episode_boundary_chunk_is_not_implicit_valid_fill(assets):
    index, source = assets
    path = source / 'lost-boundaries' / 'replay_buffer.zarr'
    root = store(path)
    item = index.register(path)
    array = root['meta/episode_ends']
    del array.chunk_store[array._chunk_key((0,))]
    with pytest.raises(ValueError, match='episode_ends'):
        index.audit(item['id'])


@pytest.mark.parametrize('kind', ['metadata', 'chunk'])
def test_internal_links_cannot_read_outside_configured_roots(assets, tmp_path, kind):
    index, source = assets
    path = source / kind / 'replay_buffer.zarr'
    root = store(path)
    item = index.register(path)
    target = path / 'data/camera_0/.zarray' if kind == 'metadata' else path / root['data/camera_0']._chunk_key((0, 0, 0, 0))
    outside = tmp_path / ('private-' + kind)
    outside.write_bytes(target.read_bytes())
    original = target.read_bytes()
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        target.write_bytes(original)
        pytest.skip('Creating file symlinks requires Windows developer privileges')
    with pytest.raises(ValueError, match='根目录'):
        index.audit(item['id'])
    with pytest.raises(ValueError, match='根目录'):
        index.frame(item['id'])


def test_workstation_migrates_old_profiles_without_writing_source(tmp_path):
    from edge_agent.ur_workflow import DEFAULTS
    base = dict(name='工位', robot_ip='', gripper='', camera_0='', camera_1='', camera_2='', port='',
                **{k: DEFAULTS[k] for k in ('capture_script', 'project', 'conda_env', 'output_root')})
    settings = WorkstationStore(tmp_path / 'settings')
    saved = settings.save(base, None)
    assert saved['profile']['capture_mode'] == 'rgb_tactile'
    assert saved['profile']['resume_path'] == ''
    assert settings.read() == saved
