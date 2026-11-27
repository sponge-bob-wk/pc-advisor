(function(){
"use strict";
var $=function(s,r){return (r||document).querySelector(s)};
var $$=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))};
var stream=$('#stream'), inp=$('#inp'), sendBtn=$('#send');

/* ═══════════════════════════════════════════════════════════════
   设备判定（2026-11-25 新增）
   ─────────────────────────────────────────────────────────────
   为什么需要：手机上自动 focus 输入框会弹软键盘，把回答顶掉。
   桌面端 focus 是好的（省一次点击），移动端是坏的。

   判据用 pointer:coarse（触屏）而不是屏幕宽度：
     · 窄窗口的桌面浏览器 → 有鼠标，focus 无害，保留
     · 触屏笔记本 → 不该弹软键盘，跳过
     · 手机/平板 → 触屏，跳过
   ═══════════════════════════════════════════════════════════════ */
function isTouch(){
  try{
    return window.matchMedia('(pointer:coarse)').matches;
  }catch(e){
    // 老浏览器没有 matchMedia：退回 UA 判断
    return /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent||'');
  }
}

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
  // 🔴 2026-11-26 减法：fTotal/fPerf/fWeight/fCat 这些 id 已从 index.html 删掉。
  //    用户原话：「你想表达很多严谨性，但是没必要展示给用户看」——
  //    首屏那四个数字方块（可推荐机型 / 有跑分实测 / 有实测重量 / 覆盖品类）
  //    是给评审看的，用户打开页面只想说需求。
  //    数字本身没删，侧边栏「数据概览」里还在。
  //    set() 有空值保护，找不到元素会静默跳过，所以不需要额外判空。
  set('asof','数据快照 '+ov.dataAsOf);
  set('pool','库内 '+ov.total+' 台');
  set('hintRight','只做 Windows 笔记本 / 台式整机 / 内存固态');
}

