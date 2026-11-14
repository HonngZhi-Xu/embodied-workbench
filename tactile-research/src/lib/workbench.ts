export type LabRecord = {id:string;kind:string;name:string;status:string;data:any;createdAt?:string;updatedAt?:string;version?:number};
export const statuses=['已验证信息','离线测试结果','待补充实验','技术风险','未知/待确认'];
export const recipes=[
 {id:'vision',name:'纯视觉基线',code:'VIS-BASE',encoder:'无触觉分支',fusion:'仅视觉观测',stage:'下游 DP',shape:'[B,T,C,H,W]',desc:'保留原有视觉链路，建立配对对照。'},
 {id:'raw',name:'原始触觉直接拼接',code:'RAW-CONCAT',encoder:'Flatten · 231 维',fusion:'视觉特征 + 原始触觉',stage:'下游 DP',shape:'[B,T,231]',desc:'作为触觉接入的最小基线，不做空间预训练。'},
 {id:'mlp',name:'空间 MLP',code:'SPATIAL-MLP',encoder:'空间 MLP',fusion:'视觉特征 + 空间特征',stage:'空间预训练',shape:'[B,T,77,3] → [B,T,128]',desc:'先仅用 Masked Force 和 Net Force 验证空间表征。'},
 {id:'gru',name:'空间 MLP + 时序 GRU',code:'MLP-GRU',encoder:'空间 MLP → GRU',fusion:'视觉特征 + 时序触觉',stage:'时序建模',shape:'[B,T,128] → [B,128]',desc:'空间表征通过离线验证后，再比较时间窗口的收益。'},
 {id:'gate',name:'空间 MLP + GRU + 接触门控',code:'CONTACT-GATE',encoder:'空间 MLP → GRU → Gate',fusion:'接触状态控制触觉贡献',stage:'融合消融',shape:'[B,T,77,3] + [B,T,1]',desc:'需接触标签、门控阈值与无接触稳定性评估。'}
];
export function initialRecords():LabRecord[]{return [
 ...recipes.map(x=>({id:'recipe-'+x.id,kind:'experiment',name:x.name,status:'待补充实验',data:{...x,isTemplate:true,templateId:x.id,datasetId:'',preprocessId:'prep-default',modelId:x.id==='vision'?'':'model-spatial',epochs:100,batchSize:64,lr:0.0001,seed:42,observationHorizon:2,predictionHorizon:16,actionHorizon:8,split:'按 episode 分组，禁止随机帧泄漏',metrics:{},curves:[],evidence:'用户指定对照方案；所有超参数为可修改起始配置，尚未训练。'}})),
 {id:'prep-default',kind:'preprocess',name:'M3025 预处理 · 待标定',status:'未知/待确认',data:{sensor:'PaXini M3025',rawShape:[77,3],shape:[77,3],samplingHz:null,timestampUnit:'s',timestampSource:'unknown',forceUnit:'待确认',coordinateFrame:'待确认',mapping:[],missing:'mask',missingPolicy:'保留掩码并记录缺失；不静默填零',normalization:'zscore',fitSplit:'train',commonFrameConfirmed:false,forceDefinition:'unknown',maxGapMs:20,maxSyncErrorMs:10,syncMethod:'nearest',notes:'时间容差为可修改起始配置；实际频率、坐标系、标定与合力定义待核实。'}},
 {id:'model-spatial',kind:'model',name:'Spatial Encoder · v0.1 配置草案',status:'待补充实验',data:{version:'v0.1',architecture:'Spatial MLP',inputTensor:'[B,77,3]',outputTensor:'[B,128]',featureDim:128,maskRatio:.3,curves:[],input:'[B,77,3] + mask + point mapping（若已标定）',output:'[B,128]（可配置）',tasks:['Masked Force','Net Force'],maskedRatio:0.3,lossWeights:{maskedForce:1,netForce:1},gruEnabled:false,dynamicsEnabled:false,features:[],checkpoint:'',pretrainValidation:'待开展受力分布区分验证；任务、维度及权重均为初始方案。'}}
]}
export function downloadText(name:string,text:string,type='application/json'){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
export function parseCSV(text:string):any[]{
 const rows:string[][]=[];let row:string[]=[],cell='',quote=false;
 for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'){if(quote&&text[i+1]==='"'){cell+='"';i++}else quote=!quote}else if(c===','&&!quote){row.push(cell);cell=''}else if((c==='\n'||c==='\r')&&!quote){if(c==='\r'&&text[i+1]==='\n')i++;row.push(cell);if(row.some(Boolean))rows.push(row);row=[];cell=''}else cell+=c}
 if(quote)throw new Error('CSV 含未闭合的引号，请先检查原文件。');
 row.push(cell);if(row.some(Boolean))rows.push(row);if(!rows.length)return[];
 const heads=rows.shift()!.map(x=>x.replace(/^\uFEFF/,'').trim());
 if(heads.some(x=>!x)||new Set(heads).size!==heads.length)throw new Error('CSV 字段名不能为空或重复。');
 return rows.map(r=>{
  if(r.length>heads.length)throw new Error('CSV 行包含多余字段，请核对分隔符或引号。');
  return Object.fromEntries(heads.map((h,i)=>{const raw=r[i];return [h,raw==null||!raw.trim()?null:finiteValue(raw)??raw]}));
 });
}
// Do not coerce booleans, whitespace, arrays or objects into sensor readings.
export function finiteValue(value:any):number|null {
 if(typeof value==='number')return Number.isFinite(value)?value:null;
 if(typeof value!=='string'||!value.trim())return null;
 const number=Number(value);return Number.isFinite(number)?number:null;
}
export function normalizeSamples(input:any[]):any[]{
 if(!Array.isArray(input))throw new Error('samples 必须是帧数组。');
 return input.map((r:any,i:number)=>{
  if(!r||typeof r!=='object'||Array.isArray(r))throw new Error(`第 ${i+1} 帧不是字段对象。`);
  let forces=r.forces;
  if(forces==null)forces=Array.from({length:77},(_,j)=>['x','y','z'].map(k=>r[`f${j}_${k}`]??r[`${k}${j}`]??null));
  if(!Array.isArray(forces)||forces.length!==77||forces.some((p:any)=>!Array.isArray(p)||p.length!==3))throw new Error(`第 ${i+1} 帧触觉形状不是 77×3`);
  const reported=r.force??r.netForce??r.net_force;
  return {timestamp:finiteValue(r.timestamp??r.t),forces:forces.map((p:any)=>p.map(finiteValue)),contact:r.contact??null,recovery:r.recovery??null,
   ...(reported!==undefined?{force:Array.isArray(reported)&&reported.length===3?reported.map(finiteValue):null}:{}),
   ...(typeof r.netForceUnit==='string'?{netForceUnit:r.netForceUnit}:{}),
   ...(typeof r.pointForceUnit==='string'?{pointForceUnit:r.pointForceUnit}:{})};
 });
}
export function reportText(type: string, records: LabRecord[]) {
 const titles: Record<string, string> = { plan: '实验计划', week: '研究周报', stage: '阶段总结', paper: '论文实验部分素材', tasks: '代码实现任务清单' };
 const now = new Date(), since = new Date(now.getTime() - 7 * 86400000);
 const dt = (v: any) => v && Number.isFinite(new Date(v).getTime()) ? new Date(v).toLocaleString('zh-CN', { timeZone: 'Asia/Hong_Kong', hour12: false }) : '未记录';
 const d = (r: LabRecord) => r.data || {};
 const clean = (v: any) => String(v ?? '待补充').replace(/\|/g, '\\|').replace(/\r?\n/g, ' ');
 const val = (v: any) => v === null || v === undefined || v === '' ? '待补充' : typeof v === 'object' ? JSON.stringify(v) : String(v);
 const pick = (kind: string, list = records) => list.filter(r => r.kind === kind);
 const byId = new Map(records.map(r => [r.id, r]));
 const link = (id: any) => id ? byId.has(String(id)) ? `${byId.get(String(id))!.name} [${id}]` : `未解析 [${id}]` : '未关联';
 const ref = (r: LabRecord) => `[${r.status || '未知/待确认'}] ${r.name} [${r.id}]${d(r).isTemplate ? '（配置模板）' : ''}`;
 const stamp = (r: LabRecord) => `创建 ${dt(r.createdAt)}；更新 ${dt(r.updatedAt)}`;
 const counts = (s: any, n: any) => finiteValue(s) !== null && finiteValue(n) !== null && Number.isInteger(+s) && Number.isInteger(+n) && +n > 0 && +s >= 0 && +s <= +n;
 const result = (r: LabRecord) => { const m = d(r).metrics || {}, n = m.total ?? m.trials ?? m.episodes ?? m.n, s = m.successes ?? m.successCount; return counts(s, n) ? `${(+s / +n * 100).toFixed(1)}% (${s}/${n})` : '—'; };
 const exps = pick('experiment'), datasets = pick('dataset'), episodes = pick('episode'), models = pick('model'), preprocess = pick('preprocess'), files = pick('file');
 const changed = records.filter(r => r.kind !== 'report' && r.updatedAt && new Date(r.updatedAt).getTime() >= since.getTime() && new Date(r.updatedAt).getTime() <= now.getTime());
 const scope = type === 'week' ? changed : records.filter(r => r.kind !== 'report');
 const unresolved = scope.flatMap(r => ['datasetId', 'preprocessId', 'modelId', 'experimentId'].filter(k => d(r)[k] && !byId.has(String(d(r)[k]))).map(k => `- ${r.name} [${r.id}]：${k} = ${d(r)[k]} 尚未解析。`));
 const conclusions = pick('conclusion', scope);
 const lines: string[] = [`# M3025 × Diffusion Policy · ${titles[type] || type}`, `生成时间：${dt(now)}（香港时间）`, '', '本稿由当前保存记录按模板整理；配置、计划和人工状态不等同于训练执行或实机验证。', '', '## 记录范围', `累计保存：数据集 ${datasets.length}；episode ${episodes.length}；实验配置 ${exps.length}（模板 ${exps.filter(r => d(r).isTemplate).length}）；模型 ${models.length}；文件 ${files.length}。`];
 if (type === 'week') {
  lines.push('', '## 最近 7 天更新', `统计窗口：${dt(since)} 至 ${dt(now)}（香港时间）。以下仅列本窗口内更新的非报告记录；上方数量为累计库存。`);
  if (!changed.length) lines.push('窗口内没有可根据更新时间确认的变更记录。');
  changed.forEach(r => lines.push(`- ${ref(r)}；${stamp(r)}`));
  lines.push('', '## 本周新增或更新的实验依据');
  const changedExps = pick('experiment', changed);
  if (!changedExps.length) lines.push('本窗口没有实验配置更新；不能据此声称本周完成了训练。');
  changedExps.forEach(r => lines.push(`- ${ref(r)}；成功率 ${result(r)}；证据：${val(d(r).evidence)}；日志 ID：${val(d(r).fileIds || [])}。`));
  lines.push('', '## 下周建议', '- 若尚无空间表征验证记录，优先完成 Masked Force + Net Force 的训练与独立留出验证。', '- 补齐本周新记录的采样、标定、划分、硬件和证据来源，再决定是否推进 GRU。');
 } else if (type === 'plan') {
  lines.push('', '## 优先级 1：空间编码器双任务预训练', '- 输入：77×3 触觉、有效测点掩码、真实通道顺序及经确认的合力目标。', '- 仅启用 Masked Force 与 Net Force；记录遮挡方式、任务权重、训练集归一化和 episode 留出划分。', '- 通过条件待研究者定义：保存遮挡测点重建指标、合力误差、相近合力但不同分布的区分实验及失败样例。', '', '## 优先级 2：下游 DP 与时序消融', '- 先比较纯视觉、原始触觉拼接、空间 MLP；固定数据划分、种子集合、训练预算及动作窗口。', '- 空间表征验证后再加入 GRU，然后独立加入接触门控；保留纯视觉无触觉输入兼容性。', '- 报告成功/总次数、恢复成功/恢复次数、推理时延、同步误差及具体失败案例。', '', '## 优先级 3：Dynamics Prediction 扩展', '- 先确认动作、触觉与状态时间对齐，明确预测对象、步长、因果输入和可观测性。', '- 与无 Dynamics 的同配置进行独立对照；没有对应日志前不写效果结论。', '', '## 当前配置与依赖');
  exps.forEach(r => lines.push(`- ${ref(r)}；数据 ${link(d(r).datasetId)}；预处理 ${link(d(r).preprocessId)}；模型 ${link(d(r).modelId)}。`));
 } else if (type === 'stage') {
  lines.push('', '## 阶段定位', '当前默认研究路线为“空间双任务验证 → 时序与融合 → Dynamics 扩展”。以下按记录自身证据状态归类；人工标记为已验证仍需审阅证据。');
  statuses.forEach(s => { const group = scope.filter(r => r.status === s); lines.push('', `### ${s}（${group.length} 条）`); if (!group.length) lines.push('暂无此状态记录。'); group.forEach(r => lines.push(`- ${ref(r)}；依据：${val(d(r).evidence || d(r).pretrainValidation || d(r).notes)}；${stamp(r)}`)); });
  lines.push('', '## 阶段是否可推进', `带有效分子/分母的实验记录：${exps.filter(r => result(r) !== '—').length}。此数量不能证明空间表征通过验证；还需独立的受力分布区分实验与训练来源。`, 'GRU、门控和 Dynamics 的阶段决策由研究者结合对应配置、数据和实际日志确认。');
 } else if (type === 'paper') {
  lines.push('', '## 实验方法素材（待核对后写入论文）', '### 数据与采集');
  datasets.forEach(r => lines.push(`- ${ref(r)}；来源 ${val(d(r).source)}；划分 ${val(d(r).split)}；描述 ${val(d(r).description)}。`));
  if (!datasets.length) lines.push('数据来源、任务、样本规模和采集设置待补充。');
  lines.push(`- 共 ${episodes.length} 条 episode 记录；记录数量不等同于有效独立试验数。`);
  lines.push('', '### 预处理与标定');
  preprocess.forEach(r => lines.push(`- ${ref(r)}；输入形状 ${val(d(r).shape || d(r).rawShape)}；采样频率 ${val(d(r).samplingHz)} Hz；时间戳单位 ${val(d(r).timestampUnit)}；力单位 ${val(d(r).forceUnit)}；合力定义 ${val(d(r).forceDefinition)}；归一化 ${val(d(r).normalization)}；拟合集 ${val(d(r).fitSplit)}；缺失处理 ${val(d(r).missing || d(r).missingPolicy)}；测点映射 ${Array.isArray(d(r).mapping) ? d(r).mapping.length + ' 个坐标' : '待核对'}。`));
  lines.push('', '### 编码器与训练协议');
  models.forEach(r => lines.push(`- ${ref(r)}；结构 ${val(d(r).architecture)}；任务 ${val(d(r).tasks)}；输入 ${val(d(r).inputTensor || d(r).input)}；输出 ${val(d(r).outputTensor || d(r).output)}；检查点 ${val(d(r).checkpoint)}。`));
  exps.forEach(r => lines.push(`- ${ref(r)}；融合 ${val(d(r).fusion)}；Epoch ${val(d(r).epochs)}；batch ${val(d(r).batchSize)}；学习率 ${val(d(r).lr)}；seed ${val(d(r).seed)}；观测/预测/动作窗口 ${val(d(r).observationHorizon)}/${val(d(r).predictionHorizon)}/${val(d(r).actionHorizon)}；划分 ${val(d(r).split)}；数据/预处理/模型 ${val(d(r).datasetId)}/${val(d(r).preprocessId)}/${val(d(r).modelId)}。`));
  lines.push('', '### 结果表素材', '仅根据明确的有效成功数和总次数计算成功率；缺失结果保留“—”。不同数据、种子和预算的结果不能直接解释为消融收益。', '', '| 实验 | 证据状态 | 成功率（成功/总次数） | 恢复成功率 | 推理 P95 / ms | 同步误差 / ms |', '| --- | --- | --- | --- | --- | --- |');
  exps.forEach(r => { const m = d(r).metrics || {}, rn = m.recoveryTotal ?? m.recoveryAttempts, rs = m.recoverySuccesses; const recovery = counts(rs, rn) ? `${(+rs / +rn * 100).toFixed(1)}% (${rs}/${rn})` : '—'; lines.push(`| ${clean(r.name)} [${r.id}] | ${clean(r.status)} | ${result(r)} | ${recovery} | ${clean(m.latencyP95Ms ?? '—')} | ${clean(m.syncErrorMs ?? '—')} |`); });
  lines.push('', '### 论文使用前待补充', '- 独立测试集、任务定义、硬件型号、标定过程、力与时间单位、模型检查点哈希、种子集合及置信区间。', '- 单次离线结果和特征投影不能直接写成实机有效性或下游收益结论。');
 } else if (type === 'tasks') {
  const tasks = [
   ['采集与时间戳适配', '传感器三轴原始数据、设备时钟、主机时钟与标定文件', '77×3 帧、时间戳、缺失掩码与原文件关联', '检查形状、时间单调性和真实采样率；不静默填零。'],
   ['episode 划分与预处理', '采集记录、标签和实验划分协议', '固定 episode ID 的 train/val/test 清单及预处理版本', '划分不交叉；归一化统计仅拟合训练集；映射按原通道验证。'],
   ['空间编码器双任务', '归一化触觉、有效掩码及已标定合力目标', 'Spatial MLP 检查点、Masked Force/Net Force 损失日志', '仅遮挡且有效点贡献重建损失；合力目标有坐标与标定依据。'],
   ['受力分布特征验证', '独立留出 episode、预训练特征与分布标签', '带来源的投影、分类/检索统计及失败样例', '记录投影方法；比较相近合力下不同分布；排除训练集泄漏。'],
   ['纯视觉兼容与 DP 接入', '视觉、本体状态与可选触觉特征', '纯视觉/原始拼接/空间 MLP 三种配置及可复现训练日志', '触觉关闭时视觉链路独立运行；张量、种子与训练预算可比。'],
   ['GRU 与接触门控消融', '通过空间验证的模型、时间序列和接触标签', 'MLP+GRU、MLP+GRU+Gate 两组独立配置', '保存窗口、阈值、滞回和无接触误触发；不得把门控与 GRU 效果混合归因。'],
   ['评估与失败案例管线', '真实执行日志、成功/恢复判据及硬件计时数据', '成功数/总数、恢复统计、同步误差、时延及可追溯失败记录', '分母有效；标明单位、统计口径、测量环境和原始文件 ID。'],
   ['Dynamics Prediction 扩展', '已对齐的触觉状态、动作和未来目标', '独立动态预测任务与无此任务的配对对照', '只在前阶段验证后启用；检查因果性、预测步长和动作时间对齐。']
  ];
  lines.push('', '## 建议实现任务（以下为待执行模板）');
  tasks.forEach(([name, input, output, acceptance], i) => lines.push('', `### ${i + 1}. ${name}`, '- [ ] 待实施与核验', `输入：${input}。`, `输出：${output}。`, `验收：${acceptance}`));
  lines.push('', '## 已保存任务状态');
  const saved = pick('task');
  if (!saved.length) lines.push('尚无保存任务；上方模板不会自动标记为完成。');
  saved.forEach(r => lines.push(`- [${d(r).done ? 'x' : ' '}] ${ref(r)}；负责人 ${val(d(r).owner)}；截止 ${val(d(r).dueDate)}；验收 ${val(d(r).acceptance)}；证据 ${val(d(r).evidence)}。`));
 }
 lines.push('', '## 未解析关联');
 lines.push(...(unresolved.length ? unresolved : ['当前报告范围未发现指向不存在记录的显式关联；空关联仍需由研究者补齐。']));
 lines.push('', type === 'week' ? '## 本窗口结论与证据' : '## 结论、证据与限制');
 if (!conclusions.length) lines.push('本报告范围内没有已记录结论，不能据配置推断实验效果。');
 conclusions.forEach(r => lines.push(`- ${ref(r)}：${val(d(r).statement || d(r).text)}；证据 ${val(d(r).evidence)}；实验 ${link(d(r).experimentId)}；限制 ${val(d(r).limitations)}。`));
 lines.push('', '## 来源记录索引', '下列 ID 和时间用于追溯保存记录，状态由记录提供，尚未进行独立外部核验。');
 scope.forEach(r => lines.push(`- ${r.id} / ${r.kind} / ${r.name}；${stamp(r)}${r.kind === 'file' ? `；SHA-256：${val(d(r).sha256)}` : ''}`));
 return lines.join('\n');
}

export function normalizeLogRows(rows:any[]){
 if(rows.some(x=>!x||typeof x!=='object'||Array.isArray(x)))throw new Error('日志数组中的每一行必须是字段对象。');
 if(rows.some(x=>'step' in x||'train_loss' in x||'masked_force_loss' in x))return {curves:rows};
 if(rows.length&&'x' in rows[0]&&'y' in rows[0])return {features:rows};
 const binary=(x:any)=>x===0||x===1||x===false||x===true||x==='0'||x==='1';
 const cases=rows.filter(x=>binary(x.success));
 return {evaluations:rows,...(cases.length?{metrics:{successes:cases.filter(x=>Number(x.success)===1).length,total:cases.length,trials:cases.length}}:{}),failures:rows.filter(x=>binary(x.success)&&Number(x.success)===0).map(x=>({episodeId:x.episode_id,reason:x.failure||x.reason||'待标注',...x}))};
}
export function prepareBackupImport(parsed:any,existing:LabRecord[],sourceFileId:string){
 const input=parsed.records||[parsed.record||parsed];
 if(!Array.isArray(input))throw new Error('配置必须是 record 对象或 records 数组。');
 const rows=input.filter((r:any)=>r?.kind!=='file');
 const kinds=['dataset','episode','preprocess','model','experiment','plan','conclusion','task','report','sensor','evaluation','log','feature','note'];
 if(rows.some((r:any)=>!r||!r.name||typeof r.name!=='string'||!kinds.includes(r.kind)||(r.data!=null&&(typeof r.data!=='object'||Array.isArray(r.data)))))throw new Error('配置缺少有效的 name、kind 或 data 对象。原文件已保留。');
 const ids=rows.map((r:any)=>r.id).filter(Boolean);if(new Set(ids).size!==ids.length)throw new Error('导入配置含重复记录 ID，请先整理后重新导入。');
 const remap=new Map(rows.filter((r:any)=>r.id&&existing.some(x=>x.id===r.id)).map((r:any)=>[r.id,crypto.randomUUID()]));
 function rewrite(o:any,k=''):any{if(Array.isArray(o))return o.map(v=>rewrite(v,k));if(o&&typeof o==='object')return Object.fromEntries(Object.entries(o).map(([key,v])=>[key,rewrite(v,key)]));return typeof o==='string'&&/Ids?$/.test(k)&&remap.has(o)?remap.get(o):o}
 return {records:rows.map((r:any)=>({...r,id:remap.get(r.id)||r.id||crypto.randomUUID(),version:undefined,status:statuses.includes(r.status)?r.status:'未知/待确认',data:{...rewrite(r.data||{}),fileIds:[...new Set([...(Array.isArray(r.data?.fileIds)?rewrite(r.data.fileIds,'fileIds'):[]),sourceFileId])],...(remap.has(r.id)?{importedFromId:r.id}:{})}})),skippedFiles:input.length-rows.length};
}
