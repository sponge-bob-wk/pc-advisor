// 纯逻辑自测：把页面里的脚本抽出来，在假 DOM 里跑一遍
// 重点验证：输入解析边界、排序引擎（按品类分维度）、国补计算、数据完整性、各分支渲染不报错
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', '电脑涨价趋势.html'), 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/);
if (!m) { console.error('没找到主脚本'); process.exit(1); }
const code = m[1];

function makeEl() {
  const el = {
    hidden: false, style: {}, dataset: {}, value: '', disabled: false,
    textContent: '', innerHTML: '', onclick: null, children: [],
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, focus() {}, closest() { return null; },
    appendChild() {}, querySelector() { return makeEl(); }
  };
  return el;
}
const cache = {};
const document = {
  querySelector(sel) { return cache[sel] || (cache[sel] = makeEl()); },
  createElement() { return makeEl(); }
};
const store = {};
const localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = v; }
};
const window = { scrollTo() {} };

const sandbox = { document, localStorage, window, console, Date, JSON, Math, String, Number, parseInt, Array, Object };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code + `
;globalThis.__t = { analyze, bandOf, fullResult, budgetOnly, unknownCard, nocoverCard,
  chartCategory, chartQuarter, rankOffers, score, dims, brandScore,
  WEIGHTPOOL, weightsOf, dimListOf, kindOf, fixMode, subsidyCalc, subsidyBlock,
  rankBlock, detailCard, upgradeBlock, brandBlock, crossSellBlock, trapsBlock,
  defectBlock, prosConsBlock, DATA, STATE,
  // STATE 会被 fullResult 等函数整体重新赋值，导出的对象是当时的快照，
  // 改它不会影响页面内部状态。给一对访问器，读写的才是同一份。
  getMode: () => STATE.mode, setMode: (m) => { STATE.mode = m; } };
`, sandbox);
const T = sandbox.__t;

let pass = 0, fail = 0;
function eq(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; }
  else { fail++; console.log('  FAIL ' + name + '\n     期望 ' + w + '\n     实际 ' + g); }
}
function ok(name, cond, detail) {
  if (cond) { pass++; } else { fail++; console.log('  FAIL ' + name + (detail ? '\n     ' + detail : '')); }
}

console.log('--- 输入解析 ---');
const C = (r) => r.err ? 'err:' + r.err : (r.cat ? r.cat.key : 'no-cat') + (r.budget ? '@' + r.budget : '');
eq('空输入', C(T.analyze('')), 'err:empty');
eq('纯空格', C(T.analyze('   ')), 'err:empty');
eq('游戏本', C(T.analyze('游戏本')), 'gaming');
eq('5000的轻薄本', C(T.analyze('5000的轻薄本')), 'thin@5000');
eq('只给预算', C(T.analyze('5000')), 'no-cat@5000');
eq('想加内存', C(T.analyze('想加内存')), 'upgrade');
eq('台式机', C(T.analyze('台式机怎么配')), 'desktop');
eq('5k', C(T.analyze('5k轻薄本')), 'thin@5000');
eq('五千', C(T.analyze('五千的轻薄本')), 'thin@5000');
eq('两万', C(T.analyze('两万的游戏本')), 'gaming@20000');
eq('MacBook', C(T.analyze('MacBook Pro 值得买吗')), 'err:nocover');
eq('胡言乱语', C(T.analyze('随便看看')), 'err:unknown');
eq('型号数字不误判', C(T.analyze('华硕无畏15')), 'err:unknown');
eq('屏幕尺寸不误判', C(T.analyze('13寸轻薄本')), 'thin');

console.log('--- 手机输入解析 ---');
eq('手机', C(T.analyze('想换个手机')), 'phone');
eq('3000的手机', C(T.analyze('3000的手机')), 'phone@3000');
eq('6000以内的手机', C(T.analyze('6000以内的手机')), 'phone@6000');
eq('iPhone', C(T.analyze('iPhone 17 值得买吗')), 'phone');
eq('华为手机', C(T.analyze('华为手机怎么样')), 'phone');
eq('拍照', C(T.analyze('拍照好的手机')), 'phone');
// 回归：品牌词同时是手机关键词，但「小米笔记本」说的是电脑
eq('小米笔记本 → 轻薄本', C(T.analyze('小米笔记本')), 'thin');
eq('华为笔记本 → 轻薄本', C(T.analyze('华为笔记本哪个好')), 'thin');
eq('荣耀笔记本 → 轻薄本', C(T.analyze('荣耀笔记本')), 'thin');
eq('小米手机 → 手机', C(T.analyze('小米手机')), 'phone');

