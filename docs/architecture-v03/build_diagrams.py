"""Generate editable architecture assets from the current code and target design."""
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent
FONT = 'Microsoft YaHei,SimHei,Arial,sans-serif'


class SVG:
    def __init__(self, width, height):
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                      '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5 0 10Z" fill="#6d8d86"/></marker></defs>',
                      f'<rect width="{width}" height="{height}" fill="#f6f8f8"/>']

    def rect(self, x, y, w, h, fill='white', stroke='#dce5e3', radius=12, dashed=False):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"'+(' stroke-dasharray="7 5"' if dashed else '')+'/>')

    def text(self, x, y, text, size=20, color='#223d3b', weight=400):
        self.parts.append(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(text)}</text>')

    def arrow(self, points, label=None, tx=None, ty=None, dashed=False):
        self.parts.append(f'<path d="{points}" fill="none" stroke="#6d8d86" stroke-width="2" marker-end="url(#arrow)"'+(' stroke-dasharray="7 6"' if dashed else '')+'/>')
        if label:
            self.text(tx, ty, label, 16, '#57746f')

    def box(self, x, y, w, h, title, lines, status='existing'):
        color = {'existing':('#ecf7f1','#b6d6c4','#237451','已有代码'),
                 'partial':('#eef3fc','#c3d1ed','#4e6b9a','部分实现'),
                 'planned':('#fff8eb','#e5cf9e','#95702a','待开发'),
                 'external':('#f0eff8','#d4cce8','#72618e','现有外部链路')}[status]
        self.rect(x,y,w,h,color[0],color[1],10,status=='planned')
        self.text(x+17,y+32,title,21,color[2],650)
        self.text(x+w-112,y+31,color[3],14,color[2],550)
        for i,line in enumerate(lines):
            self.text(x+17,y+63+i*27,line,17,'#4d6863')

    def save(self, filename):
        (OUT/filename).write_text(''.join(self.parts)+'</svg>',encoding='utf-8')


