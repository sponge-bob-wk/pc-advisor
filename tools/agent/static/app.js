(function(){
"use strict";
var $=function(s,r){return (r||document).querySelector(s)};
var $$=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))};
var stream=$('#stream'), inp=$('#inp'), sendBtn=$('#send');

/* ═══════════════════════════════════════════════════════════════
   状态管理（手册第 5 节）
   ─────────────────────────────────────────────────────────────
   规则：
   1. 所有 UI 状态都在 S 里，不在 DOM 上存业务数据
   2. S 只存数据，不存 DOM 引用
   3. 改 S 后调用对应渲染函数，不指望自动更新

   为什么不用 dataset 存状态：
   状态分散在 DOM 里，要读状态必须查 DOM，逻辑和视图耦合。
   ═══════════════════════════════════════════════════════════════ */
var S = {
  // 对话状态（发给后端的）
  slots: {},
  asked: [],
  lastStage: null,
  seen: [],
  focus: null,
  // UI 状态
  busy: false,
  online: false,
  ov: null,            // 数据概览缓存
  refreshJob: null,    // 刷新任务 id
  refreshTimer: null,  // 刷新轮询句柄
};

/* 兼容旧代码的访问器（2026-11-21）
   ─────────────────────────────────────────────────────────────
   为什么用 defineProperty 而不是 `var slots = S.slots`：
   后者是**值拷贝**，`slots = xxx` 只改变量，不改变 S.slots ——
   状态会出现两份，渲染读的和逻辑写的不是同一个。

   访问器让读写都落到 S 上，旧代码一行不用改。
   ───────────────────────────────────────────────────────────── */
function _bridge(name, key){
  Object.defineProperty(window, name, {
    get: function(){ return S[key] },
    set: function(v){ S[key] = v },
    configurable: true
  });
}
_bridge('slots', 'slots');
_bridge('asked', 'asked');
_bridge('seen', 'seen');
_bridge('focus', 'focus');
_bridge('busy', 'busy');
_bridge('lastStage', 'lastStage');
_bridge('OV', 'ov');
_bridge('on', 'online');

// ⚠️ 不要在这里 `var slots, asked, ...` ——
//    var 会声明一个局部变量（undefined），**遮蔽**上面 defineProperty
//    定义的 window 访问器，于是读到 undefined。
//    直接靠 window 属性访问即可（IIFE 内未声明的标识符会向上找 window）。

/* ── 工具 ── */
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function money(n){
  if(n==null||isNaN(n)) return '—';
  var r=Math.round(n*100)/100, s=r.toFixed(2).replace(/\.00$/,'').replace(/(\.\d)0$/,'$1');
  return s.replace(/\B(?=(\d{3})+(?!\d))/g,',');
}
/* ── 身份与访问码（2026-11-13，为了"别人连我这台"） ──
   组内同学和测试同学都连同一台服务，日志里得能分出"这是谁的一轮"，
   否则收上来的样本没法按人排查、也没法整批回滚某个人的数据。
   uid 只是个随机串，不是账号，不收集任何个人信息；?u=昵称 可以自己改。
   访问码 ?k=xxx 由部署方（我）发给大家，存本地，之后每个请求自动带上。 */
var WB_UID=(function(){
  var u='';
  try{ u=(new URLSearchParams(location.search)).get('u')||'' }catch(e){}
  if(!u){ try{ u=localStorage.getItem('wb_uid')||'' }catch(e){} }
  if(!u){ u='u'+Math.random().toString(36).slice(2,8) }
  try{ localStorage.setItem('wb_uid',u.replace(/[^0-9A-Za-z_-]/g,'').slice(0,32)) }catch(e){}
  return u.replace(/[^0-9A-Za-z_-]/g,'').slice(0,32)||'anon';
})();
var WB_KEY=(function(){
  var k='';
  try{ k=(new URLSearchParams(location.search)).get('k')||'' }catch(e){}
  if(!k){ try{ k=localStorage.getItem('wb_key')||'' }catch(e){} }
  if(k){ try{ localStorage.setItem('wb_key',k) }catch(e){} }
  return k;
})();
function wbHeaders(extra){
  var h=extra||{};
  h['X-Client-Id']=WB_UID;
  if(WB_KEY) h['X-Access-Code']=WB_KEY;
  return h;
}
/* ═══════════════════════════════════════════════════════════════
   统一 API 调用（手册第 9 节）
   ─────────────────────────────────────────────────────────────
   为什么必须封装：后端挂了会返回 HTML 错误页，
   直接 r.json() 会抛 "Unexpected token '<'" —— 用户完全看不懂。

   三层检查：
   1. HTTP 状态码
   2. Content-Type 必须是 JSON
   3. 解析失败给可读错误
   ═══════════════════════════════════════════════════════════════ */
function apiCall(url, options){
  var opt = options || {};
  opt.cache = opt.cache || 'no-store';
  opt.headers = wbHeaders(opt.headers || {});

  return fetch(url, opt).then(function(r){
    // 401 单独提示（最常见）
    if(r.status === 401){
      throw new Error('需要访问码：用带 ?k=访问码 的链接打开（找发链接的人要）');
    }
    if(!r.ok){
      throw new Error('服务返回 ' + r.status + '，请稍后重试');
    }
    var ct = r.headers.get('content-type') || '';
    if(ct.indexOf('application/json') < 0){
      throw new Error('服务异常（返回了非 JSON 内容），请检查后端是否运行');
    }
    return r.json().catch(function(){
      throw new Error('响应格式异常');
    });
  });
}

// 兼容旧名
function jget(u){ return apiCall(u); }
function el(tag,cls,html){var d=document.createElement(tag);if(cls)d.className=cls;if(html!=null)d.innerHTML=html;return d}
function toBottom(){stream.scrollTop=stream.scrollHeight}

