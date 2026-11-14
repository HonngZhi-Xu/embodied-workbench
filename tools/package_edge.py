"""Build a standalone local workbench bundle; never include sensor manuals or data."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
target = root/'dist/downloads/edge-integration.zip'
target.parent.mkdir(exist_ok=True)
paths = []
for directory in ('dist', 'edge_agent', 'robotics', 'tests', 'docs'):
    for path in (root/directory).rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(x in relative.parts for x in ('downloads', '__pycache__', '.openai', '.pytest_cache', '.git')):
            continue
        paths.append(path)
paths += [root/p for p in ('EDGE_SETUP.md', 'CAMERA_CHECK_SETUP.md', 'UR_SITE_WORKFLOW.md', 'README.md', 'HARDWARE_HANDOFF.md', 'THIRD_PARTY_NOTICES.md', 'edge-requirements.txt', 'check.cjs', 'check-platform.cjs', 'check-camera.cjs', 'check-ur-workflow.cjs', 'check-ur-experience.cjs', 'check-tactile-integration.cjs', 'tools/generate_camera_marker.py', 'dist/downloads/aruco-4x4-50-id0.png')]
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(paths):
        archive.write(path, path.relative_to(root).as_posix())
print(f'{target}: {len(paths)} files, {target.stat().st_size} bytes')
