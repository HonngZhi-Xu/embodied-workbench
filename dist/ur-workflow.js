/* Site workflow: terminal keys are sent only by the local Linux service. */
const uwDefaults = {
  capture_script: '/home/zaq/gello_software/experiments/start_live_follow_local.sh',
  project: '/home/zaq/diffusion_policy', conda_env: 'robodiff',
  dataset: '/home/zaq/runtime_logs/live_follow_capture/20261111T061114Z_real/manual_task/replay_buffer.zarr',
  output_root: '/home/zaq/diffusion_policy/data/outputs/dataset_20261111/vision_only_r1_60ep',
  n_obs_steps: 1, resize: [240,320], crop: [216,288], random_crop: true,
  lr: 5e-5, weight_decay: 1e-4, batch_size: 8, epochs: 60,
  checkpoint_every: 5, monitor_key: 'train_loss', topk: 3, exp_name: 'vision_only'
};
let uwConfig;
try { uwConfig = JSON.parse(localStorage.getItem('ur-site-workflow-v1')); } catch {}
uwConfig = {...uwDefaults, ...uwConfig};
let uwLive = {capture:null,jobs:[]}, uwDemo = null, uwBusy = false, uwTrainLegacy = false;
const uwFields = [
  ['project','训练项目目录','text'], ['conda_env','Conda 环境','text'],
  ['dataset','完整 Zarr 数据集路径','text'], ['output_root','实验输出根目录','text'],
  ['epochs','训练轮数','number'], ['batch_size','Batch size','number'],
  ['lr','学习率','number'], ['weight_decay','Weight decay','number'],
  ['n_obs_steps','观测步数','number'], ['checkpoint_every','检查点间隔 / epoch','number'],
  ['monitor_key','Top-K 监控指标','text'], ['topk','Top-K 保留数','number'],
  ['exp_name','实验名称','text']
];
const uwSession = () => pMode === 'demo' ? uwDemo : uwLive.capture;
const uwQuote = text => "'" + String(text).replaceAll("'", "'\"'\"'") + "'";
function uwPersist() { localStorage.setItem('ur-site-workflow-v1', JSON.stringify(uwConfig)); }
function uwInput(key,label,type='text',value=uwConfig[key]) {
  return `<div><label for="uw-${key}">${label}</label><input id="uw-${key}" type="${type}" ${type==='number'?'step="any"':''} value="${esc(String(value))}"></div>`;
}
function uwReadTraining() {
  const result = {...uwConfig};
  for (const [key,,type] of uwFields) result[key] = type === 'number' ? Number($('#uw-'+key).value) : $('#uw-'+key).value.trim();
  result.resize = ['resize-h','resize-w'].map(k=>Number($('#uw-'+k).value));
  result.crop = ['crop-h','crop-w'].map(k=>Number($('#uw-'+k).value));
  result.random_crop = $('#uw-random-crop').checked;
  uwConfig = result; uwPersist(); return result;
}
function uwTrainingCommand(v) {
  const overrides = [
    'dataset_path='+JSON.stringify(v.dataset), '~shape_meta.obs.tactile',
    'task.dataset.obs_keys=[camera_0,camera_1,camera_2,robot_joint,gripper_position]',
    'n_obs_steps='+v.n_obs_steps,
    'policy.obs_encoder.resize_shape='+JSON.stringify(v.resize),
    'policy.obs_encoder.crop_shape='+JSON.stringify(v.crop),
    'policy.obs_encoder.random_crop='+v.random_crop,
    'optimizer.lr='+v.lr, 'optimizer.weight_decay='+v.weight_decay,
    'dataloader.batch_size='+v.batch_size, 'training.num_epochs='+v.epochs,
    'training.resume=false', 'training.checkpoint_every='+v.checkpoint_every,
    'checkpoint.topk.monitor_key='+v.monitor_key, 'checkpoint.topk.mode=min',
    'checkpoint.topk.k='+v.topk, 'exp_name='+v.exp_name,
    'hydra.run.dir='+v.output_root.replace(/\/$/,'')+'/${now:%Y.%m.%d}/${now:%H.%M.%S}'
  ];
  return 'conda activate '+uwQuote(v.conda_env)+'\ncd '+uwQuote(v.project)+
    '\npython train.py --config-name=train_gello_live_image_20hz \\\n  '+overrides.map(uwQuote).join(' \\\n  ');
}
function uwCaptureCommand() { return 'bash '+uwQuote(uwConfig.capture_script)+' --no-tactile'; }
async function uwCopy(text) {
  try { await navigator.clipboard.writeText(text); toast('已复制'); }
  catch { modal(`<h2>选择并复制命令</h2><textarea rows="16" style="width:100%">${esc(text)}</textarea><button onclick="closeModal()">关闭</button>`); }
}
function uwCapturePanel() {
  const session = uwSession();
  const running = !!session?.running;
  const disabled = condition => uwBusy || !condition ? 'disabled' : '';
  const phase = ({waiting_align:'等待终端 ALIGN 提示',align_sent:'已发送 ALIGN，请观察对齐结果',ready:'操作员已确认对齐完成',recording:'已发送开始录制命令',between:'已发送结束／删除命令',next_sent:'已发送进入下一条命令'})[session?.phase] || '采集未启动';
  return `<div class="card-head"><h2>${phase}</h2>${badge(pMode==='demo'?'模拟终端':running?'现场终端运行中':'现场终端未运行',pMode==='demo'?'amber':'')}</div>
    <div class="actions uw-keyboard">
      <button class="primary" ${disabled(!running)} onclick="uwCaptureStart()">启动现场采集</button>
      <button ${disabled(running && session?.align_prompt && session?.phase==='waiting_align')} onclick="uwConfirm('align')">发送 ALIGN</button>
      <button ${disabled(running && session?.phase==='align_sent')} onclick="uwConfirm('ready')">确认对齐已完成</button>
      <button ${disabled(running && session?.aligned && !['recording','next_sent'].includes(session?.phase))} onclick="uwCaptureKey('start')">s ↵ 开始录制</button>
      <button ${disabled(running && ['recording','next_sent'].includes(session?.phase))} onclick="uwCaptureKey('finish')">↵ 结束本条</button>
      <button ${disabled(running && ['recording','next_sent'].includes(session?.phase))} onclick="uwConfirm('discard')">d ↵ 删除当前条</button>
      <button ${disabled(running && session?.phase==='between')} onclick="uwCaptureKey('next')">u ↵ 进入下一条</button>
      <button ${disabled(running)} onclick="uwConfirm('stop')">停止采集进程</button>
    </div><p class="subtle">状态表示平台已发送的命令。是否开始、保存或删除成功，请核对下方现场脚本日志。u 已按现场确认设置为“进入下一条录制”。</p>
    <pre class="log uw-terminal" id="uw-terminal">${esc((session?.log||[]).join('') || '启动后显示现场终端输出；模拟模式可体验按键流程。')}</pre>`;
}
pages.push(['ur-live','现场采集']);
$('#nav').insertAdjacentHTML('afterbegin','<a href="#ur-live" data-page="ur-live">⌨ UR5e 现场采集</a>');
views['ur-live'] = () => pHead('UR5e · 现场采集台','现场脚本 / 手动 ALIGN / 三路 RGB / 原生 episode 录制。', '<a href="#camera-check"><button>采前相机检查</button></a>')+
  `<div class="grid2"><section class="card fields"><h2>现场启动配置</h2>${uwInput('capture-script','采集脚本绝对路径','text',uwConfig.capture_script)}
  <p>启动参数 <code>--no-tactile</code>，当前流程为视觉采集。ALIGN 会触发现有 GELLO 的关节对齐，请先确认现场姿态与工作区。</p>
  <div class="actions"><button onclick="uwSaveCapture()">保存脚本路径</button><button onclick="uwCopy(uwCaptureCommand())">复制启动命令</button></div>
  <pre class="log">${esc(uwCaptureCommand())}</pre><p class="subtle">采集进程由本地 Linux 工作台托管。已在其他终端启动的进程，需要先结束再由本页启动。采前相机检查需连接对应的 GELLO 图像来源。</p></section>
  <section class="card"><h2>录制顺序</h2><ol><li>启动现场脚本，等待日志提示输入 ALIGN。</li><li>核对姿态后发送 ALIGN，观察机械臂完成对齐，再点击确认。</li><li>s＋Enter 开始，Enter 结束并保存本条。</li><li>录制中用 d＋Enter 删除本条。</li><li>条目结束后用 u＋Enter 进入下一条录制。</li></ol><a href="#edge-train">录制完成，进入视觉 DP 训练 →</a><p class="subtle">这里使用现场脚本自己的数据保存流程；平台旁路录制入口仍在“实机数据管理”。</p></section>
  <section class="card wide"><div id="uw-capture-panel">${uwCapturePanel()}</div></section></div>`;
