/* Workstation setup once; capture, data and training are the daily workflow. */
const uxProfileFields = [
  ['name','工位名称'], ['robot_ip','UR5e IP（独立状态连接）'], ['gripper','二指夹爪型号'],
  ['camera_0','腕部 D435 序列号'], ['camera_1','侧视一 D435 序列号'], ['camera_2','侧视二 D435 序列号'],
  ['capture_script','GELLO 采集脚本'], ['project','训练项目目录'],
  ['conda_env','训练 Conda 环境'], ['output_root','训练结果根目录'], ['port','触觉串口（选填）']
];
let uxProfiles = {demo:null, live:null}, uxEditing = false, uxSaving = false;
for (const mode of ['demo','live']) {
  try { uxProfiles[mode] = JSON.parse(localStorage.getItem('ur-workstation-'+mode)); } catch {}
}
const uxProfile = () => uxProfiles[pMode];
function uxApplyProfile() {
  const profile = uxProfile();
  if (!profile) return;
  for (const key of ['capture_script','project','conda_env','output_root']) uwConfig[key] = profile[key];
  pConfig = {...pConfig,robotIp:profile.robot_ip,serial:profile.camera_1,port:profile.port,address:profile.address,baud:profile.baud};
  uwPersist();
}
function uxDraft() {
  return uxProfile() || {name:'UR5e 抓放工位',robot_ip:pConfig.robotIp||'',gripper:'二指夹爪',
    camera_0:'',camera_1:pConfig.serial||'',camera_2:'',capture_script:uwConfig.capture_script,
    project:uwConfig.project,conda_env:uwConfig.conda_env,output_root:uwConfig.output_root,
    port:pConfig.port||'',address:Number(pConfig.address)||1,baud:Number(pConfig.baud)||921600};
}
function uxField(key,label,value,type='text') {
  return `<div><label for="ux-${key}">${label}</label><input id="ux-${key}" type="${type}" value="${esc(String(value??''))}"></div>`;
}
function uxSummary() {
  const profile = uxProfile();
  if (!profile) return '<div class="warning">先完成一次工位配置，保存硬件信息与运行环境。<a href="#ur-settings">去配置工位 →</a></div>';
  return `<div class="ux-profile-strip"><div><strong>${esc(profile.name)}</strong><span>UR5e · 两路侧视 D435＋一路腕部 D435 · ${esc(profile.conda_env)}</span></div><a href="#ur-settings">工位设置</a></div>`;
}
function uxSetupGate() {
  return pHead('先配置你的工位','保存硬件信息和运行环境，然后开始日常数采与训练。')+
    '<section class="card ux-welcome"><div class="eyebrow">首次使用</div><h2>一次配置，日常直接开工</h2><p>填写机器人、三路相机、采集脚本与训练环境，保存为这个工位的固定配置。</p><a class="ux-primary-link" href="#ur-settings">配置工位 →</a><p class="subtle">已有版本中的设备信息会带入草稿，确认后保存。演示配置与本地实机配置分别保存。</p></section>';
}
function uxRequireProfile() {
  if (uxProfile()) return true;
  toast('请先保存工位配置'); location.hash='ur-settings'; return false;
}
function uxNavLink(id,name,icon) {
  return `<a href="#${id}" data-page="${id}"><span class="ux-nav-icon">${icon}</span>${name}</a>`;
}
$('#nav').innerHTML = '<div class="nav-label">UR5e · 日常工作</div>'+
  [['ur5e','工作台','⌂'],['ur-live','数采','◉'],['edge-data','数据','▤'],['edge-train','训练','▷']].map(x=>uxNavLink(...x)).join('')+
  '<div class="nav-label">工位设置</div>'+uxNavLink('ur-settings','硬件与环境','⚙')+
  uxNavLink('camera-check','相机基准','▣')+
  '<details class="ux-other"><summary>其他项目与工具</summary>'+pages.filter(([id])=>!['ur5e','ur-live','edge-data','edge-train','sensors','camera-check','ur-settings'].includes(id)).map(([id,name])=>uxNavLink(id,name,'·')).join('')+'</details>';
