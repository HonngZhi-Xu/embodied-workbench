"""Convert legacy merged GELLO NPZ only after validating every frame and boundary."""
import argparse
from pathlib import Path
import numpy as np
import zarr
from numcodecs import Blosc
from diffusion_policy.common.gello_zarr import validate_episode


def convert_npz(source, output, trusted_object_arrays=False):
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise FileExistsError('Output exists; choose a new path to preserve the original dataset.')
    with np.load(source, allow_pickle=trusted_object_arrays) as archive:
        obs = archive['obs']
        action = np.asarray(archive['action'], dtype=np.float32)
        ends = archive['episode_ends']
        if len(obs) != len(action):
            raise ValueError(f'Observation count {len(obs)} != action count {len(action)}. '
                             'No automatic truncation is performed.')
        if (ends.ndim != 1 or not len(ends) or not np.issubdtype(ends.dtype, np.integer)
                or np.any(np.diff(np.r_[0, ends]) <= 0) or ends[-1] != len(action)):
            raise ValueError('Invalid episode_ends; final boundary must match all frame counts.')
        arrays = dict(action=action)
        for key in ('image0', 'image1', 'agent_pos'):
            arrays[key] = np.stack([frame[key] for frame in obs])
        for key in ('image0', 'image1'):
            arr = arrays[key]
            if arr.ndim != 4 or arr.shape[-1] != 3 or arr.dtype != np.uint8:
                raise ValueError(f'{key}: expected uint8 (T,H,W,3).')
        arrays['agent_pos'] = arrays['agent_pos'].astype(np.float32)
        if arrays['agent_pos'].shape != action.shape:
            raise ValueError('agent_pos must have the same (T,7) shape as action.')
        validate_episode(arrays)
    # No path is created before input checks finish. Never overwrite a previous store.
    root = zarr.open_group(str(output), mode='w-')
    data = root.create_group('data')
    compressor = Blosc(cname='zstd', clevel=5, shuffle=Blosc.BITSHUFFLE)
    for key, value in arrays.items():
        chunks = ((1,) + value.shape[1:]) if value.ndim == 4 else (min(500, len(value)), value.shape[1])
        data.array(key, value, chunks=chunks, compressor=compressor)
    root.create_group('meta').array('episode_ends', ends.astype(np.int64))
    root.attrs['source_npz'] = str(Path(source).resolve())
    root.attrs['action_semantics'] = 'six_joint_position_targets_plus_gripper'
    return str(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('output')
    parser.add_argument('--trusted-object-arrays', action='store_true',
                        help='Only use for your own trusted NPZ files containing pickled observation dictionaries.')
    args = parser.parse_args()
    print(convert_npz(args.source, args.output, args.trusted_object_arrays))