const uwOldTrainingView = views['edge-train'];
views['edge-train'] = () => {
  if (uwTrainLegacy) return '<button onclick="uwTrainLegacy=false;render()">返回现场 robodiff 配置</button>'+uwOldTrainingView();
  return pHead('UR5e · 视觉 DP 训练','robodiff / train_gello_live_image_20hz / 三路 RGB＋关节＋夹爪。', '<button onclick="uwTrainLegacy=true;render()">包内 DP 训练</button>')+
    `<div class="grid2"><section class="card fields"><h2>现场训练参数</h2><div class="uw-form">${uwFields.map(f=>uwInput(...f)).join('')}
    ${uwInput('resize-h','Resize 高','number',uwConfig.resize[0])}${uwInput('resize-w','Resize 宽','number',uwConfig.resize[1])}
    ${uwInput('crop-h','Crop 高','number',uwConfig.crop[0])}${uwInput('crop-w','Crop 宽','number',uwConfig.crop[1])}</div>
    <label><input id="uw-random-crop" type="checkbox" ${uwConfig.random_crop?'checked':''} style="width:auto"> 随机裁剪</label>
    <p>观测字段固定为 camera_0、camera_1、camera_2、robot_joint、gripper_position。当前预设删除 tactile 观测，resume=false。</p>
    <div class="actions"><button onclick="uwPreviewTraining()">更新命令 / 检查本地环境</button><button onclick="uwCopy(uwTrainingCommand(uwReadTraining()))">复制训练命令</button><button class="primary" onclick="uwStartTraining()">启动现场训练</button></div>
    <p id="uw-training-check" class="subtle">输入实际数据集地址后先更新命令。本地启动通过 conda run 使用指定环境。</p></section>
    <section class="card"><h2>可复制的训练命令</h2><pre id="uw-command" class="log uw-terminal">${esc(uwTrainingCommand(uwConfig))}</pre><p>训练输出按日期、时间及任务 ID 建目录，保留请求参数与日志。Top-K 默认监控 train_loss，验证效果请结合独立验证集和实机评测。</p></section></div>
    <section class="card"><div class="card-head"><h2>现场训练任务</h2><button onclick="uwPoll(true)">刷新任务</button></div><div id="uw-jobs">${uwJobs()}</div></section>`;
};
function uwJobs() {
  if (pMode==='demo') return '<p>演示模式可编辑和复制命令；实际任务由 Linux 本地服务启动。</p>';
  return uwLive.jobs.map(j=>`<div class="card-head"><strong>${esc(j.id)} · ${esc(j.status)}</strong>${j.status==='running'?`<button onclick="uwCancelTraining('${esc(j.id)}')">取消训练</button>`:''}</div><p>${esc(j.dataset)}</p><p>输出 ${esc(j.output_dir||'')}</p><pre class="log uw-terminal">${esc((j.log||[]).join('\n'))}</pre><p>${esc((j.checkpoints||[]).join('\n'))}</p>`).join('') || '<p>尚无现场训练任务。</p>';
}
function uwSaveCapture() { uwConfig.capture_script=$('#uw-capture-script').value.trim(); uwPersist(); toast('已保存脚本路径'); }
async function uwCaptureStart() {
  uwSaveCapture();
  if (pMode==='demo') { uwDemo={id:'DEMO-UR',running:true,aligned:false,align_prompt:true,phase:'waiting_align',log:['[模拟] 等待输入 ALIGN\n']}; render(); return; }
  if (uwBusy) return; uwBusy=true;
  try { uwLive.capture=await pApi('ur-workflow/capture/start',{script:uwConfig.capture_script}); }
  catch(e) { toast(e.message); } finally { uwBusy=false; if ($('#uw-capture-panel')) $('#uw-capture-panel').innerHTML=uwCapturePanel(); }
}
function uwConfirm(action) {
  const message=({align:'请确认现场姿态和工作区，发送 ALIGN 后由 GELLO 执行关节对齐。',ready:'请先观察终端日志与机械臂，确认关节对齐已经完成。',discard:'将向现场终端发送 d＋Enter，删除当前正在录制的条目。',stop:'停止本页托管的采集进程。请先结束当前录制并核对保存结果。'})[action];
  modal(`<h2>确认操作</h2><p>${message}</p><div class="actions"><button onclick="closeModal()">返回</button><button class="primary" onclick="closeModal();uwCaptureKey('${action}',true)">确认并执行</button></div>`);
}
async function uwCaptureKey(action,confirmed=false) {
  const session=uwSession(); if (!session?.running || uwBusy) return;
  uwBusy=true;
  try {
    if (pMode==='demo') {
      if (action==='stop') session.running=false;
      else { session.phase=({align:'align_sent',ready:'ready',start:'recording',finish:'between',discard:'between',next:'next_sent'})[action]; if(action==='ready')session.aligned=true; }
      session.log.push('[模拟] '+action+'\n');
    } else if(action==='stop') uwLive=await pApi('ur-workflow/capture/stop',{session_id:session.id});
    else uwLive.capture=await pApi('ur-workflow/capture/key',{action,session_id:session.id,confirmed});
  } catch(e) { toast(e.message); } finally { uwBusy=false; if($('#uw-capture-panel'))$('#uw-capture-panel').innerHTML=uwCapturePanel(); }
}
async function uwPreviewTraining() {
  const config=uwReadTraining(); $('#uw-command').textContent=uwTrainingCommand(config);
  if(pMode==='demo') return toast('命令已更新并保存；现场环境检查请连接 Linux 本地工作台。');
  try { const plan=await pApi('ur-workflow/training/preview',config); $('#uw-command').textContent=plan.command; $('#uw-training-check').textContent=Object.entries(plan.checks).map(([k,v])=>k+' '+(v?'✓':'缺失')).join(' · '); }
  catch(e){toast(e.message)}
}
async function uwStartTraining() {
  const config=uwReadTraining();
  if(pMode==='demo')return toast('请在现场 Linux 工作台启动，或复制命令到 robodiff 终端执行。');
  try { await pApi('ur-workflow/training/start',config); await uwPoll(); toast('训练任务已提交，状态以进程日志为准'); } catch(e){toast(e.message)}
}
async function uwCancelTraining(id) { try{await pApi('ur-workflow/training/cancel',{id}); await uwPoll()}catch(e){toast(e.message)} }
async function uwPoll(show=false) {
  if(pMode!=='live')return;
  try { uwLive=await pApi('ur-workflow/status'); if(!uwBusy&&$('#uw-capture-panel'))$('#uw-capture-panel').innerHTML=uwCapturePanel(); if($('#uw-jobs'))$('#uw-jobs').innerHTML=uwJobs(); if(show)toast('已刷新现场进程状态'); }
  catch(e){if(show)toast(e.message)}
}
views.ur5e=()=>pHead('UR5e · 现场工作台','GELLO 现场采集 → 完整轨迹 → robodiff 视觉 DP 训练。')+
  `<section class="hero"><div class="hero-copy"><div class="eyebrow">UR5e / LIVE FOLLOW / 20 HZ</div><h2>从关节对齐<br>到视觉策略训练</h2><p>三路 RealSense RGB · GELLO · 二指夹爪<br>现场预设 --no-tactile / robodiff</p><div class="actions"><button class="primary" onclick="location.hash='ur-live'">进入现场采集台</button><button onclick="location.hash='edge-train'">配置视觉 DP 训练</button></div></div><div class="robot-stage">${pArm()}</div></section>
  <div class="grid2"><section class="card"><h2>采集操作</h2><p>手动 ALIGN 对齐确认 → s 开始 → Enter 结束 → u 下一条。录制中可用 d 删除当前条。</p><a href="#ur-live">按键、日志和脚本配置 →</a></section><section class="card"><h2>当前训练预设</h2><p>60 epochs · batch 8 · lr 5e-5 · n_obs_steps 1<br>Resize 240×320 · Crop 216×288 · 随机裁剪</p><a href="#edge-train">修改参数与数据集路径 →</a></section><section class="card"><h2>设备状态</h2><div id="p-device-summary">${pDeviceSummary()}</div><a href="#sensors">接入设备 →</a></section><section class="card"><h2>采前与数据检查</h2><p>逐路确认相机位置、数据来源和完整 episode，再启动正式训练。</p><div class="actions"><a href="#camera-check">相机基准检查</a><a href="#edge-data">旁路数据管理</a></div></section></div>`;
setInterval(()=>{if(['ur-live','edge-train'].includes(page))uwPoll()},1500);
render();
