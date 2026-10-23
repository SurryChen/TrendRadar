/* Server adapter for upstream docs/index.html. No copy of the editor UI. */
(() => {
    'use strict';
    const files = [
        {tab:'config', name:'config.yaml', id:'yaml-editor', read:()=>currentYaml, write:v=>currentYaml=v},
        {tab:'frequency', name:'frequency_words.txt', id:'frequency-editor', read:()=>currentFrequency, write:v=>{currentFrequency=v;currentFrequencyData=null;}},
        {tab:'timeline', name:'timeline.yaml', id:'timeline-editor', read:()=>currentTimeline, write:v=>currentTimeline=v},
    ];
    const $ = id => document.getElementById(id);
    let csrf='', ready=false, saving=false, running=false, interest=null, timer;
    const dirty = () => ready && (files.some(f=>f.read()!==f.content) || (interest && $('server-interest-text')?.value!==interest.content));
    const message = (text, error=false) => { $('server-message').textContent=text; $('server-message').dataset.error=error; };
    async function api(path, options={}) {
        const response=await fetch(path, {...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},credentials:'same-origin'});
        if(response.status===401){ready=false;clearInterval(timer);location.replace('/login');throw Error('登录已失效');}
        const data=await response.json();
        if(!response.ok)throw Error(data.error || '请求失败');
        return data;
    }
    function badges() {
        if(!ready)return;
        files.forEach(f=>{
            const label=$(f.tab+'-save-label'), time=$(f.tab+'-save-time');
            label.removeAttribute('data-i18n');time.removeAttribute('data-i18n');
            label.classList.add('hidden');
            time.textContent=f.read()===f.content?'已保存到服务器':'有修改 · 未保存到服务器';
            time.title=time.textContent;
        });
    }
    // All upstream form mutations still call these hooks. Saving remains explicit.
    _saveToStorage = () => badges();
    updateSaveTimeDisplay = badges;
    const button = (id,text,fn) => {const b=document.createElement('button');b.id=id;b.textContent=text;b.type='button';b.addEventListener('click',()=>Promise.resolve(fn()).catch(e=>message(e.message,true)));return b;};
    function dialog(id,title) {
        const d=document.createElement('dialog');d.id=id;d.className='server-dialog';
        const close=button(id+'-close','关闭',()=>d.close());close.className='server-close';
        const h=document.createElement('h2');h.textContent=title;d.append(close,h);document.body.append(d);return d;
    }
    async function save() {
        if(!ready||saving||running)return;
        saving=true;$('server-save').disabled=true;
        try {
            let count=0;
            const changes=files.filter(f=>f.read()!==f.content);
            for(const f of changes){
                const content=f.read();
                const result=await api('/api/config/'+f.name,{method:'PUT',body:JSON.stringify({content,revision:f.revision})});
                f.content=result.content;f.revision=result.revision;count++;
            }
            if(interest && $('server-interest-text').value!==interest.content){
                interest=await api('/api/config/ai_interests.txt',{method:'PUT',body:JSON.stringify({content:$('server-interest-text').value,revision:interest.revision})});count++;
            }
            message(count?`已保存 ${count} 份配置，下一次采集生效。`:'配置已与服务器同步。');
        } catch(e){message(e.message+'；未保存的修改仍保留在当前页面。',true);}
        finally{saving=false;$('server-save').disabled=running;badges();}
    }
    async function refresh() {
        const data=await api('/api/status');
        running=data.job.state==='running';
        $('server-collect').disabled=running||saving;
        $('server-save').disabled=running||saving;
        $('server-collect').textContent=running?'正在采集…':'手动采集';
        const states={idle:'尚未采集',running:'采集中',succeeded:'采集完成',failed:'采集失败',interrupted:'采集已中断'};
        $('server-job').textContent=states[data.job.state] || data.job.state;
        $('server-log').textContent=data.log||'暂无日志';
        const list=$('server-reports');list.replaceChildren();
        data.reports.forEach(r=>{const li=document.createElement('li'),a=document.createElement('a');a.textContent=r.path+' · '+new Date(r.updated_at*1000).toLocaleString();a.href='/reports/'+r.path.split('/').map(encodeURIComponent).join('/');a.target='_blank';a.rel='noopener noreferrer';li.append(a);list.append(li);});
        if(!data.reports.length)list.textContent='暂无报告，先配置来源再手动采集。';
    }
    document.addEventListener('DOMContentLoaded',async()=>{
        const toolbar=document.createElement('div');toolbar.id='server-toolbar';
        const msg=document.createElement('span');msg.id='server-message';msg.textContent='正在读取服务器配置…';
        toolbar.append(msg,button('server-save','保存到服务器',save),button('server-collect','手动采集',async()=>{
            if(dirty()){message('请先保存修改，再开始采集。',true);return;}
            $('server-collect').disabled=true;
            try{await api('/api/collect',{method:'POST',body:'{}'});message('采集已启动，点击「报告与日志」查看进度。');}
            finally{await refresh();}
        }),button('server-results','报告与日志',async()=>{await refresh();$('server-results-dialog').showModal();}),button('server-interest','AI 关注方向',async()=>{
            if(!interest){interest=await api('/api/config/ai_interests.txt');$('server-interest-text').value=interest.content;}
            $('server-interest-dialog').showModal();
        }),button('server-account','账号',()=>$('server-account-dialog').showModal()),button('server-logout','退出',async()=>{
            if(dirty()&&!confirm('有未保存的配置，确定退出？'))return;
            await api('/api/logout',{method:'POST',body:'{}'});ready=false;location.replace('/login');
        }));
        document.querySelector('nav').after(toolbar);
        $('server-save').disabled=true;$('server-collect').disabled=true;
        const privacy=document.querySelector('[data-i18n="nav.privacy"]');privacy.removeAttribute('data-i18n');privacy.textContent='登录后编辑，配置保存到本机服务器';
        const results=dialog('server-results-dialog','采集报告与日志');
        const job=document.createElement('p');job.id='server-job';const list=document.createElement('ul');list.id='server-reports';const log=document.createElement('pre');log.id='server-log';results.append(job,list,log);
        const interests=dialog('server-interest-dialog','AI 关注方向');
        const label=document.createElement('label');label.textContent='描述希望 AI 关注的内容（启用 AI 筛选后生效）';
        const text=document.createElement('textarea');text.id='server-interest-text';label.append(text);interests.append(label,button('server-interest-save','保存到服务器',save));
        const account=dialog('server-account-dialog','账号设置');
        account.insertAdjacentHTML('beforeend','<p id="server-username"></p><form id="server-password-form"><label>当前密码<input type="password" name="old_password" autocomplete="current-password" required maxlength="256"></label><label>新密码（至少 12 字符）<input type="password" name="new_password" autocomplete="new-password" minlength="12" maxlength="256" required></label><label>再次输入新密码<input type="password" name="confirmation" autocomplete="new-password" minlength="12" maxlength="256" required></label><p>修改密码将退出所有设备。</p><button type="submit">更新密码并重新登录</button><p role="alert"></p></form>');
        $('server-password-form').addEventListener('submit',async e=>{
            e.preventDefault();const data=Object.fromEntries(new FormData(e.target));const error=e.target.querySelector('[role=alert]');
            if(data.new_password!==data.confirmation){error.textContent='两次输入的新密码不一致';return;}
            if(dirty()){error.textContent='请先保存配置，再修改密码';return;}
            try{await api('/api/password',{method:'POST',body:JSON.stringify(data)});ready=false;e.target.reset();location.replace('/login');}catch(e){error.textContent=e.message;}
        });
        try {
            const session=await api('/api/session');csrf=session.csrf;$('server-username').textContent='当前账号：'+session.username;
            const configs=await Promise.all(files.map(f=>api('/api/config/'+f.name)));
            configs.forEach((data,i)=>{const f=files[i];Object.assign(f,data);f.write(data.content);$(f.id).value=data.content;updateBackdrop(f.id,f.id.replace('editor','backdrop'));});
            syncYamlToUI();syncFrequencyToUI();syncTimelineToUI();
            ready=true;document.body.removeAttribute('data-server-loading');badges();
            message('已读取服务器配置。修改后点击「保存到服务器」。');
            await refresh();timer=setInterval(()=>{badges();refresh().catch(e=>message(e.message,true));},4000);
        }catch(e){message('无法载入配置：'+e.message+'。请刷新重试。',true);}
    });
    document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();e.stopImmediatePropagation();save();}},true);
    window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue='';}});
})();