/* ── 右栏图表：每个数都直接取 /api/overview，不在前端做统计 ── */
function rail(ov){
  var h='';

  /* 🔴 2026-11-26 精简：删掉原「1. 数据覆盖」块。
     理由：① 它和「5. 覆盖度」讲同一件事（跑分/重量台数），一处说两遍；
           ② 「多少台有跑分」是数据质量指标，用户挑电脑时不需要先读报告。
     台数信息没丢：「全库统计」标题右边还写着 N 台。 */
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

  /* 🔴 2026-11-26 精简：删掉「4. 可加装分布」全库柱状图。
     理由：每台机器详情里已有「内存 16G（可加装）」这一行。
     用户关心「我看的这台能不能加」，不是全库分布长什么样。 */
  /* 🔴 2026-11-26 精简：删掉原「5. 覆盖度」块。
     理由：和上面第 1 块重复（都在讲跑分/重量台数）。
     数据缺失该在**回答里**说 —— 比如「库里没记这台的实测重量」，
     而不是在侧栏画一张全库斜纹图让用户自己体会。 */
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

  // 🔴 2026-11-26 精简：页脚那句技术声明收进 summary 的 title，
  //    不再单独占 48px。信息没丢，只是不摆在明面上。

  // 右栏结构（2026-11-13）：上面是「当前这台」（跟着对话走），下面才是全库统计。
  // 全库统计裹进 <details>，开聊后自动折起来一次 —— 用户要看的是"我在问的这台"，
  // 不是每轮都盯着库构成。
  $('#railBody').innerHTML='<div id="railNow" class="nowbox"></div>'
    + // 🔴 2026-11-26：默认改成折叠。用户打开页面先看的是「当前这台」和自己的问题，
      //    不是一屏数据报告。要看统计时点一下展开。
      '<details class="rail-global" id="railGlobal">'
      + '<summary title="图上每个数字都直接来自接口；缺数据的地方不画，不当 0 处理。数据截至 '+esc(ov.dataAsOf)+'">'
      // 🔴 2026-11-26 修：这里原来漏了 `<span class="n">` 和 `</summary>` 闭合标签！
      //    后果：`.rg-body` 被浏览器当成 <summary> 的子元素，
      //    而 <summary> 里的内容**永远显示**（不受 <details> 控制），
      //    所以去掉 open 属性后高度还是 610px，折叠完全失效。
      //    实测证据：details 高 633 = summary 高 633，.rg-body display=block。
      + '<span class="t">全库统计</span>'
      + '<span class="n">'+ov.total+' 台</span></summary>'
    + '<div class="rg-body">'+h+'</div></details>';
  paintNow();
  $$('#railBody .bk').forEach(function(b){
    b.onclick=function(){
      inp.value='预算 '+b.dataset.bucket;
      // 移动端只填入不 focus：用户点按钮是想「用这个预算」，
      // 不是想打字；focus 会把键盘弹出来挡住他刚点的内容。
      if(isTouch()){
        autoGrow();
        $('#hintRight').textContent='已填入预算，点发送即可';
      }else{
        inp.focus(); inp.setSelectionRange(inp.value.length,inp.value.length);
        $('#hintRight').textContent='已填入预算，按 Enter 发送';
      }
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

/* ══════════════════════════════════════════════════════════════════
   🔴 2026-11-26 新增：首屏重建 + 状态重置

   为什么需要：
     「新对话」和「切回旧会话」都要清空对话区并恢复首屏，
     但原代码里没有这个能力 —— hero 在第一条消息时就被 remove() 了，
     而且示例按钮的数据来自 /api/demo（异步），重建时得复用缓存。
   ══════════════════════════════════════════════════════════════════ */

/* /api/demo 的结果缓存：重建首屏时直接用，不重复请求网络 */
/* 首屏 HTML 模板 —— newSession / loadSession 重建首屏时用。
   原来这段只在 index.html 里写死，第一条消息后 hero 被 remove() 就没了，
   没有备份就恢复不了。 */
var HERO_HTML = '<div class="eyebrow">选机助手</div>'
  + '<h1>说清楚预算和用途，<br>我告诉你<em>具体买哪一台</em>。</h1>'
  + '<p class="lede">直接给机型、到手价，以及这台机器'
  + '<b>在你这个用途下的短板</b>。库里没有的，明说没有。</p>'
  + '<div class="acts" id="acts"></div>';

/* 🔴🔴 2026-11-27：首屏只认 index.html 那一份，这里抓快照复用。
   事故（用户截图）：点「新对话」之后**向导入口不见了**。
   根因：renderHero() 用的是上面这个 HERO_HTML，而它是照着 index.html
   手抄的，**抄漏了 .wizard-entry 那一块**。
   判据写两遍一定会漂 —— 这次漂掉的正好是入口本身。

   做法：脚本在 body 末尾执行，#hero 已经在 DOM 里，
   直接把它的 innerHTML 存下来。以后 index.html 改首屏，
   这里自动跟着变，不会再漂。
   HERO_HTML 只作为「#hero 不存在」时的兜底。 */
var HERO_LOCKED = null;
try{
  var _hz = document.getElementById('hero');
  if(_hz && _hz.innerHTML) HERO_LOCKED = _hz.innerHTML;
}catch(e){}

var demoCache = null;

/* 重建首屏（hero + 示例按钮） */
function renderHero(){
  var h = document.createElement('div');
  h.className = 'hero';
  h.id = 'hero';
  h.innerHTML = HERO_LOCKED || HERO_HTML;
  stream.appendChild(h);
  /* 有缓存就把示例按钮填回去 */
  if(demoCache) demo(demoCache);
}

/* 重置会话状态（切会话/新建时必须清，否则条件串到另一个会话） */
function resetState(){
  slots = {}; S.slots = {};
  asked = []; S.asked = [];
  seen.length = 0; S.seen = [];
  focus = null; S.focus = null;
  lastStage = null; S.lastStage = null;
}

/* ── 示例（来自 /api/demo，前端不写死） ── */
function demo(d){
  demoCache = d;   /* 🔴 2026-11-26：存下来，新对话时重建首屏要用 */
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

/* ══════════════════════════════════════════════════════════════════
   🔴 2026-11-26 新增：轻量 markdown 渲染

   为什么需要（用户反馈）：
     装机分支返回的是 markdown 表格，但 parseReply 只认「段落 + 数字列表」，
     结果 `###` 标题、`| 表格 |`、`> 引用` 全被当普通文字显示，
     用户看到一堆原始符号。

   为什么不用第三方库：
     项目原则是零框架依赖（README 里写着"全栈零第三方框架"）。
     而且需要的语法就 5 种，手写 60 行比引 30KB 的 marked.js 划算。
   ══════════════════════════════════════════════════════════════════ */

/* 行内元素：**粗体** → <b> */
function inlineMd(s){
  var t = esc(s);
  // 🔴 2026-11-27：链接渲染 —— 装机单表格的型号列是 ZOL 链接，
  //    不渲染的话 [名称](URL) 原样显示（向导页已同步此逻辑）
  t = t.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>');
  t = t.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  return t;
}

/* 渲染一整块 markdown 文本（parseReply 的 markdown 分支用） */
function renderMd(text){
  var lines = String(text || '').split('\n');
  var out = [];
  var i = 0;

  while(i < lines.length){
    var ln = lines[i];

    /* ── 表格：当前行以 | 开头，且下一行是 |---| 分隔行 ── */
    if(/^\s*\|/.test(ln) && i + 1 < lines.length
       && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i+1])){
      var head = splitRow(ln);
      i += 2;                        /* 跳过表头和数据分隔行 */
      var body = [];
      while(i < lines.length && /^\s*\|/.test(lines[i])){
        body.push(splitRow(lines[i]));
        i++;
      }
      out.push(mdTable(head, body));
      continue;
    }

    /* ── 标题 # ## ### ── */
    var mh = ln.match(/^\s*(#{1,4})\s+(.+)$/);
    if(mh){
      var lvl = Math.min(mh[1].length + 2, 6);   /* 后端的 ### 映射成 h5 太小的，统一压到合适字号 */
      out.push('<div class="md-h md-h' + mh[1].length + '">'
               + inlineMd(mh[2]) + '</div>');
      i++;
      continue;
    }

    /* ── 分隔线 --- ── */
    if(/^\s*---+\s*$/.test(ln)){
      out.push('<div class="md-hr"></div>');
      i++;
      continue;
    }

    /* ── 引用 > ── */
    if(/^\s*>\s?/.test(ln)){
      var qs = [];
      while(i < lines.length && /^\s*>\s?/.test(lines[i])){
        qs.push(lines[i].replace(/^\s*>\s?/, ''));
        i++;
      }
      out.push('<div class="md-quote">' + inlineMd(qs.join(' ')) + '</div>');
      continue;
    }

    /* ── 普通段落（收集连续非空行）── */
    if(ln.trim()){
      var ps = [];
      while(i < lines.length && lines[i].trim()
            && !/^\s*\|/.test(lines[i])
            && !/^\s*#{1,4}\s/.test(lines[i])
            && !/^\s*---+\s*$/.test(lines[i])
            && !/^\s*>/.test(lines[i])){
        ps.push(lines[i]);
        i++;
      }
      if(ps.length) out.push('<p class="say">' + inlineMd(ps.join('\n')).replace(/\n/g, '<br>') + '</p>');
      continue;
    }

    i++;
  }
  return out.join('');
}

/* 拆表格行："| a | b |" → ['a','b'] */
function splitRow(ln){
  var s = ln.trim().replace(/^\|/, '').replace(/\|$/, '');
  return s.split('|').map(function(x){ return x.trim() });
}

/* 拼表格 HTML。首列做标签列（加粗、灰底），最后一列右对齐（价格） */
function mdTable(head, rows){
  var h = '<div class="md-table-wrap"><table class="md-table"><thead><tr>';
  head.forEach(function(c, k){
    var cls = (k === 0) ? ' class="k"' : (k === head.length - 1 ? ' class="num"' : '');
    h += '<th' + cls + '>' + inlineMd(c) + '</th>';
  });
  h += '</tr></thead><tbody>';
  rows.forEach(function(r){
    /* 分隔行可能被当成数据行读进来（如果表头下面没紧贴分隔行），跳过 */
    if(r.every(function(x){ return /^[-:\s]*$/.test(x) })) return;
    h += '<tr>';
    r.forEach(function(c, k){
      var cls = (k === 0) ? ' class="k"' : (k === r.length - 1 ? ' class="num"' : '');
      h += '<td' + cls + '>' + inlineMd(c) + '</td>';
    });
    h += '</tr>';
  });
  h += '</tbody></table></div>';
  return h;
}

/* ── 回复解析（只做排版，不改写一个字的正文） ── */
function parseReply(text){
  /* 🔴 2026-11-26：先判断是不是 markdown。
     装机单、结构化卡片这些分支返回的是 markdown（表格/标题/引用），
     走下面那套「段落 + 数字列表」的解析会原样显示符号。
     用户实测：「你自己看看，输出纯文本，格式化数据呢」 */
  var raw = String(text||'');
  if(/^\s*#{1,4}\s/m.test(raw) || /^\s*\|.*\|\s*$/m.test(raw)
     || /^\s*>\s/m.test(raw)){
    return {html: renderMd(raw)};
  }

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
  /* 🔴 2026-11-27：结论**只把第一句加粗**，剩下的降成正文。
     模型的 headline 经常是「数据不足，判断不了。因为没说用途……要问具体做什么的才能判。」
     这种三四句的段落，整段塞进 <h3> 就是一大块粗黑字，读起来像吵架。
     （用户截图反馈：「跳转到对话里文字排版太丑了」） */
  var _hd = String(a.headline || '');
  var _m = _hd.match(/^([\s\S]{4,60}?[。！？])([\s\S]+)$/);
  var _hl = _m ? _m[1] : _hd;
  var _rest = _m ? _m[2].replace(/^\s+/,'') : '';
  h+='<div class="ans-hd"><div class="lbl">'+(a.inline?'回答':'结论')+'</div><h3>'
   + inlineMd(_hl).replace(/\n/g,'<br>')+'</h3>'
   + (_rest ? '<div class="ans-body">'+inlineMd(_rest).replace(/\n/g,'<br>')+'</div>' : '')
   + '</div>';

  h+='<div class="ans-kv">';
  (a.rows||[]).forEach(function(r){
    // 到手价和机型这两行给专门的类：价格是选机第一信息，不能跟"评价""重量"一个字号。
    // （原来首选卡上有 30px 的衬线价，换成字段卡后这个视觉锚点丢了。）
    var cls = r.k==='到手价' ? 'rv pr' : (r.k==='机型' ? 'rv nm' : 'rv');
    h+='<div class="rk">'+esc(r.k)+'</div><div class="'+cls+'">'+inlineMd(r.v)+'</div>';
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
      /* 🔴 2026-11-26 改造（用户要求）：
         「下面再推荐同价位的，可以点直接跳转然后看另一台的配置信息」

         原来候选是纯文本，点了没反应。现在每个候选是一行可点击的条目：
         点击 → 当前这台记进 seen → focus 设为目标 → 发「这台怎么样」

         ⚠️ 顺手修了个 bug：原来判断 `c.priceIsEstimate` 决定是否标「快照价」，
            但 candidates 里根本没有这个字段（后端给的是 hasReview / note），
            所以那个标记从来没显示过。 */
      h+='<div class="ans-sec"><div class="st">同价位还有</div>';
      a.candidates.forEach(function(c){
        var hasId = !!c.id;
        h+='<div class="cand'+(hasId?' clickable':'')+'"'
         + (hasId?' data-cand-id="'+esc(c.id)+'"':'')
         + (hasId?' role="button" tabindex="0"':'')
         + '><span>'+esc(c.name)+'</span>'
         + '<b>¥'+money(c.price)+'</b>'
         + (hasId?'<i class="cand-go">查看 →</i>':'')
         + '</div>';
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
  // 🔴 2026-11-27：每轮回答后追加向导入口条 —— 用户反馈
  //    「这段对话结束了，不能跳转到向导」。开聊后也要随时能切。
  var wz=document.createElement('div');
  wz.className='wizard-strip';
  wz.innerHTML='<span>换种方式？</span>'
    +'<a href="/wizard.html" target="_blank" rel="noopener">打开选机向导 ↷</a>';
  stream.appendChild(wz);
  stream.appendChild(t);
  bindChips(t);
  bindCands(t);   /* 🔴 2026-11-26：候选机型点击跳转 */
  bindCmp(t);
  toBottom();
    return t;   /* 🔴 2026-11-26：返回节点，send() 要靠它取 innerHTML 存会话 */
  }

/* 🔴 2026-11-26 新增：候选机型点击跳转
   点击后：① 当前这台记进 seen ② focus 设成目标 ③ 发「这台怎么样」
   这样回答落在同一个会话里，不整页刷新。

   ⚠️ 为什么要把当前这台记进 seen：
      不记的话，用户点 A → 看 B → 点回 A 时，A 会被当成没看过又推一遍。 */
function bindCands(root){
  $$('.cand.clickable', root).forEach(function(el){
    var cid = el.dataset.candId;
    if(!cid) return;
    function go(ev){
      ev.preventDefault();
      if(busy) return;
      if(focus && seen.indexOf(focus) < 0) seen.push(focus);
      S.seen = seen;
      focus = cid; S.focus = cid;
      send('这台怎么样');
    }
    el.onclick = go;
    el.onkeydown = function(ev){
      if(ev.key === 'Enter' || ev.key === ' ') go(ev);
    };
  });
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
  /* 🔴 2026-11-26：用户消息落库。
     注意顺序 —— 必须在 addUser 之后、请求之前，
     这样即使请求失败，用户说过的话也还在历史里。 */
  if(window.WB && window.WB.logMsg){
    window.WB.logMsg('user', text, '', null);
  }
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

    /* 🔴 2026-11-26：助手回复落库 —— **存渲染后的 HTML**。
       原来存的是空 html，切回旧会话时表格/卡片全丢了，
       只能退回显示纯 markdown 源码。
       这里从 render() 返回的节点取 innerHTML，保证还原一致。 */
    if(window.WB && window.WB.logMsg){
      try{
        var _node = render(d);
        window.WB.logMsg('assistant', d.reply || '',
                         _node ? _node.innerHTML : '', {
          slots: slots, asked: asked, seen: seen,
          focus: focus, stage: lastStage
        });
      }catch(e){
        render(d);
        window.WB.logMsg('assistant', d.reply || '', '', null);
      }
    } else {
      render(d);
    }
  }).catch(function(e){
    ph.remove();
    var t=el('div','turn fadein');
    t.innerHTML='<div class="banner"><b>这一轮没接上</b>'+esc(e.message||'请求失败')+'。'
      + '再发一次通常就好；如果一直这样，说明本地模型没起来，可以先用上面的示例问题。</div>';
    stream.appendChild(t); toBottom();
  }).then(function(){
    S.busy=false; busy=false; sendBtn.disabled=false;
    // 🔴 2026-11-25：**移动端不自动 focus**。
    //
    //    问题：每轮回答完成后 focus 输入框，手机上会弹出软键盘，
    //    把刚出来的回答顶掉一大半 —— 用户想读答案，键盘挡住视线。
    //
    //    桌面端保留 focus：物理键盘不会遮挡，而且省一次点击，体验更好。
    //
    //    判据用 matchMedia('(pointer: coarse)')（触屏）而不是屏幕宽度：
    //    窄窗口的桌面浏览器仍然需要 focus，触屏笔记本也不该弹键盘。
    if(!isTouch()) inp.focus();
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

/* ══════════════════════════════════════════════════════════════
   🔴 2026-11-26 新增：会话栏交互

   ⚠️ 教训：这里不能再写 `window.WB.xxx = ...` ——
   `window.WB` 在文件**末尾**（L901 附近）才定义，
   而这段代码在 L821 执行，那时 window.WB 还是 undefined，
   直接赋值会抛 `Cannot set properties of undefined`，
   **整段 IIFE 中断，后面的 UI 绑定全都不执行**（页面看起来"死了"）。

   所以这里用局部函数 closeSide，最后统一挂到 window.WB 上。
   ══════════════════════════════════════════════════════════════ */
var closeSide = function(){
  document.body.classList.remove('side-open');
};
(function(){
  // 新对话
  var nb = $('#newChat');
  if(nb) nb.onclick = function(){
    if(window.WB && window.WB.newSession) window.WB.newSession();
    closeSide();
  };

  // 移动端抽屉
  var sb = $('#sideBtn'), mask = $('#sideMask');
  if(sb) sb.onclick = function(ev){
    ev.stopPropagation();
    document.body.classList.toggle('side-open');
  };
  if(mask) mask.onclick = closeSide;
  document.addEventListener('keydown', function(e){
    if(e.key === 'Escape') closeSide();
  });
})();
// 🔴 2026-11-26 减法：scopeMore / scope 已从 index.html 删掉，
//    这里必须判空 —— 原来直接 $('#scopeMore').onclick 会抛
//    TypeError: Cannot set properties of null，整段初始化中断，
//    后面的发送按钮绑定、侧边栏逻辑全都不执行（页面看起来"死了"）。
(function(){
  var sm=$('#scopeMore'), sc=$('#scope');
  if(!sm||!sc) return;
  sm.onclick=function(){
    sc.classList.toggle('open');
    this.textContent=sc.classList.contains('open')?'收起口径说明':'展开口径说明';
  };
})();
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

/* 🔴 2026-11-27：**手机上一进来必须把右侧抽屉收起来**。
   右侧「数据概览」在窄屏是抽屉，默认展开的话会盖住大半个屏幕。
   这里直接挂 class，不走 setRail() —— 走了会写 localStorage，
   用户在桌面端存过「展开」，回头手机一点就把桌面偏好覆盖成「收起」。 */
function _railByViewport(){
  if(window.matchMedia('(max-width:900px)').matches){
    document.body.classList.add('rail-mini');
  }
}
_railByViewport();
window.addEventListener('resize', function(){
  // 只处理「变窄」这一侧：变宽时交回 localStorage 的偏好，不硬拉回来
  if(window.matchMedia('(max-width:900px)').matches){
    document.body.classList.add('rail-mini');
  }
});
  // 🔴 2026-11-26：侧栏滚动后给 header 打 .is-stuck，显示一条细分割线。
  //    为什么需要：`.rail-hd` 是 sticky 且背景 = --bg，滚动时
  //    内容和 header 黏在一起，没有分隔感，看起来像"文字被压住了"。
  //    加一条 border-bottom 就分得清"这是固定栏，下面是内容"。
  (function(){
    var _rail=$('#rail'), _hd=$('#railHd');
    if(!_rail||!_hd) return;
    _rail.addEventListener('scroll', function(){
      _hd.classList.toggle('is-stuck', _rail.scrollTop > 2);
    }, {passive:true});
  })();
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
// 🔴 2026-11-25：启动时不自动 focus（移动端会一进页面就弹键盘，挡住首屏）。
//    桌面端保留 —— 用户进来就想打字，省一次点击。
if(!isTouch()) inp.focus();

/* ── ?q= 直接带一句话进来（可分享的链接，也方便自动化验收） ──
   例：http://127.0.0.1:7860/?q=学设计的，预算5100，要带去教室

   🔴 2026-11-26 加 ?id=：**按机型 id 直接打开这台的信息**。
   用户要求「推荐别的我可以直接点卡片跳到另一个推荐的机型的信息」。

   为什么不用 ?q=<机型名>：
     实测「ThinkBook 14+ 怎么样」识别失败（名字里有 + 和括号），
     而 id 是唯一键，不会撞。见 openai_bridge.py 的注释。
   例：http://127.0.0.1:7860/?id=add-xingbookproair14
*/
(function(){
  var sp;
  try{ sp=new URLSearchParams(location.search) }catch(e){ return }
  var q=(sp.get('q')||'').trim(), fill=(sp.get('fill')||'').trim();
  var mid=(sp.get('id')||'').trim();
  // ?fill= 只填不发（给「先填好再让人按回车」的分享链接、也给自动化验收用）
  if(fill){ inp.value=fill; autoGrow(); inp.focus();
    inp.setSelectionRange(fill.length,fill.length) }
  // ?id= 按机型 id 直接问这台（后端 run(text, focus=id)）
  if(mid){
    S.focus=mid;
    /* 🔴 2026-11-27：「这台怎么样」会被判成 fit（拿用途问这台行不行），
       而这句里根本没有用途 → 回一句「数据不足，判断不了」，
       从向导点进来的人第一眼看到的就是这句废话。
       改成「介绍一下这台」，落在 followup，给配置和价格。 */
    send('介绍一下这台');
    return;
  }
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
    money: money,
    closeSide: closeSide,

    /* ═══════════════════════════════════════════════════════════
       🔴 2026-11-26 新增：会话管理接口（给 sessions.js 调用）

       为什么这些函数要放在 app.js 而不是 sessions.js：
        它们需要操作 app.js 内部的 `S` / `slots` / `stream` 等私有状态。
        如果搬到 sessions.js，就得把这些状态全导出去，反而更乱。

       所以分工是：
         · sessions.js —— 管存储和左侧列表渲染（不知道对话内容怎么显示）
         · app.js      —— 管对话区渲染和状态（不知道存储细节）
       ═══════════════════════════════════════════════════════════ */

    /* 新建对话：清空对话区，重置状态 */
    newSession: function(){
      /* 🔴 2026-11-26 重写。上一版有三个 bug：
         ① hero 恢复调了 bindActs()/paintHero() —— 这两个函数不存在
         ② 新建后 getCur() 返回 ''，导致消息写进旧会话
         ③ #acts 没清，旧按钮残留
         现在统一走 renderHero()，它负责重建首屏 + 填示例按钮。 */
      stream.innerHTML = '';
      renderHero();
      resetState();
      if(window.Sessions){
        window.Sessions.setCur(null);   /* null → 下次发送时才创建新会话 */
        window.Sessions.render();
      }
      inp.value = ''; autoGrow();
      /* ⚠️ 移动端不要 focus，会把键盘弹出来挡住界面 */
      if(!isTouch()) inp.focus();
    },

    /* 加载某个会话：把历史消息画回对话区，恢复状态 */
    loadSession: function(id){
      if(!window.Sessions) return;
      var conv = window.Sessions.readConv(id);

      /* 空会话 → 当新建处理（但保留这个会话 id，不要新建一条） */
      if(!conv.length){
        stream.innerHTML = '';
        renderHero();
        resetState();
        window.Sessions.setCur(id);
        window.Sessions.render();
        return;
      }

      stream.innerHTML = '';
      var lastState = null;
      conv.forEach(function(m){
        if(m.role === 'user'){
          stream.appendChild(
            el('div', 'turn u', '<div class="ubb">' + esc(m.text) + '</div>'));
        } else {
          stream.appendChild(
            el('div', 'turn', m.html || ('<div class="say">' + esc(m.text || '') + '</div>')));
          if(m.state) lastState = m.state;
        }
      });

      /* 恢复会话状态 —— 否则下一轮「换一台」会失忆重推已看过的机器 */
      if(lastState){
        slots = lastState.slots || {};
        S.slots = slots;
        asked = lastState.asked || [];
        S.asked = asked;
        seen = (lastState.seen || []).slice();
        S.seen = seen;
        focus = lastState.focus || null;
        S.focus = focus;
        lastStage = lastState.stage || null;
        S.lastStage = lastStage;
      }
      window.Sessions.setCur(id);
      window.Sessions.render();
      toBottom();
    },

    /* 🔴 2026-11-26 新增：把当前会话的状态拿出来（给外部调试/测试用） */
    getState: function(){
      return {slots: slots, asked: asked, seen: seen,
              focus: focus, stage: lastStage,
              sessionId: window.Sessions ? window.Sessions.getCur() : null};
    },


    /* 记录一条消息到当前会话 */
    logMsg: function(role, text, html, state){
      if(!window.Sessions) return;
      var sid = window.Sessions.getCur();
      if(!sid){
        sid = window.Sessions.create(role === 'user' ? text : '');
      }
      window.Sessions.append(sid, {
        role: role, text: text || '', html: html || '',
        ts: Date.now(), state: state || null
      });
      window.Sessions.render();
    }
};

})();