console.log('--- 预算档 ---');
eq('3000 档', T.bandOf(3000).max, 3000);
eq('3001 档', T.bandOf(3001).max, 5000);
eq('6500 档', T.bandOf(6500).max, 7000);
eq('无预算', T.bandOf(null), null);
// 手机用自己的预算档，不该套电脑的
const phoneCat = T.DATA.categories.find(c => c.key === 'phone');
ok('手机有独立预算档', Array.isArray(phoneCat.budgetBands) && phoneCat.budgetBands.length >= 4);
eq('手机 6500 档 → 超国补线', T.bandOf(6500, phoneCat).max, 999999);
eq('手机 5000 档 → 国补线内', T.bandOf(5000, phoneCat).max, 6000);

console.log('--- 品类维度拆开 ---');
eq('电脑是 pc 类', T.kindOf(T.DATA.categories.find(c => c.key === 'thin')), 'pc');
eq('手机是 phone 类', T.kindOf(phoneCat), 'phone');
eq('电脑维度数', T.dimListOf('pc').length, 5);
eq('手机维度数', T.dimListOf('phone').length, 5);
ok('手机维度里没有「可加装」', T.dimListOf('phone').every(d => d.key !== 'upgrade'));
ok('手机维度含续航与影像', T.dimListOf('phone').some(d => d.key === 'battery') && T.dimListOf('phone').some(d => d.key === 'camera'));
// 手机没有 future/carry 这两个侧重（那是电脑的「可加装/便携」语义）
ok('手机没有「能加装优先」', !T.WEIGHTPOOL.phone.future);
ok('手机有「续航优先」', !!T.WEIGHTPOOL.phone.battery);
eq('电脑的 carry 到手机上会被换掉', T.fixMode('phone', 'carry'), 'value');
eq('手机的 battery 到电脑上会被换掉', T.fixMode('pc', 'battery'), 'value');

console.log('--- 排序引擎 ---');
const categories = T.DATA.categories;
const byKey = k => categories.find(c => c.key === k);

// 1. 每种侧重都排得出完整名单
for (const kind of Object.keys(T.WEIGHTPOOL)) {
  for (const cat of categories.filter(c => T.kindOf(c) === kind)) {
    for (const mode of Object.keys(T.WEIGHTPOOL[kind])) {
      const list = T.rankOffers(cat.key, null, mode);
      const expect = T.DATA.offers.filter(o => o.cat === cat.key).length;
      ok('排序完整性 ' + mode + '/' + cat.key, list.length === expect,
         '期望 ' + expect + ' 条，实得 ' + list.length);
      ok('分数均为有效数字 ' + mode + '/' + cat.key,
         list.every(o => Number.isFinite(o._score) && o._score >= 0 && o._score <= 100),
         JSON.stringify(list.map(o => o._score)));
    }
  }
}

// 2. 排序必须是可解释的降序。
// 分档排序之后，「降序」不再等于「加权总分降序」—— 排序键是
//   ①超预算 ②主维度档位 ③主维度原始分 ④加权总分 ⑤id
// 所以这里按真正的排序键逐级验证，并且验证「显示给用户的那个大数字」
// （_main）和排序方向一致。早先只验 _score，会在分档排序下假绿。
for (const kind of Object.keys(T.WEIGHTPOOL)) {
  const cat = categories.find(c => T.kindOf(c) === kind && T.DATA.offers.some(o => o.cat === c.key));
  for (const mode of Object.keys(T.WEIGHTPOOL[kind])) {
    const list = T.rankOffers(cat.key, null, mode);
    const primary = T.WEIGHTPOOL[kind][mode].primary;

    // ② 档位必须单调不增（超预算的排后面，单独验证）
    let tierOK = true;
    for (let i = 1; i < list.length; i++) {
      if (list[i]._over === list[i - 1]._over && list[i]._ptier > list[i - 1]._ptier) tierOK = false;
    }
    ok('主维度分档单调 ' + kind + '.' + mode, tierOK,
       primary ? '主维度 ' + primary + ' 的档位出现倒挂' : '无主维度却给了非零档位');

    // ③ 同档内主维度原始分不增；无主维度时退回加权分不增
    let keyOK = true;
    for (let i = 1; i < list.length; i++) {
      const a = list[i - 1], b = list[i];
      if (a._over !== b._over || a._ptier !== b._ptier) continue;
      if (primary) {
        if (b._mainRaw > a._mainRaw) keyOK = false;
      } else {
        if (b._score > a._score) keyOK = false;
      }
    }
    ok('档内按主维度降序 ' + kind + '.' + mode, keyOK,
       primary ? '同档内 ' + primary + ' 出现倒挂' : '加权总分出现倒挂');

    // 显示值必须随名次单调不增 —— 这是用户能直接看见的「自相矛盾」检测。
    // 之前修好排序但沿用加权总分当显示值，就会出现「第2名73、第3名74」。
    let showOK = true;
    for (let i = 1; i < list.length; i++) {
      const a = list[i - 1], b = list[i];
      if (a._over !== b._over || a._ptier !== b._ptier) continue;
      if (b._main > a._main) showOK = false;
    }
    ok('榜单显示分数不倒退 ' + kind + '.' + mode, showOK,
       '显示分数与名次不一致：' +
       list.filter((o, i) => i).map((o, i) => (i + 1) + '.' + o.name + '=' + o._main).join(' / '));
  }
}