/* ═══════════════════════════════════════════════════════════════
   Toast 提示（手册第 10 节）
   ─────────────────────────────────────────────────────────────
   为什么不用 alert：阻塞、丑、无法自动消失。
   为什么必须 clearTimeout：连续两条提示，第一条的计时器
   会提前关掉第二条。
   ═══════════════════════════════════════════════════════════════ */
var _toastTimer = null;
function toast(msg){
  var el = document.getElementById('toast');
  if(!el) return;
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(function(){ el.classList.remove('show') }, 2200);
}


/* ── 顶栏状态 ── */
function health(){
  jget('/api/health').then(function(h){
    var dot=$('#dot'), tx=$('#stx');
    if(h.ok){ dot.className='dot on'; tx.textContent='模型就绪'; on=true; }
    else { dot.className='dot off'; tx.textContent='模型离线 · 按规则答'; on=false; offlineNote(); }
  }).catch(function(){
    $('#dot').className='dot off'; $('#stx').textContent='服务未响应'; on=false; offlineNote();
  });
}
function offlineNote(){
  $('#offlineSlot').innerHTML='<div class="offline">Ollama 没起来，现在只能按规则答：结论仍然来自库内数据和同一套排序规则，只是话会短一些，追问细节会答不了。</div>';
}

/* ── 事实数字（全部来自 /api/overview） ── */
function facts(ov){
  // 🔴 2026-11-12：首屏可能已经被第一条消息移除（?q= 直接开聊，或用户手快）。
  //   原来这里直接 .textContent=，元素不在就抛 TypeError，
  //   外层 .catch 会把它误报成「接口 /api/overview 没响应」——  接口其实好好的。
  function set(id,v){ var n=$('#'+id); if(n) n.textContent=v }
  set('fTotal',ov.total); set('fTotal2',ov.total);
  // 2026-11-16：不再展示「有人写过优缺点」，改成**真实数据覆盖**。
  // 理由：用户要求不要暴露"人工/没人评过"这种内部区分，
  // 展示"数据覆盖到哪"才对用户有意义。
  var wp=(ov.perf||{}).withPerf, ww=(ov.weight||{}).withWeight;
  set('fPerf', wp!=null?wp:'—'); set('fPerf2', wp!=null?wp:'—');
  set('fWeight', ww!=null?ww:'—'); set('fWeight2', ww!=null?ww:'—');
  set('fCat',ov.byCat.length+' 类');
  set('asof','数据快照 '+ov.dataAsOf);
  set('pool','库内 '+ov.total+' 台');
  set('hintRight','数据截至 '+ov.dataAsOf+' · 只做 Windows 笔记本 / 台式整机 / 内存固态');
}

