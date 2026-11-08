# Source snapshot

- Software: Embodied Workbench v0.2.1, UR5e / GELLO / RealSense / PaXini / DP.
- Workbench source revision before GitHub handoff documentation:
  `3b2c55e6b5848f59283823f25281da772d1c2374`.
- Robotics upstream baseline: `03a16761ed0f5b813423a4e78ed47595bf9d5e05`.
- Scope: current source and docs, including three-camera preflight checks.
- This is a fresh source snapshot; it does not import the hosted Site's Git
  history or deployment identity.
- Excluded: `.git/`, `.openai/`, Python environments and caches, test output,
  captured datasets, trained checkpoints, vendor manuals, workstation photos
  and local credentials.
- Verification: 29 offline Python tests passed on synthetic fixtures, including
  an actual CPU DP training subprocess and checkpoint creation. Frontend script
  state tests also passed. No physical UR5e / RealSense / PaXini acceptance or
  browser layout acceptance has been completed.

The v0.3 diagrams in `docs/architecture-v03/` describe target architecture;
they do not indicate that the asset database or model registry is implemented.
