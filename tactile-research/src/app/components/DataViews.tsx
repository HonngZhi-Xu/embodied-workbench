"use client";

import { useMemo, useState } from "react";
import { ArrowRight, Check, ChevronDown, Database, FileJson, Fingerprint, FolderOpen, Info, Layers3, Plus, Save, Settings2, Upload, Waves, X } from "lucide-react";
import "./data-views.css";

type Props = { page: string; records: any[]; onSave: (record: any) => Promise<any>; onEdit: (record: any) => void; onImport: () => void; onDelete?: (record: any) => void; onViewFile?: (record: any) => void };
const states = ["未知/待确认", "待补充实验", "离线测试结果", "已验证信息", "技术风险"];
const defaultConfig = { sensor: "PaXini M3025", shape: [77, 3], channels: "Fx, Fy, Fz", samplingHz: "", timestampUnit: "s", timestampSource: "sensor", forceUnit: "待确认", netForceUnit: "待确认", forceDefinition: "sum", commonFrameConfirmed: false, calibration: "待补充零点、坐标系与标定记录", mapping: "[]", missing: "mask", maxGapMs: 20, normalization: "zscore", fitSplit: "train", splitUnit: "episode", syncMethod: "nearest", maxSyncErrorMs: 10, contactThreshold: "", contactReleaseThreshold: "", notes: "" };
const num = (x: any) => typeof x === "number" && Number.isFinite(x);
const fmt = (x: any, digits = 2) => num(x) ? x.toFixed(digits) : "—";
const recordData = (r: any) => r?.data || {};
const samplesOf = (r: any) => {
  const d = recordData(r);
  return Array.isArray(d.samples) ? d.samples : Array.isArray(d.preview?.samples) ? d.preview.samples : [];
};
const sampleForces = (s: any): any[] => {
  if (Array.isArray(s?.forces)) return s.forces;
  if (Array.isArray(s?.tactile)) return Array.isArray(s.tactile[0]) ? s.tactile : Array.from({ length: Math.floor(s.tactile.length / 3) }, (_, i) => s.tactile.slice(i * 3, i * 3 + 3));
  return [];
};

export function analyzeM3025Force(samples: any[], options: { forceUnit?: string; netForceUnit?: string; timestampUnit?: string; allowVectorSum?: boolean; maxGapMs?: number }) {
  const factor = ({ s: 1, ms: .001, us: .000001, ns: .000000001 } as Record<string, number>)[options.timestampUnit || ""];
  const vectors: (number[] | null)[] = [];
  const sources: (string | null)[] = [];
  const gapLimit = num(options.maxGapMs) && options.maxGapMs! >= 0 ? options.maxGapMs! / 1000 : null;
  let peakMagnitude: number | null = null, peakRate: number | null = null, validFrames = 0, validIntervals = 0, deviceFrames = 0, summedFrames = 0, skippedGaps = 0;
  const valid = (v: any) => Array.isArray(v) && v.length === 3 && v.every(num);
  samples.forEach((sample: any, i: number) => {
    let vector: number[] | null = null;
    let source: string | null = null;
    const reported = sample?.force ?? sample?.netForce ?? sample?.net_force;
    const reportedUnit = sample?.netForceUnit ?? options.netForceUnit ?? options.forceUnit;
    const pointUnit = sample?.pointForceUnit ?? options.forceUnit;
    // Raw M3025 points can be counts while a separate device resultant is N.
    // Never relabel the raw points or sum them just because the resultant is N.
    if (reportedUnit === "N" && valid(reported)) {
      vector = reported.slice(); source = "device";
    } else if (pointUnit === "N" && options.allowVectorSum === true) {
      const points = sampleForces(sample);
      if (points.length === 77 && points.every(valid)) {
        vector = [0, 1, 2].map(axis => points.reduce((sum: number, point: number[]) => sum + point[axis], 0));
        if (valid(vector)) source = "sum"; else vector = null;
      }
    }
    if (vector && !num(Math.hypot(...vector))) { vector = null; source = null; }
    vectors.push(vector);
    sources.push(source);
    if (vector) {
      validFrames++;
      if (source === "device") deviceFrames++; else if (source === "sum") summedFrames++;
      const magnitude = Math.hypot(...vector);
      peakMagnitude = peakMagnitude === null ? magnitude : Math.max(peakMagnitude, magnitude);
      const previous = vectors[i - 1];
      // Source switches and missing frames do not form a continuous measurement interval.
      if (previous && sources[i - 1] === source && num(factor) && num(sample.timestamp) && num(samples[i - 1]?.timestamp)) {
        const dt = (sample.timestamp - samples[i - 1].timestamp) * factor;
        if (dt > 0 && num(dt)) {
          if (gapLimit !== null && dt > gapLimit) skippedGaps++;
          else {
            const rate = Math.hypot(...vector.map((value, axis) => value - previous[axis])) / dt;
            if (num(rate)) { validIntervals++; peakRate = peakRate === null ? rate : Math.max(peakRate, rate); }
          }
        }
      }
    }
  });
  return { vectors, peakMagnitude, peakRate, validFrames, validIntervals, deviceFrames, summedFrames, skippedGaps, timeUnitKnown: num(factor), forceUnitKnown: options.forceUnit === "N" || options.netForceUnit === "N" || deviceFrames > 0 };
}