/* ── 右栏图表：每个数都直接取 /api/overview，不在前端做统计 ── */
function rail(ov){
  var h='';

  /* 1. 数据覆盖（2026-11-16 改：原来展示"有人写过优缺点/仅参数"，
        用户要求不要暴露这个内部区分，改成展示**字段覆盖**——
        跑分、重量、价格这些对用户才有意义）*/
  var wp=(ov.perf||{}).withPerf||0, np=(ov.perf||{}).noPerf||0;
  var ww=(ov.weight||{}).withWeight||0, nw=(ov.weight||{}).noWeight||0;
  var tot=ov.total;
  h+='<div class="ov"><h3>数据覆盖 <span>'+tot+' 台</span></h3>'
   + '<div class="sbar"><i class="rv" style="width:'+(wp/tot*100).toFixed(1)+'%"></i>'
   + '<i class="nr" style="width:'+(np/tot*100).toFixed(1)+'%"></i></div>'
   + '<div class="lg"><span><i class="rv"></i>有跑分 '+wp+'</span>'
   + '<span><i class="nr"></i>无跑分 '+np+'</span></div>'
   + '<div class="cap" style="margin-top:9px">跑分来自 Cinebench R23 实测，'
   + '<b>'+wp+' 台</b>可比快慢；其余按价格排序。</div>'
   + '<div class="cap" style="margin-top:6px">实测重量 <b>'+ww+' 台</b>'
   + (ov.weight&&ov.weight.min!=null?'（'+ov.weight.min+'~'+ov.weight.max+' kg）':'')
   + '。</div></div>';

  /* 2. 价位段 */
  var maxN=0;
  ov.priceBuckets.forEach(function(b){ if(b.n>maxN) maxN=b.n });
  h+='<div class="ov"><h3>价位段分布 <span>点一档填入预算</span></h3>'
   + '<div class="cap">柱长按台数，点一档即填入预算</div>';
  ov.priceBuckets.forEach(function(b){
    // 2026-11-16：原来柱子里叠一条「已核对占比」(b.reviewed)，那是"有人写过优缺点"的
    // 内部区分，用户要求不展示。改成纯台数柱。
    h+='<button class="bk" data-bucket="'+esc(b.label)+'">'
     + '<span class="r1"><span>'+esc(b.label)+'</span><em>'+b.n+' 台</em></span>'
     + '<span class="tr" style="width:'+(b.n/maxN*100).toFixed(1)+'%">'
     + '<i class="rv" style="width:100%"></i></span></button>';
  });
  h+='</div>';

  /* 3. 品类价格区间 */
  var gMin=1e9,gMax=0;
  ov.byCat.forEach(function(c){ if(c.priceMin<gMin) gMin=c.priceMin; if(c.priceMax>gMax) gMax=c.priceMax });
  var span=gMax-gMin||1;
  h+='<div class="ov"><h3>品类价格区间 <span>min–median–max</span></h3>'
   + '<div class="cap">竖线是中位价，不是我们算的均价</div>';
  ov.byCat.forEach(function(c){
    var l=(c.priceMin-gMin)/span*100, w=(c.priceMax-c.priceMin)/span*100;
    var med=(c.priceMedian-gMin)/span*100;
    h+='<div class="rng-row"><div class="r1"><span>'+esc(c.name)+' <em>'+c.n+' 台</em></span>'
     + '<em>¥'+money(c.priceMin)+' – ¥'+money(c.priceMax)+'</em></div>'
     + '<div class="rng"><i class="'+esc(c.cat)+(c.cat==='upgrade'?' parts':'')+'" style="left:'+l.toFixed(1)+'%;width:'+Math.max(w,1).toFixed(1)+'%"></i>'
     + '<b style="left:'+med.toFixed(1)+'%"></b></div></div>';
  });
  h+='<div class="rng-note">内存固态（'+ (ov.byCat.filter(function(c){return c.cat==='upgrade'})[0]||{}).n +' 条）不是整机，价格不能和笔记本横向比。</div></div>';

  /* 4. 可加装分布 */
  var maxU=0;
  ov.upgrade.forEach(function(u){ if(u.n>maxU) maxU=u.n });
  h+='<div class="ov"><h3>可加装分布 <span>0–3 档</span></h3>'
   + '<div class="cap">0 分 = 内存焊死，四年后没有后路</div><div class="up">';
  ov.upgrade.forEach(function(u){
    h+='<div><em>'+u.n+'</em><i class="'+(u.score===0?'z':'')+'" style="height:'+Math.max(u.n/maxU*46,2).toFixed(0)+'px"></i></div>';
  });
  h+='</div><div class="up-x">'+ov.upgrade.map(function(u){return '<span>'+u.score+' 档</span>'}).join('')+'</div></div>';

  /* 5. 覆盖度 */
  h+='<div class="ov"><h3>数据覆盖 <span>缺的就是缺的</span></h3>';
  h+='<div class="sbar" style="margin-top:6px">'
   + '<i class="rv" style="width:'+(ov.perf.withPerf/tot*100).toFixed(1)+'%"></i>'
   + '<i class="unk" style="width:'+(ov.perf.noPerf/tot*100).toFixed(1)+'%"></i></div>'
   + '<div class="lg"><span><i class="rv"></i>有跑分 '+ov.perf.withPerf+'</span>'
   + '<span><i class="unk"></i>无跑分 '+ov.perf.noPerf+'（图上标未知）</span></div>';
  h+='<div class="sbar" style="margin-top:9px">'
   + '<i class="rv" style="width:'+(ov.weight.withWeight/tot*100).toFixed(1)+'%"></i>'
   + '<i class="unk" style="width:'+(ov.weight.noWeight/tot*100).toFixed(1)+'%"></i></div>'
   + '<div class="lg"><span><i class="rv"></i>有重量 '+ov.weight.withWeight+'</span>'
   + '<span><i class="unk"></i>没记重量 '+ov.weight.noWeight+'</span></div>'
   + '<div class="cap" style="margin-top:9px">'+esc(ov.weight.note||'')+'</div>';

  /* 6. 价格冲突 */
  var pc=ov.priceConflicts||[];
  if(pc.length){
    // 2026-11-16：措辞从「人工核定 / 采集池」改成「报价 A / 报价 B」——
    // 前者暴露了数据来源分层，用户要求不展示这类内部区分。
    h+='<details class="conf"><summary><span>两处报价口径不一致 '+pc.length+' 处</span><em>未裁决</em></summary>';
    pc.forEach(function(c){
      h+='<div class="li"><b>'+esc(c.name)+'</b>'
       + '<span>报价 A ¥'+money(c.reviewPrice)+' · 报价 B ¥'+money(c.poolPrice)+'</span>'
       + '<u>　采用：'+esc(c.kept)+'</u></div>';
    });
    h+='</details>';
  }
  h+='</div>';

  h+='<div class="rail-foot">图上每个数字都直接来自接口；缺数据的地方画成斜纹「未知」，不当 0 处理。数据截至 '+ov.dataAsOf+'。</div>';

  // 右栏结构（2026-11-13）：上面是「当前这台」（跟着对话走），下面才是全库统计。
  // 全库统计裹进 <details>，开聊后自动折起来一次 —— 用户要看的是"我在问的这台"，
  // 不是每轮都盯着库构成。
  $('#railBody').innerHTML='<div id="railNow" class="nowbox"></div>'
    + '<details class="rail-global" id="railGlobal" open>'
    + '<summary><span class="t">全库统计</span><span class="n">'+ov.total+' 台</span></summary>'
    + '<div class="rg-body">'+h+'</div></details>';
  paintNow();
  $$('#railBody .bk').forEach(function(b){
    b.onclick=function(){
      inp.value='预算 '+b.dataset.bucket;
      inp.focus(); inp.setSelectionRange(inp.value.length,inp.value.length);
      $('#hintRight').textContent='已填入预算，按 Enter 发送';
    };
  });
}

/* ── 右栏上半：当前这台（2026-11-13 新增） ──
   数据全部来自 /api/model，前端只排版，不补字段、不换算。 */