// 3. 换侧重，第一名会变 —— 这是"排序有意义"的证据
for (const kind of Object.keys(T.WEIGHTPOOL)) {
  const cat = categories.find(c => T.kindOf(c) === kind && T.DATA.offers.some(o => o.cat === c.key));
  const tops = {};
  for (const mode of Object.keys(T.WEIGHTPOOL[kind])) tops[mode] = T.rankOffers(cat.key, null, mode)[0].name;
  console.log('  ' + kind + ' 各侧重第一名：' + JSON.stringify(tops));
  ok('侧重确实影响排名 ' + kind, new Set(Object.values(tops)).size >= 2,
     '只有 ' + new Set(Object.values(tops)).size + ' 种第一名，权重没起作用');
}

// 3b. 手机：续航优先 → 头名必须是续航分最高的；拍照优先 → 头名必须是影像分最高的。
// 这两条是手机排序「说了算」的证据，不能只靠权重和凑数。
const phPool = T.DATA.offers.filter(o => o.cat === 'phone');
const bestBy = k => phPool.reduce((a, b) => (b[k] > a[k] ? b : a));
const phBat = T.rankOffers('phone', null, 'battery')[0];
const phCam = T.rankOffers('phone', null, 'camera')[0];
ok('续航优先 → 头名续航最高', phBat.battery === bestBy('battery').battery,
   '头名 ' + phBat.name + ' battery=' + phBat.battery + '，最高应是 ' + bestBy('battery').battery);
ok('拍照优先 → 头名影像最高', phCam.camera === bestBy('camera').camera,
   '头名 ' + phCam.name + ' camera=' + phCam.camera + '，最高应是 ' + bestBy('camera').camera);

// 3c. 芯片账面的硬约束。用户原话：「华为国产的芯片离世界一流移动端芯片还差一截子」。
// 这一组断言就是为了防止再出现「麒麟 9030 的性能分高过天玑 9500 / A19」这种数据污染。
console.log('  ' + phPool.slice().sort((a, b) => b.perf - a.perf)
  .map(o => o.name + '=' + o.perf).join(' / '));
const phByName = n => phPool.find(o => o.name === n);
ok('所有手机都有芯片型号与 GB6 跑分',
   phPool.every(o => typeof o.chip === 'string' && o.chip.length > 0 &&
                     Array.isArray(o.gb6) && o.gb6.length === 2),
   '缺的：' + phPool.filter(o => !o.chip || !o.gb6).map(o => o.name).join(','));
ok('perf 可由 GB6 推出（不许手改）',
   phPool.every(o => Math.abs(o.perf - Math.round(
     100 * (0.5 * o.gb6[0] / 4000 + 0.5 * o.gb6[1] / 12000))) <= 1));
// 麒麟 9030 只到骁龙 8 Gen 2 水平，必须低于同池里的天玑 9500 / A19 / 骁龙 8 Elite
const hw = phByName('华为 Mate80'), viv = phByName('vivo X300'),
      ip = phByName('iPhone 17'), k90 = phByName('红米 K90');
ok('华为 Mate80 的算力低于天玑 9500 机型', hw.perf < viv.perf,
   '华为 ' + hw.perf + ' vs vivo ' + viv.perf);
ok('华为 Mate80 的算力低于 A19 机型', hw.perf < ip.perf,
   '华为 ' + hw.perf + ' vs iPhone ' + ip.perf);
ok('华为 Mate80 的算力低于骁龙 8 Elite 机型', hw.perf < k90.perf,
   '华为 ' + hw.perf + ' vs 红米K90 ' + k90.perf);
ok('算力最高的是小米 17 Ultra（骁龙 8 Elite Gen5）',
   phPool.reduce((a, b) => (b.perf > a.perf ? b : a)).name === '小米 17 Ultra');
// 性能优先下，华为不该进前二 —— 这是用户这次质疑的原始现象
const perfTop2 = T.rankOffers('phone', null, 'perf').slice(0, 2).map(o => o.name);
ok('性能优先 → 华为 Mate80 不在前二', !perfTop2.includes('华为 Mate80'),
   '性能优先前二：' + perfTop2.join('、'));
// 续航项必须是可核实的 mAh，而不是来路不明的 0-100
ok('续航项存的是真实容量 mAh', phPool.every(o => o.battery >= 3000 && o.battery <= 12000),
   '越界的：' + phPool.filter(o => !(o.battery >= 3000 && o.battery <= 12000))
     .map(o => o.name + '=' + o.battery).join(','));

