"""Offline DP training. Never starts a robot or a GELLO teleoperation process."""
import hydra
from hydra.utils import get_class
from omegaconf import OmegaConf

OmegaConf.register_new_resolver('eval', eval, replace=True)


@hydra.main(version_base=None, config_path='diffusion_policy/config', config_name='train_gello_dp')
def main(cfg):
    workspace = get_class(cfg._target_)(cfg)
    workspace.run()


if __name__ == '__main__':
    main()
