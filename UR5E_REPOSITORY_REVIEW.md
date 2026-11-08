# presistin/gello_software 接入检查

检查版本：main，提交 `03a16761ed0f5b813423a4e78ed47595bf9d5e05`。
检查方式：GitHub 文件与 Git tree 静态读取、Zarr 元数据读取。没有下载全部数据块、执行仓库代码、连接机器人或启动训练。连接当前可读取仓库，未获得推送权限。

## 已有能力

- `experiments/run_env.py` 已接入 wrist、base、camera_2 三路相机客户端；此前关于上游相机默认关闭的判断不适用于此版本。
- `gello/cameras/realsense_camera.py` 配置 640×480、30 fps，输出 RGB 图像。具体相机实物型号仍需确认。
- `gello/robots/ur.py` 通过 RTDE 控制 UR 关节，启用夹爪时接口为 7 维。夹爪后端支持 onrobot（默认）和 wsg50，OnRobot 支持读取实际宽度，读取失败时回退到平滑目标值。
- `gello/utils/control_utils.py` 已有直接写 Zarr 的 DiffusionPolicySaveInterface，使用后台保存线程，并保存 camera_0/1/2、robot_joint、gripper_position、action、timestamp、stage。
- 仓库有 NPZ 合并与 Zarr 转换脚本，以及 DP 模型、workspace 和任务配置。

## 阻塞与适配项

1. **7 维动作与加载器不兼容。** `diffusion_policy/config/task/gello_image.yaml` 指定 action=[7]，却使用 `RealPushTImageDataset`。该类 `_get_replay_buffer` 的断言只接受 (2,) 或 (6,)，按此配置初始化会失败。
2. **数据容器布局不一致。** 该加载器调用的 `real_data_to_replay_buffer` 需要 dataset_path/replay_buffer.zarr 和 dataset_path/videos。当前任务路径直接指向 Zarr；新采集器把图像直接写入 Zarr，也未按该视频布局存放。仅修改动作断言不足以接通。
3. **新旧字段约定不一致。** 当前采集保存 camera_0/1/2、robot_joint；任务配置和旧转换脚本使用 image0/image1、agent_pos。需要为直接 Zarr 数据增加适配器，并明确使用两路还是三路相机。
4. **样例时间长度不一致。** `data/gello_replay_buffer.zarr` 的元数据为 action=(897,7)、agent_pos=(896,7)、image0/image1=(896,480,640,3)。episode_ends 数组长度为 5，但本次未解码其内容，不能据此确认片段边界正确。需追溯错位来源，不能简单裁一帧作为修复。
5. **训练入口与配置还需补齐。** 检查的 Git tree 没有根级 train.py；`gello_image.yaml` 没有 env_runner，而 `TrainDiffusionUnetImageWorkspace` 会读取 cfg.task.env_runner。需提供离线训练入口与匹配的离线 runner/评测处理。
6. **观测有效性需显式记录。** UR 接口中 joint_velocities 实为 joints、ee_pos_quat 为零占位；当前 RobotEnv 没有向采集器透传这两项，首期可继续仅使用真实关节反馈。夹爪读取失败时的估计值应与真实测量值区分。
7. **采集有效性及时序。** 保存器对缺失相机补黑图、异常动作截断/补零，缺少有效性标记。保存时间采用统一 time.time()，相机与机器人顺序获取，不能据此认定已做来源时戳同步。训练前应检查缺帧、实际采样间隔及动作延迟。

## 建议最先实现的改动

新增专用 GELLO 直接 Zarr 数据集适配器 → 统一任务字段与 7 维关节/夹爪动作 → 增加只读数据检查命令 → 提供独立离线训练入口与最小训练配置 → 用已有片段做小规模训练冒烟验证 → 接入真实推理及受控实机评测。

复用现有遥操作及硬件驱动。第一轮先完成数据与离线训练，不因启动训练而实例化机器人控制对象。工作台仍为模拟原型，之后通过本地服务调用这些模块。

## 文件证据（固定提交）

- [采集入口](https://github.com/presistin/gello_software/blob/03a16761ed0f5b813423a4e78ed47595bf9d5e05/experiments/run_env.py)
- [采集保存器](https://github.com/presistin/gello_software/blob/03a16761ed0f5b813423a4e78ed47595bf9d5e05/gello/utils/control_utils.py)
- [UR 和夹爪接口](https://github.com/presistin/gello_software/blob/03a16761ed0f5b813423a4e78ed47595bf9d5e05/gello/robots/ur.py)
- [GELLO 训练任务配置](https://github.com/presistin/gello_software/blob/03a16761ed0f5b813423a4e78ed47595bf9d5e05/diffusion_policy/config/task/gello_image.yaml)
- [数据加载器](https://github.com/presistin/gello_software/blob/03a16761ed0f5b813423a4e78ed47595bf9d5e05/diffusion_policy/dataset/real_pusht_image_dataset.py)
- [样例数据](https://github.com/presistin/gello_software/tree/03a16761ed0f5b813423a4e78ed47595bf9d5e05/data/gello_replay_buffer.zarr)