// 3d. 电脑的芯片硬约束 —— 和手机 GB6 同一个道理，锁住「凭印象打档位分」这个 bug。
// 改造前的实况：酷睿5 320 和 R7 H255 都被拍成 28 分，而 R23 多核 7540 vs 15475；
// 同一颗 U7-251HX 在不同机型上甚至给了 48 和 52 两个值。
const pcPool = T.DATA.offers.filter(o => o.cat !== 'phone' && o.cat !== 'upgrade');
const pcByName = n => pcPool.find(o => o.name === n);
ok('所有电脑都有芯片型号与 R23 跑分',
   pcPool.every(o => o.chip && Array.isArray(o.cb23) && o.cb23.length === 2),
   '缺数据的：' + pcPool.filter(o => !o.chip || !Array.isArray(o.cb23))
     .map(o => o.name).join(','));
ok('电脑的 perf 可由 R23 推出（不许手改）',
   pcPool.every(o => Math.abs(o.perf - Math.round(
     100 * (0.5 * o.cb23[0] / 2400 + 0.5 * o.cb23[1] / 40000))) <= 1),
   '对不上的：' + pcPool.filter(o => Math.abs(o.perf - Math.round(
     100 * (0.5 * o.cb23[0] / 2400 + 0.5 * o.cb23[1] / 40000))) > 1)
     .map(o => o.name + '=' + o.perf).join(','));
// 酷睿5 320 是 6 核 6 线程，R7 H255 是 8 核 16 线程，多核差近一倍 —— 不该同分
const c5 = pcByName('荣耀 X14 战斗版 2026'), h255 = pcByName('惠普战 66 八代锐龙版');
ok('酷睿5 320 的算力明显低于 R7 H255', c5.perf < h255.perf,
   '酷睿5 320=' + c5.perf + ' vs R7 H255=' + h255.perf + '（改造前两者都是 28）');
// 同一颗 CPU 在同一张表里只能有一个性能分
const byChip = {};
let chipClash = null;
for (const o of pcPool) {
  if (byChip[o.chip] !== undefined && byChip[o.chip] !== o.perf) chipClash = o.chip;
  byChip[o.chip] = o.perf;
}
ok('同款 CPU 不出现两个性能分', chipClash === null,
   '冲突的芯片：' + chipClash + '（改造前 U7-251HX 在不同机型上是 48 和 52）');
// 游戏本里 R9-9955HX 必须是第一，且要高过所有 U7-25xHX
const cl = pcByName('机械革命 苍龙 18 Pro'), u7 = pcByName('机械革命 极光 X');
ok('R9-9955HX 的算力高于 Ultra 7 251HX', cl.perf > u7.perf,
   '9955HX=' + cl.perf + ' vs U7-251HX=' + u7.perf);
ok('性能优先 → 游戏本头名是 9955HX 那台',
   T.rankOffers('gaming', null, 'perf')[0].name === '机械革命 苍龙 18 Pro');

// 3e. 没有 CPU 的品类，「性能」必须退场，而不是编一个分数
const upg = T.DATA.offers.filter(o => o.cat === 'upgrade');
ok('内存/固态的 perf 是 null 而不是编的分数', upg.every(o => o.perf === null),
   '被编了分的：' + upg.filter(o => o.perf !== null).map(o => o.name + '=' + o.perf).join(','));
ok('内存/固态在「性能优先」下显示「不涉及」',
   T.rankOffers('upgrade', null, 'perf').every(o => o._main === '不涉及'),
   '显示成：' + T.rankOffers('upgrade', null, 'perf').map(o => o._main).join(','));
// 不适用维度退出加权后，剩余权重应重新归一，总分仍在 0-100
ok('不适用维度退出加权后总分不越界',
   T.rankOffers('upgrade', null, 'perf').every(o => o._score >= 0 && o._score <= 100),
   '越界：' + T.rankOffers('upgrade', null, 'perf').map(o => o._score).join(','));
ok('内存/固态的权重确实被重新归一（wsum 应 < 1）',
   T.score(upg[0], 'upgrade', null, 'perf').wsum < 0.999,
   '实际 wsum=' + T.score(upg[0], 'upgrade', null, 'perf').wsum);

// 4. 预算约束：超预算的排后面，且被标记
const bList = T.rankOffers('thin', 4200, 'value');
const overs = bList.filter(o => o._over);
ok('超预算被标记', overs.length > 0, '4200 预算下没有一台超预算，测试无效');
ok('超预算全排在最后',
   bList.slice(0, bList.length - overs.length).every(o => !o._over) &&
   bList.slice(bList.length - overs.length).every(o => o._over));