def architecture():
    s=SVG(2200,2090)
    s.text(55,50,'EMBODIED STUDIO  /  SOFTWARE DEVELOPMENT BLUEPRINT',17,'#63877c',600)
    s.text(55,108,'具身智能研发平台 · 完整软件开发架构',40,'#193b33',700)
    s.text(55,147,'一期实机：UR5e + GELLO + RealSense RGB + PaXini M3025　｜　扩展：G1 / 因时 / SONIC / TeleopIt / SuperMimic / VLA',21,'#6a7f79')
    for x,label,color in [(55,'已有代码（离线验证）','#237451'),(405,'部分实现 / 仍有缺口','#4e6b9a'),(755,'待开发（目标架构）','#95702a'),(1105,'现场已有外部控制链路','#72618e')]:
        s.rect(x,173,15,15,color,color,3)
        s.text(x+27,188,label,17,color)
    s.text(1580,188,'架构版本 v0.3  ·  2026-11-08',18,'#6a7f79')

    s.rect(55,220,2090,130,'#193e35','#193e35')
    s.text(80,258,'统一研发工作台 / Web UI',25,'#fff',650)
    s.text(80,297,'设备调试　｜　RGB / 触觉监测　｜　遥操作数采　｜　数据集管理　｜　模型选择　｜　训练实验　｜　模型版本　｜　部署与运维',22,'#d4e9de')
    s.text(80,329,'当前：在线演示 + 本地实机界面　　目标：项目 / 用户 / 设备 / 数据 / 模型统一管理，所有操作可追溯',17,'#a9c7b8')
    s.arrow('M1100 350V386')
    s.rect(55,390,2090,96,'#e9f0ef','#cedcd8')
    s.text(80,426,'管理 API 与任务入口',23,'#315b50',650)
    s.text(80,460,'已有：本地 HTTP / JSON、状态轮询、采集和训练任务 API　　待开发：身份认证、角色权限、项目隔离、持久化任务索引、状态事件推送',20,'#526e66')
    s.arrow('M555 486V530')
    s.arrow('M1645 486V530')

    s.rect(55,534,1010,585,'#eef7f2','#d1e5d9')
    s.text(80,574,'边 · 现场数据与执行服务',27,'#2b6550',650)
    s.text(80,605,'部署在采集工作站 / 边缘计算机；实时执行留在本地',18,'#769083')
    s.box(80,626,465,130,'E01 设备接入与状态', ['UR5e 只读 RTDE / RealSense RGB','M3025 UART 分帧、LRC 与通道解码'])
    s.box(570,626,470,130,'E02 遥操作与动作采集', ['GELLO 数据发布 / PNG 原图','记录反馈、目标指令、序号与时间戳'])
    s.box(80,778,465,130,'E03 多模态记录', ['RGB + 关节 + 动作 + 分布力 / 合力','新鲜度检查 / Zarr / 完整片段边界'])
    s.box(570,778,470,130,'E04 数据预处理与同步', ['已有：格式、帧数、缺块、有限值校验','待补：时钟标定、重采样、流式恢复'],'partial')
    s.box(80,930,465,160,'E05 受控策略执行', ['策略推理 / 动作空间适配','控制权仲裁 / 约束 / 过期指令处置','人工接管 / 模型回退 / 执行数据回流'],'planned')
    s.box(570,930,470,160,'E06 边缘节点管理', ['当前：本地进程与错误日志','待补：节点注册、任务恢复、资源监控','断点续传 / 断网缓存 / 设备健康告警'],'partial')

    s.rect(1105,534,1040,585,'#eef2f9','#d7dfed')
    s.text(1130,574,'云 / 私有服务器 · 数据与学习服务',27,'#4e6487',650)
    s.text(1130,605,'首期可与边缘服务同机；后续拆分至实验室 GPU 服务器',18,'#7c8da6')
    s.box(1130,626,480,130,'C01 数据资产服务', ['已有：片段扫描 / 检查 / 新副本合并','待补：标注、冻结版本、划分清单、检索'],'partial')
    s.box(1635,626,485,130,'C02 训练与实验服务', ['已有：DP 子进程、取消、日志、检查点','待补：实验持久化、远端调度、恢复'],'partial')
    s.box(1130,778,480,130,'C03 算法插件与适配', ['DP：RGB 或 RGB + 触觉已有计算链路','ACT / VLA、全身控制分别定义契约'],'partial')
    s.box(1635,778,485,130,'C04 评测与模型版本库', ['验证损失已有；实机评测与模型注册待补','权重、归一化、设备兼容性、审批状态'],'partial')
    s.box(1130,930,480,160,'C05 模型发布与部署', ['模型包签名 / 校验 / 节点兼容检查','固定版本发布 / 撤回 / 部署历史','候选 → 已评测 → 可部署 → 已归档'],'planned')
    s.box(1635,930,485,160,'C06 统一资产与权限', ['任务 / 设备 / 数据 / 实验 / 模型索引','角色权限 / 审计 / 配额 / 保留策略','元数据与实际文件通过 ID、URI、哈希关联'],'planned')
    s.arrow('M1065 688H1105')
    s.text(1070,718,'数据',14,'#69857c')
    s.arrow('M1105 1007H1065',dashed=True)
    s.text(1069,1036,'模型',14,'#69857c')
    s.arrow('M555 1158V1119',label='观测 / 反馈上行；模型参考下行属后续建设',tx=110,ty=1148)
    s.arrow('M1645 1119V1161',label='索引 / 版本 / 文件读写',tx=1680,ty=1148)

    s.rect(55,1165,1010,235,'#f2eff8','#ddd4e8')
    s.text(80,1205,'端 · 机器人与传感器',26,'#75628c',650)
    s.box(80,1230,465,140,'UR5e + 二指夹爪', ['GELLO 沿用现场已有运动控制路径','平台目前只读状态，不下发运动指令','现场保护、急停与人工接管保留'],'external')
    s.box(570,1230,470,140,'RGB / 触觉 / 人形扩展', ['RealSense / PaXini M3025 已有驱动','G1 / 因时：目前为演示，实机待适配','SONIC / TeleopIt / SuperMimic 分层接入'],'partial')

    s.rect(1105,1165,1040,235,'#faf4e8','#e5d8b9')
    s.text(1130,1205,'统一存储：资产数据库 + 文件 / 对象存储',25,'#8c6d30',650)
    s.box(1130,1230,480,140,'S01 元数据数据库', ['首期 SQLite；多人 / 多站点升级 PostgreSQL','保存索引、版本、关联、权限和状态','registry.db 及业务表尚待建设'],'planned')
    s.box(1635,1230,485,140,'S02 实际文件存储', ['已有：本地 Zarr / 日志 / 检查点','待补：对象存储、内容校验、备份与生命周期','RGB、触觉数组、模型权重与日志文件'],'partial')

    s.rect(55,1440,2090,205,'#fff','#d8e3df')
    s.text(80,1480,'资产血缘与研发闭环',25,'#315f50',650)
    labels=[('采集片段','设备 / 标定 / 来源'),('数据集版本','片段清单 / 固定划分'),('训练实验','代码 / 参数 / 环境'),('模型版本','权重 / 归一化 / 契约'),('评测与部署','任务结果 / 运行版本'),('失败数据回流','接管 / 异常 / 再训练')]
    for i,(title,sub) in enumerate(labels):
        x=80+i*341
        s.rect(x,1510,310,99,'#f2f7f4','#dce7e0',9)
        s.text(x+16,1546,title,22,'#315b4c',600)
        s.text(x+16,1580,sub,17,'#7c8e84')
        if i<5:s.arrow(f'M{x+310} 1557H{x+338}')
    s.text(80,1630,'目标要求：任一模型可追溯其数据版本、训练配置、代码版本、设备契约和评测记录。当前版本尚未形成完整持久化血缘。',18,'#778c82')

    s.rect(55,1685,2090,210,'#eaf0f1','#d3dfe1')
    s.text(80,1725,'贯穿全栈的软件工程与运维',25,'#4f6870',650)
    for i,(title,lines) in enumerate([
        ('研发工程',['代码 / 配置版本与依赖隔离','现有离线测试；自动 CI/CD 待建设']),
        ('数据与设备契约',['关节顺序 / 动作空间 / 相机角色','触觉单位 / 标定版本 / 时间基准']),
        ('可观测性与审计',['已有日志、连接错误与状态','资源指标、审计库、告警待建设']),
        ('可靠性与交付',['检查点原子写入与保存失败传播已有','备份恢复 / 权限 / 灰度发布待建设'])]):
        x=80+i*512
        s.text(x,1767,title,21,'#4f6870',600)
        for j,line in enumerate(lines):s.text(x,1802+j*29,line,18,'#6d8187')
    s.text(55,1946,'实施顺序：① 设备与数采现场验收 → ② 数据库与数据版本 → ③ 实验 / 模型注册 → ④ 实机评测与受控部署 → ⑤ 人形与端边云扩展',21,'#345e50',600)
    s.text(55,1992,'状态依据：当前 v0.2 源码与离线测试。已有代码不等于现场硬件验收，在线页面的演示状态不等于设备连接状态。',19,'#7b8b83')
    s.text(55,2028,'设计原则：管理面与实时控制分离；原始数据保留；训练版本冻结；模型带运行契约；云端不进入机器人实时控制回路。',19,'#7b8b83')
    s.save('software-architecture.svg')


