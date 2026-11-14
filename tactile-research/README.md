# M3025 触觉研究平台适配模块

此目录将用户提供的 `M3025_Tactile_Workbench_Source.zip` 中研究界面改为可由现有 edge_agent 和静态演示承载的 React 模块。源归属与逐文件原始 SHA-256 见 `SOURCE.json`。没有复制原站点身份、云端绑定或认证配置。

保留八个研究模块：实验总览、数据集与 Episode、传感器与预处理、编码器与预训练配置、融合与 DP 实验、评估与失败分析、研究追溯、计划与研究产出。配置创建和日志可视化不等于已启动训练；本模块不自动连接串口或实际硬件。

## 构建

```sh
cd tactile-research
npm ci
npm run build
npm test
```

固定版本依赖：React / React DOM 19.2.6、lucide-react 1.31.0、esbuild 0.25.12。无需原 Vinext、D1 或 R2 环境。输出到 `../dist/tactile-lab/`，可直接由原平台静态服务器提供。分发许可见生成的 `THIRD_PARTY_LICENSES.txt`，应用来源代码未擅自另加许可证。

## 运行模式

- `index.html?mode=live`：使用同源 `/api/tactile-lab/`，记录与附件存入本机 edge_agent 数据目录。与现有 UR5e 数据资产关联，支持只读选取一个已保存的 M3025 Episode。服务不提供传感器控制或 GPU 预训练任务执行。
- `index.html?mode=demo`：明确标记“浏览器演示”。研究记录和原始附件保存在隔离的 IndexedDB 数据库，不上传云端，不与本地服务共享，不生成虚构实验结果。
- `embedded=1`：父平台 iframe 模式，八个子页导航改为顶部标签，样式与原平台隔离。

原始 JSON / CSV / JSONL 可解析，H5 / NPZ 等只保存附件并提示先转换。单文件上限 20 MiB，单记录 4 MiB；长采集应明确分 Episode 或分块。配置备份不内嵌原始附件，恢复时保持缺失来源提示。文件下载强制作为附件，不执行上传文件。

测点原始 counts 与设备合力 N 分开记录；坐标、标定、时间单位或缺失状态未知时保留未知，不将缺失值静默补零。评价指标来自用户实际日志，布尔/空白输入不会被误算为有效成功分母。
