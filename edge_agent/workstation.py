"""Persistent workstation settings, independent of device connection state."""
import json
from pathlib import Path
import threading
import time
import uuid
from edge_agent.ur_workflow import training_plan
from edge_agent.assets import CAPTURE_MODES, DEFAULT_EXTERNAL_ROOTS, ExternalAssets


class WorkstationStore:
    def __init__(self, data_root):
        self.path = Path(data_root) / 'workstation.json'
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            if not self.path.exists():
                return dict(configured=False, profile=None)
            profile = json.loads(self.path.read_text('utf-8'))
            profile.setdefault('capture_mode', 'rgb_tactile')
            profile.setdefault('resume_path', '')
            profile.setdefault('external_data_roots', list(DEFAULT_EXTERNAL_ROOTS))
            return dict(configured=True, profile=profile)

    def save(self, profile, expected_revision):
        text_fields = ('name', 'robot_ip', 'gripper', 'camera_0', 'camera_1', 'camera_2',
                       'capture_script', 'project', 'conda_env', 'output_root', 'port')
        clean = {}
        for key in text_fields:
            value = profile.get(key, '')
            if not isinstance(value, str) or len(value) > 1024 or any(c in value for c in ('\0','\n','\r')):
                raise ValueError('Invalid workstation field: ' + key)
            clean[key] = value.strip()
        for key in ('name', 'capture_script', 'project', 'conda_env', 'output_root'):
            if not clean[key]:
                raise ValueError('请填写工位名称和运行环境配置：' + key)
        if not clean['capture_script'].endswith('/start_live_follow_local.sh'):
            raise ValueError('采集脚本需指向 start_live_follow_local.sh。')
        if not clean['capture_script'].startswith('/'):
            raise ValueError('请填写采集脚本的 Linux 绝对路径。')
        training_plan({k: clean[k] for k in ('project', 'conda_env', 'output_root')})
        serials = [clean[k] for k in ('camera_0', 'camera_1', 'camera_2') if clean[k]]
        if len(set(serials)) != len(serials):
            raise ValueError('三路相机序列号不能重复。')
        for key, default in (('address', 1), ('baud', 921600)):
            value = profile.get(key, default)
            if type(value) is not int or not 1 <= value <= (255 if key == 'address' else 4000000):
                raise ValueError('Invalid ' + key)
            clean[key] = value
        clean['capture_mode'] = profile.get('capture_mode', 'rgb_tactile')
        if clean['capture_mode'] not in CAPTURE_MODES:
            raise ValueError('采集模式必须是 rgb_tactile 或 rgb_only。')
        clean['resume_path'] = profile.get('resume_path', '')
        if (not isinstance(clean['resume_path'], str) or len(clean['resume_path']) > 2048
                or any(c in clean['resume_path'] for c in '\0\n\r')):
            raise ValueError('Invalid resume_path.')
        clean['resume_path'] = clean['resume_path'].strip()
        roots = profile.get('external_data_roots', list(DEFAULT_EXTERNAL_ROOTS))
        validator = ExternalAssets(self.path.parent)
        validator.configure(roots)
        clean['external_data_roots'] = list(roots)
        if clean['resume_path']:
            validator.check_resume(clean['resume_path'], clean['capture_mode'])
        with self.lock:
            current = self.read()['profile']
            if expected_revision != (current['revision'] if current else None):
                raise ValueError('工位配置已更新，请重新加载后再修改。')
            clean.update(revision=uuid.uuid4().hex, saved_at=time.time())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
            return dict(configured=True, profile=clean)