// 4b. 回归：超预算 5% 以内不该被踢出（5009 元对 5000 预算）
const b5000 = T.rankOffers('thin', 5000, 'future');
const zhan66 = b5000.find(o => o.id === 'thin-zhan66');
ok('5009 对 5000 预算不算超', zhan66 && !zhan66._over,
   '战66 被误判为超预算，说明容差没生效');
ok('能加装优先 + 5000 预算 → 选中留插槽的', b5000[0].upgrade === 2,
   '头名 ' + b5000[0].name + ' upgrade=' + b5000[0].upgrade);
const b4000 = T.rankOffers('thin', 4000, 'value');
ok('超 5% 以上仍判超预算', b4000.some(o => o._over));

// 5. 维度分都在 0-100
for (const cat of categories) {
  const kind = T.kindOf(cat);
  for (const o of T.DATA.offers.filter(x => x.cat === cat.key)) {
    const d = T.dims(o, cat.key, 6000, T.fixMode(kind, 'value'));
    for (const k in d) {
      ok('维度分合法 ' + o.id + '.' + k, d[k] >= 0 && d[k] <= 100, k + '=' + d[k]);
    }
    // 只该出现本大类的维度，不该混进另一类的
    const dl = T.dimListOf(kind).map(x => x.key);
    ok('维度集合正确 ' + o.id, dl.every(x => x in d) && Object.keys(d).length === dl.length,
       '期望 ' + JSON.stringify(dl) + '，实得 ' + JSON.stringify(Object.keys(d)));
  }
}

// 6. 权重和必须为 1，否则打分尺度会飘
for (const kind in T.WEIGHTPOOL) {
  for (const mode in T.WEIGHTPOOL[kind]) {
    const sum = Object.values(T.WEIGHTPOOL[kind][mode].w).reduce((a, b) => a + b, 0);
    ok('权重和为 1 ' + kind + '.' + mode, Math.abs(sum - 1) < 1e-9, '实为 ' + sum);
  }
}

// 7. 「能加装优先」应当把留插槽的排前面
const upList = T.rankOffers('thin', null, 'future');
ok('能加装优先 → 头名留插槽', upList[0].upgrade >= 1,
   '头名 ' + upList[0].name + ' upgrade=' + upList[0].upgrade);

// 7b. 回归：性能分必须能分辨出强弱，否则「性能优先」选不出性能最高的那台。
// 注意：改成实测换算后，多台可能真的同分（同为 R7 H255 的四台就是 62 分）。
// 那不是 bug，是事实 —— 同款 CPU 就该同分。所以这里验的是
// 「头名的性能分 == 池内最高性能分」，而不是「头名是某一台特定机器」。
for (const cat of categories) {
  const pool = T.DATA.offers.filter(o => o.cat === cat.key);
  const perfOf = o => T.dims(o, cat.key, null, 'perf').perf;
  const maxPerf = Math.max.apply(null, pool.map(perfOf).filter(v => v !== null));
  const top = T.rankOffers(cat.key, null, 'perf')[0];
  if (cat.key === 'thin' || cat.key === 'phone') {
    ok('性能优先 → 头名性能分等于池内最高 ' + cat.key, perfOf(top) === maxPerf,
       '头名 ' + top.name + '(perf ' + perfOf(top) + ')，池内最高 ' + maxPerf);
  }
  // 内存/固态没有 CPU，perf 恒为 null，跳过。
  // 跨度阈值不再写死 20/30 —— 换成实测分后，跨多大是「事实」而不是「设计」。
  // desktop 只有两台（14600KF 和 9800X3D），R23 多核 24683 vs 23163 本来就接近，
  // 硬要它们差 20 分等于逼数据造假。这里只验「池内不是所有机器同分」，
  // 即分数有区分能力；具体差多少由 build.py 的跑分换算校验兜底。
  if (cat.key === 'upgrade') continue;
  const vals = pool.map(perfOf).filter(v => v !== null);
  ok('性能分不是一池同分 ' + cat.key, new Set(vals).size >= 2,
     '池内所有机型性能分相同（' + vals.join('/') + '），排序会退化成价格排序');
}

// 7c. 便携优先应当选出最轻的（仅电脑有这个维度）
const carryTop = T.rankOffers('thin', null, 'carry')[0];
ok('要带出门 → 头名是便携最高档', carryTop.portable === 3,
   '头名 ' + carryTop.name + ' portable=' + carryTop.portable);

