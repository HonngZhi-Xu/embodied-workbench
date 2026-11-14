/* Integrate the research workbench without sharing its styles or runtime with capture. */
pages.push(['tactile-research','触觉研究']);
$('#nav').insertAdjacentHTML('beforeend','<div class="nav-label">M3025 · 研究与分析</div><a href="#tactile-research" data-page="tactile-research"><span class="ux-nav-icon">◈</span>触觉研究工作台</a>');
views['tactile-research']=()=>{
  const mode=pMode==='live'?'live':'demo';
  const url='tactile-lab/index.html?mode='+mode;
  return pHead('触觉研究工作台','M3025 数据与 Episode → 预处理 → 编码器配置 → 实验评估与追溯。',`<a class="ux-primary-link" href="${url}" target="_blank" rel="noopener">打开完整工作台 ↗</a>`)+
    `<section class="card tactile-context"><strong>${mode==='live'?'本地研究记录':'浏览器演示'}</strong><p>${mode==='live'?'记录与原始附件保存在本地服务的 tactile_lab 目录；可导入已登记、已结束采集的触觉 Episode。':'当前为隔离的浏览器演示，数据仅保存在此浏览器；真实资产导入和持久文件管理需要连接本地工作台。'}</p><p>研究配置与分析不会启动 GPU 训练、修改原采集程序或再次打开传感器。预训练和融合配方仍需实际算法实现与实验验证。</p></section>`+
    `<iframe class="tactile-research-frame" src="${url}&embedded=1" title="M3025 触觉研究与实验管理" loading="eager"></iframe>`;
};
render();
