"""Strict frame construction; no zero-image substitutes or silent action padding."""
import numpy as np


def build_dp_frame(obs, action, camera_mapping, image_resolution):
    joints = np.asarray(obs['joints'], dtype=np.float32)
    gripper = np.asarray(obs['gripper'], dtype=np.float32).reshape(-1)
    action = np.asarray(action, dtype=np.float32)
    if joints.shape != (7,) or action.shape != (7,) or gripper.shape != (1,):
        raise ValueError(f'Expected joints/action=(7,), gripper=(1,); got '
                         f'{joints.shape}/{action.shape}/{gripper.shape}.')
    frame = dict(robot_joint=joints.copy(), gripper_position=gripper.copy(), action=action.copy())
    for obs_key, camera_key in camera_mapping.items():
        if obs_key not in obs:
            raise ValueError(f'Missing camera {obs_key}; refusing to save a black placeholder.')
        image = np.asarray(obs[obs_key])
        if image.shape != (*image_resolution, 3) or image.dtype != np.uint8:
            raise ValueError(f'{obs_key}: expected uint8 {(*image_resolution, 3)}, '
                             f'got {image.shape}/{image.dtype}.')
        frame[camera_key] = image.copy()
    for key, value in frame.items():
        if not np.isfinite(value).all():
            raise ValueError(f'{key}: nonfinite values.')
    return frame
