'use strict';
/* ============================================================================
   证件照批量处理工具 —— 前端逻辑
   ----------------------------------------------------------------------------
   桥接层：在 pywebview 窗口里走 window.pywebview.api（调 Python）；
           在普通浏览器里走内置假数据。这样调样式阶段完全不用启动 Python，
           改一行存一下刷新即可。
   ========================================================================== */

const $ = (id) => document.getElementById(id);

/* ── 内置假数据（仅浏览器调试用）────────────────────────────────────────── */
const MOCK = (() => {
  const names = ['张伟-证件照.jpg', '李娜 生活照 01.jpg', 'b.jpg', 'c.jpg',
                 '子目录/王芳.jpg', '子目录/赵磊 02.png'];
  let images = names.map((n, i) => ({
    name: n,
    size: 180000 + i * 143000,
    status: ['success', 'pending', 'pending', 'fail', 'pending', 'processing'][i],
  }));
  let sel = 1;
  const SWATCH = 'data:image/svg+xml;utf8,' + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="267">
       <rect width="200" height="267" fill="#dfe3e8"/>
       <ellipse cx="100" cy="105" rx="38" ry="46" fill="#c8a68b"/>
       <ellipse cx="87" cy="98" rx="6" ry="6" fill="#4a4038"/>
       <ellipse cx="113" cy="98" rx="6" ry="6" fill="#4a4038"/>
       <rect x="52" y="150" width="96" height="117" fill="#4a5a7a"/>
     </svg>`);
  return {
    init: () => ({
      sizes: ['默认 190×260', '小1寸 (22×32mm) 260×378', '1寸 (25×35mm) 295×413', '自定义'],
      sizeIndex: 0, w: '190', h: '260', kb: '20',
      out: 'C:/Users/Public/证件照输出',
      ratioOn: false, aspect: 0.75, fine: 1,
      presets: [{ label: '3:4', value: 0.75 }, { label: '2:3', value: 0.6667 },
                { label: '4:5', value: 0.8 }, { label: '9:16', value: 0.5625 },
                { label: '1:1', value: 1 }],
    }),
    list: () => ({ images, selected: sel }),
    preview: () => ({
      orig: SWATCH, proc: SWATCH,
      infoOrig: `${images[sel] ? images[sel].name : ''}  |  1200×1600  |  ${fmt(images[sel] ? images[sel].size : 0)}`,
      infoProc: '190×260  |  18.4 KB',
      ratioText: '横向 ×1.00',
      warn: sel === 3 ? '⚠ 未启用人脸检测，构图可能不准' : '',
    }),
    select: (i) => { sel = i; return true; },
    pick_files: () => { images.push({ name: '新加入.jpg', size: 220000, status: 'pending' }); return true; },
    pick_dir: () => false,
    pick_out_dir: () => true,
    remove_selected: () => { images = images.filter((_, i) => i !== sel); sel = Math.max(0, sel - 1); return true; },
    set_params: () => true,
    set_ratio: () => true,
    run: () => false,
    cancel: () => true,
    window_action: () => {},
  };
})();

/* ── 桥接 ────────────────────────────────────────────────────────────────── */
const bridge = {
  api: null,
  isMock: true,
  async init() {
    if (window.pywebview && window.pywebview.api) {
      this.api = window.pywebview.api;
      this.isMock = false;
    }
  },
  call(name, ...args) {
    const fn = this.api ? this.api[name] : MOCK[name];
    if (typeof fn !== 'function') return Promise.resolve(null);
    try {
      const r = fn.apply(this.isMock ? MOCK : this.api, args);
      return r && typeof r.then === 'function' ? r : Promise.resolve(r);
    } catch (e) {
      return Promise.reject(e);
    }
  },
};

/* ── 工具 ────────────────────────────────────────────────────────────────── */
function fmt(n) {
  if (!n && n !== 0) return '';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}

const ST = {
  pending:    { g: '○', c: 'st-pending' },
  processing: { g: '◎', c: 'st-processing' },
  success:    { g: '✓', c: 'st-success' },
  fail:       { g: '✗', c: 'st-fail' },
  skip:       { g: '–', c: 'st-skip' },
};

/* ── 应用状态 ────────────────────────────────────────────────────────────── */
const S = {
  images: [],
  selected: -1,
  sizes: [],
  sizeChoice: '',
  presets: [],
  aspect: 0.75,
  running: false,
  previewToken: 0,
  ratioJob: null,
};

/* ── 渲染：列表 ──────────────────────────────────────────────────────────── */
function renderList() {
  const box = $('list');
  box.textContent = '';
  $('listCount').textContent = S.images.length + ' 张';

  if (!S.images.length) {
    const d = document.createElement('div');
    d.className = 'empty';
    d.textContent = '还没有照片';
    box.appendChild(d);
  } else {
    S.images.forEach((info, i) => {
      const st = ST[info.status] || ST.pending;
      const row = document.createElement('div');
      row.className = 'item' + (i === S.selected ? ' on' : '');
      row.innerHTML =
        `<span class="mark ${st.c}">${st.g}</span>` +
        `<span class="name"></span>` +
        `<span class="size">${fmt(info.size)}</span>`;
      row.querySelector('.name').textContent = info.name;   // 用 textContent 防注入
      row.title = info.name;
      row.addEventListener('click', () => selectIndex(i));
      box.appendChild(row);
    });
  }
  $('btnRemove').disabled = S.selected < 0 || S.running;
}

/* ── 渲染：导航 ──────────────────────────────────────────────────────────── */
function renderNav() {
  const n = S.images.length;
  const i = S.selected;
  $('navLabel').textContent = (i < 0 || !n) ? '未选图片' : `${i + 1} / ${n}`;
  $('btnPrev').disabled = S.running || i <= 0;
  $('btnNext').disabled = S.running || i < 0 || i >= n - 1;
}

/* ── 渲染：预览 ──────────────────────────────────────────────────────────── */
function setInfo(el, text, cls) {
  el.textContent = text || '';
  el.className = 'info' + (cls ? ' ' + cls : '');
}

async function renderPreview() {
  const token = ++S.previewToken;

  if (S.selected < 0) {
    $('imgOrig').removeAttribute('src');
    $('imgProc').removeAttribute('src');
    setInfo($('infoOrig'), '');
    setInfo($('infoProc'), '');
    return;
  }

  let r;
  try {
    r = await bridge.call('preview');
  } catch (e) {
    if (token !== S.previewToken) return;
    setInfo($('infoProc'), '处理失败：' + (e && e.message ? e.message : e), 'bad');
    return;
  }
  if (token !== S.previewToken || !r) return;   // 已被更新的请求取代

  if (r.orig) $('imgOrig').src = r.orig; else $('imgOrig').removeAttribute('src');
  if (r.proc) $('imgProc').src = r.proc; else $('imgProc').removeAttribute('src');

  setInfo($('infoOrig'), r.infoOrig);
  if (r.error) {
    setInfo($('infoProc'), '处理失败：' + r.error, 'bad');
  } else {
    setInfo($('infoProc'), [r.infoProc, r.warn].filter(Boolean).join('  '),
            r.warn ? 'warn' : '');
  }
  $('ratioOut').textContent = r.ratioText || '横向 ×1.00';
}

/* ── 渲染：参数栏 ────────────────────────────────────────────────────────── */
function renderParams(cfg) {
  S.sizes = cfg.sizes || [];
  S.sizeChoice = S.sizes[cfg.sizeIndex] || S.sizes[0] || '';
  renderSizeDropdown();

  $('inW').value = cfg.w;
  $('inH').value = cfg.h;
  $('inKB').value = cfg.kb;
  $('inOut').value = cfg.out || '';
  $('chkRatio').checked = !!cfg.ratioOn;
  $('ratioRange').value = cfg.fine;
  $('ratioRow').hidden = !cfg.ratioOn;
  $('ratioRow').classList.toggle('off', !cfg.ratioOn);

  S.presets = cfg.presets || [];
  S.aspect = cfg.aspect;
  renderPresets();

  const disabled = !cfg.ratioOn;
  $('ratioRange').disabled = disabled;
  document.querySelectorAll('.preset').forEach((b) => { b.disabled = disabled; });
}

function renderPresets() {
  const box = $('ratioPresets');
  box.textContent = '';
  S.presets.forEach((p) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'preset';
    b.textContent = p.label;
    b.dataset.value = p.value;
    b.setAttribute('aria-pressed', String(Math.abs(p.value - S.aspect) < 0.002));
    b.addEventListener('click', () => {
      S.aspect = parseFloat(b.dataset.value);
      renderPresets();
      pushRatio();
    });
    box.appendChild(b);
  });
}

/* ── 自绘下拉（原生 select 的弹出列表无法用 CSS 控制）────────────────────── */
function renderSizeDropdown() {
  $('sizeValue').textContent = S.sizeChoice || '—';
  const list = $('sizeList');
  list.textContent = '';
  S.sizes.forEach((s) => {
    const it = document.createElement('div');
    it.className = 'dd-item';
    it.setAttribute('role', 'option');
    it.setAttribute('aria-selected', String(s === S.sizeChoice));
    it.textContent = s;
    it.addEventListener('click', (e) => { e.stopPropagation(); pickSize(s); });
    list.appendChild(it);
  });
}

function openSizeDd(open) {
  $('sizeList').hidden = !open;
  $('sizeDd').setAttribute('aria-expanded', String(!!open));
}

async function pickSize(s) {
  S.sizeChoice = s;
  renderSizeDropdown();
  openSizeDd(false);
  await pushParams();
}

/* ── 渲染：进度与状态 ────────────────────────────────────────────────────── */
function renderProgress(p) {
  S.running = !!p.running;
  $('bar').style.width = (Math.max(0, Math.min(1, p.progress || 0)) * 100) + '%';
  $('status').textContent = p.status || '就绪';
  $('btnRun').disabled = S.running;
  $('btnRun').textContent = S.running ? '处 理 中…' : '开 始 处 理';
  $('btnRun').classList.toggle('btn-primary', !S.running);
  document.querySelectorAll('.input').forEach((el) => { el.disabled = S.running; });
  $('sizeDd').classList.toggle('disabled', S.running);
  if (S.running) openSizeDd(false);
  $('chkRatio').disabled = S.running;
  $('ratioRange').disabled = S.running || !$('chkRatio').checked;
  document.querySelectorAll('.preset').forEach((b) => {
    b.disabled = S.running || !$('chkRatio').checked;
  });
  renderList();
  renderNav();
}

/* Python 侧通过 evaluate_js 调这两个函数推进度。
   ⚠️ 名字必须与脚本内的函数声明区分开 —— 顶层 function 声明也会挂到 window 上，
   用 applyList 这个名字赋值会覆盖函数声明、让箭头函数递归调用自己。 */
window.pvApplyProgress = (p) => { try { renderProgress(p || {}); } catch (e) { /* 忽略 */ } };
window.pvApplyList = (r) => { try { applyList(r); } catch (e) { /* 忽略 */ } };

/* 批量处理时逐张更新状态。整表重建是 O(N²)，这里只改那一行的符号 */
window.pvSetItemStatus = (i, status) => {
  try {
    if (S.images[i]) S.images[i].status = status;
    const row = $('list').children[i];
    if (!row || !row.classList.contains('item')) return;
    const st = ST[status] || ST.pending;
    const mark = row.querySelector('.mark');
    if (mark) { mark.className = 'mark ' + st.c; mark.textContent = st.g; }
  } catch (e) { /* 忽略 */ }
};

/* 输出文件夹在选择后回填 */
window.pvSetOut = (p) => { try { $('inOut').value = p || ''; } catch (e) { /* 忽略 */ } };

/* 应用内提示框。pywebview 这一版没有 alert API，自己画一个反而和设计更一致 */
window.pvAlert = (title, message) => {
  try {
    $('modalTitle').textContent = title || '提示';
    $('modalMsg').textContent = message || '';
    $('modal').hidden = false;
    $('modalOk').focus();
  } catch (e) { /* 忽略 */ }
};

/* ── 动作 ────────────────────────────────────────────────────────────────── */
function applyList(r) {
  if (!r) return;
  S.images = r.images || [];
  S.selected = (typeof r.selected === 'number') ? r.selected : -1;
  renderList();
  renderNav();
}

async function refreshList() { applyList(await bridge.call('list')); }

async function selectIndex(i) {
  if (S.running) return;
  // 切图不做防抖：连按 ◀▶ 必须立刻响应
  if (S.ratioJob !== null) { clearTimeout(S.ratioJob); S.ratioJob = null; }
  await bridge.call('select', i);
  await refreshList();
  renderPreview();
}

async function pushParams() {
  await bridge.call('set_params', {
    size: S.sizeChoice,
    sizeIndex: S.sizes.indexOf(S.sizeChoice),
    w: $('inW').value,
    h: $('inH').value,
    kb: $('inKB').value,
  });
  renderPreview();
}

async function pushRatio() {
  await bridge.call('set_ratio', {
    on: $('chkRatio').checked,
    aspect: S.aspect,
    fine: parseFloat($('ratioRange').value),
  });
}

/* 滑块拖动防抖：每格都算一次 stretch+detect+process 会明显卡 */
function scheduleRatioPreview() {
  if (S.ratioJob !== null) clearTimeout(S.ratioJob);
  S.ratioJob = setTimeout(async () => {
    S.ratioJob = null;
    await pushRatio();
    renderPreview();
  }, 180);
}

/* ── 事件绑定 ────────────────────────────────────────────────────────────── */
function wire() {
  // 窗口按钮
  $('btnMin').addEventListener('click', () => bridge.call('window_action', 'min'));
  $('btnMax').addEventListener('click', () => bridge.call('window_action', 'max'));
  $('btnClose').addEventListener('click', () => bridge.call('window_action', 'close'));

  // 导入
  $('btnPickFiles').addEventListener('click', async () => {
    if (S.running) return;
    if (await bridge.call('pick_files')) { await refreshList(); renderPreview(); }
  });
  $('btnPickDir').addEventListener('click', async () => {
    if (S.running) return;
    if (await bridge.call('pick_dir')) { await refreshList(); renderPreview(); }
  });
  $('btnRemove').addEventListener('click', async () => {
    if (S.running) return;
    if (await bridge.call('remove_selected')) { await refreshList(); renderPreview(); }
  });

  // 输出文件夹
  $('btnOutDir').addEventListener('click', async () => {
    if (S.running) return;
    await bridge.call('pick_out_dir');
  });

  // 导航
  $('btnPrev').addEventListener('click', () => selectIndex(S.selected - 1));
  $('btnNext').addEventListener('click', () => selectIndex(S.selected + 1));

  // 参数
  $('sizeDd').addEventListener('click', () => {
    if (S.running) return;
    openSizeDd($('sizeList').hidden);
  });
  $('sizeDd').addEventListener('keydown', (e) => {
    if (S.running) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      openSizeDd($('sizeList').hidden);
    } else if (e.key === 'Escape') {
      openSizeDd(false);
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if ($('sizeList').hidden) { openSizeDd(true); return; }
      const i = S.sizes.indexOf(S.sizeChoice);
      const next = e.key === 'ArrowDown' ? Math.min(S.sizes.length - 1, i + 1)
                                         : Math.max(0, i - 1);
      if (next !== i) pickSize(S.sizes[next]);
    }
  });
  // 点击别处收起
  document.addEventListener('click', (e) => {
    if (!$('sizeDd').contains(e.target)) openSizeDd(false);
  });

  ['inW', 'inH', 'inKB'].forEach((id) => {
    $(id).addEventListener('input', () => { pushParams(); });
  });

  // 比例校正
  $('chkRatio').addEventListener('change', async () => {
    $('ratioRow').hidden = !$('chkRatio').checked;
    $('ratioRow').classList.toggle('off', !$('chkRatio').checked);
    const off = !$('chkRatio').checked;
    $('ratioRange').disabled = off;
    document.querySelectorAll('.preset').forEach((b) => { b.disabled = off; });
    await pushRatio();
    renderPreview();
  });
  $('ratioRange').addEventListener('input', () => {
    const base = parseFloat($('ratioRange').value);
    $('ratioOut').textContent = '横向 ×' + base.toFixed(2) + '（微调）';
    scheduleRatioPreview();
  });

  // 开始处理
  $('btnRun').addEventListener('click', async () => {
    if (S.running) return;
    await bridge.call('run');
  });

  // 提示框
  $('modalOk').addEventListener('click', () => { $('modal').hidden = true; });

  // 键盘
  window.addEventListener('keydown', (e) => {
    // 提示框打开时只处理关闭键，别让方向键穿到底下的列表
    if (!$('modal').hidden) {
      if (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        $('modal').hidden = true;
      }
      return;
    }
    if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    // 焦点在下拉里时方向键归它管，别穿透到底下的图片列表
    if ($('sizeDd').contains(document.activeElement) || !$('sizeList').hidden) return;
    if (e.key === 'ArrowLeft')  { e.preventDefault(); selectIndex(S.selected - 1); }
    if (e.key === 'ArrowRight') { e.preventDefault(); selectIndex(S.selected + 1); }
  });
}

/* ── 拖拽视觉反馈 ────────────────────────────────────────────────────────── */
function wireDragFeedback() {
  const zone = $('dropzone');
  let depth = 0;

  // 不 preventDefault 的话 WebView 会尝试直接打开拖入的文件
  ['dragenter', 'dragover'].forEach((ev) => {
    document.addEventListener(ev, (e) => { e.preventDefault(); });
  });
  document.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    zone.classList.remove('over');
  });
  document.addEventListener('dragleave', (e) => {
    if (e.relatedTarget) return;
    depth = 0;
    zone.classList.remove('over');
  });

  zone.addEventListener('dragenter', (e) => {
    e.preventDefault();
    depth += 1;
    zone.classList.add('over');
  });
  zone.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (depth === 0) zone.classList.remove('over');
  });
  zone.addEventListener('drop', () => {
    depth = 0;
    zone.classList.remove('over');
    // 真实路径由 pywebview 在 Python 侧通过 pywebviewFullPath 注入，
    // 这里只负责视觉反馈；刷新列表由 Python 侧的导入逻辑触发。
    setTimeout(async () => { await refreshList(); renderPreview(); }, 120);
  });
}

/* ── 启动 ────────────────────────────────────────────────────────────────── */
async function boot() {
  wire();
  wireDragFeedback();

  // pywebview.api 在 onload 时尚未就绪，必须等 pywebviewready
  if (!(window.pywebview && window.pywebview.api)) {
    await new Promise((res) => {
      let done = false;
      const fin = () => { if (!done) { done = true; res(); } };
      window.addEventListener('pywebviewready', fin, { once: true });
      setTimeout(fin, 800);          // 浏览器里没有这个事件，超时后按假数据跑
    });
    await bridge.init();
  } else {
    await bridge.init();
  }

  const cfg = await bridge.call('init');
  if (cfg) renderParams(cfg);

  await refreshList();
  renderPreview();
  renderProgress({ running: false, progress: 0, status: '就绪' });
}

document.addEventListener('DOMContentLoaded', boot);
