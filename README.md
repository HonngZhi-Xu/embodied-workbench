# 具身工坊 · G1 / UR5e 多模态研发平台

当前交付 **v0.2.1**，优先供机器人同学接入 **UR5e＋GELLO＋二指夹爪＋三路 RealSense（两固定侧视、一腕部）＋PaXini M3025 串口触觉**，完成抓取放置数据采集和 Diffusion Policy 离线训练。首次接入请先读 [硬件交接说明](HARDWARE_HANDOFF.md)。

```bash
git clone https://github.com/HonngZhi-Xu/embodied-workbench.git
cd embodied-workbench
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r edge-requirements.txt
# 独立机械臂状态 / RealSense 预览按需安装：
# pip install ur-rtde pyrealsense2
python -m edge_agent.server
```

建议 Python 3.11，打开 http://127.0.0.1:8787/#ur5e 后点击“连接本地工作台”。现有 GELLO 运行环境和运动流程由现场同学管理，边缘工作台只读机器人状态，并通过可选发布器接收采集帧；不会在启动时自动移动机械臂。

| 入口 | 内容 |
| --- | --- |
| [HARDWARE_HANDOFF.md](HARDWARE_HANDOFF.md) | 首次接入顺序、待确认设备项、验收记录和当前限制 |
| [EDGE_SETUP.md](EDGE_SETUP.md) | 设备、串口、GELLO 发布、采集、数据与本地训练 |
| [CAMERA_CHECK_SETUP.md](CAMERA_CHECK_SETUP.md) | 三相机基准、标记／内参、腕部参考姿态与采集门控 |
| [架构说明](docs/architecture-v03/ARCHITECTURE_SPEC.md) | 端边云、数据与模型资产库的目标设计 |
| [robotics/UR5E_DP_SETUP.md](robotics/UR5E_DP_SETUP.md) | 7D 动作、直接 Zarr 适配、历史数据检查与训练 |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | 上游来源与现有许可证 |

已有离线验证：29 项 Python 测试及前端脚本检查通过；未完成现场硬件验收。当前录制缓冲上限为 256MiB，三路 RGB 在 10Hz 下约 9 秒量级，首次只录 3–5 秒验证。正式长片段采集需先补流式写盘。完整数据库、模型注册表、实机策略执行与 VLA 尚待开发；以下 G1 流程保留演示标识。

离线回归命令（不连接机器人）：

```bash
node check.cjs
node check-platform.cjs
node check-camera.cjs
python -m pytest tests/test_camera_guard.py tests/test_edge_integration.py robotics/tests/test_gello_dp.py -q --basetemp=outputs/pytest
```

Python 测试会使用合成数据运行真实 CPU 训练子进程并保存检查点，需要安装完整依赖与可用磁盘空间。

v0.2 保留 G1 / 因时灵巧手演示，并集成 UR5e / GELLO、RealSense RGB、PaXini M3025 串口以及实际离线 DP 训练后端。在线静态版提供演示；本地工作台通过 edge_agent 接入实际设备与训练。具体安装和实测边界见 [EDGE_SETUP.md](EDGE_SETUP.md)。

本地实机工作台：`python -m edge_agent.server`，访问 http://127.0.0.1:8787/#ur5e 。必须先按 EDGE_SETUP.md 安装依赖；不会在服务启动时自动连接设备。下面原有 G1 页面仍为模拟流程。

v0.2.1 新增“三相机采前检查”：两个固定侧视、一个腕部相机，支持标记相对位姿、静态参照画面比较、人工调整指导与采集拦截；基准版本和检查记录关联 Zarr。原始 RGB 不做配准覆盖。配置说明见 [CAMERA_CHECK_SETUP.md](CAMERA_CHECK_SETUP.md)，本地入口 http://127.0.0.1:8787/#camera-check 。尚未完成现场硬件验证。

## 运行

直接打开 `dist/index.html`，或在本目录运行 `python -m http.server 8765 --directory dist`，访问 http://localhost:8765 。无需安装前端依赖。

## 演示路径

1. 设备调试：调整归一化示意控制量，运行模拟自检。
2. 遥操作数采：开始采集，等待数秒，保存片段。
3. 数据管理：搜索与筛选，查看回放时间轴，导出 JSON 清单。
4. 模型中心：选择 ACT 或 π₀ / VLA；SONIC、SuperMimic 显示接口预留。
5. 训练实验：选择数据集与模型，提交模拟训练，约 25 秒完成。
6. 部署运行：发布完成的实验到模拟节点，生成失败片段回流或撤回版本。
7. 整体架构：查看并下载 SVG 架构图。

数据保存在当前浏览器 localStorage。侧边栏可重置。不同浏览器与不同来源地址不共享数据。

## 文件与边界

- `dist/index.html` / `style.css` / `app.js`：静态界面与交互。
- `dist/architecture.svg`：可独立查看的整体架构图。
- `ARCHITECTURE.md`：模块边界与后续接入说明。

机器人示意和相机画面为 SVG 示意图，并非真实遥测或数字孪生。具体 G1 版本、因时型号、通信协议、相机及触觉配置需在实际接入时确认。
