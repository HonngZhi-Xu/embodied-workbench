# UR5e / GELLO 的 DP 离线训练修复

基于 `presistin/gello_software` 的 `03a16761ed0f5b813423a4e78ed47595bf9d5e05`。本次只修改采集保存、数据读取和离线训练；没有执行机械臂控制、修改夹爪控制参数或进行实机训练。

## 已修复的代码问题

- 使用专用 `GelloZarrImageDataset` 读取直接保存的 Zarr，不再走 Push-T 的视频目录加载器；支持 `[7]` 关节/夹爪动作。
- 当前三相机格式使用 `camera_0/1/2`、`robot_joint`；历史两相机格式使用 `image0/image1`、`agent_pos`，各有独立任务配置。也可显式配置 key_map，禁止猜测字段映射。
- 按完整 episode 划分训练/验证；采样和 padding 不越过片段边界；归一化仅拟合训练集。
- 图像保留在磁盘中，按需读取观测帧；多进程 DataLoader 通过重开只读 store 工作。
- 新增 `train.py`、`train_gello_dp` 配置与离线 runner。训练不会创建机器人对象；检查点按验证损失选择，未伪造实机成功率。
- 新增只读检查和副本修复工具；训练加载器默认检查物理 chunk，缺失块不能被默认为有效黑图/零值。
- 新采集帧先整体校验再追加：缺图、非 7 维动作、非有限值会报错，不再静默补黑图、补零或截断动作。完整 episode 写盘前再次校验长度。
- 旧 NPZ 转换器改为显式输入输出参数，先检查帧数和边界，再创建新 Zarr，拒绝覆盖原文件。

## 安装

建议独立 Python 3.11 环境。不要在已经运行 GELLO 实机采集的环境中直接升级依赖。

```bash
python -m venv .venv
# Linux:
source .venv/bin/activate
# Windows PowerShell:
# .\.venv\Scripts\Activate.ps1
pip install -r requirements-dp.txt
```

requirements-dp.txt 为离线计算依赖。需要 GPU 时，按现场驱动安装对应 CUDA 构建的 torch/torchvision；本次验证使用 Windows / Python 3.11 / PyTorch 2.5.1 CPU。

## 检查数据

```bash
python tools/check_gello_dataset.py /path/to/replay_buffer.zarr --check-chunks --json-output report.json
```

也接受包含 replay_buffer.zarr 的父目录。非有效数据返回退出码 2。应使用完整的原始数据目录，稀疏下载的图像数据会被 chunk 检查拒绝。检查时间戳只能证明结构和局部单调性，不能证明相机、主从臂已经硬件同步。

## 训练

当前三路相机直接 Zarr 数据：

```bash
python train.py --config-name=train_gello_dp task.dataset.dataset_path=/path/to/replay_buffer.zarr
```

历史两路相机直接 Zarr 数据（必须先通过完整数据检查）：

```bash
python train.py --config-name=train_gello_dp task=gello_legacy_image task.dataset.dataset_path=/path/to/valid_legacy.zarr
```

首次只验证运行链路，可加：

```bash
python train.py --config-name=train_gello_dp task.dataset.dataset_path=/path/to/valid.zarr training.device=cpu training.num_epochs=1 training.max_train_steps=1 training.max_val_steps=1 dataloader.batch_size=2 val_dataloader.batch_size=2 dataloader.num_workers=0 val_dataloader.num_workers=0 policy.num_inference_steps=2 logging.mode=disabled
```

正式训练默认使用本地离线 WandB 日志，不上传数据。验证比例为 10%，至少需要两条完整 episode。训练动作是 6 个关节位置目标和 1 个夹爪目标，不是 TCP 位姿，也不是 VLA。

## 旧 NPZ 转换

```bash
python npz_to_zarr.py /path/to/merged_all_episodes.npz /path/to/new_legacy.zarr --trusted-object-arrays
```

只对自己可信的观测字典 NPZ 使用该参数。观测/动作数量不一致、非法边界、非 uint8 图像或非 7 维动作将直接报错，输出目录不会被创建。

## 仓库样例的实际检查结果

`data/gello_replay_buffer.zarr`：

- action: `(897, 7)`。
- agent_pos: `(896, 7)`。
- image0 / image1: `(896, 480, 640, 3)`。
- episode_ends: `[400, 526, 602, 757, 897]`。
- action 首行是全零。数据中没有 timestamp。

**没有自动删除任何一行。** 首行动作是零，并不能证明首行就是唯一多出的动作；简单删末行并把边界改为 896 也不能证明时间对齐。现有元数据、动作与状态不足以确定缺失观测位于哪一帧。真正恢复该样例，需要找到原始逐帧 NPZ/采集记录，或者重新采集。训练器会在此前明确拒绝它，避免生成错位标签模型。

如已从原始记录确认多出的 action 索引及所有新的 episode 边界，可使用：

```bash
python tools/check_gello_dataset.py SOURCE --drop-action-index VERIFIED_INDEX --episode-ends VERIFIED_COMMA_SEPARATED_BOUNDARIES --output NEW_COPY --reason "原始记录核对依据"
```

该命令只支持“观测数组长度一致，action 恰好多一行”的情形；输出必须是未存在的独立目录。它先检查源 chunk，再复制、删除指定 action 行、更新指定边界，最终复检并写 repair_manifest.json。它不会自动推断时间对齐。上面的 VERIFIED 参数必须根据真实记录填写，不是可直接运行的示例值。

## 验证

```bash
python -m pytest tests/test_gello_dp.py -q
```

本次 11 项测试通过，覆盖新旧 Zarr、片段隔离、正常归一化、错帧与缺块拒绝、副本修复不修改原始数据、严格采集帧、旧 NPZ 转换写前校验、Hydra 配置，以及 DP 损失/反向传播/动作预测。完整 workspace 测试在合成数据上运行一次 CPU 优化步骤和验证，并生成可读的真实检查点。**这验证了计算链路，不代表已在真实抓放数据上完成训练或达到实机成功率。**

代码位于本地独立目录。当前 GitHub 连接对原仓库只有读取权限，因此没有推送远程。补丁可应用到相同基线：

```bash
git apply --check ur5e-gello-dp-fixes.patch
git apply ur5e-gello-dp-fixes.patch
```