// 8. 品牌分能查到。同名品牌必须按大类分开查，不能串。
ok('品牌分可算', T.brandScore('联想', 'pc') > 0, '联想的品牌分是 ' + T.brandScore('联想', 'pc'));
ok('未知品牌不炸', T.brandScore('不存在', 'pc') === 60, '实为 ' + T.brandScore('不存在', 'pc'));
const hwPc = T.DATA.brands.find(b => b.name === '华为' && b.scope === 'pc');
const hwPh = T.DATA.brands.find(b => b.name === '华为' && b.scope === 'phone');
ok('华为在电脑和手机里都有登记', !!hwPc && !!hwPh);
// 品牌分现在映射到 60-100 区间，压掉了原先「原始差 1 分 → 权重里差 10 分」的放大
const brandExpect = (b) => {
  const raw = (b.service + b.resale) / 2;
  return 60 + Math.max(0, Math.min(1, (raw - 2) / 3)) * 40;
};
ok('同名品牌分按大类取',
   T.brandScore('华为', 'pc') === brandExpect(hwPc) &&
   T.brandScore('华为', 'phone') === brandExpect(hwPh),
   'pc=' + T.brandScore('华为', 'pc') + ' phone=' + T.brandScore('华为', 'phone'));
ok('品牌分落在 60-100',
   T.DATA.brands.every(b => {
     const s = T.brandScore(b.name, b.scope || 'pc');
     return s >= 60 && s <= 100;
   }));

console.log('--- 国补计算 ---');
const R = T.DATA.subsidyRules;
// 电脑：无门槛，15% 封顶 1500
eq('电脑 5000 → 补 750', T.subsidyCalc(R.pc, 5000).got, 750);
eq('电脑 12000 → 封顶 1500', T.subsidyCalc(R.pc, 12000).got, 1500);
eq('电脑 10000 → 正好吃满 1500', T.subsidyCalc(R.pc, 10000).got, 1500);
eq('电脑 20000 → 仍是 1500', T.subsidyCalc(R.pc, 20000).got, 1500);
// 手机：≤6000 才补，15% 封顶 500
eq('手机 2000 → 补 300', T.subsidyCalc(R.phone, 2000).got, 300);
eq('手机 3333 → 吃满 500', T.subsidyCalc(R.phone, 3333).got, 500);
eq('手机 5999 → 仍是 500', T.subsidyCalc(R.phone, 5999).got, 500);
eq('手机 6000 → 仍在线上', T.subsidyCalc(R.phone, 6000).got, 500);
eq('手机 6001 → 一分不补', T.subsidyCalc(R.phone, 6001).got, 0);
ok('手机 6001 被标记超线', T.subsidyCalc(R.phone, 6001).overLine === true);
eq('手机 7999 → 一分不补', T.subsidyCalc(R.phone, 7999).got, 0);
// 同样的价格，两类差多少 —— 这是本工具的核心差价
// 7999×15% = 1199.85，两者都四舍五入到 1200，所以差价是 1200（电脑封顶 1500 未触发）
const gap = T.subsidyCalc(R.pc, 7999).got - T.subsidyCalc(R.phone, 7999).got;
eq('7999 的电脑比手机多补 1200', gap, 1200);
eq('电脑无门槛', R.pc.threshold, null);
eq('手机门槛 6000', R.phone.threshold, 6000);

console.log('--- 数据完整性 ---');
const kindMap = {};
categories.forEach(c => kindMap[c.key] = T.kindOf(c));
ok('非 noOffer 品牌必须有机型（按大类）', T.DATA.brands.filter(b => !b.noOffer)
   .every(b => T.DATA.offers.some(o => o.brand === b.name && kindMap[o.cat] === (b.scope || 'pc'))),
   '缺机型：' + T.DATA.brands.filter(b => !b.noOffer)
     .filter(b => !T.DATA.offers.some(o => o.brand === b.name && kindMap[o.cat] === (b.scope || 'pc')))
     .map(b => b.name + '/' + (b.scope || 'pc')).join(','));
ok('noOffer 品牌不应有机型', T.DATA.brands.filter(b => b.noOffer)
   .every(b => !T.DATA.offers.some(o => o.brand === b.name && kindMap[o.cat] === (b.scope || 'pc'))));
ok('每个机型都有至少 1 条来源', T.DATA.offers.every(o => o.sources && o.sources.length > 0));
ok('每个机型都有注意项', T.DATA.offers.every(o => o.caution && o.caution.length > 3));
ok('每个机型都有优点', T.DATA.offers.every(o => Array.isArray(o.pros) && o.pros.length >= 2));
ok('每个机型都有缺点', T.DATA.offers.every(o => Array.isArray(o.cons) && o.cons.length >= 1),
   '缺点不足的：' + T.DATA.offers.filter(o => !o.cons || o.cons.length < 1).map(o => o.name).join(','));
ok('优缺点不重复同一句', T.DATA.offers.every(o =>
   o.pros.every(p => !o.cons.some(c => c === p))));
ok('手机不含 upgrade/portable', T.DATA.offers.filter(o => o.cat === 'phone')
   .every(o => !('upgrade' in o) && !('portable' in o)));
ok('手机都有续航与影像分', T.DATA.offers.filter(o => o.cat === 'phone')
   .every(o => typeof o.battery === 'number' && typeof o.camera === 'number'));
