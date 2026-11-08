from diffusion_policy.env_runner.base_image_runner import BaseImageRunner


class OfflineImageRunner(BaseImageRunner):
    """No hardware instantiation and no fabricated success metric during training."""
    def run(self, policy):
        return {}