pages.push(['ur-settings','工位设置']);
for (const [id,name] of [['ur5e','工作台'],['ur-live','数采'],['edge-data','数据'],['edge-train','训练']]) pages.find(p=>p[0]===id)[1]=name;
pages.find(p=>p[0]==='sensors')[1]='工位设置';

views['ur-settings'] = () => {
  const profile=uxProfile(), draft=uxDraft();
  const editor = !profile || uxEditing;
  const body = editor ? `<div class="uw-form">${uxProfileFields.map(([key,label])=>uxField(key,label,draft[key])).join('')}${uxField('address','触觉设备地址',draft.address,'number')}${uxField('baud','触觉波特率',draft.baud,'number')}</div>
    <p class="subtle">相机序列号用于记录通道绑定，现场 GELLO 相机服务也需采用同样的绑定。未填序列号的通道由现场脚本管理。</p>
    <div class="actions"><button class="primary" ${uxSaving?'disabled':''} onclick="uxSaveProfile()">${uxSaving?'保存中…':'保存工位配置'}</button>${profile?'<button onclick="uxEditing=false;render()">取消修改</button>':''}</div>` :
    `<dl class="ux-profile-list">${uxProfileFields.map(([key,label])=>`<div><dt>${label}</dt><dd>${esc(profile[key]||'未填写')}</dd></div>`).join('')}</dl><div class="actions"><button onclick="uxEditing=true;render()">修改工位配置</button><a href="#ur-live">进入数采 →</a></div>`;
  return pHead('工位设置','硬件与运行环境保存在工位中，设备更换或路径变化时再修改。',pMode==='live'?'<button onclick="uxReloadProfile(true)">重新读取已保存配置</button>':'')+
    `<div class="grid2"><section class="card fields"><div class="card-head"><h2>${profile?'当前工位':'首次配置'}</h2>${badge(profile?'配置已保存':'待配置',profile?'':'amber')}</div>${body}</section>
    <section class="card"><h2>配置后的使用顺序</h2><ol><li>进入数采，核对相机与现场状态。</li><li>启动采集并完成 ALIGN，按条录制。</li><li>进入数据，检查片段并选择训练集。</li><li>进入训练，提交实验并查看结果。</li></ol><p class="subtle">${pMode==='demo'?'演示配置保存在当前浏览器。':'实机配置写入本地服务数据目录的 workstation.json。'}保存配置后，设备连接状态仍以实际数据流为准。</p><a href="#camera-check">建立或复查相机基准 →</a></section>
    ${profile?`<section class="card wide"><details><summary>设备连接与诊断</summary><div class="grid2 ux-diagnostics"><div><h3>UR5e 状态</h3><button onclick="pConnect('robot')">连接只读状态</button><button onclick="pDisconnect('robot')">断开</button><div id="p-robot-values">${pRobotReadings()}</div></div><div><h3>D435 独立预览</h3><p class="subtle">相机已被 GELLO 占用时，查看 GELLO 画面。</p><button onclick="pConnect('rgb')">连接侧视一预览</button><button onclick="pDisconnect('rgb')">断开</button><div id="p-rgb-live">${pRgb()}</div></div><div><h3>M3025 触觉</h3><button onclick="pConnect('tactile')">连接触觉</button><button onclick="pDisconnect('tactile')">断开</button><div id="p-touch-live">${pHeatmap()}</div></div><div id="p-device-errors">${pDiagnostics()}</div></div></details></section>`:''}</div>`;
};
views.sensors=views['ur-settings'];
async function uxSaveProfile() {
  if (uxSaving) return;
  const profile=Object.fromEntries(uxProfileFields.map(([key])=>[key,$('#ux-'+key).value.trim()]));
  profile.address=Number($('#ux-address').value); profile.baud=Number($('#ux-baud').value);
  if(['name','capture_script','project','conda_env','output_root'].some(k=>!profile[k]))return toast('请填写工位名称、采集脚本和训练环境');
  uxSaving=true;
  try {
    if(pMode==='live') {
      const result=await pApi('workstation',{profile,expected_revision:uxProfile()?.revision||null});
      uxProfiles.live=result.profile;
    } else uxProfiles.demo={...profile,revision:'demo-'+Date.now(),saved_at:Date.now()/1000};
    localStorage.setItem('ur-workstation-'+pMode,JSON.stringify(uxProfile()));
    uxApplyProfile(); uxEditing=false; toast('工位已保存，日常可直接进入数采');
  } catch(e){toast(e.message)} finally{uxSaving=false;render()}
}
async function uxReloadProfile(show=false) {
  if(pMode!=='live')return;
  try {
    const result=await pApi('workstation'); uxProfiles.live=result.profile;
    localStorage.setItem('ur-workstation-live',JSON.stringify(result.profile));
    uxApplyProfile(); uxEditing=false; render(); if(show)toast('已读取工位配置');
  }catch(e){toast('读取工位失败：'+e.message)}
}
const uxSwitchMode=pSwitchMode;
pSwitchMode=async function(){await uxSwitchMode();if(pMode==='live')await uxReloadProfile();else{uxEditing=false;uxApplyProfile();render()}};
const uxCaptureStart=uwCaptureStart;
uwSaveCapture=function(){uxApplyProfile()};
uwCaptureStart=async function(){if(uxRequireProfile())await uxCaptureStart()};

