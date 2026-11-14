# Third-party source and licensing

The `robotics/` directory contains source based on
[`presistin/gello_software`](https://github.com/presistin/gello_software),
upstream baseline `03a16761ed0f5b813423a4e78ed47595bf9d5e05`,
including GELLO / Diffusion Policy code and local data/training adaptations.
Retain the original MIT license and copyright notice in `robotics/LICENSE`.
Dependencies retain their respective licenses; they are installed separately.

The PaXini serial adapter implements protocol definitions from the user-supplied
PX-6AX GEN3 manual V1.0.5. The vendor manual, SDK binaries and original hardware
photos are not redistributed in this repository.

Public repository visibility does not by itself apply the upstream MIT license
to every newly authored workbench component. This handoff preserves existing
license notices and does not assign a new project-wide license.

## User-provided M3025 research application

The tactile research client is adapted from the user's
`M3025_Tactile_Workbench_Source.zip` export, original source revision
`a4be0537773615ccdc322726b2b3a70c794d64ed`. Selected React UI and analysis
helpers retain their source attribution in `tactile-research/SOURCE.json`.
The supplied application has no top-level license granting a new project-wide
license; this integration does not assign one. Third-party React, Lucide,
esbuild and their dependencies retain their own package licenses.

Changes include static client bundling, explicit browser-demo/local modes,
platform-owned SQLite/file storage, platform asset import, separate raw-count
and resultant-force units, numerical validation and an isolated platform view.
The export's Site identity, auth scaffolding and cloud bindings are not reused.