def database():
    s=SVG(2200,1440)
    s.text(55,60,'统一数据与模型资产库 · 逻辑实体关系',36,'#193b33',700)
    s.text(55,103,'目标数据库设计（待开发）｜ 数据库保存结构化索引；大文件保存于本地 / 对象存储；所有版本有唯一 ID。',21,'#778d83')
    columns=[65,605,1145,1685]
    rows=[165,510,855]
    entities=[
        ('device_profile_versions',['id / robot_id / robot_type','关节定义 / 相机角色 / 触觉通道','标定版本 / 单位 / 控制契约']),
        ('episodes',['id / task_id / device_profile_version_id','source_uri / captured_at / source','成功标记 / 质量状态 / 时间基准']),
        ('dataset_versions',['id / dataset_id / version','schema_hash / preprocessing_version','split_manifest_uri / frozen_at']),
        ('training_runs',['id / dataset_version_id / algorithm','code_commit / config_uri / environment','status / log_uri / started_at / seed']),
        ('tasks + projects',['task_id / project_id / owner_id','任务定义 / 标签 / 验收标准','项目成员 / 数据范围 / 访问策略']),
        ('dataset_members',['dataset_version_id / episode_id','片段选取范围 / partition','train / validation / test']),
        ('model_versions',['id / model_id / training_run_id','checkpoint_asset_id / input_contract','normalizer / status / compatibility']),
        ('evaluations',['id / model_version_id / task_id','评测场景 / 试验数 / 成功率','接管 / 超时 / 失败 / 报告URI']),
        ('file_assets',['id / uri / sha256 / bytes','kind / media_type / storage_backend','备份位置 / 校验状态 / 创建时间']),
        ('users + roles + audit_logs',['用户 / 项目成员 / 权限规则','actor / action / resource / timestamp','元数据审计，避免把图像写入审计表']),
        ('deployments',['id / model_version_id / target_node','device_profile_version_id / config_uri','active_version / status / rollback']),
        ('runtime_events',['id / deployment_id / episode_id','接管 / 故障 / 执行结果 / 时间','失败片段 → 新 episode → 新版本'])]
    for i,(name,fields) in enumerate(entities):
        x,y=columns[i%4],rows[i//4]
        s.box(x,y,450,205,name,fields,'planned')
    s.arrow('M515 265H605',label='1:N',tx=535,ty=248)
    s.arrow('M1595 265H1685',label='1:N',tx=1620,ty=248)
    s.arrow('M829 370V510',label='片段 1:N 成员记录',tx=620,ty=435)
    s.arrow('M1260 370V455H950V510',label='数据版本 1:N 成员记录',tx=1020,ty=432)
    s.arrow('M1800 370V455H1500V510',label='实验 1:N 模型版本',tx=1550,ty=432)
    s.arrow('M515 560H565V300H605',label='任务归属',tx=455,ty=443)
    s.arrow('M1595 611H1685',label='1:N',tx=1620,ty=593)
    s.arrow('M1369 715V855',label='一个模型可部署多次',tx=1391,ty=792)
    s.arrow('M1595 955H1685')
    s.text(65,1140,'核心关系',26,'#315c4c',650)
    for i,line in enumerate([
        '设备配置版本 1:N 采集片段；数据集版本 N:M 采集片段，通过 dataset_members 固定来源与训练 / 验证 / 测试划分。',
        '数据集版本 1:N 训练实验；训练实验 1:N 模型版本；模型版本 1:N 评测与部署；执行失败片段进入新的采集记录。',
        'file_assets 为图像、Zarr、权重、配置、日志与报告登记位置及校验值；实际文件不作为数据库的大型二进制字段存放。',
        '首次采用 SQLite + 本地文件目录；多人协作升级 PostgreSQL + 共享文件 / 对象存储，保持唯一 ID 与版本契约不变。',
        '冻结的数据集版本保持成员清单和预处理定义不变；发布的模型包固定权重、归一化、输入输出、设备要求与训练来源。'
    ]):s.text(65,1181+i*43,line,20,'#6d8177')
    s.save('asset-database.svg')


if __name__=='__main__':
    architecture()
    database()
    print(OUT)