ok('超 6000 的手机 caution 提到补贴', T.DATA.offers
   .filter(o => o.cat === 'phone' && o.price > R.phone.threshold)
   .every(o => /国补|补贴/.test(o.caution)),
   '漏提醒的：' + T.DATA.offers.filter(o => o.cat === 'phone' && o.price > R.phone.threshold)
     .filter(o => !/国补|补贴/.test(o.caution)).map(o => o.name).join(','));
ok('设计缺陷表至少 5 条', T.DATA.defects.items.length >= 5);
ok('每条缺陷都有症状和修法', T.DATA.defects.items.every(d => d.symptom && d.fix && d.who));

console.log('--- 渲染不报错 + 关键内容 ---');
const cases = [
  ['thin@5000', T.fullResult(byKey('thin'), 5000, '5000的轻薄本')],
  ['gaming-nobudget', T.fullResult(byKey('gaming'), null, '游戏本')],
  ['desktop', T.fullResult(byKey('desktop'), 8000, '台式整机')],
  ['upgrade', T.fullResult(byKey('upgrade'), null, '想加内存')],
  ['phone@3000', T.fullResult(byKey('phone'), 3000, '3000的手机')],
  ['phone-nobudget', T.fullResult(byKey('phone'), null, '手机')],
  ['budgetOnly', T.budgetOnly(4000)],
  ['unknown', T.unknownCard('随便看看')],
  ['nocover', T.nocoverCard()]
];
for (const [name, out] of cases) {
  if (typeof out !== 'string' || out.length < 200) { fail++; console.log('  FAIL ' + name + ' 输出过短'); continue; }
  if (out.includes('undefined') || out.includes('NaN')) { fail++; console.log('  FAIL ' + name + ' 含 undefined/NaN'); continue; }
  pass++;
}
ok('榜单在结果页最前面', cases[0][1].indexOf('该买哪台') < cases[0][1].indexOf('你这类机器涨了多少'));
ok('结果页含品牌块', cases[0][1].includes('品牌怎么选'));
ok('结果页含陷阱块', cases[0][1].includes('比涨价更贵的坑'));
ok('游戏本页含跨品类对比', cases[1][1].includes('同样一张 RTX 5060'));
ok('电脑页含国补块', cases[0][1].includes('国补怎么算'));
ok('电脑页含设计缺陷块', cases[0][1].includes('电脑的设计缺陷'));
ok('电脑页有升级指南', cases[0][1].includes('能不能加内存'));
ok('手机页不含升级指南（手机内存不可换）', !cases[4][1].includes('一分钟判断你的机器能不能加内存'));
ok('手机页不含电脑设计缺陷块', !cases[4][1].includes('电脑的设计缺陷'));
ok('手机页含国补块', cases[4][1].includes('国补怎么算'));
ok('手机页含 6000 门槛对比', cases[4][1].includes('6000 这条线'));
ok('手机页讲存储占成本', cases[4][1].includes('存储占成本'));
ok('每个电脑侧重都有按钮', Object.keys(T.WEIGHTPOOL.pc).every(k => cases[0][1].includes(T.WEIGHTPOOL.pc[k].label)));
ok('手机侧重按钮不出现在电脑页', !cases[0][1].includes('续航优先'));
ok('电池优先按钮出现在手机页', cases[4][1].includes('续航优先'));
ok('电脑品牌表不含手机品牌', !cases[0][1].includes('iQOO') && cases[0][1].includes('惠普'));
ok('手机品牌表不含电脑专属', cases[4][1].includes('iQOO') && !cases[4][1].includes('机械革命'));

console.log('--- 高亮逻辑（回归） ---');
const withCat = T.chartCategory('thin');
const noCat = T.chartCategory(null);
eq('选中品类时其余置灰', (withCat.match(/row dim/g) || []).length, 3);
eq('未选品类时不置灰任何一条', (noCat.match(/row dim/g) || []).length, 0);
eq('未选品类时最后一根柱不被误高亮', noCat.includes('全网 PC 均价</span><span class="val">+17%'), true);
// 手机图表不该混进电脑的数据
const phChart = T.chartCategory('phone');
ok('手机图表只有手机数据', phChart.includes('小米 17 Ultra') && !phChart.includes('Y9000P'));

