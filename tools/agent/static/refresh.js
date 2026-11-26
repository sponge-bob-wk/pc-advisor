/* ── 刷新数据（2026-11-16）─────────────────────────────────────────
   流程：点「↻ 刷新数据」→ 选粒度 → POST /api/refresh 拿 jobId
        → 每 1.5s 轮询 /api/refresh/status → 画进度条 + 步骤结果 + 交叉验证

   为什么是"触发+轮询"而不是同步等待：
      全量抓取要 3-5 分钟，HTTP 请求会超时；而且刷新时用户还可能在对话，
      不能把界面卡住。                                    */
(function(){
  "use strict";

  // 🔴 2026-11-21 修：原版用了 $() 和 wbHeaders()，
  //    但那两个变量定义在 app.js 的 IIFE 内部，作用域不外泄 ——
  //    刷新面板一执行就 ReferenceError，功能其实一直是坏的。
  //    现在从 window.WB 取，并自带 $ 工具。
  var $ = function(s, r){ return (r || document).querySelector(s) };
  var WB = window.WB || {};
  var wbHeaders = WB.wbHeaders || function(h){ return h || {} };
  var apiCall = WB.apiCall || function(u, o){
    return fetch(u, o).then(function(r){ return r.json() })
  };

  var btn=$('#rfBtn'), panel=$('#rfPanel'), close=$('#rfClose');
  var choices=$('#rfChoices'), prog=$('#rfProg'), bar=$('#rfBar');
  var cur=$('#rfCur'), steps=$('#rfSteps'), cross=$('#rfCross'), msg=$('#rfMsg');
  if(!btn||!panel) return;

  /* 🔴 2026-11-26 重构（用户要求）：
     「全量刷新，分笔记本和台式机吧，各取所需」

     笔记本和台式机是两套独立数据：
       笔记本 → machine_pool.json（243 台机型）
       台式机 → parts.json（459 个配件）
     抓取源、耗时都不同，混在一起刷很浪费。

     按数据域分组，每组一个标题 + 若干按钮。 */
  var PRESET_GROUPS = [
    { title:'笔记本', hint:'机型价格 · 跑分 · 判定统计', items:[
      ['laptop','全量刷新笔记本','价格+跑分+重算，约 8 分钟'],
      ['prices','只刷价格','去 ZOL 抓最新机型价格，约 2 分钟'],
      ['bench','只刷跑分','去 Notebookcheck 抓 CPU 跑分，约 2 分钟'],
      ['quick','快速重算','不联网，只重算判定/统计/页面，约 2 秒']
    ]},
    { title:'台式机', hint:'装机配件价格 · 物理尺寸', items:[
      ['desktop','全量刷新台式机','配件价格+尺寸，实测约 4.5 分钟'],
      ['parts','只刷配件价格','9 类配件，约 90 秒'],
      ['partsAll','价格+尺寸','顺带抓显卡长度/机箱限长，约 4.5 分钟']
    ]},
    { title:'全部', hint:'两块数据一起刷', items:[
      ['all','全部刷新','笔记本 + 台式机，约 12 分钟']
    ]}
  ];

  /* 老预设名兼容：后端可能还有 full，映射到 all */
  var PRESET_ALIAS = { full:'all' };
  var timer=null, jobId=null;

  function openPanel(){
    panel.hidden=false;
    if(!choices.dataset.ready){ loadOptions(); }
  }
  function closePanel(){ panel.hidden=true; }

  function loadOptions(){
    fetch('/api/refresh/options',{cache:'no-store',headers:wbHeaders()})
      .then(function(r){return r.json()})
      .then(function(d){
        if(!d.ok) throw new Error(d.err||'加载失败');
        choices.innerHTML='';
        /* 🔴 2026-11-26：按「笔记本 / 台式机 / 全部」分组渲染。
       只渲染后端真实存在的预设 —— 防止前端有、后端没有时点了 404。
       （实测踩过：前端写死 4 个标签，后端加了新预设却看不到。） */
        var _have={};
        (d.presets||[]).forEach(function(p){ _have[p.id]=p });

        PRESET_GROUPS.forEach(function(g){
          // 组内先过滤出后端真实存在的
          var items=g.items.filter(function(it){
            return _have[it[0]] || _have[PRESET_ALIAS[it[0]]];
          });
          if(!items.length) return;

          var box=document.createElement('div');
          box.className='rf-group';
          var hd=document.createElement('div');
          hd.className='rf-group-hd';
          hd.innerHTML='<b>'+g.title+'</b><span>'+(g.hint||'')+'</span>';
          box.appendChild(hd);

          items.forEach(function(it){
            var pid=it[0], title=it[1], note=it[2];
            var b=document.createElement('button');
            b.className='rf-opt';
            b.innerHTML='<b>'+title+'</b><span>'+note+'</span>';
            b.onclick=function(){ run(pid); };
            box.appendChild(b);
          });
          choices.appendChild(box);
        });
        choices.dataset.ready='1';
      })
      .catch(function(e){ choices.innerHTML='<div class="rf-cross"><div class="error">'+e.message+'</div></div>'; });
  }

  function run(preset){
    choices.querySelectorAll('.rf-opt').forEach(function(b){ b.disabled=true; });
    prog.hidden=false; bar.style.width='0'; cur.textContent='正在启动…';
    steps.innerHTML=''; cross.innerHTML=''; msg.textContent='';
    fetch('/api/refresh',{
      method:'POST',cache:'no-store',
      headers:wbHeaders({'Content-Type':'application/json'}),
      body:JSON.stringify({preset:preset})
    }).then(function(r){return r.json()}).then(function(d){
      if(!d.ok){ cur.textContent='启动失败：'+(d.err||''); choices.querySelectorAll('.rf-opt').forEach(function(b){b.disabled=false}); return; }
      jobId=d.jobId; btn.disabled=true;
      timer=setInterval(poll,1500); poll();
    }).catch(function(e){
      cur.textContent='启动失败：'+e.message;
      choices.querySelectorAll('.rf-opt').forEach(function(b){ b.disabled=false; });
    });
  }

  function poll(){
    if(!jobId) return;
    fetch('/api/refresh/status?job='+jobId,{cache:'no-store',headers:wbHeaders()})
      .then(function(r){return r.json()}).then(function(d){
        if(!d.ok) return;
        var j=d.job||{};
        bar.style.width=(j.progress||0)+'%';
        cur.textContent = j.status==='running'
          ? ('进行中：'+(j.current||'…')+'（'+(j.progress||0)+'%）')
          : (j.status==='done'?'完成':'出错：'+(j.err||''));
        // 步骤结果
        var h='';
        (j.order||[]).forEach(function(n){
          var v=(j.steps||{})[n]||{}, st=v.status||'pending';
          var cls={ok:'ok',fail:'fail',skipped:'skipped'}[st]||'';
          var mark={ok:'✓',fail:'✗',skipped:'–'}[st]||'·';
          h+='<div class="rf-st"><span>'+mark+' '+n+'</span><span class="'+cls+'">'+
             (st==='ok'?(v.ms||0)+'ms':(v.err||st))+'</span></div>';
        });
        steps.innerHTML=h;
        // 交叉验证
        if(j.cross&&j.cross.items){
          cross.innerHTML=j.cross.items.map(function(it){
            var cls={warn:'warn',error:'error'}[it.level]||'';
            return '<div class="'+cls+'">['+it.level+'] '+it.text+'</div>';
          }).join('');
        }
        if(j.status!=='running'){
          clearInterval(timer); timer=null; btn.disabled=false;
          choices.querySelectorAll('.rf-opt').forEach(function(b){ b.disabled=false; });
          msg.textContent = j.status==='done'?'已更新':'失败';
          // 刷新完重拉一次 overview，让顶栏「数据快照 / 库内 N 台」跟着变。
          // 🔴 函数名是 `facts(ov)` 不是 `loadOverview()` —— 首版写错了，
          //    结果顶栏数字永远停在刷新前（而且 typeof 判断让它静默失败）。
          if(j.status==='done'){
            jget('/api/overview').then(function(ov){
              try{ facts(ov); }catch(e){}
              try{ rail(ov); }catch(e){}
            }).catch(function(){});
          }
          setTimeout(function(){ msg.textContent=''; },4000);
        }
      });
  }

  btn.onclick=openPanel;
  close.onclick=closePanel;
  panel.addEventListener('click',function(e){ if(e.target===panel) closePanel(); });
})();
