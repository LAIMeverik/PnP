const state = { data: null };

async function api(path, method = 'GET', body = null) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) {
    opts.body = body;
  } else if (body != null) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const payload = await res.json();
  if (!res.ok) throw new Error(payload.detail || 'API error');
  return payload;
}

function renderTable(el, rows, doneSet, key) {
  if (!rows?.length) {
    el.innerHTML = '<tr><td>Нет данных</td></tr>';
    return;
  }
  const headers = Object.keys(rows[0]);
  const head = `<tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr>`;
  const body = rows.map(r => {
    const done = doneSet?.has(r[key]) ? 'done' : '';
    return `<tr class="${done}" data-name="${(r[key] || '').replace(/"/g, '&quot;')}">${headers.map(h => `<td>${r[h] ?? ''}</td>`).join('')}</tr>`;
  }).join('');
  el.innerHTML = head + body;
}

function render() {
  const d = state.data;
  if (!d) return;

  const status = document.getElementById('status');
  status.textContent = [d.summary?.status, d.summary?.recharge_warning, ...(d.summary?.shortages || [])].filter(Boolean).join(' | ');
  status.className = (d.summary?.shortages?.length ? 'err' : 'warn');

  const summary = document.getElementById('summary');
  summary.innerHTML = `
    <div>Текущая плата: <b>${d.current_board || '-'}</b></div>
    <div>Всего компонентов: <b>${d.summary?.total_parts ?? 0}</b> | Уникальных: <b>${d.summary?.unique_parts ?? 0}</b></div>
    <div>${d.summary?.feeders_used || ''}</div>
    <div>Рекомендованная следующая плата: <b>${d.summary?.next_board || 'нет'}</b></div>
    <div>Плат в панели: <b>${d.board_multiplier || d.summary?.board_multiplier || 1}</b> | К снятию перед текущим заходом: <b>${d.summary?.pending_remove ?? 0}</b></div>
  `;

  const boardSel = document.getElementById('boardSel');
  const selected = boardSel.value || d.current_board;
  boardSel.innerHTML = (d.boards || []).map(b => `<option value="${b}">${b}</option>`).join('');
  boardSel.value = d.current_board || selected || '';
  document.getElementById('boardMultiplier').value = String(d.board_multiplier || 1);
  document.getElementById('chipFeederLimit').value = String(d.chip_feeder_limit || 50);

  const limits = document.getElementById('tapeLimits');
  limits.innerHTML = Object.entries(d.tape_limits || {}).map(([k, v]) => (
    `<label>${k}мм <input data-mm="${k}" type="number" min="0" value="${v}" style="width:80px;"></label>`
  )).join('');

  const progress = document.getElementById('progress');
  progress.innerHTML = (d.progress || []).map(p => `
    <div class="progress-row">
      <div>${p.batch}</div>
      <progress value="${p.done}" max="${p.total}"></progress>
      <div>${p.done}/${p.total}</div>
    </div>
  `).join('') || 'Нет данных';

  const center = document.getElementById('machineCenter');

  const mult = d.board_multiplier || d.summary?.board_multiplier || 1;
  const gridRows = Math.ceil(Math.sqrt(mult));
  const gridCols = Math.ceil(mult / gridRows);

  let gridHtml = `<div style="display: grid; grid-template-columns: repeat(${gridCols}, 1fr); gap: 10px; margin: 15px 0;">`;
  for (let i = 0; i < mult; i++) {
    gridHtml += `<div style="border: 2px solid #4ade80; border-radius: 8px; padding: 10px; background: rgba(74, 222, 128, 0.1);">Плата ${i+1}</div>`;
  }
  gridHtml += `</div>`;

  center.innerHTML = `
    <div style="width: 100%;">
      <div style="font-size:20px; margin-bottom:8px;">РАБОЧАЯ ЗОНА</div>
      <div>Текущая плата: ${d.current_board || '-'}</div>
      ${gridHtml}
      <div style="margin-top:6px;">Следующий заход: ${d.summary?.next_board || 'нет'}</div>
      <div style="margin-top:6px;">Красный — снять, Зеленый — поставить, Серый — оставить</div>
    </div>
  `;

  const slots = d.visual?.chipshooter || [];
  const left = slots.filter(s => String(s.slot).startsWith('L'));
  const right = slots.filter(s => String(s.slot).startsWith('R'));
  const slotHtml = s => `
    <div class="slot ${s.status || ''} ${s.enabled === false ? 'disabled' : ''}" data-remove="${(s.component || '').replace(/"/g, '&quot;')}">
      <div><b>${s.slot}</b>${s.enabled === false ? ' <span style="opacity:.7">(вне лимита)</span>' : ''}</div>
      <div>${s.component || '—'}</div>
    </div>
  `;
  document.getElementById('chipLeft').innerHTML = left.map(slotHtml).join('');
  document.getElementById('chipRight').innerHTML = right.map(slotHtml).join('');

  const spider = d.visual?.spider || [];
  const spiderTable = document.getElementById('spiderTable');
  if (!spider.length) spiderTable.innerHTML = '<tr><td>Нет установленных компонентов</td></tr>';
  else {
    spiderTable.innerHTML = '<tr><th>Слот</th><th>Компонент</th><th>Батч</th><th>Статус</th></tr>' +
      spider.map(r => `<tr><td>${r.slot}</td><td>${r.component}</td><td>${r.batch}</td><td>${r.status}</td></tr>`).join('');
  }

  renderTable(
    document.getElementById('setupTable'),
    d.table_setup || [],
    new Set(d.setup_completed?.[d.current_board] || []),
    'ЧТО СТАВИМ'
  );
  renderTable(
    document.getElementById('instrTable'),
    d.table_instr || [],
    new Set(d.instr_completed?.[d.current_board] || []),
    'ДЕТАЛЬ'
  );

  const wh = d.warehouse || [];
  const whTable = document.getElementById('warehouseTable');
  if (!wh.length) {
    whTable.innerHTML = '<tr><td>Склад пуст</td></tr>';
  } else {
    whTable.innerHTML = '<tr><th>Номер</th><th>Название</th><th>Ширина</th><th>Катушка</th><th>Остаток</th></tr>' +
      wh.map(r => `<tr><td>${r['Номер']}</td><td>${r['Название']}</td><td>${r['ШиринаЛенты']}</td><td>${r['Катушка']}</td><td>${r['Остаток']}</td></tr>`).join('');
  }
}

