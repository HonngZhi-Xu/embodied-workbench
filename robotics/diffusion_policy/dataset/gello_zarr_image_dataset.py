"""Lazy image dataset for GELLO's direct Zarr format, supporting seven actions."""
import copy
import numpy as np
import torch
import zarr
from diffusion_policy.common.gello_zarr import require_valid, resolve_path
from diffusion_policy.common.replay_buffer import ReplayBuffer
from diffusion_policy.common.sampler import SequenceSampler, get_val_mask
from diffusion_policy.dataset.base_dataset import BaseImageDataset
from diffusion_policy.model.common.normalizer import LinearNormalizer, SingleFieldLinearNormalizer
from diffusion_policy.common.normalize_util import get_image_range_normalizer


class GelloZarrImageDataset(BaseImageDataset):
    def __init__(self, dataset_path, shape_meta, horizon=16, pad_before=1,
                 pad_after=7, n_obs_steps=2, seed=42, val_ratio=0.1,
                 key_map=None, check_chunks=True):
        self.path = str(resolve_path(dataset_path))
        require_valid(self.path, check_chunks=check_chunks)
        self.shape_meta = shape_meta
        self.horizon, self.pad_before, self.pad_after = horizon, pad_before, pad_after
        self.n_obs_steps = n_obs_steps
        if not 1 <= n_obs_steps <= horizon or not 0 <= val_ratio < 1:
            raise ValueError('Invalid observation horizon or validation ratio.')
        self.key_map = dict(key_map or {})
        self.rgb_keys = [k for k, v in shape_meta['obs'].items() if v.get('type') == 'rgb']
        self.lowdim_keys = [k for k in shape_meta['obs'] if k not in self.rgb_keys]
        self._root = zarr.open_group(self.path, mode='r')
        self.ends = self._root['meta/episode_ends'][:]
        self.val_mask = get_val_mask(len(self.ends), val_ratio, seed)
        if val_ratio > 0 and not self.val_mask.any():
            raise ValueError('At least two episodes are required for a validation split.')
        self.episode_mask = ~self.val_mask
        data = self._root['data']
        for key, meta in shape_meta['obs'].items():
            source = self.key_map.get(key, key)
            if source not in data:
                raise ValueError(f'Missing {source}; provide an explicit key_map for legacy arrays.')
            arr = data[source]
            if key in self.rgb_keys and arr.ndim != 4:
                raise ValueError(f'{source}: expected (T,H,W,C) RGB array, got {arr.shape}.')
            expected = tuple(meta['shape'])
            actual = (arr.shape[-1], *arr.shape[1:3]) if key in self.rgb_keys else arr.shape[1:]
            if actual != expected or (key in self.rgb_keys and (arr.ndim != 4 or arr.dtype != np.uint8)):
                raise ValueError(f'{source}: expected {expected}, uint8 HWC for RGB; got {arr.shape}, {arr.dtype}.')
        if tuple(shape_meta['action']['shape']) != (7,):
            raise ValueError('GELLO joint/gripper actions require shape_meta.action.shape=[7].')
        self._make_sampler()

    def _make_sampler(self):
        if self._root is None:
            self._root = zarr.open_group(self.path, mode='r')
        data = {k: self._root['data'][self.key_map.get(k, k)] for k in self.lowdim_keys}
        data['action'] = self._root['data/action']
        self.sampler = SequenceSampler(
            ReplayBuffer({'data': data, 'meta': {'episode_ends': self.ends}}),
            sequence_length=self.horizon, pad_before=self.pad_before,
            pad_after=self.pad_after, episode_mask=self.episode_mask)
        if self.episode_mask.any() and not len(self.sampler):
            raise ValueError('No usable sequences; episodes too short for this horizon/padding.')

    def __getstate__(self):
        state = self.__dict__.copy()
        state['_root'], state['sampler'] = None, None
        return state

    def __len__(self):
        if self.sampler is None:
            self._make_sampler()
        return len(self.sampler)

    def __getitem__(self, index):
        if self.sampler is None:
            self._make_sampler()
        sample = self.sampler.sample_sequence(index)
        start, end, sample_start, _ = self.sampler.indices[index]
        obs_indices = np.clip(np.arange(self.n_obs_steps) + start - sample_start, start, end - 1)
        obs = {}
        for key in self.shape_meta['obs']:
            if key in self.rgb_keys:
                # Read only observation frames, not the entire future action horizon.
                value = self._root['data'][self.key_map.get(key, key)].oindex[obs_indices]
                value = np.moveaxis(value, -1, 1).astype(np.float32) / 255.0
            else:
                value = sample[key][:self.n_obs_steps].astype(np.float32)
            obs[key] = torch.from_numpy(np.ascontiguousarray(value))
        return {'obs': obs, 'action': torch.from_numpy(sample['action'].astype(np.float32))}

    def get_validation_dataset(self):
        dataset = copy.copy(self)
        dataset.episode_mask = self.val_mask.copy()
        dataset._make_sampler()
        return dataset

    def _training_values(self, source_key):
        root = zarr.open_group(self.path, mode='r') if self._root is None else self._root
        starts = np.r_[0, self.ends[:-1]]
        return np.concatenate([root['data'][source_key][s:e] for s, e, keep in
                               zip(starts, self.ends, ~self.val_mask) if keep])

    def get_normalizer(self, **kwargs):
        # Fit ONLY training episodes; held-out episodes must not leak into statistics.
        result = LinearNormalizer()
        result['action'] = SingleFieldLinearNormalizer.create_fit(self._training_values('action'), **kwargs)
        for key in self.lowdim_keys:
            result[key] = SingleFieldLinearNormalizer.create_fit(
                self._training_values(self.key_map.get(key, key)), **kwargs)
        for key in self.rgb_keys:
            result[key] = get_image_range_normalizer()
        return result

    def get_all_actions(self):
        return torch.from_numpy(self._training_values('action').astype(np.float32))
