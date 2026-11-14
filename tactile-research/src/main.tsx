import React from 'react';
import {createRoot} from 'react-dom/client';
import Home from './app/page';
import {liveMode,embedded} from './platform-api';
import './app/globals.css';
import './platform.css';
if(embedded)document.documentElement.classList.add('platform-embedded');
function App(){return <><div className={'platform-context '+(liveMode?'local':'demo')}><strong>{liveMode?'本地研究记录':'浏览器演示'}</strong><span>{liveMode?'数据与附件保存在本机 edge_agent；本模块管理研究记录，不启动 GPU 训练。':'记录与附件只保存在当前浏览器，不上传云端；不同浏览器不共享。'}</span><a href="../#ur5e" target="_top">返回具身工坊</a></div><Home/></>}
createRoot(document.getElementById('root')!).render(<App/>);
