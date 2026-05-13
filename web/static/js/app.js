/* ── State ─────────────────────────────────────────────────────────────── */
const state = {
  projectId: null,
  projectName: '',
  boards: [],
  currentBoard: '',
  setupRows: [],
  instrRows: [],
  batchComps: {},
  setupDone: {},
  instrDone: {},
  feederMap: {},
  summary: {},
  tapeLimits: { 8: 0, 12: 10, 16: 1, 24: 4, 32: 1, 44: 2 },
  warehouse: [],
  visualSlots: [],
  visualStation: 'ЧИПШУТЕР',
  activeTab: 'dashboard',
};

/* ── DOM helpers ───────────────────────────────────────────────────────── */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function showLoader(v) { $('#loader').style.display = v ? 'flex' : 'none'; }

function toast(msg, type = 'info', duration = 3500) {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  $('#toast-container').appendChild(el);
  setTimeout(() => el.remove(), duration);
}

function setStatus(msg, type = '') {
  const el = $('#header-status');
  el.textContent = msg;
  el.className = `header-status ${type}`;
}

/* ── API wrapper ───────────────────────────────────────────────────────── */
async function api(method, path, body, isFormData = false) {
  const opts = { method };
  if (body) {
    if (isFormData) {
      opts.body = body;
    } else {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/* ── Navigation ────────────────────────────────────────────────────────── */
function activateTab(tabId) {
  state.activeTab = tabId;
  $$('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tabId));
  $$('.tab-panel').forEach(p => p.style.display = p.id === `tab-${tabId}` ? '' : 'none');
  if (tabId === 'warehouse') loadWarehouse();
  if (tabId === 'visual')    renderVisualMap();
  if (tabId === 'setup' || tabId === 'instructions') renderTables();
  if (tabId === 'dashboard') renderDashboard();
}

/* ── Init ──────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', async () => {
  // Wire nav buttons
  $$('.nav-btn[data-tab]').forEach(btn => {
    btn.addEventListener('click', () => activateTab(btn.dataset.tab));
  });

  // Tape limits
  await loadTapeLimits();

  // Check for existing projects
  const projects = await api('GET', '/api/bom/projects').catch(() => []);
  if (projects.length) {
    await openProject(projects[0].id, projects[0].name);
  }

  // Upload BOM
  const uploadZone = $('#upload-zone');
  const fileInput  = $('#bom-file-input');

  uploadZone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', e => { if (e.target.files[0]) uploadBOM(e.target.files[0]); });
  uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('drag-over'); });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
  uploadZone.addEventListener('drop', e => {
    e.preventDefault();
    uploadZone.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) uploadBOM(e.dataTransfer.files[0]);
  });

  // Calculate button
  $('#btn-calculate').addEventListener('click', calculate);
  // Next batch
  $('#btn-next-batch').addEventListener('click', nextBatch);
  // Save tape limits
  $('#btn-save-limits').addEventListener('click', saveTapeLimits);
  // Board selector
  $('#board-select').addEventListener('change', e => {
    state.currentBoard = e.target.value;
    loadBoardData();
  });
  // Station selector (visual map)
  $$('.station-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      state.visualStation = btn.dataset.station;
      $$('.station-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderVisualMap();
    });
  });

  // Warehouse upload
  const whUploadZone  = $('#wh-upload-zone');
  const whFileInput   = $('#wh-file-input');
  whUploadZone.addEventListener('click', () => whFileInput.click());
  whFileInput.addEventListener('change', e => { if (e.target.files[0]) uploadWarehouseExcel(e.target.files[0]); });
  whUploadZone.addEventListener('dragover', e => { e.preventDefault(); whUploadZone.classList.add('drag-over'); });
  whUploadZone.addEventListener('dragleave', () => whUploadZone.classList.remove('drag-over'));
  whUploadZone.addEventListener('drop', e => {
    e.preventDefault();
    whUploadZone.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) uploadWarehouseExcel(e.dataTransfer.files[0]);
  });

  $('#btn-add-wh').addEventListener('click', addWarehouseItem);
  $('#wh-search').addEventListener('input', renderWarehouseTable);

  activateTab('dashboard');
  loadWarehouse();
});

/* ── BOM Upload ────────────────────────────────────────────────────────── */
async function uploadBOM(file) {
  showLoader(true);
  try {
    const fd = new FormData();
    fd.append('file', file);
    const data = await api('POST', '/api/bom/upload', fd, true);
    toast(`✅ BOM загружен: ${data.project_name} (${data.total_rows} строк)`, 'success');
    await openProject(data.project_id, data.project_name, data.sheets);
  } catch (e) {
    toast(`❌ ${e.message}`, 'error', 6000);
  } finally {
    showLoader(false);
  }
}

async function openProject(id, name, sheets) {
  state.projectId = id;
  state.projectName = name;
  $('#project-name').textContent = name;

  if (!sheets) {
    const data = await api('GET', `/api/pnp/boards/${id}`).catch(() => ({ boards: [] }));
    state.boards = Array.isArray(data) ? data : [];
  } else {
    state.boards = sheets.map(s => ({ name: s, completed: false }));
  }
  rebuildBoardSelect();
  setStatus(`Проект: ${name}`, 'ok');
  renderDashboard();
}

function rebuildBoardSelect() {
  const sel = $('#board-select');
  sel.innerHTML = state.boards.map(b =>
    `<option value="${esc(b.name)}">${esc(b.name)}${b.completed ? ' ✓' : ''}</option>`
  ).join('');
  if (state.boards.length) {
    state.currentBoard = state.boards[0].name;
    sel.value = state.currentBoard;
  }
}

/* ── Tape limits ───────────────────────────────────────────────────────── */
async function loadTapeLimits() {
  try {
    const data = await api('GET', '/api/pnp/tape_limits');
    state.tapeLimits = {};
    for (const [k, v] of Object.entries(data)) state.tapeLimits[parseInt(k)] = v;
    renderTapeLimitsUI();
  } catch {}
}

function renderTapeLimitsUI() {
  const container = $('#tape-limits-inputs');
  if (!container) return;
  container.innerHTML = [8, 12, 16, 24, 32, 44].map(sz => `
    <div class="tape-item">
      <label>${sz}мм</label>
      <input type="number" min="0" max="100" value="${state.tapeLimits[sz] ?? 0}"
             id="tl-${sz}" class="tape-limit-input">
    </div>
  `).join('');
}

async function saveTapeLimits() {
  const body = {};
  [8, 12, 16, 24, 32, 44].forEach(sz => {
    const el = document.getElementById(`tl-${sz}`);
    if (el) body[sz] = parseInt(el.value) || 0;
  });
  try {
    await api('POST', '/api/pnp/tape_limits', body);
    state.tapeLimits = body;
    toast('✅ Лимиты лент сохранены', 'success');
  } catch (e) {
    toast(`❌ ${e.message}`, 'error');
  }
}

/* ── Calculate ─────────────────────────────────────────────────────────── */
async function calculate() {
  if (!state.projectId || !state.currentBoard) { toast('Загрузите BOM и выберите плату', 'warning'); return; }
  showLoader(true);
  try {
    await saveTapeLimitsQuiet();
    const data = await api('POST', `/api/pnp/calculate/${state.projectId}`, {
      board: state.currentBoard, auto_assign: true,
    });
    applyCalcResult(data);
    toast(`✅ Рассчитано: ${state.currentBoard}`, 'success');
  } catch (e) {
    toast(`❌ ${e.message}`, 'error', 6000);
  } finally {
    showLoader(false);
  }
}

async function saveTapeLimitsQuiet() {
  const body = {};
  [8, 12, 16, 24, 32, 44].forEach(sz => {
    const el = document.getElementById(`tl-${sz}`);
    if (el) body[sz] = parseInt(el.value) || 0;
  });
  try { await api('POST', '/api/pnp/tape_limits', body); } catch {}
}

function applyCalcResult(data) {
  state.setupRows   = data.setup_rows   || [];
  state.instrRows   = data.instr_rows   || [];
  state.batchComps  = data.batch_comps  || {};
  state.feederMap   = data.feeder_map   || {};
  state.summary     = data.summary      || {};
  renderDashboard();
  renderTables();
  if (state.activeTab === 'visual') renderVisualMap();
  if (data.warnings && data.warnings.length) {
    data.warnings.forEach(w => toast(`⚠️ ${w}`, 'warning', 8000));
  }
}

async function loadBoardData() {
  if (!state.projectId || !state.currentBoard) return;
  showLoader(true);
  try {
    const data = await api('POST', `/api/pnp/calculate/${state.projectId}`, {
      board: state.currentBoard, auto_assign: false,
    });
    applyCalcResult(data);
  } catch {}
  await loadProgress();
  showLoader(false);
}

async function loadProgress() {
  if (!state.projectId) return;
  try {
    const data = await api('GET', `/api/pnp/progress/${state.projectId}`);
    state.setupDone = data.setup || {};
    state.instrDone = data.instr || {};
    renderTables();
    renderDashboard();
  } catch {}
}

/* ── Next batch ────────────────────────────────────────────────────────── */
async function nextBatch() {
  if (!state.projectId || !state.currentBoard) return;
  showLoader(true);
  try {
    const data = await api('POST', `/api/pnp/next_batch/${state.projectId}`, {
      board: state.currentBoard,
    });
    if (data.next_board) {
      state.currentBoard = data.next_board;
      $('#board-select').value = data.next_board;
      if (data.board_data) applyCalcResult(data.board_data);
      await loadProgress();
      toast(
        `➡️ Переход на: ${data.next_board} (${data.shared_components} общих деталей). ` +
        `Снято: ${data.removed_from_machine.length} компонентов.`,
        'info', 6000,
      );
    } else {
      toast(data.message || 'Нет незавершённых плат', 'info');
    }
  } catch (e) {
    toast(`❌ ${e.message}`, 'error');
  } finally {
    showLoader(false);
  }
}

/* ── Dashboard ─────────────────────────────────────────────────────────── */
function renderDashboard() {
  const s = state.summary;
  setStatCard('stat-total',  s.total_parts   || 0);
  setStatCard('stat-unique', s.unique_parts  || 0);
  setStatCard('stat-cs',     s.chipshooter_tapes || 0);
  setStatCard('stat-batches',s.chipshooter_batches || 0);

  const warn = [];
  if ((s.cs_batch_count || 0) > 1) warn.push(`Чипшутер: ${s.cs_batch_count} захода`);
  if ((s.pv_batch_count || 0) > 1) warn.push(`Павук: ${s.pv_batch_count} захода`);
  const warnEl = $('#recharge-warning');
  if (warnEl) {
    if (warn.length) {
      warnEl.textContent = '⚠️ Требуется перезаправка: ' + warn.join(', ');
      warnEl.className = 'badge badge-warn';
    } else if (s.total_parts) {
      warnEl.textContent = '✅ Влазит в один заход';
      warnEl.className = 'badge badge-done';
    } else {
      warnEl.textContent = '';
    }
  }

  // Progress bars by batch
  const pbContainer = $('#progress-bars');
  if (!pbContainer) return;
  const board = state.currentBoard;
  const instrDoneForBoard = new Set(state.instrDone[board] || []);
  const batches = Object.entries(state.batchComps);
  if (!batches.length) { pbContainer.innerHTML = '<p style="color:var(--text-dim)">Загрузите BOM и нажмите "Рассчитать"</p>'; return; }

  pbContainer.innerHTML = batches.map(([bName, comps]) => {
    const done = comps.filter(c => instrDoneForBoard.has(c)).length;
    const pct  = comps.length ? Math.round(done / comps.length * 100) : 0;
    const cls  = pct === 100 ? 'green' : '';
    return `
      <div class="progress-bar-wrap">
        <div class="progress-bar-label">
          <span>${esc(bName)}</span>
          <span>${done} / ${comps.length} (${pct}%)</span>
        </div>
        <div class="progress-bar-track">
          <div class="progress-bar-fill ${cls}" style="width:${pct}%"></div>
        </div>
      </div>`;
  }).join('');
}

function setStatCard(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

/* ── Tables ────────────────────────────────────────────────────────────── */
function renderTables() {
  renderSetupTable();
  renderInstrTable();
}

function renderSetupTable() {
  const board = state.currentBoard;
  const done  = new Set(state.setupDone[board] || []);
  const tbody = $('#setup-tbody');
  if (!tbody) return;

  tbody.innerHTML = state.setupRows.map((r, i) => {
    const isDone  = done.has(r.name);
    const isWarn  = !isDone && parseInt(r.batch?.replace(/\D/g,'')) > 1;
    const cls     = isDone ? 'done-row' : isWarn ? 'warn-row' : '';
    const stBadge = r.station === 'ЧИПШУТЕР' ? 'badge-cs' : 'badge-pv';
    return `<tr class="${cls}" data-comp="${esc(r.name)}" onclick="toggleRow(this,'setup')">
      <td><span class="badge ${stBadge}">${esc(r.station)}</span></td>
      <td>${esc(r.batch)}</td>
      <td><code>${esc(r.slot)}</code></td>
      <td>${esc(r.name)}</td>
      <td>${r.quantity}</td>
    </tr>`;
  }).join('');
}

function renderInstrTable() {
  const board = state.currentBoard;
  const done  = new Set(state.instrDone[board] || []);
  const tbody = $('#instr-tbody');
  if (!tbody) return;

  tbody.innerHTML = state.instrRows.map((r) => {
    const isDone  = done.has(r.name);
    const cls     = isDone ? 'done-row' : '';
    const stBadge = r.priority.includes('ЧИПШУТЕР') ? 'badge-cs' : 'badge-pv';
    return `<tr class="${cls}" data-comp="${esc(r.name)}" onclick="toggleRow(this,'instr')">
      <td><span class="badge ${stBadge}">${esc(r.priority)}</span></td>
      <td>${esc(r.name)}</td>
      <td><code>${esc(r.slot)}</code></td>
      <td>${r.quantity}</td>
      <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.designators)}</td>
    </tr>`;
  }).join('');
}

async function toggleRow(tr, table) {
  if (!state.projectId || !state.currentBoard) return;
  const comp   = tr.dataset.comp;
  const board  = state.currentBoard;
  const doneMap = table === 'setup' ? state.setupDone : state.instrDone;
  const done   = new Set(doneMap[board] || []);
  const markDone = !done.has(comp);

  // Optimistic UI
  if (markDone) done.add(comp); else done.delete(comp);
  doneMap[board] = [...done];
  table === 'setup' ? renderSetupTable() : renderInstrTable();
  renderDashboard();

  const endpoint = table === 'setup' ? 'toggle_setup' : 'toggle_instr';
  try {
    await api('POST', `/api/pnp/progress/${state.projectId}/${endpoint}`, {
      board, comps: [comp], mark_done: markDone,
    });
  } catch (e) {
    toast(`❌ ${e.message}`, 'error');
    // revert
    if (markDone) done.delete(comp); else done.add(comp);
    doneMap[board] = [...done];
    table === 'setup' ? renderSetupTable() : renderInstrTable();
  }
}

/* ── Visual Map ────────────────────────────────────────────────────────── */
async function renderVisualMap() {
  if (!state.projectId) return;
  const container = $('#visual-slots-left');
  const containerR = $('#visual-slots-right');
  if (!container) return;

  try {
    const data = await api('GET',
      `/api/pnp/visual_map/${state.projectId}?board=${encodeURIComponent(state.currentBoard)}&station=${encodeURIComponent(state.visualStation)}`
    );
    state.visualSlots = data.slots || [];
  } catch { return; }

  const slots = state.visualSlots;
  const isCS  = state.visualStation === 'ЧИПШУТЕР';

  container.innerHTML = '';
  if (containerR) containerR.innerHTML = '';

  if (isCS) {
    // L column and R column
    slots.forEach(s => {
      const side = s.slot.startsWith('L') ? container : containerR;
      side.insertAdjacentHTML('beforeend', slotHtml(s));
    });
  } else {
    slots.forEach(s => {
      const side = s.slot.startsWith('L') ? container : (containerR || container);
      side.insertAdjacentHTML('beforeend', slotHtml(s));
    });
  }

  // Side table
  const occupied = slots.filter(s => s.comp && s.status !== 'empty');
  const sideTbody = $('#visual-side-tbody');
  if (sideTbody) {
    sideTbody.innerHTML = occupied.map(s =>
      `<tr><td><code>${esc(s.slot)}</code></td><td>${esc(s.comp)}</td></tr>`
    ).join('');
  }
}

function slotHtml(s) {
  return `<div class="slot-item" data-status="${s.status}" title="${esc(s.comp)}">
    <span class="slot-id">${esc(s.slot)}</span>
    <span class="slot-comp">${s.comp ? esc(s.comp) : '—'}</span>
  </div>`;
}

/* ── Warehouse ─────────────────────────────────────────────────────────── */
async function loadWarehouse() {
  try {
    state.warehouse = await api('GET', '/api/warehouse/');
    renderWarehouseTable();
    renderWarehouseGrid();
  } catch {}
}

function renderWarehouseTable() {
  const tbody  = $('#wh-tbody');
  if (!tbody) return;
  const q = ($('#wh-search')?.value || '').toLowerCase();
  const rows = state.warehouse.filter(r => !q || r.name.toLowerCase().includes(q));

  tbody.innerHTML = rows.map(r => {
    const qtyClass = r.quantity > 10 ? 'ok' : r.quantity > 0 ? 'low' : 'out';
    return `<tr>
      <td>${esc(r.position_num)}</td>
      <td>${esc(r.name)}</td>
      <td>${r.tape_width}мм</td>
      <td>${esc(r.coil_id)}</td>
      <td><span class="wh-card-qty ${qtyClass}" style="font-size:13px">${r.quantity}</span></td>
      <td>
        <button class="btn btn-ghost btn-sm" onclick="editWhQty(${r.id},${r.quantity})">✏️</button>
        <button class="btn btn-danger  btn-sm" onclick="deleteWhItem(${r.id})">🗑️</button>
      </td>
    </tr>`;
  }).join('');
}

function renderWarehouseGrid() {
  const grid = $('#wh-grid');
  if (!grid) return;
  // Group by name
  const grouped = {};
  for (const r of state.warehouse) {
    if (!grouped[r.name]) grouped[r.name] = { pos: r.position_num, width: r.tape_width, coils: [], total: 0 };
    grouped[r.name].coils.push(`${r.coil_id}: ${r.quantity}`);
    grouped[r.name].total += r.quantity;
  }
  const entries = Object.entries(grouped);
  if (!entries.length) { grid.innerHTML = '<p style="color:var(--text-dim)">Склад пуст</p>'; return; }

  grid.innerHTML = entries.map(([name, g]) => {
    const qc = g.total > 10 ? 'ok' : g.total > 0 ? 'low' : 'out';
    return `<div class="wh-card">
      <div class="wh-card-name">${esc(name)}</div>
      <div class="wh-card-pos">Поз: ${esc(g.pos)} · ${g.width}мм</div>
      <div class="wh-card-qty ${qc}">${g.total}</div>
      <div class="wh-card-coils">${g.coils.join(' · ')}</div>
    </div>`;
  }).join('');
}

async function addWarehouseItem() {
  const name = $('#wh-name').value.trim();
  const pos  = $('#wh-pos').value.trim();
  const width = parseInt($('#wh-width').value) || 8;
  const qty  = parseInt($('#wh-qty').value) || 0;
  if (!name) { toast('Укажите название', 'warning'); return; }
  if (qty <= 0) { toast('Количество должно быть > 0', 'warning'); return; }
  try {
    await api('POST', '/api/warehouse/', { name, position_num: pos, tape_width: width, quantity: qty });
    toast('✅ Компонент добавлен', 'success');
    $('#wh-name').value = ''; $('#wh-pos').value = ''; $('#wh-qty').value = '';
    await loadWarehouse();
  } catch (e) { toast(`❌ ${e.message}`, 'error'); }
}

async function editWhQty(id, currentQty) {
  const newQty = prompt('Новое количество:', currentQty);
  if (newQty === null) return;
  const qty = parseInt(newQty);
  if (isNaN(qty) || qty < 0) { toast('Неверное количество', 'warning'); return; }
  try {
    await api('PATCH', `/api/warehouse/${id}`, { quantity: qty });
    toast('✅ Количество обновлено', 'success');
    await loadWarehouse();
  } catch (e) { toast(`❌ ${e.message}`, 'error'); }
}

async function deleteWhItem(id) {
  if (!confirm('Удалить позицию?')) return;
  try {
    await api('DELETE', `/api/warehouse/${id}`);
    await loadWarehouse();
  } catch (e) { toast(`❌ ${e.message}`, 'error'); }
}

async function uploadWarehouseExcel(file) {
  showLoader(true);
  try {
    const fd = new FormData();
    fd.append('file', file);
    const data = await api('POST', '/api/warehouse/upload', fd, true);
    toast(`✅ Склад обновлён: ${data.total} позиций`, 'success');
    await loadWarehouse();
  } catch (e) { toast(`❌ ${e.message}`, 'error', 6000); }
  finally { showLoader(false); }
}

/* ── Util ──────────────────────────────────────────────────────────────── */
function esc(str) {
  return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