views.ur5e=()=>!uxProfile()?uxSetupGate():pHead('工作台','选择今天要做的工作。')+uxSummary()+
  `<div class="ux-daily-grid">${[['01','ur-live','数采','对齐、录制、保存下一条'],['02','edge-data','数据','查看片段、检查并选择训练集'],['03','edge-train','训练','创建实验、查看日志与模型结果']].map(([n,id,title,copy])=>`<a class="card ux-task" href="#${id}"><span>${n}</span><h2>${title}</h2><p>${copy}</p><strong>进入${title} →</strong></a>`).join('')}</div>
  <section class="card"><div class="card-head"><h2>当前工位状态</h2><a href="#ur-settings">硬件与环境设置</a></div><div id="p-device-summary">${pDeviceSummary()}</div></section>`;
views['ur-live']=()=>!uxProfile()?uxSetupGate():pHead('数采','采前核对 → 关节对齐 → 逐条录制。')+uxSummary()+
  `<div class="ux-step-strip"><span>1 采前核对</span><span>2 ALIGN 对齐</span><span>3 录制与保存</span><a href="#edge-data">4 查看数据 →</a></div>
  <section class="card ux-preflight"><div><h2>采前核对</h2><p>确认相机视角、物体位置和工作区，然后启动采集。</p></div><a href="#camera-check"><button>检查相机画面与基准</button></a></section>
  <section class="card"><div id="uw-capture-panel">${uwCapturePanel()}</div></section>
  <details class="card ux-advanced"><summary>操作说明与终端命令</summary><p>ALIGN 后观察对齐完成再确认。s 开始，Enter 结束，d 删除当前条，u 进入下一条。</p><p>使用已保存的工位脚本，当前启动参数为 --no-tactile。录制结果以脚本日志为准。</p><pre class="log">${esc(uwCaptureCommand())}</pre><button onclick="uwCopy(uwCaptureCommand())">复制启动命令</button></details>`;

