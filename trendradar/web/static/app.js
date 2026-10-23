'use strict';
const $ = id => document.getElementById(id);
let csrf = '', activeFile = 'config.yaml', revision = '', dirty = false, polling;
const labels = {'idle':'等待采集','running':'采集中','succeeded':'采集完成','failed':'采集失败','interrupted':'已中断'};
const helps = {
 'config.yaml':'在 platforms.sources 中管理热榜，在 rss.feeds 中添加订阅；AI、推送开关也在这里配置。',
 'frequency_words.txt':'空行分组，普通词表示匹配；!词 表示排除；+词 表示必须包含。',
 'ai_interests.txt':'用自然语言描述关注方向。只有主配置启用 AI 筛选并设置模型服务后才会使用。',
 'timeline.yaml':'定义每次运行时采用的采集、筛选与推送策略；本页面不会启动定时采集。'
};
async function api(path, options={}) {
 const headers = {'Content-Type':'application/json', ...options.headers};
 if (csrf) headers['X-CSRF-Token'] = csrf;
 const response = await fetch(path,{...options,headers,credentials:'same-origin'});
 let data;
 try {data=await response.json();} catch {throw new Error('服务响应异常，请稍后重试');}
 if(!response.ok){if(response.status===401 && path!=='/api/login')showLogin();throw new Error(data.error || data.detail || '请求失败');}
 return data;
}
function notice(message,error=false){$('notice').hidden=false;$('notice').textContent=message;$('notice').classList.toggle('error',error);}
function showLogin(){clearInterval(polling);csrf='';$('workspace').hidden=true;$('login-screen').hidden=false;$('config-content').value='';$('log').textContent='';dirty=false;revision='';$('password-form').reset();history.replaceState(null,'','/login');}
async function enter(session){csrf=session.csrf;$('username').textContent=session.username;$('login-screen').hidden=true;$('workspace').hidden=false;history.replaceState(null,'','/');selectPage('overview');await refresh();polling=setInterval(()=>refresh().catch(e=>notice(e.message,true)),5000);}
function selectPage(page){document.querySelectorAll('section[id^="page-"]').forEach(s=>s.hidden=s.id!==`page-${page}`);document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b.dataset.page===page));$('breadcrumb').textContent='工作空间 / '+({overview:'采集概览',settings:'来源与配置',account:'账号设置'}[page]);if(page==='settings'&&!revision)loadConfig();}
async function refresh(){
 const data=await api('/api/status');
 $('job-state').textContent=labels[data.job.state]||data.job.state;
 $('job-time').textContent=data.job.started_at?new Date(data.job.started_at*1000).toLocaleString('zh-CN'):data.job.message;
 $('collect').disabled=data.job.state==='running';$('collect').textContent=data.job.state==='running'?'↻ 正在采集…':'↻ 开始采集';
 $('save-config').disabled=data.job.state==='running';
 $('log').textContent=data.log||'暂无采集记录';
 const list=$('reports');list.replaceChildren();
 $('report-time').textContent=data.reports.length?new Date(data.reports[0].updated_at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'暂无';
 data.reports.slice(0,8).forEach(report=>{
   const li=document.createElement('li'),a=document.createElement('a'),icon=document.createElement('span'),info=document.createElement('span'),name=document.createElement('b'),date=document.createElement('span'),arrow=document.createElement('span');
   a.href='/reports/'+report.path.split('/').map(encodeURIComponent).join('/');a.target='_blank';a.rel='noopener noreferrer';
   icon.className='report-icon';icon.textContent='≡';info.className='report-info';name.textContent=report.path;date.textContent=new Date(report.updated_at*1000).toLocaleString('zh-CN');arrow.textContent='↗';info.append(name,date);a.append(icon,info,arrow);li.append(a);list.append(li);
 });
 if(!data.reports.length){const li=document.createElement('li');li.className='empty';li.textContent='还没有报告。配置来源后，点击「开始采集」。';list.append(li);}
 // Counts are parsed from a separate safe summary instead of attempting to parse YAML in JS.
 const counts=await api('/api/summary');$('platform-count').textContent=counts.platforms;$('rss-count').textContent=counts.rss;
}
async function loadConfig(){try{const data=await api('/api/config/'+activeFile);revision=data.revision;$('config-content').value=data.content;$('config-title').textContent=activeFile;$('config-help').textContent=helps[activeFile];dirty=false;}catch(e){notice(e.message,true);}}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;$('login-error').textContent='';try{const data=Object.fromEntries(new FormData(e.target));const session=await api('/api/login',{method:'POST',body:JSON.stringify(data)});e.target.reset();await enter(session);}catch(error){$('login-error').textContent=error.message;}finally{button.disabled=false;}});
document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>selectPage(b.dataset.page)));
document.querySelectorAll('[data-config]').forEach(b=>b.addEventListener('click',()=>{if(dirty&&!confirm('当前配置尚未保存，确定切换吗？'))return;activeFile=b.dataset.config;document.querySelectorAll('.tab').forEach(t=>{t.classList.toggle('active',t===b);t.setAttribute('aria-selected',String(t===b));});loadConfig();}));
$('config-content').addEventListener('input',()=>dirty=true);
$('reload-config').addEventListener('click',()=>{if(!dirty||confirm('当前内容尚未保存，确定重新读取吗？'))loadConfig();});
$('save-config').addEventListener('click',async()=>{const button=$('save-config');button.disabled=true;try{const data=await api('/api/config/'+activeFile,{method:'PUT',body:JSON.stringify({content:$('config-content').value,revision})});revision=data.revision;dirty=false;notice('配置已保存。下一次采集将使用这份配置。');await refresh();}catch(e){notice(e.message,true);}finally{button.disabled=false;}});
$('collect').addEventListener('click',async()=>{if(dirty){notice('请先保存尚未提交的配置，再开始采集。',true);return;}$('collect').disabled=true;try{await api('/api/collect',{method:'POST',body:'{}'});notice('采集已启动，可在下方日志中查看进度。');await refresh();}catch(e){notice(e.message,true);$('collect').disabled=false;}});
$('refresh').addEventListener('click',()=>refresh().catch(e=>notice(e.message,true)));
$('logout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});showLogin();}catch(e){notice(e.message,true);}});
$('password-form').addEventListener('submit',async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));if(data.new_password!==data.confirmation){notice('两次输入的新密码不一致',true);return;}try{await api('/api/password',{method:'POST',body:JSON.stringify(data)});e.target.reset();showLogin();$('login-error').textContent='密码已更新，请重新登录。';}catch(error){notice(error.message,true);}});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
api('/api/session').then(enter).catch(showLogin);
