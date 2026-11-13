// 布局探针：用无头 Edge 打开页面，注入脚本点选侧重、量真实几何。
// 模型读不了图，所以不截图，只取数字：横向溢出 / 文字截断 / 最小字号 / 整页高度。
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const EDGE = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
].find(p => fs.existsSync(p));
if (!EDGE) { console.error('找不到 Edge，跳过布局检查'); process.exit(0); }

const html = path.resolve(__dirname, '..', '电脑涨价趋势.html');

// 探针单独写在一段 <script> 里，避免把 JS 塞进模板字符串时被转义搞坏
const PROBE = `
(function(){
  var out = { views: [] };
  function measure(tag){
    var de = document.documentElement, vw = de.clientWidth;
    var overflow = 0, clipped = 0, minFont = 99;
    var all = document.querySelectorAll('*');
    for (var i = 0; i < all.length; i++){
      var el = all[i], r = el.getBoundingClientRect();
      if (r.width > 0 && (r.right > vw + 1.5 || r.left < -1.5)) overflow++;
      if (!el.children.length && el.textContent.trim()){
        var s = getComputedStyle(el), f = parseFloat(s.fontSize);
        if (f < minFont) minFont = f;
        if (el.scrollWidth > el.clientWidth + 2 && s.overflow !== 'visible') clipped++;
      }
    }
    out.views.push({ tag: tag, vw: vw, docH: Math.round(de.scrollHeight),
                     overflow: overflow, clipped: clipped, minFont: minFont });
  }
  function clickAll(prefix){
    var btns = document.querySelectorAll('#weigh .wbtn');
    for (var i = 0; i < btns.length; i++){
      try { btns[i].click(); measure(prefix + i); } catch(e){ out.err = String(e); }
    }
  }
  try { run('5000 的轻薄本'); measure('thin0'); clickAll('thin'); } catch(e){ out.err1 = String(e); }
  try { run('5000 的手机'); measure('phone0'); clickAll('phone'); } catch(e){ out.err2 = String(e); }
  try { run('想加内存'); measure('upg'); } catch(e){ out.err3 = String(e); }
  document.title = 'PROBE::' + JSON.stringify(out);
})();
`;

const raw = fs.readFileSync(html, 'utf8');
const injected = raw.replace('</body>', '<script>' + PROBE + '</' + 'script></body>');
const tmpHtml = path.join(path.dirname(html), '.probe-tmp.html');
fs.writeFileSync(tmpHtml, injected, 'utf8');

const tmpUser = fs.mkdtempSync(path.join(os.tmpdir(), 'edge-'));
let dom;
try {
  dom = execFileSync(EDGE, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--hide-scrollbars',
    '--window-size=430,3000', '--virtual-time-budget=5000',
    '--user-data-dir=' + tmpUser, '--dump-dom',
    'file:///' + tmpHtml.replace(/\\/g, '/'),
  ], { maxBuffer: 1 << 26 }).toString();
} finally {
  try { fs.unlinkSync(tmpHtml); } catch (e) { }
  try { fs.rmSync(tmpUser, { recursive: true, force: true }); } catch (e) { }
}

const m = dom.match(/<title>PROBE::([\s\S]*?)<\/title>/);
if (!m) { console.error('没拿到探针结果（页面可能没跑起来）'); process.exit(1); }
const res = JSON.parse(m[1]
  .replace(/&quot;/g, '"').replace(/&amp;/g, '&')
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>'));

const errs = ['err1', 'err2', 'err3', 'err'].filter(k => res[k]);
if (errs.length) console.log('页面报错：' + errs.map(k => k + '=' + res[k]).join(' | '));

console.log('视口 %d px', res.views[0].vw);
let bad = 0;
for (const v of res.views) {
  const flag = (v.overflow > 2 || v.clipped > 0 || v.minFont < 10) ? '  <-- 有问题' : '';
  if (flag) bad++;
  console.log('  %s  高 %dpx  横向溢出 %d  文字截断 %d  最小字号 %s%s',
    String(v.tag).padEnd(8), v.docH, v.overflow, v.clipped, v.minFont, flag);
}
process.exit(bad ? 1 : 0);