views['edge-data']=()=>!uxProfile()?uxSetupGate():pHead('数据','选择完整轨迹，检查后用于训练。')+uxSummary()+
  `<section class="card fields"><h2>使用现场已有数据集</h2><label for="ux-dataset">完整 Zarr 路径</label><input id="ux-dataset" value="${esc(uwConfig.dataset)}"><div class="actions"><button class="primary" onclick="uxUseDataset()">选择并前往训练 →</button></div><p class="subtle">填写现场脚本保存的 replay_buffer.zarr；启动训练前会检查数据。</p></section>
  <section class="card"><div class="card-head"><h2>平台数据片段</h2><div class="actions"><button onclick="pRefreshData()">刷新</button><button onclick="pCombine()">合并所选片段</button></div></div><div id="p-data-table">${pDataTable()}</div></section>
  <details class="card fields ux-advanced"><summary>平台旁路录制工具</summary><p>通过 GELLO 帧推送录制到平台数据目录。</p><label><input id="p-touch-enable" type="checkbox" style="width:auto"> 包含 M3025 触觉</label><div class="actions"><button onclick="pCapture('start')">开始旁路片段</button><button onclick="pCapture('save')">结束并保存</button><button onclick="pCapture('discard')">丢弃片段</button></div><p id="p-capture-status">${pMode==='demo'?'模拟片段':pStatus.capture?.active?'正在录制':'未录制'}</p></details>`;
function uxUseDataset(){const path=$('#ux-dataset').value.trim();if(!path)return toast('请填写数据集路径');uwConfig.dataset=path;uwPersist();location.hash='edge-train'}

function uxTrainingFieldGroup(keys){return uwFields.filter(([key])=>keys.includes(key)).map(f=>uwInput(...f)).join('')}
views['edge-train']=()=>!uxProfile()?uxSetupGate():pHead('训练','选择数据、调整本次实验参数，然后查看训练结果。')+uxSummary()+
  `<div class="grid2"><section class="card fields"><h2>创建训练实验</h2>${uxTrainingFieldGroup(['dataset','exp_name'])}<div class="uw-form">${uxTrainingFieldGroup(['epochs','batch_size','lr'])}</div>
  <details class="ux-advanced"><summary>高级训练参数</summary><div class="uw-form">${uxTrainingFieldGroup(['weight_decay','n_obs_steps','checkpoint_every','monitor_key','topk'])}
    ${uwInput('resize-h','Resize 高','number',uwConfig.resize[0])}${uwInput('resize-w','Resize 宽','number',uwConfig.resize[1])}${uwInput('crop-h','Crop 高','number',uwConfig.crop[0])}${uwInput('crop-w','Crop 宽','number',uwConfig.crop[1])}</div><label><input id="uw-random-crop" type="checkbox" style="width:auto" ${uwConfig.random_crop?'checked':''}> 随机裁剪</label></details>
    <div hidden>${uxTrainingFieldGroup(['project','conda_env','output_root'])}</div>
    <div class="actions"><button onclick="uwPreviewTraining()">检查数据与环境</button><button class="primary" onclick="uwStartTraining()">开始训练</button></div><p id="uw-training-check" class="subtle">当前使用工位保存的训练环境，预设为三路视觉 DP。</p></section>
  <section class="card"><h2>本次训练环境</h2><dl class="ux-profile-list"><div><dt>工位</dt><dd>${esc(uxProfile().name)}</dd></div><div><dt>算法</dt><dd>Diffusion Policy · 20Hz 配置</dd></div><div><dt>观测</dt><dd>三路 RGB＋关节＋夹爪状态</dd></div><div><dt>环境</dt><dd>${esc(uxProfile().conda_env)}</dd></div></dl><a href="#ur-settings">需要更换环境？修改工位设置 →</a>
    <details class="ux-advanced"><summary>查看或复制训练命令</summary><pre id="uw-command" class="log uw-terminal">${esc(uwTrainingCommand(uwConfig))}</pre><button onclick="uwCopy(uwTrainingCommand(uwReadTraining()))">复制命令</button></details>
    <details class="ux-advanced"><summary>包内 DP 训练工具</summary><button onclick="uxShowBundledTraining()">打开包内训练工具</button></details></section></div>
  <section class="card"><div class="card-head"><h2>训练任务与结果</h2><button onclick="uwPoll(true)">刷新</button></div><div id="uw-jobs">${uwJobs()}</div></section>`;
function uxShowBundledTraining(){modal(uwOldTrainingView())}
uxApplyProfile();
render();