var NOW={id:null,purpose:[]};
var railAutoClosed=false;
function paintNow(){
  var box=$('#railNow'); if(!box) return;
  if(!NOW.id){
    box.innerHTML='<div class="ov"><h3>当前这台 <span>跟着对话走</span></h3>'
      + '<div class="cap">开始提问后，这里会钉住你正在问的那台机器：配置、短板、和其他候选的差距。</div></div>';
    return;
  }
  var u='/api/model?id='+encodeURIComponent(NOW.id)
    + (NOW.purpose.length?('&purpose='+encodeURIComponent(NOW.purpose.join(','))):'');
  jget(u).then(function(r){
    var b=$('#railNow'); if(!b) return;
    if(!r.ok){ b.innerHTML='<div class="ov"><div class="cap">这台不在库里</div></div>'; return }
    var m=r.model;
    var hard=(m.blockers||[]).filter(function(x){return x.level==='hard'});
    var soft=(m.blockers||[]).filter(function(x){return x.level!=='hard'});
    var h='<div class="ov now"><h3>当前这台 <span>'+(CAT_CN[m.cat]||'')+'</span></h3>'
      + '<div class="now-nm">'+esc(m.name)+'</div>'
      + '<div class="now-pr"><span>¥</span><b>'+money(m.price)+'</b><em>到手价</em></div>'
      + '<div class="now-sp">'+esc(m.spec||'')+'</div>';
    h+='<div class="now-kv">'
      // 2026-11-16：原来这一格显示「评价：有人写过优缺点/库里没人评过」。
      // 用户要求不要暴露这个区分 —— 换成「价格口径」，那是用户真正需要知道的
      // （同一个数字是到手价还是平台快照价，含义完全不同）。
      + '<div><span>价格口径</span><b>'+(m.priceIsEstimate?'平台快照价':'到手价')+'</b></div>'
      + '<div><span>性能分</span><b>'+(m.perf==null?'无实测':m.perf+' / 100')+'</b></div>'
      + '<div><span>内存</span><b>'+(m.ram==null?'未知':m.ram+'G')+'</b></div>'
      + '<div><span>重量</span><b>'+(m.weightKg==null?'库里没记':m.weightKg+' kg')+'</b></div>'
      + '</div>';
    if(hard.length||soft.length){
      h+='<div class="now-gaps">';
      hard.forEach(function(x){ h+='<div class="gap hard"><i>硬伤</i><span>'+esc(x.why)+'</span></div>' });
      soft.forEach(function(x){ h+='<div class="gap soft"><i>不算强</i><span>'+esc(x.why)+'</span></div>' });
      h+='</div>';
    }
    if(m.caution) h+='<div class="now-cau"><b>注意</b><span>'+esc(m.caution)+'</span></div>';
    h+='</div>';
    b.innerHTML=h;
  }).catch(function(){});
}

/* ── 示例（来自 /api/demo，前端不写死） ── */
function demo(d){
  var acts=(d.acts||[]).filter(function(a){return a.turns&&a.turns.length});
  $('#demoChips').innerHTML=acts.slice(0,5).map(function(a){
    return '<button data-q="'+esc(a.turns[0])+'" title="'+esc(a.note||'')+'">'+esc(a.title)+'</button>';
  }).join('');
  $('#acts').innerHTML=acts.slice(0,4).map(function(a,i){
    return '<button class="act" data-q="'+esc(a.turns[0])+'">'
      + '<span class="t"><i>'+String.fromCharCode(65+i)+'</i>'+esc(a.title)+'</span>'
      + '<span class="d">'+esc(a.note||'')+'</span><span class="arr">→</span></button>';
  }).join('');
  $$('#demoChips button, #acts .act').forEach(function(b){
    b.onclick=function(){ send(b.dataset.q) };
  });
}

/* ── 回复解析（只做排版，不改写一个字的正文） ── */
function parseReply(text){
  var blocks=String(text||'').split(/\n{2,}/).filter(function(x){return x.trim()});
  var out='', quote='';
  blocks.forEach(function(b){
    if(/^按你的专业要求/.test(b)){
      quote='<div class="majorq"><b>你这组的配置要求</b><p>'+esc(b).replace(/\n/g,'<br>')+'</p></div>';
      return;
    }
    var lines=b.split(/\n/).filter(function(l){return l.trim()});
    var seg=[];
    lines.forEach(function(l){
      var m=l.match(/^\s*(\d+)[.、]\s*(.+)$/);
      if(m) seg.push({t:'li',n:parseInt(m[1],10),s:m[2]});
      else seg.push({t:'p',s:l});
    });
    var i=0;
    while(i<seg.length){
      if(seg[i].t==='p'){
        var arr=[];
        while(i<seg.length&&seg[i].t==='p'){ arr.push(seg[i].s); i++ }
        out+='<p class="say">'+esc(arr.join('\n')).replace(/\n/g,'<br>')+'</p>';
      } else {
        var startN=seg[i].n, items=[];
        while(i<seg.length&&seg[i].t==='li'){ items.push(seg[i].s); i++ }
        out+='<ol class="numlist">'+items.map(function(s,k){
          return '<li><span class="n">'+(startN+k)+'</span>'+itemHTML(s)+'</li>';
        }).join('')+'</ol>';
      }
    }
  });
  return {html:out+quote};
}
function itemHTML(s){
  return esc(s).replace(/^(.+?)\s*——\s*/, '<b>$1</b> —— ');
}

var SLOT_CN={cat:'品类',budget:'预算',purpose:'用途',years:'用几年',carry:'带出门',dormWatt:'宿舍功耗'};
var CAT_CN={thin:'轻薄本',gaming:'游戏本',desktop:'台式整机',upgrade:'内存固态'};
var PURPOSE_CN={design:'设计',video:'视频',gaming:'游戏',dev:'编程',office:'办公',threeD:'三维',audio:'音频',ai:'本地模型',light:'轻度'};
var MODE_CN={work:'按用途匹配',perf:'性能优先',value:'性价比',carry:'便携优先',daily:'日常平衡',future:'留着以后加'};

function slotRows(s){
  var keys=Object.keys(SLOT_CN);
  var out=[];
  keys.forEach(function(k){
    var v=s[k];
    if(v===null||v===undefined||(Array.isArray(v)&&!v.length)) return;
    var txt;
    if(k==='budget') txt='¥'+money(v);
    else if(k==='cat') txt=CAT_CN[v]||v;
    else if(k==='carry') txt=v?'需要':'不需要';
    else if(k==='years') txt=v+' 年';
    else if(k==='dormWatt') txt='≤ '+v+'W';
    else if(k==='purpose') txt=(Array.isArray(v)?v:[v]).map(function(x){return PURPOSE_CN[x]||x}).join(' / ');
    else txt=String(v);
    out.push('<div class="kv"><span>'+(SLOT_CN[k]||k)+'</span><span>'+esc(txt)+'</span></div>');
  });
  return out.length?out.join(''):'<div style="color:var(--ink3)">（这一轮没有新增条件）</div>';
}
function proof(k,d){
  var noNum=(k.untraceable||[]).length===0, noOut=(k.outside||[]).length===0;
  var checks='<div class="chk '+(noNum?'ok':'no')+'"><i>'+(noNum?'✓':'✕')+'</i><span>数字可追溯'
    +(noNum?'（全部能在库里对上）':'：'+esc(k.untraceable.join('、')))+'</span></div>'
    +'<div class="chk '+(noOut?'ok':'no')+'"><i>'+(noOut?'✓':'✕')+'</i><span>推荐机型都在库内'
    +(noOut?'':'：'+esc(k.outside.join('、')))+'</span></div>'
    +'<div class="chk ok"><i>✓</i><span>排序由内核决定，模型没有排序权和数字写权</span></div>';
  return '<details class="proof"><summary>决策依据</summary><div class="bd">'
    +'<div class="sec">读到的条件</div>'+slotRows(d.slots||{})
    +'<div class="sec">校验</div>'+checks
    +'<div class="sec">数据</div>'
    +'<div class="kv"><span>快照日期</span><span>'+esc(k.dataAsOf||(d.dataAsOf||'—'))+'</span></div>'
    +'<div class="kv"><span>排序侧重</span><span>'+esc(MODE_CN[k.mode]||k.mode||'—')+'</span></div>'
    +'</div></details>';
}

