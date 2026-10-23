'use strict';
const form=document.getElementById('login-form');
form.addEventListener('submit',async e=>{
    e.preventDefault();const button=form.querySelector('button'),error=document.getElementById('login-error');button.disabled=true;error.textContent='';
    try{
        const response=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(Object.fromEntries(new FormData(form)))});
        const data=await response.json();if(!response.ok)throw Error(data.error||'登录失败');
        form.reset();location.replace('/');
    }catch(e){error.textContent=e.message;}finally{button.disabled=false;}
});