export default function DataViews({ page, records, onSave, onEdit, onImport, onDelete, onViewFile }: Props) {
  const datasets = records.filter(r => r.kind === "dataset");
  const episodes = records.filter(r => r.kind === "episode");
  const preprocessing = records.filter(r => r.kind === "preprocess");
  const files = records.filter(r => r.kind === "file");
  const [tab, setTab] = useState("dataset");
  const [selection, setSelection] = useState("");
  const [search, setSearch] = useState("");
  const [splitFilter, setSplitFilter] = useState("all");
  const [modal, setModal] = useState<null | string>(null);
  const [draft, setDraft] = useState<any>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [configId, setConfigId] = useState("");
  const [config, setConfig] = useState<any>(defaultConfig);
  const [configName, setConfigName] = useState("M3025 预处理配置 v1");
  const [frame, setFrame] = useState(0);
  const [axis, setAxis] = useState("norm");
  const current = episodes.find(r => r.id === selection) || episodes[0];
  const samples = samplesOf(current);
  const forces = sampleForces(samples[Math.min(frame, Math.max(0, samples.length - 1))]);
  const currentSample = samples[Math.min(frame, Math.max(0, samples.length - 1))];
  const reportedForce = currentSample?.force || currentSample?.netForce || currentSample?.net_force;
  const currentDataset = datasets.find(r => r.id === recordData(current).datasetId);
  const linkedPreprocessId = recordData(current).preprocessId || recordData(currentDataset).preprocessId;
  const linkedPreprocess = preprocessing.find(r => r.id === linkedPreprocessId);
  const linkedConfig = recordData(linkedPreprocess);
  const forceUnit = recordData(current).forceUnit || linkedConfig.forceUnit;
  const netForceUnit = recordData(current).netForceUnit || linkedConfig.netForceUnit;
  const timestampUnit = recordData(current).timestampUnit || linkedConfig.timestampUnit;
  const allowVectorSum = !!linkedPreprocess && linkedConfig.commonFrameConfirmed === true && linkedConfig.forceUnit === "N" && linkedConfig.forceDefinition === "sum";
  const maxGapMs = linkedPreprocess && num(linkedConfig.maxGapMs) ? linkedConfig.maxGapMs : undefined;
  const forceAnalysis = useMemo(() => analyzeM3025Force(samples, { forceUnit, netForceUnit, timestampUnit, allowVectorSum, maxGapMs }), [samples, forceUnit, netForceUnit, timestampUnit, allowVectorSum, maxGapMs]);
  const currentLabels = recordData(current).labelIntervals ?? recordData(current).labels ?? [];
  const quality = useMemo(() => {
    let total = 0, missing = 0, invalidShape = 0, nonMonotonic = 0;
    let prev: number | null = null;
    samples.forEach((s: any) => {
      const f = sampleForces(s);
      if (f.length !== 77 || f.some(v => !Array.isArray(v) || v.length !== 3)) invalidShape++;
      total += 231;
      for (let i = 0; i < 77; i++) for (let j = 0; j < 3; j++) if (!num(f[i]?.[j])) missing++;
      if (num(s.timestamp)) { if (prev !== null && s.timestamp <= prev) nonMonotonic++; prev = s.timestamp; }
    });
    return { missing: total ? missing / total * 100 : null, invalidShape, nonMonotonic };
  }, [samples]);
  const saveDraft = async () => {
    setMessage("");
    if (!String(draft.name || "").trim()) { setMessage("请填写名称。"); return; }
    if (modal === "episode" && !draft.datasetId) { setMessage("请先选择数据集。"); return; }
    let labels: any[] = [];
    if (modal === "labels") {
      try { labels = JSON.parse(draft.labels || "[]"); if (!Array.isArray(labels)) throw Error(); for (const x of labels) if (!num(x.start) || !num(x.end) || x.end < x.start || !["free", "contact", "slip", "recovery", "stable"].includes(x.state)) throw Error(); }
      catch { setMessage('标签应为数组，例如 [{"start":0,"end":1,"state":"contact"}]，时间需递增，状态限 free/contact/slip/recovery/stable。'); return; }
    }
    setBusy(true);
    try {
      const kind = modal === "labels" ? "episode" : modal;
      const data = modal === "dataset" ? { description: draft.description || "", split: draft.split || "未划分", sensor: "M3025", shape: [77, 3], source: draft.source || "待导入", notes: draft.notes || "" }
        : modal === "labels" ? { ...recordData(current), labelIntervals: labels, labelSource: "人工标注", datasetId: draft.datasetId || recordData(current).datasetId || "", preprocessId: draft.preprocessId || "", timestampUnit: draft.timestampUnit || "", forceUnit: draft.forceUnit || "", netForceUnit: draft.netForceUnit || recordData(current).netForceUnit || "", notes: draft.notes || "" }
        : { datasetId: draft.datasetId, preprocessId: draft.preprocessId || "", timestampUnit: draft.timestampUnit || "", forceUnit: draft.forceUnit || "", netForceUnit: draft.netForceUnit || "", outcome: draft.outcome || "unknown", split: draft.split || "未划分", duration: draft.duration === "" ? null : Number(draft.duration), sensor: "M3025", shape: [77, 3], notes: draft.notes || "", labelIntervals: [] };
      await onSave({ ...(modal === "labels" ? current : {}), kind, name: draft.name.trim(), status: draft.status || "未知/待确认", data });
      setModal(null);
    } catch (e: any) { setMessage(e.message || "保存失败，请重试。"); } finally { setBusy(false); }
  };
  const saveConfig = async () => {
    setMessage("");
    if (!configName.trim()) { setMessage("请填写配置名称。"); return; }
    if (config.samplingHz !== "" && !(Number(config.samplingHz) > 0)) { setMessage("采样频率须大于 0，未知时留空。"); return; }
    let mapping;
    try {
      mapping = JSON.parse(config.mapping || "[]");
      if (!Array.isArray(mapping)) throw Error();
      if (mapping.length && (mapping.length !== 77 || mapping.some((p: any) => !Array.isArray(p) || ![2, 3].includes(p.length) || p.some((v: any) => !num(v))))) throw Error();
    } catch { setMessage("空间映射应为空数组，或含 77 个二维/三维坐标的数组；未知时保留 []。"); return; }
    if (Number(config.maxGapMs) < 0 || Number(config.maxSyncErrorMs) < 0) { setMessage("时间阈值不能为负数。"); return; }
    setBusy(true);
    try {
      const record = await onSave({ ...(preprocessing.find(r => r.id === configId) || {}), kind: "preprocess", name: configName.trim(), status: "待补充实验", data: { ...config, samplingHz: config.samplingHz === "" ? null : Number(config.samplingHz), mapping, shape: [77, 3], maxGapMs: Number(config.maxGapMs), maxSyncErrorMs: Number(config.maxSyncErrorMs), trainOnlyStatistics: true } });
      if (record?.id) setConfigId(record.id);
      setMessage("配置已保存。实际预处理与训练需由外部代码执行，再导入日志核验。");
    } catch (e: any) { setMessage(e.message || "保存失败，请重试。"); } finally { setBusy(false); }
  };
  const loadConfig = (id: string) => {
    setConfigId(id); const r = preprocessing.find(r => r.id === id);
    setConfigName(r?.name || "M3025 预处理配置 v1");
    const d = r?.data || {};
    setConfig({ ...defaultConfig, ...d, samplingHz: d.samplingHz ?? "", mapping: typeof d.mapping === "string" ? d.mapping : JSON.stringify(d.mapping || [], null, 2) });
    setMessage("");
  };
  const update = (key: string, value: any) => setConfig((c: any) => ({ ...c, [key]: value }));
  const items = (tab === "dataset" ? datasets : episodes).filter(r => (r.name + " " + r.id).toLowerCase().includes(search.toLowerCase()) && (splitFilter === "all" || recordData(r).split === splitFilter));
  const showNew = (type: string) => { setDraft({ name: "", status: "未知/待确认", datasetId: datasets[0]?.id || "", preprocessId: "", timestampUnit: "", forceUnit: "", duration: "", split: "未划分" }); setMessage(""); setModal(type); };

  if (page === "preprocess") return <div className="dv-root">
    <div className="dv-topline"><div><span className="eyebrow">SENSOR & PREPROCESSING</span><h2>让每一帧触觉都有明确含义</h2><p>定义传感器、时间基准和数据处理约定，配置版本随实验完整保留。</p></div><button className="btn btn-primary" onClick={saveConfig} disabled={busy}><Save size={16}/>{busy ? "保存中…" : "保存配置版本"}</button></div>
    <div className="dv-config-bar panel"><label>配置版本<select value={configId} onChange={e => loadConfig(e.target.value)}><option value="">新配置</option>{preprocessing.map(r => <option value={r.id} key={r.id}>{r.name}</option>)}</select></label><label className="dv-grow">配置名称<input value={configName} onChange={e => setConfigName(e.target.value)}/></label><span className="tag">77 测点 × 3 轴</span></div>
    {message && <div className="dv-notice" role="status"><Info size={16}/>{message}</div>}
    <div className="dv-grid">
      <section className="panel"><div className="panel-head"><div><span className="eyebrow">01 / ACQUISITION</span><h3>采集与时间基准</h3></div><Waves size={19}/></div><div className="dv-fields">
        <label>传感器<input value={config.sensor} onChange={e => update("sensor", e.target.value)}/></label>
        <label>采样频率 / Hz<input type="number" min="0.001" step="any" placeholder="未知，待实测" value={config.samplingHz} onChange={e => update("samplingHz", e.target.value)}/></label>
        <label>时间戳单位<select value={config.timestampUnit} onChange={e => update("timestampUnit", e.target.value)}><option value="s">秒 s</option><option value="ms">毫秒 ms</option><option value="us">微秒 μs</option><option value="ns">纳秒 ns</option></select></label>
        <label>时间戳来源<select value={config.timestampSource} onChange={e => update("timestampSource", e.target.value)}><option value="sensor">传感器时钟</option><option value="host">主机接收时钟</option><option value="hardware">硬件同步时钟</option><option value="unknown">未知 / 待确认</option></select></label>
        <label>测点原始单位<select value={config.forceUnit} onChange={e => update("forceUnit", e.target.value)}><option>待确认</option><option>N</option><option>原始读数</option><option>counts</option></select></label>
        <label>设备合力单位<select value={config.netForceUnit||"待确认"} onChange={e=>update("netForceUnit",e.target.value)}><option>待确认</option><option>N</option><option>counts</option></select></label>
        <label>同步方法<select value={config.syncMethod} onChange={e => update("syncMethod", e.target.value)}><option value="nearest">最近时间戳匹配</option><option value="interpolate">线性插值至视觉时刻</option><option value="hardware">硬件触发对齐</option></select></label>
        <label>同步误差阈值 / ms<input type="number" min="0" value={config.maxSyncErrorMs} onChange={e => update("maxSyncErrorMs", e.target.value)}/></label>
      </div></section>
      <section className="panel"><div className="panel-head"><div><span className="eyebrow">02 / FORCE DEFINITION</span><h3>三维合力与接触判据</h3></div><Fingerprint size={19}/></div><div className="dv-fields">
        <label className="dv-span2">合力来源<select value={config.forceDefinition} onChange={e => update("forceDefinition", e.target.value)}><option value="sum">将 77 个测点力向量求和</option><option value="device">使用设备提供的三维合力</option><option value="unknown">待确认定义</option></select></label>
        <label className="dv-span2 dv-check"><input type="checkbox" checked={config.commonFrameConfirmed} onChange={e => update("commonFrameConfirmed", e.target.checked)}/><span>已确认测点力统一坐标系与标定；允许求和</span></label>
        <label>接触进入阈值<input type="number" step="any" placeholder="由训练集标定" value={config.contactThreshold} onChange={e => update("contactThreshold", e.target.value)}/></label>
        <label>接触退出阈值<input type="number" step="any" placeholder="可设置滞回" value={config.contactReleaseThreshold} onChange={e => update("contactReleaseThreshold", e.target.value)}/></label>
        <label className="dv-span2">零点 / 标定 / 坐标约定<textarea rows={3} value={config.calibration} onChange={e => update("calibration", e.target.value)}/></label>
      </div><div className="dv-inline-note">向量求和成立依赖坐标系与标定。配置阈值不等同于已验证接触标签。</div></section>
      <section className="panel"><div className="panel-head"><div><span className="eyebrow">03 / CLEAN & NORMALIZE</span><h3>缺失数据与归一化</h3></div><Settings2 size={19}/></div><div className="dv-fields">
        <label>缺失处理<select value={config.missing} onChange={e => update("missing", e.target.value)}><option value="mask">保留缺失掩码</option><option value="interpolate">短缺口插值 + 掩码</option><option value="drop">剔除缺失帧</option><option value="zero">填零 + 掩码</option></select></label>
        <label>允许插值缺口 / ms<input type="number" min="0" value={config.maxGapMs} onChange={e => update("maxGapMs", e.target.value)}/></label>
        <label>归一化<select value={config.normalization} onChange={e => update("normalization", e.target.value)}><option value="zscore">逐轴 Z-score</option><option value="minmax">逐轴 Min–Max</option><option value="robust">中位数 / IQR</option><option value="none">不归一化</option></select></label>
        <label>划分粒度<select value={config.splitUnit} onChange={e => update("splitUnit", e.target.value)}><option value="episode">按 Episode 划分</option><option value="session">按采集 Session 划分</option><option value="object">按对象划分</option></select></label>
        <label className="dv-span2">统计量拟合范围<input readOnly value="仅训练集（train）"/></label>
      </div><div className="dv-inline-note"><Check size={14}/>训练集拟合统计量，验证集与测试集复用同一版本，避免信息泄漏。</div></section>
      <section className="panel"><div className="panel-head"><div><span className="eyebrow">04 / SPATIAL MAPPING</span><h3>测点空间映射</h3></div><Layers3 size={19}/></div><p className="dv-muted">输入 77 个测点的实际二维或三维坐标，顺序须与原始通道一致。未知时留空数组，不自动假设传感器几何。</p><label className="dv-block-label">positions[77][2 or 3]<textarea className="dv-code" rows={6} value={config.mapping} onChange={e => update("mapping", e.target.value)} spellCheck={false}/></label><label className="dv-block-label">备注<textarea rows={2} value={config.notes} onChange={e => update("notes", e.target.value)}/></label></section>
    </div>
  </div>;

  return <div className="dv-root">
    <div className="dv-topline"><div><span className="eyebrow">DATASET REGISTRY</span><h2>从真实数据开始</h2><p>管理采集版本、Episode、接触标注与文件，让实验拥有可复现的数据基础。</p></div><div className="dv-actions"><button className="btn" onClick={onImport}><Upload size={16}/>导入数据</button><button className="btn btn-primary" onClick={() => showNew(tab === "dataset" ? "dataset" : "episode")}><Plus size={16}/>{tab === "dataset" ? "新建数据集" : "新建 Episode"}</button></div></div>
    <div className="dv-summary"><div><Database size={19}/><span><strong>{datasets.length}</strong> 数据集</span></div><div><Layers3 size={19}/><span><strong>{episodes.length}</strong> Episodes</span></div><div><FileJson size={19}/><span><strong>{files.length}</strong> 原始文件</span></div><div><Fingerprint size={19}/><span><strong>77 × 3</strong> 原始输入</span></div></div>
    <section className="panel dv-registry"><div className="dv-toolbar"><div className="dv-tabs"><button className={tab === "dataset" ? "active" : ""} onClick={() => setTab("dataset")}>数据集 <small>{datasets.length}</small></button><button className={tab === "episode" ? "active" : ""} onClick={() => setTab("episode")}>Episodes <small>{episodes.length}</small></button></div><div className="dv-searchbar"><input aria-label="搜索数据集或 Episode" placeholder="搜索名称或 ID…" value={search} onChange={e => setSearch(e.target.value)}/><select aria-label="筛选数据划分" value={splitFilter} onChange={e => setSplitFilter(e.target.value)}><option value="all">全部划分</option><option value="train">训练集</option><option value="val">验证集</option><option value="test">测试集</option><option value="未划分">未划分</option></select></div></div>
      <div className="table-wrap"><table><thead><tr><th>{tab === "dataset" ? "数据集 / VERSION" : "EPISODE / ID"}</th><th>{tab === "dataset" ? "Episodes" : "所属数据集"}</th><th>数据划分</th><th>{tab === "dataset" ? "数据来源" : "任务结果"}</th><th>证据状态</th><th></th></tr></thead><tbody>{items.map(r => <tr key={r.id} className={selection === r.id ? "dv-selected" : ""}><td><button className="dv-record-name" onClick={() => { if (tab === "episode") { setSelection(r.id); setFrame(0); } else onEdit(r); }}><strong>{r.name}</strong><small>{r.id.slice(0, 18)}</small></button></td><td>{tab === "dataset" ? episodes.filter(e => recordData(e).datasetId === r.id).length : datasets.find(d => d.id === recordData(r).datasetId)?.name || "未关联"}</td><td><span className="dv-split">{recordData(r).split || "未划分"}</span></td><td>{tab === "dataset" ? recordData(r).source || "待补充" : ({ success: "成功", failure: "失败", unknown: "未记录" } as any)[recordData(r).outcome] || "未记录"}</td><td><span className="tag">{r.status || "未知/待确认"}</span></td><td><button className="dv-text-btn" onClick={() => onEdit(r)}>编辑 <ArrowRight size={13}/></button></td></tr>)}</tbody></table></div>
      {!items.length && <div className="dv-empty"><div className="dv-empty-icon"><FolderOpen size={28}/></div><h3>{search ? "没有匹配记录" : tab === "dataset" ? "添加第一个真实数据集" : "尚无 Episode 记录"}</h3><p>{search ? "调整关键词或数据划分筛选。" : "导入文件或建立数据记录后，继续关联预处理与实验配置。"}</p><button className="btn" onClick={onImport}><Upload size={15}/>导入数据文件</button></div>}
    </section>
    <div className="dv-grid dv-bottom-grid"><section className="panel"><div className="panel-head"><div><span className="eyebrow">TACTILE FRAME INSPECTOR</span><h3>原始触觉帧检查</h3></div><span className="tag">按通道索引排列</span></div><div className="dv-frame-toolbar"><select aria-label="选择触觉 Episode" value={current?.id || ""} onChange={e => { setSelection(e.target.value); setFrame(0); }}><option value="" disabled>选择含样本的 Episode</option>{episodes.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select><select aria-label="选择受力分量" value={axis} onChange={e => setAxis(e.target.value)}><option value="norm">向量模长</option><option value="0">Fx</option><option value="1">Fy</option><option value="2">Fz</option></select></div><div className="dv-taxels">{Array.from({ length: 77 }, (_, i) => { const a = forces[i]; const v = Array.isArray(a) && a.every(num) ? (axis === "norm" ? Math.hypot(...a) : a[Number(axis)]) : null; const all = forces.map(a => Array.isArray(a) && a.every(num) ? (axis === "norm" ? Math.hypot(...a) : Math.abs(a[Number(axis)])) : 0); const max = Math.max(...all, 0.00001); return <div key={i} title={`测点 ${i + 1}: ${v === null ? "缺失 / 无数据" : fmt(v, 4)}`} className={v === null ? "missing" : ""} style={v === null ? {} : { background: `rgba(46, 103, 87, ${0.12 + Math.min(1, Math.abs(v) / max) * 0.88})`, color: Math.abs(v) / max > 0.5 ? "white" : "#36584b" }}>{i + 1}</div>; })}</div><div className="dv-net-force"><span>设备三维合力</span>{["Fx", "Fy", "Fz"].map((label, i) => <div key={label}><small>{label}</small><strong>{fmt(reportedForce?.[i], 3)}</strong></div>)}<small>单位随采集配置</small></div><div className="dv-frame-meta"><span>{samples.length ? `帧 ${Math.min(frame + 1, samples.length)} / ${samples.length}` : "等待导入原始触觉样本"}</span><span>t = {fmt(samples[frame]?.timestamp, 4)}</span></div><input className="dv-frame-range" aria-label="触觉帧" type="range" min={0} max={Math.max(0, samples.length - 1)} value={Math.min(frame, Math.max(0, samples.length - 1))} disabled={!samples.length} onChange={e => setFrame(Number(e.target.value))}/><p className="dv-inline-note">此网格仅展示通道索引，不代表 M3025 真实测点位置。颜色使用当前帧自适应范围。</p></section>
      <section className="panel"><div className="panel-head"><div><span className="eyebrow">EPISODE QUALITY & LABELS</span><h3>数据质量与阶段标注</h3></div><button className="dv-text-btn" disabled={!current} onClick={() => { setDraft({ name: current.name, status: current.status, notes: recordData(current).notes || "", datasetId: recordData(current).datasetId || "", preprocessId: recordData(current).preprocessId || "", timestampUnit: recordData(current).timestampUnit || "", forceUnit: recordData(current).forceUnit || "", netForceUnit: recordData(current).netForceUnit || "", labels: JSON.stringify(currentLabels, null, 2) }); setMessage(""); setModal("labels"); }}>编辑标注 <ArrowRight size={13}/></button></div><div className="dv-quality"><div><span>已解析样本</span><strong>{samples.length || "—"}</strong></div><div><span>原始通道缺失率</span><strong>{quality.missing === null ? "—" : fmt(quality.missing) + "%"}</strong></div><div><span>形状异常帧</span><strong>{samples.length ? quality.invalidShape : "—"}</strong></div><div><span>时间戳非递增</span><strong>{samples.length ? quality.nonMonotonic : "—"}</strong></div></div><div className="dv-label-legend"><span><i/>自由运动</span><span><i/>接触</span><span><i/>滑移</span><span><i/>恢复</span><span><i/>稳定</span></div>{Array.isArray(currentLabels) && currentLabels.length ? <div className="dv-label-list">{currentLabels.map((l: any, i: number) => <div key={i}><span className="tag">{({ free: "自由运动", contact: "接触", slip: "滑移", recovery: "恢复", stable: "稳定" } as any)[l.state] || l.state}</span><span>{l.start} → {l.end}</span></div>)}</div> : <div className="dv-mini-empty">尚未提供接触状态与恢复阶段标签。<br/>标注保留时间区间与人工来源。</div>}<div className="dv-force-analysis"><div className="dv-force-analysis-head"><span>力变化分析</span><small>{linkedPreprocess?.name || "未关联预处理版本"}</small></div><div className="dv-force-values"><div><span>峰值合力模长</span><strong>{fmt(forceAnalysis.peakMagnitude, 3)} <small>N</small></strong></div><div><span>峰值力变化率</span><strong>{fmt(forceAnalysis.peakRate, 3)} <small>N/s</small></strong></div></div><p>{forceAnalysis.forceUnitKnown ? `有效合力帧 ${forceAnalysis.validFrames} / ${samples.length}；有效相邻区间 ${forceAnalysis.validIntervals}。导入合力 ${forceAnalysis.deviceFrames} 帧，已授权测点求和 ${forceAnalysis.summedFrames} 帧。` : "力单位未知或未确认 N，物理量分析暂不计算。"}</p><p>{forceAnalysis.timeUnitKnown ? `时间戳单位：${timestampUnit}；变化率定义为 ‖Fᵢ − Fᵢ₋₁‖ / Δt。` : "时间戳单位未知，力变化率不计算。"}测点单位：{forceUnit||"未知"}；设备合力单位：{netForceUnit||"未知"}。仅使用完整有效向量与严格递增的相邻时间戳，不跨缺失帧连线；已跳过长间隔 {forceAnalysis.skippedGaps} 处。</p>{!allowVectorSum && <p>未授权原始测点求和；需关联已确认公共坐标系、N 单位且合力定义为求和的预处理版本。</p>}</div><p className="dv-inline-note">质量与力指标仅针对当前 Episode 已解析的 samples，不代表完整文件。缺少数据或单位时显示“—”。</p></section></div>
    {files.length > 0 && <section className="panel"><div className="panel-head"><div><span className="eyebrow">SOURCE FILES</span><h3>关联原始文件</h3></div><span className="tag">{files.length} 个文件</span></div><div className="dv-file-grid">{files.map(f => <button key={f.id} onClick={() => onViewFile?.(f)}><FileJson size={20}/><span><strong>{f.name}</strong><small>{recordData(f).size ? (recordData(f).size / 1024).toFixed(1) + " KB" : "原始文件"}</small></span><ArrowRight size={15}/></button>)}</div></section>}
    {modal && <div className="dv-modal-backdrop" onClick={() => !busy && setModal(null)}><div className="dv-modal" role="dialog" aria-modal="true" aria-label={modal === "labels" ? "编辑阶段标签" : modal === "dataset" ? "新建数据集" : "新建 Episode"} onClick={e => e.stopPropagation()}><div className="panel-head"><div><span className="eyebrow">DATA RECORD</span><h3>{modal === "labels" ? "编辑接触与恢复标签" : modal === "dataset" ? "新建数据集" : "新建 Episode"}</h3></div><button className="dv-icon-btn" aria-label="关闭" onClick={() => setModal(null)}><X size={20}/></button></div><div className="dv-fields"><label className="dv-span2">名称<input autoFocus value={draft.name || ""} onChange={e => setDraft({ ...draft, name: e.target.value })}/></label><label className="dv-span2">证据状态<select value={draft.status || "未知/待确认"} onChange={e => setDraft({ ...draft, status: e.target.value })}>{states.map(s => <option key={s}>{s}</option>)}</select></label>{modal !== "labels" && <><label>数据划分<select value={draft.split || "未划分"} onChange={e => setDraft({ ...draft, split: e.target.value })}><option>未划分</option><option value="train">train / 训练</option><option value="val">val / 验证</option><option value="test">test / 测试</option></select></label>{modal === "dataset" ? <><label>来源<input placeholder="采集日期 / Session / 文件" value={draft.source || ""} onChange={e => setDraft({ ...draft, source: e.target.value })}/></label><label className="dv-span2">描述<textarea rows={3} value={draft.description || ""} onChange={e => setDraft({ ...draft, description: e.target.value })}/></label></> : <><label>任务结果<select value={draft.outcome || "unknown"} onChange={e => setDraft({ ...draft, outcome: e.target.value })}><option value="unknown">未记录</option><option value="success">成功</option><option value="failure">失败</option></select></label><label className="dv-span2">所属数据集<select value={draft.datasetId || ""} onChange={e => setDraft({ ...draft, datasetId: e.target.value })}><option value="">请选择</option>{datasets.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label><label>时长 / s<input type="number" min="0" step="any" value={draft.duration || ""} onChange={e => setDraft({ ...draft, duration: e.target.value })}/></label></>}</>}{modal !== "dataset" && <><label className="dv-span2">关联预处理版本<select value={draft.preprocessId || ""} onChange={e => setDraft({ ...draft, preprocessId: e.target.value })}><option value="">未关联</option>{preprocessing.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><label>时间戳单位<select value={draft.timestampUnit || ""} onChange={e => setDraft({ ...draft, timestampUnit: e.target.value })}><option value="">继承关联配置 / 未知</option><option value="s">秒 s</option><option value="ms">毫秒 ms</option><option value="us">微秒 μs</option><option value="ns">纳秒 ns</option></select></label><label>测点原始单位<select value={draft.forceUnit || ""} onChange={e => setDraft({ ...draft, forceUnit: e.target.value })}><option value="">继承关联配置 / 未知</option><option value="N">N（已确认）</option><option value="待确认">待确认</option><option value="原始读数">原始读数</option><option value="counts">counts</option></select></label><label>设备合力单位<select value={draft.netForceUnit||""} onChange={e=>setDraft({...draft,netForceUnit:e.target.value})}><option value="">继承关联配置 / 未知</option><option value="N">N（设备已确认）</option><option value="待确认">待确认</option><option value="counts">counts</option></select></label></>}{modal === "labels" && <label className="dv-span2">时间区间标签 JSON<textarea className="dv-code" rows={8} value={draft.labels} onChange={e => setDraft({ ...draft, labels: e.target.value })}/><small>状态：free / contact / slip / recovery / stable；时间单位随数据配置。</small></label>}<label className="dv-span2">备注<textarea rows={2} value={draft.notes || ""} onChange={e => setDraft({ ...draft, notes: e.target.value })}/></label></div>{message && <div className="dv-notice" role="alert">{message}</div>}<div className="dv-modal-actions"><button className="btn" disabled={busy} onClick={() => setModal(null)}>取消</button><button className="btn btn-primary" disabled={busy} onClick={saveDraft}><Save size={15}/>{busy ? "保存中…" : "保存记录"}</button></div></div></div>}
  </div>;
}