/* ── 固定字段版回答（2026-11-13） ──
   结构全部来自后端 answer 字段，前端只排版：不补字段、不换算、不改顺序。
   后端怎么给的，这里就怎么显示 —— 前端一旦自己算，就没有审计了。 */
function ansCard(a){
  var h='<div class="ans">';
  h+='<div class="ans-hd"><div class="lbl">'+(a.inline?'回答':'结论')+'</div><h3>'
   + esc(a.headline).replace(/\n/g,'<br>')+'</h3></div>';

  h+='<div class="ans-kv">';
  (a.rows||[]).forEach(function(r){
    // 到手价和机型这两行给专门的类：价格是选机第一信息，不能跟"评价""重量"一个字号。
    // （原来首选卡上有 30px 的衬线价，换成字段卡后这个视觉锚点丢了。）
    var cls = r.k==='到手价' ? 'rv pr' : (r.k==='机型' ? 'rv nm' : 'rv');
    h+='<div class="rk">'+esc(r.k)+'</div><div class="'+cls+'">'+esc(r.v)+'</div>';
  });
  h+='</div>';

  if((a.gaps||[]).length){
    h+='<div class="ans-sec"><div class="st">这个用途下要注意</div>';
    a.gaps.forEach(function(g){
      var lbl=g.level==='hard'?'硬伤':(g.level==='soft'?'不算强':'未记录');
      h+='<div class="gap '+esc(g.level)+'"><i>'+lbl+'</i><span>'+esc(g.text)+'</span></div>';
    });
    h+='</div>';
  }

  if((a.blocks||[]).length){
    h+='<div class="ans-sec"><div class="st">补充说明</div>';
    a.blocks.forEach(function(b){
      h+='<div class="blk"><b>'+esc(b.k)+'</b><p>'+esc(b.v)+'</p></div>';
    });
    h+='</div>';
  }

  if((a.candidates||[]).length){
    h+='<div class="ans-sec"><div class="st">其余候选</div>';
    a.candidates.forEach(function(c){
      // 2026-11-16：去掉「仅参数」标记（暴露了"有没有人写过"这个内部区分）。
      // 改成标「快照价」—— 那是用户需要知道的口径差异。
      h+='<div class="cand"><span>'+esc(c.name)
       + (c.priceIsEstimate?'<em>快照价</em>':'')+'</span><b>¥'+money(c.price)+'</b></div>';
    });
    h+='</div>';
  }
  h+='</div>';
  return h;
}

/* ── 首选卡 ── */
function card(top){
  var tags='<div class="tags">';
  // 2026-11-16：去掉「仅参数」标记（暴露"有没有人写过"）。
  // 换成「快照价」—— 提醒用户这个数字不是到手价，那才是有用的信息。
  if(top.priceIsEstimate) tags+='<span class="tag raw" title="平台标价快照，不含券与国补，不等于到手价">快照价</span>';
  if(top.overBudget) tags+='<span class="tag over">超预算</span>';
  if(top.hasReview!==false && !top.overBudget) tags+='<span class="tag pick">推荐</span>';
  tags+='</div>';

  var hard=(top.blockers||[]).filter(function(b){return b.level==='hard'});
  var soft=(top.blockers||[]).filter(function(b){return b.level!=='hard'});
  var notes='';
  if(hard.length) notes+='<div class="nh"><b>按你的用途，这台的短板</b>'+hard.map(function(b){return esc(b.why)}).join('<br>')+'</div>';
  if(soft.length) notes+='<div class="ns"><b>不算强，但够用</b>'+soft.map(function(b){return esc(b.why)}).join('<br>')+'</div>';
  if(top.noPerfData) notes+='<div class="nn">库里没有这台的性能实测 —— 它是按配置和价格排进来的，不是按跑分。</div>';
  if(top.caution) notes+='<div class="nw"><b>注意</b><span>'+esc(top.caution)+'</span></div>';

  return '<div class="card" data-mid="'+esc(top.id)+'">'
    + '<div class="card-hd"><div><div class="card-nm">'+esc(top.name)+'</div>'
    + '<div class="card-sp">'+esc(top.spec||'')+'</div></div>'+tags+'</div>'
    + '<div class="price-row"><span class="cur">¥</span><b>'+money(top.price)+'</b><span class="lb">到手价</span></div>'
    + '<div class="price-sub"><span>页面价 ¥'+money(top.original)+'</span>'
    + (top.subsidy?' · 其中含国补 ¥'+money(top.subsidy):'')+'</div>'
    + (notes?'<div class="notes">'+notes+'</div>':'')
    + '</div>';
}