console.log('--- 榜单渲染 ---');
const rb = T.rankBlock(byKey('thin'), 5000);
ok('榜单有最优解标记', rb.includes('最优解'));
const headPart = rb.slice(rb.indexOf('id="rank"'), rb.indexOf('id="rankrest"'));
const restPart = rb.slice(rb.indexOf('id="rankrest"'));
ok('首屏只展示 3 台', (headPart.match(/class="rk/g) || []).length === 3,
   '首屏实得 ' + ((headPart.match(/class="rk/g) || []).length) + ' 条');
ok('其余在折叠区', (restPart.match(/class="rk/g) || []).length === 7,
   '折叠区实得 ' + ((restPart.match(/class="rk/g) || []).length) + ' 条');
ok('折叠区标题正确', rb.includes('还有 7 台备选'));
ok('榜单总数不丢机型', (rb.match(/class="rk/g) || []).length === 10);
ok('榜单带价格', rb.includes('¥5,009') || rb.includes('¥3,000'));
ok('榜单无 undefined', !rb.includes('undefined'));
ok('榜单带优缺点速览', rb.includes('pq') && rb.includes('✓'));
const rbOver = T.rankBlock(byKey('thin'), 3200);
ok('超预算会显示差额', rbOver.includes('超预算'));

console.log('--- 详情页 ---');
const dc = [];
T.DATA.offers.forEach(o => { T.detailCard(o.id); dc.push([o, cache['#result'].innerHTML]); });
ok('所有详情页都能渲染', dc.every(([, h]) => h && h.length > 300));
ok('详情页都不含 undefined/NaN', dc.every(([, h]) => !h.includes('undefined') && !h.includes('NaN')),
   '出问题的：' + dc.filter(([, h]) => h.includes('undefined') || h.includes('NaN')).map(([o]) => o.name).join(','));
ok('详情页都有优点段', dc.every(([, h]) => h.includes('优点')));
ok('详情页都有缺点段', dc.every(([, h]) => h.includes('缺点')));
const phoneDetail = dc.find(([o]) => o.cat === 'phone')[1];
ok('手机详情页讲续航影像', phoneDetail.includes('续航') && phoneDetail.includes('影像'));
// 口径改了：续航显示真实 mAh，同时给出池内相对位；性能显示芯片与 GB6 原始跑分
ok('手机详情页给出真实容量与池内相对位',
   /续航<\/dt><dd>[\d,]+ mAh（池内相对位 \d+\/100）/.test(phoneDetail),
   phoneDetail.match(/续航<\/dt><dd>[^<]*/));
ok('手机详情页给出芯片与 GB6 单核/多核',
   /芯片<\/dt><dd>[^<]*GB6 单核 [\d,]+ \/ 多核 [\d,]+/.test(phoneDetail),
   phoneDetail.match(/芯片<\/dt><dd>[^<]*/));
// 同一个指标不许给两个量纲不同的数字（曾经的坑：性能 46 与 GB6 1866 并排出现）
ok('手机详情页不出现「池内归一位」这种混口径说法',
   !phoneDetail.includes('池内归一位'), '仍然混用归一值和原始值');
// 排序依据必须写在详情页上，否则「综合分」会被误读成名次分。
// 注意：默认侧重是「省钱优先」，它没有主维度，此时名次依据就是综合分本身 ——
// 所以要在有主维度的侧重下检查（这里切到性能优先）。
T.setMode('perf');
T.detailCard(phPool[0].id);
const perfDetail = cache['#result'].innerHTML;
ok('详情页说明本次排序依据', perfDetail.includes('名次依据'),
   '性能优先下详情页没有交代名次是按什么排的');
ok('详情页说明名次是分档排的', perfDetail.includes('先按这一项分三档'));
ok('详情页注明综合分仅供参考',
   perfDetail.includes('仅供参考') || perfDetail.includes('不是名次依据'));
T.setMode('value');
ok('手机详情页不讲可加装', !phoneDetail.includes('可加装'));
const pcDetail = dc.find(([o]) => o.cat === 'thin')[1];
ok('电脑详情页讲可加装', pcDetail.includes('可加装'));
// 电脑的性能行必须给出 R23 原始跑分，读者能自己核账；不许再出现「按档位归一」这种旧口径
ok('电脑详情页给出 Cinebench R23 单核/多核',
   /性能<\/dt><dd>\d+ 分<span[^>]*>（Cinebench R23 [\d,]+\/[\d,]+ 实测换算）/.test(pcDetail),
   pcDetail.match(/性能<\/dt><dd>[^<]*/));
ok('页面上不再出现「按档位归一」的旧口径',
   dc.every(([, h]) => !h.includes('按档位归一')),
   '仍在用的：' + dc.filter(([, h]) => h.includes('按档位归一')).map(([o]) => o.name).join(','));
const upgDetail = dc.find(([o]) => o.cat === 'upgrade')[1];
ok('内存/固态详情页的性能行写「不涉及」而不是 0 分',
   upgDetail.includes('不涉及') && !/性能<\/dt><dd>0 /.test(upgDetail),
   upgDetail.match(/性能<\/dt><dd>[^<]*/));

console.log('');
console.log(fail === 0 ? ('全部通过：' + pass + ' 项') : ('通过 ' + pass + '，失败 ' + fail));
process.exit(fail === 0 ? 0 : 1);