"""Read-only validation for direct GELLO Zarr v2 replay buffers (no robot imports)."""
from pathlib import Path
import numpy as np
import zarr


def resolve_path(path):
    path = Path(path).expanduser().resolve()
    if not (path / '.zgroup').exists() and (path / 'replay_buffer.zarr').is_dir():
        path = path / 'replay_buffer.zarr'
    return path


def inspect_dataset(path, check_chunks=False, scan_values=True):
    path = resolve_path(path)
    root = zarr.open_group(str(path), mode='r')
    errors, warnings = [], []
    report = dict(path=str(path), valid=False, errors=errors, warnings=warnings, arrays={})
    if 'data' not in root or 'meta/episode_ends' not in root:
        errors.append('Expected data/* and meta/episode_ends in a direct Zarr v2 replay buffer.')
        return report
    ends = root['meta/episode_ends'][:]
    report['episode_ends'] = ends.tolist()
    if ends.ndim != 1 or not np.issubdtype(ends.dtype, np.integer) or len(ends) == 0:
        errors.append('episode_ends must be a nonempty integer vector.')
        return report
    if np.any(np.diff(np.r_[0, ends]) <= 0):
        errors.append('episode_ends must be positive and strictly increasing.')
    n = int(ends[-1])
    report['n_steps'], report['n_episodes'] = n, len(ends)
    for key, arr in root['data'].arrays():
        report['arrays'][key] = dict(shape=list(arr.shape), dtype=str(arr.dtype))
        if arr.ndim == 0 or arr.shape[0] != n:
            errors.append(f'{key}: length {arr.shape[0] if arr.ndim else 0} != episode_ends[-1] ({n}).')
        if key == 'action' and (arr.ndim != 2 or arr.shape[1] != 7):
            errors.append('action must have shape (T, 7): six joint targets + gripper target.')
        if check_chunks:
            # Sparse/incomplete downloads otherwise silently read as fill_value (often zeros).
            counts = [(s + c - 1) // c for s, c in zip(arr.shape, arr.chunks)]
            missing = sum(arr._chunk_key(idx) not in arr.chunk_store for idx in np.ndindex(*counts))
            if missing:
                errors.append(f'{key}: {missing} missing physical chunks; refusing implicit fill values.')
        if scan_values and arr.ndim <= 2 and np.issubdtype(arr.dtype, np.number):
            for start in range(0, arr.shape[0], 4096):
                if not np.isfinite(arr[start:start + 4096]).all():
                    errors.append(f'{key}: nonfinite values near row {start}.')
                    break
    if 'action' not in root['data']:
        errors.append('Missing action array.')
    if 'timestamp' in root['data']:
        ts = root['data/timestamp'][:]
        if ts.ndim != 1:
            errors.append('timestamp must be a vector.')
        else:
            intervals = []
            for start, end in zip(np.r_[0, ends[:-1]], ends):
                dt = np.diff(ts[start:end])
                if np.any(dt <= 0):
                    errors.append(f'timestamp is not strictly increasing in episode starting {start}.')
                intervals.extend(dt.tolist())
            if intervals:
                report['median_dt_seconds'] = float(np.median(intervals))
    else:
        warnings.append('No timestamps: structural validity does not prove observation/action alignment.')
    report['valid'] = not errors
    return report


def require_valid(path, check_chunks=False):
    report = inspect_dataset(path, check_chunks=check_chunks)
    if not report['valid']:
        raise ValueError('Invalid GELLO dataset:\n' + '\n'.join(report['errors']))
    return report


def validate_episode(episode):
    """Reject malformed collection output before ReplayBuffer can partially append it."""
    if 'action' not in episode:
        raise ValueError('Missing action.')
    action = np.asarray(episode['action'])
    if action.ndim != 2 or action.shape[1] != 7 or not len(action):
        raise ValueError('Expected nonempty (T,7) joint/gripper action.')
    n = len(action)
    for key, value in episode.items():
        value = np.asarray(value)
        if not value.ndim or len(value) != n:
            raise ValueError(f'{key}: episode frame count differs from action ({n}).')
        if not np.isfinite(value).all():
            raise ValueError(f'{key}: nonfinite values.')
    if 'timestamp' in episode and np.any(np.diff(episode['timestamp']) <= 0):
        raise ValueError('Episode timestamps must increase.')


def repair_action_row(source, output, drop_index, reason, episode_ends=None):
    """Copy-only repair; caller must provide an externally verified extra action index.

    No time alignment is inferred. Observation lengths must agree and the action
    array must have exactly one extra row. The caller supplies its verified index
    and, if necessary, verified episode boundaries. Source is never opened for writing.
    """
    import json
    import shutil
    src = resolve_path(source)
    dst = Path(output).expanduser().resolve()
    if dst == src or src in dst.parents or dst in src.parents:
        raise ValueError('Output must be separate from the source, not its ancestor or child.')
    if dst.exists():
        raise FileExistsError('Output already exists; refusing overwrite.')
    if not reason.strip():
        raise ValueError('A documented alignment justification is required.')
    root = zarr.open_group(str(src), mode='r')
    ends = root['meta/episode_ends'][:]
    arrays = dict(root['data'].arrays())
    lengths = {a.shape[0] for k, a in arrays.items() if k != 'action'}
    if len(lengths) != 1:
        raise ValueError('Observation arrays disagree; an explicit per-field recovery is needed.')
    n = lengths.pop()
    if arrays['action'].shape[0] != n + 1:
        raise ValueError('Repair only supports exactly one extra action row with all other lengths valid.')
    new_ends = ends if episode_ends is None else np.asarray(episode_ends)
    if (new_ends.ndim != 1 or len(new_ends) != len(ends)
            or not np.issubdtype(new_ends.dtype, np.integer)
            or np.any(np.diff(np.r_[0, new_ends]) <= 0) or new_ends[-1] != n):
        raise ValueError('Supply verified episode_ends with the original episode count, '
                         'positive increasing boundaries, and final boundary equal to observation length.')
    if not 0 <= drop_index < n + 1:
        raise ValueError('drop_index out of bounds.')
    # Refuse missing source chunks, including image chunks, before creating output.
    report = inspect_dataset(src, check_chunks=True)
    allowed = {f'{k}: length {a.shape[0]} != episode_ends[-1] ({int(ends[-1])}).'
               for k, a in arrays.items()}
    if int(ends[-1]) not in (n, n + 1) or [e for e in report['errors'] if e not in allowed]:
        raise ValueError('Source has additional errors: ' + '; '.join(report['errors']))
    shutil.copytree(src, dst)
    out = zarr.open_group(str(dst), mode='a')
    arr = out['data/action']
    values = np.delete(arr[:], drop_index, axis=0)
    arr.resize(values.shape)
    arr[:] = values
    out['meta/episode_ends'][:] = new_ends
    require_valid(dst, check_chunks=True)
    manifest = dict(source=str(src), dropped_action_row=drop_index, reason=reason,
                    original_shape=list(arrays['action'].shape),
                    repaired_shape=list(arr.shape), original_episode_ends=ends.tolist(),
                    repaired_episode_ends=new_ends.tolist(), alignment_verified_by='caller')
    (dst / 'repair_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest
