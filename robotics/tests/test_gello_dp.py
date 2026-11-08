import pickle
import sys
from pathlib import Path
import numpy as np
import pytest
import torch
import zarr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diffusion_policy.common.gello_zarr import inspect_dataset, repair_action_row, validate_episode
from diffusion_policy.dataset.gello_zarr_image_dataset import GelloZarrImageDataset
from gello.data_utils.dp_frame import build_dp_frame


def make_data(path, legacy=False):
    root = zarr.open_group(str(path), mode='w')
    d = root.create_group('data')
    meta = root.create_group('meta')
    meta.array('episode_ends', np.array([12, 24, 36], dtype=np.int64))
    values = np.repeat(np.array([0.1, 0.4, 0.9], dtype=np.float32), 12)
    d.array('action', np.repeat(values[:, None], 7, axis=1), chunks=(12, 7))
    d.array('agent_pos' if legacy else 'robot_joint', np.repeat(values[:, None], 7, axis=1))
    keys = ['image0', 'image1'] if legacy else ['camera_0', 'camera_1', 'camera_2']
    for key in keys:
        # Nonzero frames so missing physical chunks cannot masquerade as samples.
        d.array(key, np.full((36, 8, 8, 3), 127, dtype=np.uint8), chunks=(1, 8, 8, 3))
    return root


def shapes(legacy=False):
    keys = ['image0', 'image1'] if legacy else ['camera_0', 'camera_1', 'camera_2']
    obs = {key: {'shape': [3, 8, 8], 'type': 'rgb'} for key in keys}
    obs['agent_pos' if legacy else 'robot_joint'] = {'shape': [7], 'type': 'low_dim'}
    return {'obs': obs, 'action': {'shape': [7]}}


@pytest.mark.parametrize('legacy', [False, True])
def test_direct_zarr_seven_actions_and_split(tmp_path, legacy):
    path = tmp_path/'direct.zarr'
    make_data(path, legacy)
    ds = GelloZarrImageDataset(str(path), shapes(legacy), horizon=8, pad_before=1, pad_after=3)
    sample = ds[0]
    assert sample['action'].shape == (8, 7)
    key = 'image0' if legacy else 'camera_0'
    assert sample['obs'][key].shape == (2, 3, 8, 8)
    assert sample['obs'][key].dtype == torch.float32
    assert torch.allclose(sample['obs'][key], torch.tensor(127/255))
    val = ds.get_validation_dataset()
    assert not (ds.episode_mask & val.episode_mask).any()
    # An entire window, including padding, must stay within one episode.
    for i in range(len(ds)):
        assert ds[i]['action'][:, 0].unique().numel() == 1
    normalizer = ds.get_normalizer()
    expected = ds.get_all_actions()
    torch.testing.assert_close(normalizer['action'].get_input_stats()['max'], expected.max(dim=0).values)
    torch.testing.assert_close(normalizer['action'].get_input_stats()['min'], expected.min(dim=0).values)
    restored = pickle.loads(pickle.dumps(ds))
    torch.testing.assert_close(restored[0]['action'], sample['action'])


def test_reject_length_mismatch_and_missing_chunk(tmp_path):
    path = tmp_path/'bad.zarr'
    root = make_data(path)
    root['data/action'].resize((37, 7))
    with pytest.raises(ValueError, match='length 37'):
        GelloZarrImageDataset(path, shapes())
    root['data/action'].resize((36, 7))
    del root.store[root['data/camera_0']._chunk_key((3, 0, 0, 0))]
    with pytest.raises(ValueError, match='missing physical chunks'):
        GelloZarrImageDataset(path, shapes())


def test_bad_episode_boundaries_and_nonfinite(tmp_path):
    root = make_data(tmp_path/'bad.zarr')
    root['meta/episode_ends'][:] = [12, 12, 36]
    assert not inspect_dataset(tmp_path/'bad.zarr')['valid']
    root['meta/episode_ends'][:] = [12, 24, 36]
    root['data/action'][2, 0] = np.nan
    assert any('nonfinite' in e for e in inspect_dataset(tmp_path/'bad.zarr')['errors'])


def test_repair_copy_preserves_source_and_requires_boundaries(tmp_path):
    source, target = tmp_path/'source.zarr', tmp_path/'fixed.zarr'
    root = make_data(source)
    root['data/action'].resize((37, 7))
    root['data/action'][36] = .9
    root['meta/episode_ends'][:] = [12, 24, 37]
    with pytest.raises(ValueError, match='episode_ends'):
        repair_action_row(source, target, 36, 'verified test fixture extra terminal action')
    assert not target.exists()
    repair_action_row(source, target, 36, 'verified test fixture extra terminal action', [12, 24, 36])
    assert root['data/action'].shape == (37, 7)
    assert root['meta/episode_ends'][-1] == 37
    assert inspect_dataset(target, check_chunks=True)['valid']
    assert (target/'repair_manifest.json').exists()
    with pytest.raises(FileExistsError):
        repair_action_row(source, target, 36, 'verified', [12, 24, 36])
    with pytest.raises(ValueError, match='separate'):
        repair_action_row(source, source/'child', 36, 'verified')


def test_strict_collection_frame_no_padding_or_placeholder():
    obs = {'joints': np.ones(7), 'gripper': np.ones((1, 1)),
           'image_wrist': np.ones((8, 8, 3), dtype=np.uint8)}
    mapping = {'image_wrist': 'camera_0'}
    frame = build_dp_frame(obs, np.ones(7), mapping, (8, 8))
    validate_episode({k: v[None] for k, v in frame.items()})
    with pytest.raises(ValueError, match='Expected'):
        build_dp_frame(obs, np.ones(6), mapping, (8, 8))
    with pytest.raises(ValueError, match='Missing camera'):
        build_dp_frame(obs, np.ones(7), {'missing': 'camera_0'}, (8, 8))
    with pytest.raises(ValueError, match='frame count'):
        validate_episode({'action': np.ones((3, 7)), 'camera_0': np.ones((2, 8, 8, 3))})


@pytest.mark.parametrize('extra_action', [False, True])
def test_legacy_npz_checks_before_writing(tmp_path, extra_action):
    from npz_to_zarr import convert_npz
    obs = np.array([dict(image0=np.ones((8, 8, 3), dtype=np.uint8),
                         image1=np.ones((8, 8, 3), dtype=np.uint8),
                         agent_pos=np.ones(7, dtype=np.float32)) for _ in range(24)], dtype=object)
    n = 25 if extra_action else 24
    source, output = tmp_path/'merged.npz', tmp_path/'converted.zarr'
    np.savez(source, obs=obs, action=np.ones((n, 7), dtype=np.float32), episode_ends=[12, n])
    if extra_action:
        with pytest.raises(ValueError, match='Observation count'):
            convert_npz(source, output, trusted_object_arrays=True)
        assert not output.exists()
    else:
        convert_npz(source, output, trusted_object_arrays=True)
        assert inspect_dataset(output, check_chunks=True)['valid']
        with pytest.raises(FileExistsError):
            convert_npz(source, output, trusted_object_arrays=True)


def test_training_config_composes_and_instantiates_dataset(tmp_path):
    from hydra import compose, initialize_config_dir
    from hydra.utils import instantiate
    from omegaconf import OmegaConf
    OmegaConf.register_new_resolver('eval', eval, replace=True)
    path = tmp_path/'config.zarr'
    make_data(path)
    cfg_dir = str(Path(__file__).resolve().parents[1]/'diffusion_policy/config')
    with initialize_config_dir(version_base=None, config_dir=cfg_dir):
        cfg = compose(config_name='train_gello_dp', overrides=[f'task.dataset.dataset_path={path.as_posix()}'])
    for k in ['camera_0', 'camera_1', 'camera_2']:
        cfg.task.shape_meta.obs[k]['shape'] = [3, 8, 8]
    ds = instantiate(cfg.task.dataset)
    assert ds[0]['action'].shape == (16, 7)
    assert cfg.checkpoint.topk.monitor_key == 'val_loss'
    runner = instantiate(cfg.task.env_runner, output_dir=str(tmp_path))
    assert runner.run(None) == {}


def test_dp_loss_backward_and_predict(tmp_path):
    from diffusers.schedulers.scheduling_ddpm import DDPMScheduler
    from diffusion_policy.policy.diffusion_unet_image_policy import DiffusionUnetImagePolicy
    from diffusion_policy.model.vision.multi_image_obs_encoder import MultiImageObsEncoder
    torch.set_num_threads(1)
    path = tmp_path/'train.zarr'
    make_data(path)
    meta = shapes()
    ds = GelloZarrImageDataset(path, meta, horizon=8, pad_after=3)
    encoder = MultiImageObsEncoder(meta, torch.nn.Sequential(
        torch.nn.Conv2d(3, 8, 3, padding=1), torch.nn.AdaptiveAvgPool2d(1), torch.nn.Flatten()),
        share_rgb_model=True)
    policy = DiffusionUnetImagePolicy(meta, DDPMScheduler(num_train_timesteps=10), encoder,
                                     horizon=8, n_action_steps=4, n_obs_steps=2,
                                     diffusion_step_embed_dim=16, down_dims=[16, 32],
                                     n_groups=4, num_inference_steps=2)
    policy.set_normalizer(ds.get_normalizer())
    batch = next(iter(torch.utils.data.DataLoader(ds, batch_size=2)))
    trained_parameter = next(p for p in policy.model.parameters() if p.numel() and p.requires_grad)
    before = trained_parameter.detach().clone()
    optimizer = torch.optim.Adam(policy.parameters(), lr=1e-3)
    loss = policy.compute_loss(batch)
    assert torch.isfinite(loss)
    loss.backward()
    optimizer.step()
    assert not torch.equal(before, trained_parameter.detach())
    policy.eval()
    with torch.no_grad():
        result = policy.predict_action(batch['obs'])
    assert result['action'].shape == (2, 4, 7)
    assert torch.isfinite(result['action']).all()


def test_full_offline_workspace_checkpoint(tmp_path):
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf
    from diffusion_policy.workspace.train_diffusion_unet_image_workspace import TrainDiffusionUnetImageWorkspace
    torch.set_num_threads(1)
    path = tmp_path/'full.zarr'
    make_data(path)
    OmegaConf.register_new_resolver('eval', eval, replace=True)
    config_dir = str(Path(__file__).resolve().parents[1]/'diffusion_policy/config')
    with initialize_config_dir(version_base=None, config_dir=config_dir):
        cfg = compose(config_name='train_gello_dp', overrides=[
            f'task.dataset.dataset_path={path.as_posix()}', 'training.device=cpu',
            'training.num_epochs=1', 'training.max_train_steps=1', 'training.max_val_steps=1',
            'training.use_ema=false', 'logging.mode=disabled',
            'dataloader.num_workers=0', 'val_dataloader.num_workers=0',
            'dataloader.batch_size=2', 'val_dataloader.batch_size=2',
            'policy.down_dims=[16,32]', 'policy.n_groups=4',
            'policy.diffusion_step_embed_dim=16', 'policy.num_inference_steps=2'])
    for k in ['camera_0', 'camera_1', 'camera_2']:
        cfg.task.shape_meta.obs[k]['shape'] = [3, 8, 8]
    cfg.policy.obs_encoder.resize_shape = [32, 32]
    output = tmp_path/'output'
    output.mkdir()
    workspace = TrainDiffusionUnetImageWorkspace(cfg, output_dir=str(output))
    workspace.run()
    if workspace._saving_thread:
        workspace._saving_thread.join()
    checkpoint = output/'checkpoints/latest.ckpt'
    assert checkpoint.is_file() and checkpoint.stat().st_size > 0
    import dill
    payload = torch.load(checkpoint, pickle_module=dill, map_location='cpu')
    assert payload['state_dicts']['model']
    assert (output/'logs.json.txt').is_file()