/* ── 三台对比：同一把尺子，缺的维度写「未知」 ── */
// 三台对比只是"区分身份"：蓝 / 砖红 / 青，三种色相拉得开，一眼能对上列。
var CMP_COLORS=['#2D6CDF','#C2532F','#0F8B8D'];
function cmp(ranked){
  if(!ranked.length) return '';
  var list=ranked.slice(0,3);
  // 同一行内相对最大值的比例 —— 这是**唯一**能让"数值相同 ⇒ 条等长"成立的算法。
  // 之前四行各用各的基准（价格÷最高价、跑分直接当百分比、内存÷64、重量÷全库最重），
  // 结果三台跑分都是 62，条却看起来长短不一，用户会当成数据矛盾。
  function maxOf(key){
    var v=0;
    list.forEach(function(r){ if(typeof r[key]==='number'&&r[key]>v) v=r[key] });
    return v||1;
  }
  var maxPrice=maxOf('price'), maxPerf=maxOf('perf'), maxRam=maxOf('ram'), maxWt=maxOf('weightKg');

  function head(){
    return '<tr><th></th>'+list.map(function(r,i){
      return '<th><button data-mid="'+esc(r.id)+'" title="'+esc(r.name)+'">'
        + '<span class="mdot" style="background:'+CMP_COLORS[i]+'"></span>'+esc(r.name)+'</button></th>';
    }).join('')+'</tr>';
  }
  function row(label,cells){
    return '<tr><td>'+label+'</td>'+cells.map(function(c){return '<td>'+c+'</td>'}).join('')+'</tr>';
  }
  function bar(val,pct,color){
    return '<div class="bar"><i style="width:'+Math.max(2,Math.min(100,pct)).toFixed(1)+'%;background:'+color+'"></i></div>';
  }

  var priceRow=row('到手价',list.map(function(r,i){
    return bar(r.price,r.price/maxPrice*100,CMP_COLORS[i])+'<div class="cv">¥'+money(r.price)+'</div>';
  }));
  var perfRow=row('跑分',list.map(function(r,i){
    return r.perf==null? '<div class="cv unk">无跑分</div>'
      : bar(r.perf,r.perf/maxPerf*100,CMP_COLORS[i])+'<div class="cv">'+r.perf+'<span class="u">/100</span></div>';
  }));
  var ramRow=row('内存',list.map(function(r,i){
    return r.ram==null? '<div class="cv unk">未知</div>'
      : bar(r.ram,r.ram/maxRam*100,CMP_COLORS[i])+'<div class="cv">'+r.ram+'G</div>';
  }));
  var wtRow=row('重量',list.map(function(r,i){
    return r.weightKg==null? '<div class="cv unk">库里没记</div>'
      : bar(r.weightKg,r.weightKg/maxWt*100,CMP_COLORS[i])+'<div class="cv">'+r.weightKg+' kg</div>';
  }));
  var upRow=row('可加装',list.map(function(r){
    var u=(r.upgrade==null?0:r.upgrade);
    var cells='';
    for(var i=0;i<4;i++) cells+='<i class="'+(i<u?'f':'')+'"></i>';
    return '<div class="sig">'+cells+'</div>'
      + (u===0?'<div class="sig-note" style="color:var(--warn)">内存焊死</div>':'<div class="sig-note">'+u+' / 3 档</div>');
  }));

  return '<div class="cmp"><div class="cmp-hd"><b>放在同一把尺子上</b>'
    + '<span>同一行里按最大值比（条越长＝该项数值越大），跨行不可比 · 缺数据标「未知」</span></div>'
    + '<table class="cmp-tb"><thead>'+head()+'</thead><tbody>'
    + priceRow+perfRow+ramRow+wtRow+upRow+'</tbody></table></div>';
}

/* ── 其他候选：名字和价格分行 ── */
/* ── 「其他候选」列表已并入上面的对比表，这里不再重复渲染 ──
   2026-11-12：cmp(ranked) 已经展示前 3 台，alts(ranked.slice(1)) 又把第 2、3 名
   列了一遍，同一屏里同两台出现两次。函数保留但不再调用，未来 ranked 超过 3 台时可以复用。 */
function alts(rest){
  if(!rest.length) return '';
  return '<div class="alts"><div class="alts-hd">同样条件下还有 '+rest.length+' 台（同一套规则的往后排）</div>'
    + rest.map(function(r){
        return '<div class="alt" data-mid="'+esc(r.id)+'"><div class="l">'
          + '<div class="nm">'+esc(r.name)+'</div>'
          + '<div class="sp">'+esc(r.spec||'')+'</div></div>'
          + '<div class="r"><div class="pr">¥'+money(r.price)+'</div>'
          // 2026-11-16：原「仅参数」标记去掉（暴露内部区分），换成价格口径提示
          + (r.priceIsEstimate?'<span class="bg">快照价</span>':'')
          + (r.overBudget?'<span class="bg" style="color:var(--warn);background:var(--warn-bg);border-color:#EBD9AE">超预算</span>':'')
          + '</div></div>';
      }).join('')+'</div>';
}

function warnbar(ws){
  return '<div class="warnbar">'+ws.map(esc).join('；')+'</div>';
}
function chipBox(label,q,chips,atSlots,atAsked){
  if(!chips.length) return '';
  return '<div class="askbox"><div class="lbl">'+esc(label)+'</div>'
    + '<div class="q">'+esc(q)+'</div><div class="chips">'
    + chips.map(function(c){return '<button data-t="'+esc(c)+'">'+esc(c)+'</button>'}).join('')
    + '</div></div>';
}

var CHIP_MAP={
  Q1:['计算机相关','设计相关','工商管理','医学 / 其他'],
  Q2:['宿舍不限电','限电 500W','限电 800W','限电 1000W'],
  Q3:['要带去教室','不用带，放宿舍'],
  Q5:['四年内不换','两三年就换']
};
var MISS_MAP={
  '品类':['笔记本','台式整机'],
  '预算':['预算 4000 以内','预算 5000 左右','预算 8000 左右','预算 1 万左右'],
  '用途':['办公学习','打游戏','剪视频','写代码'],
  '携带':['要带去教室','放宿舍不动'],
  '年限':['四年内不换','两三年就换']
};

