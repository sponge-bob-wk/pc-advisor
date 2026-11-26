/* ══════════════════════════════════════════════════════════════════
   sessions.js —— 会话管理（新对话 / 历史 / 切换 / 删除 / 重命名）

   ## 为什么单独一个文件

   app.js 已经有 900+ 行，再塞会话管理会更难维护。
   而且这块逻辑完全独立（只碰 localStorage 和左侧列表 DOM），
   不依赖 app.js 内部的 S 对象 —— 通过 window.WB 的接口交互。

   ## 存储结构

     localStorage['wb_sessions']      = [{id, title, createdAt, updatedAt, n}]
     localStorage['wb_conv_' + id]   = [{role, text, html, ts, extra}]

   ## 关键设计：会话里必须存「对话状态」

   只存消息文本是不够的 —— 第二轮说「换一台」时，后端需要知道
   上一轮推荐了什么（seen/slots/focus）。所以每条助手消息旁边
   还要存当时的 slots / asked / seen / focus。

   否则切回旧会话继续聊，系统会「失忆」，把看过的机器再推一遍。
   ══════════════════════════════════════════════════════════════════ */
(function(){
  'use strict';

  var IDX_KEY = 'wb_sessions';
  var CONV_PREFIX = 'wb_conv_';
  var MAX_SESSIONS = 50;   /* 超过就删最旧的，防止 localStorage 撑爆 */

  /* ── 存储读写（都带 try：localStorage 可能被隐私模式禁用）── */
  function readIdx(){
    try{
      var s = localStorage.getItem(IDX_KEY);
      if(!s) return [];
      var a = JSON.parse(s);
      return Array.isArray(a) ? a : [];
    }catch(e){ return [] }
  }

  function writeIdx(list){
    try{ localStorage.setItem(IDX_KEY, JSON.stringify(list)) }catch(e){}
  }

  function readConv(id){
    try{
      var s = localStorage.getItem(CONV_PREFIX + id);
      if(!s) return [];
      var a = JSON.parse(s);
      return Array.isArray(a) ? a : [];
    }catch(e){ return [] }
  }

  function writeConv(id, msgs){
    try{
      localStorage.setItem(CONV_PREFIX + id, JSON.stringify(msgs));
      return true;
    }catch(e){
      /* 容量满了：先清最旧的几个再试一次 */
      var idx = readIdx();
      for(var i = idx.length - 1; i >= 0 && i > idx.length - 6; i--){
        try{ localStorage.removeItem(CONV_PREFIX + idx[i].id) }catch(e2){}
      }
      try{
        localStorage.setItem(CONV_PREFIX + id, JSON.stringify(msgs));
        return true;
      }catch(e3){ return false }
    }
  }

  function newId(){
    return 'c' + Date.now().toString(36) +
           Math.random().toString(36).slice(2, 6);
  }

  /* ── 标题：取第一条用户消息的前 18 个字 ── */
  function makeTitle(text){
    var t = (text || '').replace(/\s+/g, ' ').trim();
    if(!t) return '新对话';
    return t.length > 18 ? t.slice(0, 18) + '…' : t;
  }

  /* ── 当前会话 id ── */
  var curId = null;

  function setCur(id){
    curId = id || null;
    try{
      if(curId) localStorage.setItem('wb_cur', curId);
      else localStorage.removeItem('wb_cur');
    }catch(e){}
  }

  function getCur(){
    /* 🔴 2026-11-26 修：原来 if(curId) return curId 在 curId='' 时
       会跳过判断走到 localStorage，但读回来还是 ''，
       导致「新建对话后 getCur 返回 ''」而不是 null，
       上层 if(!sid) create() 判断失效 → 消息被写进旧会话。
       这里显式把空串归一成 null。 */
    if(curId) return curId;
    try{
      var v = localStorage.getItem('wb_cur');
      curId = (v && v.length) ? v : null;
    }catch(e){ curId = null }
    return curId;
  }

  /* ── 创建 / 删除 / 重命名 ── */

  function create(firstText){
    var id = newId();
    var now = Date.now();
    var idx = readIdx();
    idx.unshift({
      id: id,
      title: makeTitle(firstText),
      createdAt: now,
      updatedAt: now,
      n: 0
    });
    /* 超量裁剪 */
    while(idx.length > MAX_SESSIONS){
      var drop = idx.pop();
      try{ localStorage.removeItem(CONV_PREFIX + drop.id) }catch(e){}
    }
    writeIdx(idx);
    writeConv(id, []);
    setCur(id);
    return id;
  }

  function remove(id){
    var idx = readIdx().filter(function(x){ return x.id !== id });
    writeIdx(idx);
    try{ localStorage.removeItem(CONV_PREFIX + id) }catch(e){}
    if(getCur() === id){
      setCur(idx.length ? idx[0].id : null);
    }
  }

  function rename(id, title){
    var idx = readIdx();
    for(var i = 0; i < idx.length; i++){
      if(idx[i].id === id){
        idx[i].title = (title || '').trim().slice(0, 40) || idx[i].title;
        idx[i].updatedAt = Date.now();
        break;
      }
    }
    writeIdx(idx);
  }

  /* ── 追加一条消息 ── */
  function append(id, msg){
    var conv = readConv(id);
    conv.push(msg);
    var ok = writeConv(id, conv);

    /* 更新索引里的计数和时间 */
    var idx = readIdx();
    for(var i = 0; i < idx.length; i++){
      if(idx[i].id === id){
        idx[i].n = conv.length;
        idx[i].updatedAt = Date.now();
        /* 第一条用户消息决定标题 */
        if(msg.role === 'user' && idx[i].title === '新对话'){
          idx[i].title = makeTitle(msg.text);
        }
        break;
      }
    }
    writeIdx(idx);
    return ok;
  }

  /* ── 列表渲染 ── */
  function render(){
    var box = document.querySelector('#sessList');
    if(!box) return;
    var idx = readIdx();
    if(!idx.length){
      box.innerHTML = '<div class="sess-empty">还没有对话记录</div>';
      return;
    }
    var h = '';
    var cur = getCur();
    idx.forEach(function(s){
      var active = (s.id === cur) ? ' active' : '';
      h += '<div class="sess-item' + active + '" data-id="' + s.id + '">'
         +   '<span class="sess-t" title="' + esc(s.title) + '">'
         +     esc(s.title)
         +   '</span>'
         +   '<button class="sess-x" data-del="' + s.id + '" title="删除">✕</button>'
         + '</div>';
    });
    box.innerHTML = h;

    /* 点击切换 */
    box.querySelectorAll('.sess-item').forEach(function(el){
      el.onclick = function(ev){
        if(ev.target.classList.contains('sess-x')) return;
        var id = el.dataset.id;
        if(typeof window.WB.loadSession === 'function'){
          window.WB.loadSession(id);
        }
      };
    });

    /* 删除 */
    box.querySelectorAll('.sess-x').forEach(function(b){
      b.onclick = function(ev){
        ev.stopPropagation();
        var id = b.dataset.del;
        if(confirm('删除这个对话？删除后不能恢复。')){
          remove(id);
          render();
          /* 删的是当前会话 → 清空对话区 */
          if(getCur() !== id && !readIdx().length){
            if(typeof window.WB.newSession === 'function'){
              window.WB.newSession();
            }
          }
        }
      };
    });
  }

  function esc(s){
    return String(s == null ? '' : s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  /* ── 对外接口 ── */
  window.Sessions = {
    newId: newId,
    create: create,
    remove: remove,
    rename: rename,
    append: append,
    render: render,
    readConv: readConv,
    writeConv: writeConv,
    readIdx: readIdx,
    getCur: getCur,
    setCur: setCur,
    makeTitle: makeTitle
  };

  /* 启动时渲染一次 */
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', render);
  }else{
    render();
  }
})();