async function refresh() {
  state.data = await api('/api/state');
  render();
}

async function withRefresh(action) {
  try {
    state.data = await action();
    render();
  } catch (e) {
    alert(e.message || String(e));
    await refresh();
  }
}

document.getElementById('uploadBomBtn').onclick = async () => {
  const f = document.getElementById('bomFile').files[0];
  if (!f) return alert('Выберите файл BOM');
  const fd = new FormData();
  fd.append('file', f);
  await withRefresh(() => api('/api/upload-bom', 'POST', fd));
};

document.getElementById('calcBtn').onclick = async () => withRefresh(() => api('/api/calculate', 'POST'));
document.getElementById('nextBtn').onclick = async () => withRefresh(() => api('/api/next-batch', 'POST'));

document.getElementById('boardSel').onchange = async (e) => {
  await withRefresh(() => api('/api/board', 'POST', { board: e.target.value }));
};

document.getElementById('saveLimitsBtn').onclick = async () => {
  const limits = {};
  document.querySelectorAll('#tapeLimits input[data-mm]').forEach(i => {
    limits[i.dataset.mm] = Number(i.value || 0);
  });
  await withRefresh(() => api('/api/tape-limits', 'POST', { limits }));
};

document.getElementById('setupTable').ondblclick = async (e) => {
  const tr = e.target.closest('tr[data-name]');
  if (!tr) return;
  await withRefresh(() => api('/api/toggle-setup', 'POST', { components: [tr.dataset.name] }));
};

document.getElementById('instrTable').ondblclick = async (e) => {
  const tr = e.target.closest('tr[data-name]');
  if (!tr) return;
  await withRefresh(() => api('/api/toggle-instr', 'POST', { components: [tr.dataset.name] }));
};

const chipClick = async (e) => {
  const slot = e.target.closest('.slot');
  if (!slot) return;
  if (slot.classList.contains('disabled')) return;
  const comp = slot.dataset.remove || '';
  if (!comp) return;
  await withRefresh(() => api('/api/remove-component', 'POST', { component: comp }));
};
document.getElementById('chipLeft').onclick = chipClick;
document.getElementById('chipRight').onclick = chipClick;

document.getElementById('uploadWhBtn').onclick = async () => {
  const f = document.getElementById('whExcelFile').files[0];
  if (!f) return alert('Выберите Excel склада');
  const fd = new FormData();
  fd.append('file', f);
  await withRefresh(() => api('/api/warehouse/upload', 'POST', fd));
};

document.getElementById('addWhBtn').onclick = async () => {
  const num = document.getElementById('whNum').value || '';
  const name = document.getElementById('whName').value || '';
  const width = Number(document.getElementById('whWidth').value || 8);
  const qty = Number(document.getElementById('whQty').value || 0);
  await withRefresh(() => api('/api/warehouse/manual', 'POST', { num, name, width, qty }));
};

document.getElementById('saveBoardMultiplierBtn').onclick = async () => {
  const multiplier = Number(document.getElementById('boardMultiplier').value || 1);
  await withRefresh(() => api('/api/board-multiplier', 'POST', { multiplier }));
};

document.getElementById('saveChipFeederLimitBtn').onclick = async () => {
  const limit = Number(document.getElementById('chipFeederLimit').value || 1);
  await withRefresh(() => api('/api/feeders-limit', 'POST', { limit }));
};

refresh().catch(err => alert(err.message || String(err)));