/* ── 渲染一轮回复 ── */
function render(d){
  var t=el('div','turn fadein');
  var stage=d.stage, h='';

  // 右栏「当前这台」跟着对话走（2026-11-13）：
  //   只要这一轮定下了 focus，就把那台的挂牌信息钉在右栏上；
  //   顺便把全库统计折起来一次 —— 开聊之后用户看的是"这台"，不是库构成。
  if(d.focus){
    NOW.id=d.focus;
    NOW.purpose=((d.slots||{}).purpose)||[];
    paintNow();
    var g=$('#railGlobal');
    if(g && g.open && !railAutoClosed){ g.open=false; railAutoClosed=true }
  }

  if(stage==='refuse'){
    h='<div class="banner"><b>不在覆盖范围</b>'+esc(d.reply)+'</div>';

  } else if(stage==='clarify'){
    var miss=d.missing||[], chips=[];
    miss.forEach(function(m){ chips=chips.concat(MISS_MAP[m]||[]) });
    if(!chips.length) chips=['换一台','把其余候选列出来','说说当前这台'];
    h=parseReply(d.reply).html
      + chipBox('还差一点信息', '', chips.slice(0,8));

  } else if(stage==='social'){
    // 社交回应（招呼/道谢/纠结/太贵了）：只有一句话，不挂字段卡、不挂"决策依据"，
    // 也不带"接着问"那排按钮（那排按钮属于选机流程，放在道谢后面很机械）。
    h=parseReply(d.reply).html;

  } else if(stage==='followup'||stage==='fit'||stage==='exhausted'||stage==='compare'){
    var tag=stage==='fit'?'正在评估':stage==='compare'?'对比':stage==='exhausted'?'候选翻完了':'';
    // 追问也用同一个"结论 + 字段"的壳（后端 answer.inline=true 表示 headline 就是正文）。
    if(d.answer){
      h=ansCard(d.answer);
    } else {
      h=parseReply(d.reply).html
        + (d.focusName&&tag?'<div class="focuschip">'+tag+' '+esc(d.focusName)+'</div>':'');
    }
    if(stage==='exhausted') h+=chipBox('想放宽哪一条','',['预算放宽一点','换游戏本看看','台式整机也行']);
    if(stage==='followup'&&(d.outside||[]).length){
      h+='<div class="warnbar">提到了库里没有的机型：'+esc(d.outside.join('、'))+'，没有拿别的机器顶替。</div>';
    }

  } else {
    var k=d.kernel||{}, ranked=(k.ranked||[]).slice(0,3), top=ranked[0], a=d.answer;
    // 🔴 2026-11-13：主视图改成「一句话结论 + 固定字段卡」。
    //   原来这里直接铺模型写的正文（parseReply），同一台机器换个问法句子结构就变，
    //   用户没法逐栏对照。现在字段卡当主视图，正文折进「详细说明」——
    //   正文没有删：它是过了出口审计的那份，字段卡只是把同样的事实重新排一遍。
    if(a){
      h=ansCard(a);
      h+='<details class="prose"><summary>详细说明（模型措辞，已过出口审计）</summary>'
       + parseReply(d.reply).html+'</details>';
    } else {
      h=parseReply(d.reply).html;
    }
    if(top){
      if(!a) h+=card(top);          // 有字段卡就不再叠一张旧卡，避免同一屏说两遍
      h+=cmp(ranked);
      h+=chipBox('接着问','',['屏幕怎么样','保修几年','有多重','换一台'],d.slots,d.asked);
    }
    if((k.warnings||[]).length) h+=warnbar(k.warnings);
    if((k.questions||[]).length){
      var q=k.questions[0];
      h+=chipBox('还需确认', q.q, CHIP_MAP[q.id]||[], d.slots, d.asked);
    }
    h+=proof(k,d);
  }

  // 后端建议的下一步：直接给按钮，不让用户打「1/2/3」
  if((d.suggest||[]).length) h+=chipBox('你可以','',d.suggest,d.slots,d.asked);

  t.innerHTML=h;
  stream.appendChild(t);
  bindChips(t);
  bindCmp(t);
  toBottom();
}

function bindChips(root){
  $$('.chips button',root).forEach(function(b){
    b.onclick=function(){
      $$('.chips button',root).forEach(function(x){x.disabled=true});
      send(b.dataset.t);
    };
  });
}
function bindCmp(root){
  $$('[data-mid]',root).forEach(function(x){
    x.addEventListener('mouseenter',function(){ hl(x.dataset.mid,true) });
    x.addEventListener('mouseleave',function(){ hl(x.dataset.mid,false) });
    x.addEventListener('click',function(){ hl(x.dataset.mid,true) });
  });
}
function hl(mid,onOff){
  $$('.card[data-mid="'+mid+'"]').forEach(function(c){ c.classList.toggle('hl',onOff) });
}

/* ── 发送 ── */
function addUser(text){
  var d=el('div','turn u fadein','<div class="ubb">'+esc(text)+'</div>');
  stream.appendChild(d); toBottom();
}
function addTyping(){
  var d=el('div','turn fadein','<div class="typing"><i></i><i></i><i></i></div>');
  stream.appendChild(d); toBottom(); return d;
}
function mergeSlots(a,b){
  var o={}; for(var k in a) o[k]=a[k];
  for(var j in b){ var v=b[j]; if(v!==null&&v!==undefined&&!(Array.isArray(v)&&!v.length)) o[j]=v }
  return o;
}

