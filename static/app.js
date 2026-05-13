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
  `;

  const boardSel = document.getElementById('boardSel');
  const selected = boardSel.value || d.current_board;
  boardSel.innerHTML = (d.boards || []).map(b => `<option value="${b}">${b}</option>`).join('');
  boardSel.value = d.current_board || selected || '';

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

  const chipGrid = document.getElementById('chipGrid');
  chipGrid.innerHTML = (d.visual?.chipshooter || []).map(s => `
    <div class="slot ${s.status}" data-remove="${(s.component || '').replace(/"/g, '&quot;')}">
      <div><b>${s.slot}</b></div>
      <div>${s.component || '—'}</div>
    </div>
  `).join('');

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

document.getElementById('chipGrid').onclick = async (e) => {
  const slot = e.target.closest('.slot');
  if (!slot) return;
  const comp = slot.dataset.remove || '';
  if (!comp) return;
  await withRefresh(() => api('/api/remove-component', 'POST', { component: comp }));
};

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

refresh().catch(err => alert(err.message || String(err)));
