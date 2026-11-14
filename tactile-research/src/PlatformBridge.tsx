import React,{useState} from 'react';
import {Database, X} from 'lucide-react';
import {liveMode,labApi} from './platform-api';

export default function PlatformBridge({onImported}:{onImported:()=>Promise<any>}) {
  const [open,setOpen]=useState(false),[assets,setAssets]=useState<any[]>([]),[assetId,setAssetId]=useState(''),[episode,setEpisode]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
  async function show() {
    setOpen(true);setError('');setMessage('');
    if(!liveMode) return;
    setBusy(true);
    try{const r=await fetch('/api/datasets');const value=await r.json();if(!r.ok)throw new Error(value.error||'数据索引不可用');setAssets(Array.isArray(value)?value.filter(a=>typeof a.id==='string'&&a.id.startsWith('external-')):[]);}catch(e:any){setError(e.message);}finally{setBusy(false);}
  }
  const selected=assets.find(a=>a.id===assetId);
  async function importEpisode() {
    setBusy(true);setError('');setMessage('');
    try {const result=await labApi('/api/import-asset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset_id:assetId,episode_index:episode})});await onImported();setMessage(`已建立 ${result.records?.length||0} 条研究记录，保留原始资产来源。`);}catch(e:any){setError(e.message);}finally{setBusy(false);}
  }
  return <>
    <button className="btn" onClick={show}><Database size={16}/>导入平台 Episode</button>
    {open&&<div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="导入平台触觉 Episode"><div className="modal-head"><h2>从平台已保存数据导入</h2><button className="icon-btn" aria-label="关闭平台导入" onClick={()=>!busy&&setOpen(false)}><X size={20}/></button></div>
      <p className="modal-description">选择已登记且已退出采集的数据资产。只读提取一个 Episode，不连接传感器、不改写原 Zarr，也不会将未标定 counts 转成 N。</p>
      {!liveMode?<div className="notice-strip">当前是浏览器演示。真实平台数据只能从本地工作台进入；你仍可使用“导入数据 / 日志”上传文件体验研究流程。</div>:<>
        <label className="field">平台已登记外部数据资产<select value={assetId} onChange={e=>{setAssetId(e.target.value);setEpisode(0);setMessage('');}}><option value="">请选择含触觉的已登记外部数据集</option>{assets.map(a=><option key={a.id} value={a.id} disabled={a.active||!a.arrays?.tactile}>{a.name||a.id} · {a.active?'采集中，禁止读取':!a.arrays?.tactile?'无触觉字段':`${a.n_episodes||0} episodes`}</option>)}</select></label>
        <label className="field">Episode 序号（从 0 开始）<input type="number" min="0" max={Math.max(0,(selected?.n_episodes||1)-1)} value={episode} onChange={e=>setEpisode(Number(e.target.value))}/></label>
        <p className="muted">导入研究记录不等于通过策略训练门控。单位、时钟、完整性、归一化和数据划分仍需核对；过大的 Episode 请在本地先明确分块。</p>
      </>}
      {error&&<div className="error-banner" role="alert">{error}</div>}{message&&<div className="notice-strip" role="status">{message}</div>}
      <div className="modal-foot"><button className="btn" disabled={busy} onClick={()=>setOpen(false)}>关闭</button><button className="btn btn-primary" disabled={!liveMode||busy||!selected||selected.active||!selected.arrays?.tactile||!Number.isInteger(episode)||episode<0||episode>=selected.n_episodes} onClick={importEpisode}>{busy?'读取中…':'只读导入本条'}</button></div>
    </section></div>}
  </>;
}