function send(text){
  text=(text||inp.value||'').trim();
  if(!text||busy) return;
  S.busy=true; busy=true; sendBtn.disabled=true;
  var hero=$('#hero'); if(hero) hero.remove();
  inp.value=''; autoGrow();
  addUser(text);
  var ph=addTyping();

  apiCall('/api/chat',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      text:text, known:S.slots, asked:S.asked,
      last_stage:S.lastStage, seen:S.seen, focus:S.focus
    })
  }).then(function(d){
    ph.remove();
    if(d.error) throw new Error(d.error);
    if(d.slots){ slots=mergeSlots(slots,d.slots); S.slots=slots; }
    if(d.asked){ asked=d.asked; S.asked=asked; }
    lastStage=d.stage; S.lastStage=lastStage;
    if(d.focus!==undefined){ focus=d.focus; S.focus=focus; }
    // 🔴 2026-11-17：原来只从 kernel.ranked 累积 seen ——
    //    fit / followup 分支没有 kernel.ranked，所以前端不记。
    //    后果（用户实测）：问了 MateBook D16（fit 分支）→ 说「换一台」
    //    → seen 是空的 → 又把 MateBook 推一遍。
    //    修法：后端的 seen 字段优先（它会把 focus 累积进去），
    //    kernel.ranked 作为补充。
    if(d.seen && d.seen.length){
      d.seen.forEach(function(id){ if(id && seen.indexOf(id)<0) seen.push(id) });
    }
    ((d.kernel&&d.kernel.ranked)||[]).forEach(function(x){
      if(x&&x.id&&seen.indexOf(x.id)<0) seen.push(x.id);
    });
    render(d);
  }).catch(function(e){
    ph.remove();
    var t=el('div','turn fadein');
    t.innerHTML='<div class="banner"><b>这一轮没接上</b>'+esc(e.message||'请求失败')+'。'
      + '再发一次通常就好；如果一直这样，说明本地模型没起来，可以先用上面的示例问题。</div>';
    stream.appendChild(t); toBottom();
  }).then(function(){
    S.busy=false; busy=false; sendBtn.disabled=false; inp.focus();
  });
}

/* ── 输入 ── */
function autoGrow(){
  // +1：行高是 22.44px 这种小数，取整后可能比内容少 1px，浏览器就认为"有溢出"并画出滚动条
  // ——也就是用户看到那个"点了没反应的上下三角"。给 1px 余量把这个假溢出消掉。
  // ⚠️ 不能同时用 CSS 的 max-height + JS 的 height:auto —— 有 max-height 时元素被撑到
  //    上限，height:auto 读回来的 scrollHeight 就是上限值，于是"两行文字也撑到 118px"。
  //    上限只由这里管：先量真实内容高度，再自己封顶。
  inp.style.height='auto';
  var h=inp.scrollHeight;
  inp.style.height=(h>118?118:Math.min(h+1,118))+'px';
}
inp.addEventListener('input',autoGrow);
inp.addEventListener('keydown',function(e){
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){ e.preventDefault(); send() }
});
sendBtn.onclick=function(){ send() };
$('#scopeMore').onclick=function(){
  var s=$('#scope');
  s.classList.toggle('open');
  this.textContent=s.classList.contains('open')?'收起口径说明':'展开口径说明';
};
/* ── 侧边栏收纳 ──
   收起后右侧留一个竖排把手「数据概览」，点一下就回来。
   状态存 localStorage，下次打开还是上次的样子（?rail=0/1 可以强制指定）。 */
function setRail(mini){
  var app=$('#app');
  app.classList.toggle('mini', !!mini);
  // 把手在 main 外面，所以状态挂在 body 上（挂在 .app 上选择器匹配不到）
  document.body.classList.toggle('rail-mini', !!mini);
  try{ localStorage.setItem('railMini', mini?'1':'0') }catch(e){}
}
$('#railBtn').onclick=function(){ setRail(true) };
$('#railTab').onclick=function(){ setRail(false) };
(function(){
  var forced=null;
  try{ forced=(new URLSearchParams(location.search)).get('rail') }catch(e){}
  if(forced==='0'||forced==='1'){ setRail(forced==='1'); return }
  try{ if(localStorage.getItem('railMini')==='1') setRail(true) }catch(e){}
})();

/* ── 启动 ── */
health();
jget('/api/overview').then(function(ov){
  if(!ov.ok){ throw new Error('overview') }
  OV=ov;
  // 首屏数字和右栏各自独立，任何一个出错都不许把另一个拖下水，
  // 也不许把渲染错误说成"接口没响应"。
  try{ facts(ov) }catch(e){}
  try{ rail(ov) }catch(e){
    $('#railBody').innerHTML='<div class="ov"><div class="cap">数据概览渲染出错了（接口本身有返回）。'
      + '对话不受影响，下面是错误信息：<br>'+esc(String(e&&e.message||e))+'</div></div>';
  }
}).catch(function(){
  $('#railBody').innerHTML='<div class="ov"><div class="cap">数据概览没读出来：接口 /api/overview 没响应。对话仍然可用，只是右边这些图暂时空着。</div></div>';
});
jget('/api/demo').then(function(d){ if(d.ok) demo(d) }).catch(function(){});
inp.focus();

/* ── ?q= 直接带一句话进来（可分享的链接，也方便自动化验收） ──
   例：http://127.0.0.1:7860/?q=学设计的，预算5100，要带去教室 */
(function(){
  var sp;
  try{ sp=new URLSearchParams(location.search) }catch(e){ return }
  var q=(sp.get('q')||'').trim(), fill=(sp.get('fill')||'').trim();
  // ?fill= 只填不发（给「先填好再让人按回车」的分享链接、也给自动化验收用）
  if(fill){ inp.value=fill; autoGrow(); inp.focus();
    inp.setSelectionRange(fill.length,fill.length) }
  if(q){ inp.value=q; autoGrow(); send(q); }
})();

/* ═══════════════════════════════════════════════════════════════
   对外导出（2026-11-21）
   ─────────────────────────────────────────────────────────────
   为什么需要：refresh.js 是独立的 IIFE，用不到本文件内部的
   $ / wbHeaders / apiCall。原版靠"碰巧同名"在浏览器全局作用域
   撞上，但 IIFE 内的 var 不外泄 → 刷新面板一执行就 ReferenceError。

   这里显式挂到 window，其他脚本从这里取。
   ═══════════════════════════════════════════════════════════════ */
window.WB = {
  apiCall: apiCall,
  wbHeaders: wbHeaders,
  toast: toast,
  esc: esc,
  money: money
};

})();