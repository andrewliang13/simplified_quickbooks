'use strict';
/* Simplified QuickBooks - single page UI (no build step, no dependencies). */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = n => (n == null || n === '') ? '' :
  Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const cents = s => { const v = parseFloat(String(s == null ? '' : s).replace(/[$,\s]/g, '')); return isNaN(v) ? 0 : Math.round(v * 100); };
const money = c => fmt(c / 100);
const amt = n => n == null ? '' : `<span class="${n < 0 ? 'neg' : ''}">${fmt(n)}</span>`;
const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const today = () => iso(new Date());
const localTime = s => s ? new Date(s.replace(' ', 'T') + 'Z').toLocaleString() : '';

const S = {accounts: [], customers: [], vendors: [], items: [], taxRates: [], meta: {}};

async function api(method, url, body) {
  const res = await fetch(url, {method, headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
async function loadRefs() {
  [S.accounts, S.customers, S.vendors, S.meta, S.items, S.taxRates] = await Promise.all([
    api('GET', '/api/accounts?include_inactive=1'), api('GET', '/api/customers?include_inactive=1'),
    api('GET', '/api/vendors?include_inactive=1'), api('GET', '/api/meta'), api('GET', '/api/items?include_inactive=1'),
    api('GET', '/api/tax_rates?include_inactive=1')]);
  $('#company').textContent = S.meta.company_name;
}
let toastTimer;
function toast(msg, err) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'show' + (err ? ' err' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = ''; }, err ? 5000 : 2200);
}
async function run(fn) { try { return await fn(); } catch (e) { toast(e.message, true); } }

function modal(title, html, wide) {
  const wrap = document.createElement('div');
  wrap.className = 'modal-backdrop';
  wrap.innerHTML = `<div class="modal ${wide ? 'wide' : ''}"><div class="modal-head"><h3>${esc(title)}</h3>
    <button class="x" data-close title="Close">&times;</button></div><div class="modal-body">${html}</div></div>`;
  document.body.appendChild(wrap);
  const close = () => wrap.remove();
  wrap.addEventListener('click', e => { if (e.target.closest('[data-close]')) close(); });
  return {el: wrap, body: $('.modal-body', wrap), close};
}

function acctOptions(sel, filter, blank = true) {
  let html = blank ? '<option value=""></option>' : '', last = null;
  for (const a of S.accounts) {
    if ((!a.active && a.id != sel) || (filter && !filter(a))) continue;
    if (a.type !== last) { if (last) html += '</optgroup>'; html += `<optgroup label="${esc(a.type)}">`; last = a.type; }
    html += `<option value="${a.id}" ${a.id == sel ? 'selected' : ''}>${'&nbsp;&nbsp;'.repeat(a.depth)}${esc((a.number ? a.number + ' ' : '') + a.name)}</option>`;
  }
  return html + (last ? '</optgroup>' : '');
}
function nameOptions(sel, kinds = ['customer', 'vendor']) {
  let html = '<option value=""></option>';
  for (const k of kinds) {
    const list = (k === 'customer' ? S.customers : S.vendors).filter(n => n.active || n.id == sel);
    if (!list.length) continue;
    html += `<optgroup label="${k === 'customer' ? 'Customers' : 'Vendors'}">` +
      list.map(n => `<option value="${n.id}" ${n.id == sel ? 'selected' : ''}>${esc(n.name)}</option>`).join('') + '</optgroup>';
  }
  return html;
}
const isFeedAcct = a => a.type === 'Bank' || a.type === 'Credit Card';
const acctById = id => S.accounts.find(a => a.id == id);

/* ------------------------------------------------------------------ router */
const pages = {};
function parseHash() {
  const [page, qs] = (location.hash.slice(1) || 'dashboard').split('?');
  return {page, params: Object.fromEntries(new URLSearchParams(qs || ''))};
}
async function render() {
  const {page, params} = parseHash();
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.page === page));
  const main = $('#main');
  await run(async () => { await loadRefs(); await (pages[page] || pages.dashboard)(main, params); });
}
// Give each table cell its column name, so narrow screens can show rows as labelled cards.
function labelTables() {
  $$('.tbl table').forEach(t => {
    const hs = $$('thead th', t).map(th => th.textContent.trim());
    $$('tbody tr', t).forEach(tr => [...tr.children].forEach((td, i) => {
      if (td.colSpan === 1 && hs[i] !== undefined && td.dataset.label === undefined) td.dataset.label = hs[i];
    }));
  });
}
let labelTimer;
new MutationObserver(() => { clearTimeout(labelTimer); labelTimer = setTimeout(labelTables, 30); })
  .observe(document.documentElement, {childList: true, subtree: true});

window.addEventListener('hashchange', () => { $$('.modal-backdrop').forEach(x => x.remove()); render(); });
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const all = $$('.modal-backdrop');
  if (all.length) all[all.length - 1].remove();
});
window.addEventListener('DOMContentLoaded', () => {
  $('#settings-btn').onclick = settingsModal;
  render();
});

function settingsModal() {
  const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const m = modal('Company settings', `<div class="form">
    <label class="full">Company name<input id="s-name" value="${esc(S.meta.company_name)}"></label>
    <label class="full">Home currency (set before entering transactions)<input id="s-cur" maxlength="3" value="${esc(S.meta.home_currency || 'USD')}"></label>
    <label class="full">First month of fiscal year<select id="s-fy">${months.map((x, i) =>
      `<option value="${i + 1}" ${S.meta.fiscal_year_start_month === i + 1 ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
    </div><div class="foot"><button data-close>Cancel</button><button class="primary" id="s-save">Save</button></div>
    <h2>Backups</h2>
    <p class="muted">A backup is saved automatically each day you open the app (last 30 kept in the <b>backups</b> folder next to your company file).</p>
    <div class="bar"><a href="/api/backups/download"><button>Download a copy</button></a><button id="b-now">Back up now</button>
      <label class="inline"><span class="btn" style="cursor:pointer">Restore from file&hellip;</span><input type="file" id="b-file" accept=".db" hidden></label></div>
    <div id="b-list"></div>`);
  $('#s-save', m.el).onclick = () => run(async () => {
    await api('PUT', '/api/settings', {company_name: $('#s-name', m.el).value, fiscal_year_start_month: $('#s-fy', m.el).value,
      home_currency: $('#s-cur', m.el).value});
    m.close(); toast('Saved'); render();
  });
  const restored = r => { m.close(); toast(`Restored. Your previous books were saved as ${r.safety_backup}`); render(); };
  const sure = () => confirm('Replace ALL current books with this backup? Your current books are backed up first.');
  const list = async () => {
    const b = await api('GET', '/api/backups');
    $('#b-list', m.el).innerHTML = b.length ? `<div class="tbl" style="max-height:220px"><table><thead><tr><th>Backup</th><th>Saved</th><th></th></tr></thead><tbody>
      ${b.map(x => `<tr><td>${esc(x.file)}</td><td class="nowrap">${x.created}</td><td class="actions"><button class="small" data-restore="${esc(x.file)}">Restore</button></td></tr>`).join('')}</tbody></table></div>`
      : '<div class="muted">No backups yet.</div>';
  };
  run(list);
  $('#b-now', m.el).onclick = () => run(async () => { const r = await api('POST', '/api/backups'); toast('Saved ' + r.file); list(); });
  $('#b-list', m.el).onclick = e => run(async () => {
    const b = e.target.closest('[data-restore]');
    if (b && sure()) restored(await api('POST', '/api/backups/restore', {file: b.dataset.restore}));
  });
  $('#b-file', m.el).onchange = e => run(async () => {
    const f = e.target.files[0];
    if (!f || !sure()) return;
    const buf = new Uint8Array(await f.arrayBuffer());
    let bin = '';
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    restored(await api('POST', '/api/backups/restore', {content_b64: btoa(bin)}));
  });
}

/* ------------------------------------------------------------------ dashboard */
pages.dashboard = async main => {
  const d = await api('GET', '/api/dashboard');
  main.innerHTML = `<div class="head"><h1>${esc(d.company)}</h1></div>
    <div class="cards">
      <div class="card"><div class="label">Net income this month</div><div class="big">${amt(d.net_income_month)}</div></div>
      <div class="card"><div class="label">Net income, fiscal year to date</div><div class="big">${amt(d.net_income_ytd)}</div></div>
      <div class="card"><div class="label">Income YTD</div><div class="big">${fmt(d.income_ytd)}</div></div>
      <div class="card"><div class="label">Expenses YTD</div><div class="big">${fmt(d.expenses_ytd)}</div></div>
      <div class="card"><div class="label">Open invoices (${d.open_invoices})</div><div class="big">${fmt(d.open_invoice_total)}</div></div>
      <div class="card"><div class="label">Overdue invoices (${d.overdue_invoices})</div><div class="big neg">${fmt(d.overdue_total)}</div></div>
      <div class="card"><div class="label">Unpaid bills (${d.open_bills})</div><div class="big">${fmt(d.open_bill_total)}</div></div>
    </div>
    <h2>Bank and credit card accounts</h2>
    <div class="tbl"><table><thead><tr><th>Account</th><th>Type</th><th class="num">Balance in books</th><th class="num">To review</th><th></th></tr></thead>
    <tbody>${d.accounts.map(a => `<tr><td>${esc(a.name)}</td><td>${a.type}</td><td class="num">${amt(a.balance)}</td>
      <td class="num">${a.review_count}</td><td class="actions"><a href="#banking?account_id=${a.account_id}">Open</a></td></tr>`).join('')}</tbody></table></div>`;
};

/* ------------------------------------------------------------------ banking */
const bank = {account_id: null, status: 'review'};
pages.banking = async (main, params) => {
  const sum = await api('GET', '/api/feed/summary');
  if (params.account_id) bank.account_id = +params.account_id;
  if (!sum.some(a => a.account_id === bank.account_id)) bank.account_id = sum.length ? sum[0].account_id : null;
  if (!bank.account_id) { main.innerHTML = '<h1>Banking</h1><div class="card empty">Add a Bank or Credit Card account in the Chart of Accounts first.</div>'; return; }
  const feed = await api('GET', `/api/feed?account_id=${bank.account_id}&status=${bank.status}`);
  const isCC = feed.account_type === 'Credit Card';
  const out = isCC ? 'Charge' : 'Spent', inn = isCC ? 'Payment / credit' : 'Received';
  let rows = '';
  for (const r of feed.rows) {
    const money2 = `<td class="num">${r.amount < 0 ? fmt(-r.amount) : ''}</td><td class="num">${r.amount > 0 ? fmt(r.amount) : ''}</td>`;
    if (bank.status === 'review') {
      const sg = r.suggestion;
      rows += `<tr data-id="${r.id}"><td><input type="checkbox" class="pick"></td><td class="nowrap">${r.date}</td>
        <td class="desc">${esc(r.description)}${r.matches.length ? ` <span class="badge match">${r.matches.length} match${r.matches.length > 1 ? 'es' : ''}</span>` : ''}</td>${money2}
        <td><select class="cat">${acctOptions(sg && sg.account_id, a => a.id !== bank.account_id)}</select>
          ${sg ? `<span class="badge ${sg.source}">${sg.source}</span>` : ''}</td>
        <td><select class="payee">${nameOptions(sg && sg.name_id)}</select></td>
        <td class="actions">${r.matches.length ? '<button class="small primary" data-act="match">Match</button>' : ''}
          <button class="small ${r.matches.length ? '' : 'primary'}" data-act="add">Add</button>
          <button class="small" data-act="split">Split</button><button class="small" data-act="rule">Rule</button>
          <button class="small" data-act="exclude">Exclude</button></td></tr>`;
    } else if (bank.status === 'posted') {
      rows += `<tr data-id="${r.id}"><td class="nowrap">${r.date}</td><td>${esc(r.description)}</td>${money2}
        <td>${r.matched ? '<span class="badge match">Matched</span> ' : ''}${esc(r.category || '')}</td><td>${esc(r.name || '')}</td>
        <td>${r.txn_id ? `<button class="link" data-txn="${r.txn_id}">${esc(r.txn_type)}</button>` : ''}</td>
        <td class="actions">${r.matched || !r.txn_id ? '' : `<button class="small" data-recat="${r.txn_id}">Edit</button>`}<button class="small" data-act="undo">Undo</button></td></tr>`;
    } else {
      rows += `<tr data-id="${r.id}"><td class="nowrap">${r.date}</td><td>${esc(r.description)}</td>${money2}
        <td class="actions"><button class="small" data-act="undo">Undo</button></td></tr>`;
    }
  }
  const heads = {
    review: `<th></th><th>Date</th><th>Description</th><th class="num">${out}</th><th class="num">${inn}</th><th>Category</th><th>Customer / vendor</th><th></th>`,
    posted: `<th>Date</th><th>Description</th><th class="num">${out}</th><th class="num">${inn}</th><th>Category</th><th>Customer / vendor</th><th>Entry</th><th></th>`,
    excluded: `<th>Date</th><th>Description</th><th class="num">${out}</th><th class="num">${inn}</th><th></th>`};
  main.innerHTML = `<div class="head"><h1>Banking</h1><button id="b-rules">Rules</button><button class="primary" id="b-import">Import statement</button></div>
    <div class="cards">${sum.map(a => `<div class="card pick ${a.account_id === bank.account_id ? 'active' : ''}" data-acct="${a.account_id}">
      <div class="label">${esc(a.type)}</div><div><b>${esc(a.name)}</b></div><div class="big">${amt(a.balance)}</div>
      <div class="muted">${a.review_count} to review</div></div>`).join('')}</div>
    <div class="tabs">${[['review', 'For review'], ['posted', 'Categorized'], ['excluded', 'Excluded']].map(([k, l]) =>
      `<button data-tab="${k}" class="${bank.status === k ? 'active' : ''}">${l} (${feed.counts[k]})</button>`).join('')}</div>
    ${bank.status === 'review' && feed.rows.length ? `<div class="bar"><label class="inline"><input type="checkbox" id="b-all"> Select all</label>
      <button id="b-accept">Add selected</button><button id="b-excl">Exclude selected</button>
      <span class="muted">Pick a category for each line, then Add. Lines with a suggestion are pre-filled.</span></div>` : ''}
    <div class="tbl"><table><thead><tr>${heads[bank.status]}</tr></thead><tbody>${rows ||
      `<tr><td colspan="8" class="empty">${bank.status === 'review' ? 'Nothing to review. Import a bank or credit card statement to get started.' : 'Nothing here yet.'}</td></tr>`}</tbody></table></div>`;
  $$('.card.pick', main).forEach(c => c.onclick = () => { bank.account_id = +c.dataset.acct; location.hash = '#banking'; render(); });
  $$('[data-tab]', main).forEach(b => b.onclick = () => { bank.status = b.dataset.tab; render(); });
  $('#b-import').onclick = importModal;
  $('#b-rules').onclick = rulesModal;
  const byId = Object.fromEntries(feed.rows.map(r => [r.id, r]));
  const picked = () => $$('tbody tr', main).filter(tr => $('.pick', tr) && $('.pick', tr).checked);
  if ($('#b-all')) {
    $('#b-all').onchange = e => $$('.pick', main).forEach(c => { c.checked = e.target.checked; });
    $('#b-accept').onclick = () => run(async () => {
      let done = 0, skipped = 0;
      for (const tr of picked()) {
        const cat = $('.cat', tr).value;
        if (!cat) { skipped++; continue; }
        await api('POST', `/api/feed/${tr.dataset.id}/categorize`, {account_id: cat, name_id: $('.payee', tr).value || null});
        done++;
      }
      toast(`${done} added${skipped ? `, ${skipped} skipped (no category)` : ''}`); render();
    });
    $('#b-excl').onclick = () => run(async () => {
      const ids = picked().map(tr => +tr.dataset.id);
      if (!ids.length) return;
      await api('POST', '/api/feed/batch', {action: 'exclude', ids}); render();
    });
  }
  main.onclick = e => run(async () => {
    const txn = e.target.closest('[data-txn]');
    if (txn) return txnModal(+txn.dataset.txn);
    const rc = e.target.closest('[data-recat]');
    if (rc) return recategorizeModal(+rc.dataset.recat);
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const tr = b.closest('tr'), id = tr.dataset.id, r = byId[id], act = b.dataset.act;
    if (act === 'add') {
      const cat = $('.cat', tr).value;
      if (!cat) throw new Error('Choose a category first');
      await api('POST', `/api/feed/${id}/categorize`, {account_id: cat, name_id: $('.payee', tr).value || null});
      toast('Added'); render();
    } else if (act === 'exclude') { await api('POST', `/api/feed/${id}/exclude`); render();
    } else if (act === 'undo') { await api('POST', `/api/feed/${id}/undo`); toast('Moved back to For review'); render();
    } else if (act === 'match') matchModal(r);
    else if (act === 'split') splitModal(r, $('.payee', tr).value);
    else if (act === 'rule') ruleForm({match_text: r.description, direction: r.amount > 0 ? 'in' : 'out',
      account_id: $('.cat', tr).value, name_id: $('.payee', tr).value});
  });
};

function matchModal(r) {
  const m = modal('Match to an existing entry', `<p>${r.date} &middot; ${esc(r.description)} &middot; <b>${fmt(r.amount)}</b></p>
    <div class="tbl"><table><tbody>${r.matches.map((x, i) => `<tr><td>${x.date}</td><td>${esc(x.label)}</td>
      <td class="actions"><button class="small primary" data-i="${i}">Match</button></td></tr>`).join('')}</tbody></table></div>
    <p class="muted">Matching links this statement line to the entry already in your books, so it is not counted twice.</p>`);
  m.body.onclick = e => run(async () => {
    const b = e.target.closest('[data-i]'); if (!b) return;
    const x = r.matches[+b.dataset.i];
    await api('POST', `/api/feed/${r.id}/match`, x.kind === 'invoice' ? {invoice_id: x.invoice_id} : x.kind === 'bill' ? {bill_id: x.bill_id}
      : x.kind === 'undeposited' ? {undeposited: true} : {line_id: x.line_id});
    m.close(); toast('Matched'); render();
  });
}

function splitModal(r, nameId) {
  const total = Math.abs(cents(r.amount));
  const line = () => `<tr><td><select class="s-acct">${acctOptions(null, a => a.id !== r.account_id)}</select></td>
    <td><input class="num s-amt"></td><td><input class="s-memo"></td><td><button class="small" data-rm>&times;</button></td></tr>`;
  const m = modal('Split transaction', `<p>${r.date} &middot; ${esc(r.description)} &middot; <b>${fmt(Math.abs(r.amount))}</b></p>
    <table class="lines"><thead><tr><th>Category</th><th class="num">Amount</th><th>Memo</th><th></th></tr></thead><tbody>${line()}${line()}</tbody></table>
    <button class="small" id="s-add">Add line</button>
    <div class="totals"><span>Split total <span id="s-tot">0.00</span></span><span>Remaining <span id="s-rem"></span></span></div>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="s-save">Save split</button></div>`, true);
  const recalc = () => {
    const t = $$('.s-amt', m.el).reduce((s, i) => s + cents(i.value), 0);
    $('#s-tot', m.el).textContent = money(t); $('#s-rem', m.el).textContent = money(total - t);
  };
  recalc();
  m.body.addEventListener('input', recalc);
  m.body.addEventListener('click', e => { if (e.target.closest('[data-rm]')) { e.target.closest('tr').remove(); recalc(); } });
  $('#s-add', m.el).onclick = () => { $('tbody', m.el).insertAdjacentHTML('beforeend', line()); };
  $('#s-save', m.el).onclick = () => run(async () => {
    const splits = $$('tbody tr', m.el).map(tr => ({account_id: $('.s-acct', tr).value, amount: $('.s-amt', tr).value, memo: $('.s-memo', tr).value}))
      .filter(s => cents(s.amount));
    if (splits.some(s => !s.account_id)) throw new Error('Choose a category on every line');
    await api('POST', `/api/feed/${r.id}/categorize`, {splits, name_id: nameId || null});
    m.close(); toast('Added'); render();
  });
}

function ruleForm(rule, after) {
  const m = modal(rule.id ? 'Edit rule' : 'New rule', `<div class="form">
    <label class="full">When the bank description contains<input id="r-text" value="${esc(rule.match_text || '')}"></label>
    <label>Money<select id="r-dir">${[['any', 'In or out'], ['out', 'Money out'], ['in', 'Money in']].map(([v, l]) =>
      `<option value="${v}" ${rule.direction === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
    <label>Category<select id="r-acct">${acctOptions(rule.account_id)}</select></label>
    <label class="full">Customer / vendor (optional)<select id="r-name">${nameOptions(rule.name_id)}</select></label></div>
    <p class="muted">Shorten the text to the part that repeats (for example "OFFICE DEPOT") so future statements match.</p>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="r-save">Save rule</button></div>`);
  $('#r-save', m.el).onclick = () => run(async () => {
    const body = {match_text: $('#r-text', m.el).value, direction: $('#r-dir', m.el).value,
      account_id: $('#r-acct', m.el).value, name_id: $('#r-name', m.el).value || null};
    await api(rule.id ? 'PUT' : 'POST', '/api/rules' + (rule.id ? '/' + rule.id : ''), body);
    m.close(); toast('Rule saved'); (after || render)();
  });
}

async function rulesModal() {
  const m = modal('Bank rules', '<div id="rules"></div><div class="foot"><button class="primary" id="r-new">New rule</button></div>', true);
  const draw = async () => {
    const rules = await api('GET', '/api/rules');
    $('#rules', m.el).innerHTML = rules.length ? `<div class="tbl"><table><thead><tr><th>Description contains</th><th>Money</th><th>Category</th><th>Customer / vendor</th><th></th></tr></thead>
      <tbody>${rules.map(r => `<tr><td>${esc(r.match_text)}</td><td>${{any: 'In or out', in: 'Money in', out: 'Money out'}[r.direction]}</td><td>${esc(r.account)}</td><td>${esc(r.name || '')}</td>
      <td class="actions"><button class="small" data-edit="${r.id}">Edit</button><button class="small danger" data-del="${r.id}">Delete</button></td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">No rules yet. Rules pre-fill the category for statement lines whose description contains your text.</div>';
    $('#rules', m.el).onclick = e => run(async () => {
      const ed = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]');
      if (ed) ruleForm(rules.find(r => r.id == ed.dataset.edit), () => { draw(); render(); });
      if (del) { await api('DELETE', '/api/rules/' + del.dataset.del); draw(); render(); }
    });
  };
  $('#r-new', m.el).onclick = () => ruleForm({direction: 'any'}, () => { draw(); render(); });
  run(draw);
}

function importModal() {
  const m = modal('Import a bank or credit card statement', `<div class="form">
    <label>Account<select id="i-acct">${acctOptions(bank.account_id, isFeedAcct, false)}</select></label>
    <label>Statement file (CSV, QFX/OFX/QBO, PDF)<input type="file" id="i-file" accept=".csv,.txt,.tsv,.ofx,.qfx,.qbo,.pdf"></label>
    <label class="full inline"><input type="checkbox" id="i-flip"> Reverse the signs (use when charges or withdrawals show as positive numbers)</label></div>
    <div id="i-prev"></div>
    <div class="foot"><button data-close>Cancel</button><button id="i-go" class="primary" disabled>Import</button></div>`, true);
  let payload = null;
  const preview = () => run(async () => {
    const f = $('#i-file', m.el).files[0];
    if (!f) return;
    const buf = new Uint8Array(await f.arrayBuffer());
    let bin = '';
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    payload = {account_id: $('#i-acct', m.el).value, filename: f.name, content_b64: btoa(bin), flip_sign: $('#i-flip', m.el).checked};
    $('#i-go', m.el).disabled = true;
    let p;
    try { p = await api('POST', '/api/feed/preview', payload); } catch (e) {
      $('#i-prev', m.el).innerHTML = `<div class="note err">${esc(e.message)}</div>`; return;
    }
    const cc = acctById(payload.account_id).type === 'Credit Card';
    $('#i-prev', m.el).innerHTML = `${p.warnings.map(w => `<div class="note warn">${esc(w)}</div>`).join('')}
      <div class="note"><b>${p.new_count}</b> new transaction(s), ${p.duplicate_count} already imported (skipped). ${p.start || ''} to ${p.end || ''}.
      ${cc ? 'Charges' : 'Money out'}: <b>${fmt(p.money_out)}</b> &middot; ${cc ? 'Payments / credits' : 'Money in'}: <b>${fmt(p.money_in)}</b>.
      If these look swapped, tick "Reverse the signs".</div>
      <p class="muted">You can correct any row before importing: change the date, description or amount, use &#8645; to move an amount between the columns, or &times; to leave a row out.</p>
      <div class="tbl" style="max-height:360px"><table><thead><tr><th>Date</th><th>Description</th><th class="num">${cc ? 'Charge' : 'Spent'}</th><th class="num">${cc ? 'Payment / credit' : 'Received'}</th><th></th></tr></thead>
      <tbody>${p.rows.map((r, i) => r.duplicate ? `<tr class="muted"><td class="nowrap">${r.date}</td><td>${esc(r.description)}</td>
        <td class="num">${r.amount < 0 ? fmt(-r.amount) : ''}</td><td class="num">${r.amount > 0 ? fmt(r.amount) : ''}</td><td>already imported</td></tr>`
        : `<tr class="i-row" data-i="${i}"><td><input type="date" class="i-date" value="${r.date}"></td><td><input class="i-desc" style="width:100%" value="${esc(r.description)}"></td>
        <td><input class="num i-out" value="${r.amount < 0 ? fmt(-r.amount) : ''}"></td><td><input class="num i-in" value="${r.amount > 0 ? fmt(r.amount) : ''}"></td>
        <td class="nowrap"><button class="small" data-flip title="Move to the other column">&#8645;</button><button class="small" data-drop title="Leave out">&times;</button></td></tr>`).join('')}</tbody></table></div>`;
    payload.reviewed = p.rows;
    const count = () => { const n = $$('.i-row', m.el).length; $('#i-go', m.el).disabled = !n; $('#i-go', m.el).textContent = `Import ${n} transaction(s)`; };
    $('#i-prev', m.el).onclick = e => {
      const tr = e.target.closest('tr');
      if (e.target.closest('[data-drop]')) { tr.parentNode.removeChild(tr); count(); }
      if (e.target.closest('[data-flip]')) { const o = $('.i-out', tr), n = $('.i-in', tr), t = o.value; o.value = n.value; n.value = t; }
    };
    count();
  });
  ['#i-file', '#i-flip', '#i-acct'].forEach(s => { $(s, m.el).onchange = preview; });
  $('#i-go', m.el).onclick = () => run(async () => {
    const rows = $$('.i-row', m.el).map(tr => {
      const src = payload.reviewed[+tr.dataset.i];
      return {date: $('.i-date', tr).value, description: $('.i-desc', tr).value, fitid: src.fitid,
        amount: (cents($('.i-in', tr).value) - cents($('.i-out', tr).value)) / 100};
    });
    const r = await api('POST', '/api/feed/import', {account_id: payload.account_id, filename: payload.filename, rows});
    bank.account_id = +payload.account_id; bank.status = 'review';
    m.close(); toast(`${r.imported} transaction(s) imported`); location.hash = '#banking'; render();
  });
}

/* ------------------------------------------------------------------ transaction viewer */
async function txnModal(id) {
  const t = await api('GET', '/api/transactions/' + id);
  const m = modal(`${t.type}${t.ref ? ' #' + t.ref : ''}`, `<p>${t.date}${t.name ? ' &middot; ' + esc(t.name) : ''}${t.memo ? ' &middot; ' + esc(t.memo) : ''}
    ${t.reconciled ? ' <span class="badge done">Reconciled</span>' : ''}</p>
    <div class="tbl"><table><thead><tr><th>Account</th><th>Name</th><th>Memo</th><th class="num">Debit</th><th class="num">Credit</th></tr></thead>
    <tbody>${t.lines.map(l => `<tr><td>${esc(l.account)}</td><td>${esc(l.name || '')}</td><td>${esc(l.memo)}</td>
      <td class="num">${l.debit ? fmt(l.debit) : ''}</td><td class="num">${l.credit ? fmt(l.credit) : ''}</td></tr>`).join('')}
    <tr class="sum"><td colspan="3">Total</td><td class="num">${fmt(t.total)}</td><td class="num">${fmt(t.total)}</td></tr></tbody></table></div>
    <div class="foot"><button class="danger" id="t-del">Delete</button><span class="spacer"></span>
    ${t.type === 'Journal' || t.invoice_id || t.payment_id || t.bill_id || t.bill_payment_id || t.paycheck_id || RECAT.includes(t.type)
      ? '<button id="t-edit">Edit</button>' : ''}<button data-close>Close</button></div>`, true);
  $('#t-del', m.el).onclick = () => run(async () => {
    if (!confirm('Delete this transaction?' + (t.feed_id ? ' Its statement line will go back to For review.' : ''))) return;
    await api('DELETE', '/api/transactions/' + id);
    $$('.modal-backdrop').forEach(x => x.remove()); toast('Deleted'); render();
  });
  if ($('#t-edit', m.el)) $('#t-edit', m.el).onclick = () => {
    $$('.modal-backdrop').forEach(x => x.remove());
    if (t.invoice_id) invoiceModal(t.invoice_id);
    else if (t.payment_id) paymentModal({payment_id: t.payment_id});
    else if (t.bill_id) docModal('purchases', t.bill_id);
    else if (t.bill_payment_id) payModal('purchases', {payment_id: t.bill_payment_id});
    else if (t.paycheck_id) run(async () => paycheckModal(await api('GET', '/api/employees?include_inactive=1'), await api('GET', '/api/paychecks/' + t.paycheck_id)));
    else if (RECAT.includes(t.type)) recategorizeModal(t.id);
    else journalModal(t);
  };
}

const RECAT = ['Expense', 'Deposit', 'Credit Card Credit', 'Transfer', 'Credit Card Payment'];
async function recategorizeModal(id) {
  const t = await api('GET', '/api/transactions/' + id);
  const anchor = t.lines.find(l => ['Bank', 'Credit Card'].includes(l.account_type)) || t.lines[0];
  const total = anchor.debit || anchor.credit, others = t.lines.filter(l => l.id !== anchor.id);
  const line = (l = {}) => `<tr><td><select class="s-acct">${acctOptions(l.account_id, a => a.id !== anchor.account_id)}</select></td>
    <td><input class="num s-amt" value="${l.debit || l.credit ? fmt(l.debit || l.credit) : ''}"></td><td><input class="s-memo" value="${esc(l.memo || '')}"></td>
    <td><button class="small" data-rm>&times;</button></td></tr>`;
  const m = modal(`Edit ${t.type.toLowerCase()}`, `<p>${esc(anchor.account)} &middot; <b>${fmt(total)}</b> ${anchor.debit ? 'in' : 'out'}
      ${t.reconciled ? ' <span class="badge done">Reconciled</span> The amount and bank side stay as they are.' : ''}</p>
    <div class="form three"><label>Date<input type="date" id="rc-date" value="${t.date}" ${t.reconciled ? 'disabled' : ''}></label>
      <label>Customer / vendor<select id="rc-name">${nameOptions(t.name_id)}</select></label><label>Memo<input id="rc-memo" value="${esc(t.memo)}"></label></div>
    <table class="lines" style="margin-top:12px"><thead><tr><th>Category</th><th class="num">Amount</th><th>Memo</th><th></th></tr></thead><tbody>${others.map(line).join('')}</tbody></table>
    <button class="small" id="rc-add">Add split line</button>
    <div class="totals"><span>Total <span id="rc-tot"></span></span><span>Remaining <span id="rc-rem"></span></span></div>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="rc-save">Save</button></div>`, true);
  const recalc = () => { const s = $$('.s-amt', m.el).reduce((x, i) => x + cents(i.value), 0);
    $('#rc-tot', m.el).textContent = money(s); $('#rc-rem', m.el).textContent = money(cents(total) - s); };
  recalc();
  m.body.addEventListener('input', recalc);
  m.body.addEventListener('click', e => { if (e.target.closest('[data-rm]')) { e.target.closest('tr').remove(); recalc(); } });
  $('#rc-add', m.el).onclick = () => $('tbody', m.el).insertAdjacentHTML('beforeend', line());
  $('#rc-save', m.el).onclick = () => run(async () => {
    const splits = $$('tbody tr', m.el).map(tr => ({account_id: $('.s-acct', tr).value, amount: $('.s-amt', tr).value, memo: $('.s-memo', tr).value})).filter(s => cents(s.amount));
    if (splits.some(s => !s.account_id)) throw new Error('Choose a category on every line');
    await api('PUT', `/api/transactions/${id}/categories`, {splits, date: $('#rc-date', m.el).value, name_id: $('#rc-name', m.el).value || null, memo: $('#rc-memo', m.el).value});
    $$('.modal-backdrop').forEach(x => x.remove()); toast('Saved'); render();
  });
}

/* ------------------------------------------------------------------ chart of accounts */
let showInactiveAccts = false;
pages.accounts = async main => {
  const list = S.accounts.filter(a => showInactiveAccts || a.active);
  main.innerHTML = `<div class="head"><h1>Chart of Accounts</h1>
    <label class="inline"><input type="checkbox" id="a-inact" ${showInactiveAccts ? 'checked' : ''}> Show inactive</label>
    <button class="primary" id="a-new">New account</button></div>
    <div class="tbl"><table><thead><tr><th>Number</th><th>Name</th><th>Type</th><th class="num">Balance</th><th></th></tr></thead>
    <tbody>${list.map(a => `<tr class="${a.active ? '' : 'muted'}"><td>${esc(a.number)}</td>
      <td style="padding-left:${10 + a.depth * 22}px">${esc(a.name)}${a.active ? '' : ' <span class="badge">inactive</span>'}${a.system ? ' <span class="badge">system</span>' : ''}</td>
      <td>${a.type}</td><td class="num">${a.balance == null ? '' : amt(a.balance)}</td>
      <td class="actions"><a href="#reports?type=transaction_detail&account_id=${a.id}">Transactions</a>
        <button class="small" data-edit="${a.id}">Edit</button>
        ${a.system ? '' : `<button class="small" data-toggle="${a.id}">${a.active ? 'Make inactive' : 'Make active'}</button>
        <button class="small danger" data-del="${a.id}">Delete</button>`}</td></tr>`).join('')}</tbody></table></div>`;
  $('#a-inact').onchange = e => { showInactiveAccts = e.target.checked; render(); };
  $('#a-new').onclick = () => accountModal();
  main.onclick = e => run(async () => {
    const ed = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]'), tg = e.target.closest('[data-toggle]');
    if (ed) accountModal(acctById(ed.dataset.edit));
    if (del && confirm(`Delete the account "${acctById(del.dataset.del).name}"?`)) { await api('DELETE', '/api/accounts/' + del.dataset.del); toast('Deleted'); render(); }
    if (tg) { const a = acctById(tg.dataset.toggle); await api('PUT', '/api/accounts/' + a.id, {active: !a.active}); render(); }
  });
};

function accountModal(a) {
  const edit = !!a;
  a = a || {type: 'Expense'};
  const bsTypes = S.meta.account_types.filter(t => !['income', 'expense'].includes(t.group)).map(t => t.type);
  const m = modal(edit ? 'Edit account' : 'New account', `<div class="form">
    <label>Account type<select id="ac-type" ${a.system ? 'disabled' : ''}>${S.meta.account_types.map(t =>
      `<option ${t.type === a.type ? 'selected' : ''}>${t.type}</option>`).join('')}</select></label>
    <label>Number (optional)<input id="ac-num" value="${esc(a.number || '')}"></label>
    <label class="full">Name<input id="ac-name" value="${esc(a.name || '')}"></label>
    <label class="full">Sub-account of (optional)<select id="ac-parent"></select></label>
    <label class="full">Description<input id="ac-desc" value="${esc(a.description || '')}"></label>
    ${edit ? '' : `<label class="ob">Opening balance<input id="ac-ob" class="num"></label>
    <label class="ob">As of<input type="date" id="ac-obd" value="${today()}"></label>`}</div>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="ac-save">Save</button></div>`);
  const sync = () => {
    const t = $('#ac-type', m.el).value;
    $('#ac-parent', m.el).innerHTML = acctOptions(a.parent_id, x => x.type === t && x.id !== a.id);
    $$('.ob', m.el).forEach(el => { el.style.display = bsTypes.includes(t) ? '' : 'none'; });
  };
  sync();
  $('#ac-type', m.el).onchange = sync;
  $('#ac-save', m.el).onclick = () => run(async () => {
    const body = {type: $('#ac-type', m.el).value, number: $('#ac-num', m.el).value, name: $('#ac-name', m.el).value,
      parent_id: $('#ac-parent', m.el).value || null, description: $('#ac-desc', m.el).value};
    if (!edit && bsTypes.includes(body.type)) { body.opening_balance = $('#ac-ob', m.el).value; body.opening_date = $('#ac-obd', m.el).value; }
    await api(edit ? 'PUT' : 'POST', '/api/accounts' + (edit ? '/' + a.id : ''), body);
    m.close(); toast('Saved'); render();
  });
}

/* ------------------------------------------------------------------ customers / vendors */
const showInactiveNames = {customer: false, vendor: false};
function namesPage(kind) {
  const title = kind === 'customer' ? 'Customers' : 'Vendors', path = `/api/${kind}s`;
  return async main => {
    const list = (kind === 'customer' ? S.customers : S.vendors).filter(n => showInactiveNames[kind] || n.active);
    main.innerHTML = `<div class="head"><h1>${title}</h1>
      <label class="inline"><input type="checkbox" id="n-inact" ${showInactiveNames[kind] ? 'checked' : ''}> Show inactive</label>
      <button class="primary" id="n-new">New ${kind}</button></div>
      <div class="tbl"><table><thead><tr><th>Name</th><th>Company</th><th>Email</th><th>Phone</th><th class="num">${kind === 'customer' ? 'Open balance' : 'Balance owed'}</th><th></th></tr></thead>
      <tbody>${list.map(n => `<tr class="${n.active ? '' : 'muted'}"><td>${esc(n.name)}${n.active ? '' : ' <span class="badge">inactive</span>'}</td>
        <td>${esc(n.company)}</td><td>${esc(n.email)}</td><td>${esc(n.phone)}</td><td class="num">${amt(n.balance)}${n.currency && n.currency !== home() ? ' <span class="badge">' + n.currency + '</span>' : ''}</td>
        <td class="actions">${kind === 'customer' && n.active ? `<button class="small" data-inv="${n.id}">New invoice</button>` : ''}${kind === 'vendor' && n.active ? `<button class="small" data-bill="${n.id}">New bill</button>` : ''}
        <button class="small" data-edit="${n.id}">Edit</button>
        <button class="small" data-toggle="${n.id}">${n.active ? 'Make inactive' : 'Make active'}</button>
        <button class="small danger" data-del="${n.id}">Delete</button></td></tr>`).join('') ||
        `<tr><td colspan="6" class="empty">No ${title.toLowerCase()} yet.</td></tr>`}</tbody></table></div>`;
    const find = id => list.find(n => n.id == id);
    const form = n => {
      n = n || {};
      const m = modal(n.id ? `Edit ${kind}` : `New ${kind}`, `<div class="form">
        <label class="full">Name<input id="n-name" value="${esc(n.name || '')}"></label>
        <label>Company<input id="n-company" value="${esc(n.company || '')}"></label>
        <label>Email<input id="n-email" value="${esc(n.email || '')}"></label>
        <label>Phone<input id="n-phone" value="${esc(n.phone || '')}"></label>
        <label>Address<input id="n-address" value="${esc(n.address || '')}"></label>
        <label>Currency<input id="n-currency" maxlength="3" value="${esc(n.currency || home())}" ${n.id && n.balance ? 'disabled' : ''}></label>
        <label class="full">Notes<textarea id="n-notes" rows="2">${esc(n.notes || '')}</textarea></label></div>
        <div class="foot"><button data-close>Cancel</button><button class="primary" id="n-save">Save</button></div>`);
      $('#n-save', m.el).onclick = () => run(async () => {
        const body = {};
        ['name', 'company', 'email', 'phone', 'address', 'notes'].forEach(f => { body[f] = $('#n-' + f, m.el).value; });
        if (!$('#n-currency', m.el).disabled) body.currency = $('#n-currency', m.el).value;
        await api(n.id ? 'PUT' : 'POST', path + (n.id ? '/' + n.id : ''), body);
        m.close(); toast('Saved'); render();
      });
    };
    $('#n-inact').onchange = e => { showInactiveNames[kind] = e.target.checked; render(); };
    $('#n-new').onclick = () => form();
    main.onclick = e => run(async () => {
      const ed = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]'), tg = e.target.closest('[data-toggle]'), inv = e.target.closest('[data-inv]');
      if (ed) form(find(ed.dataset.edit));
      if (inv) invoiceModal(null, +inv.dataset.inv);
      const bl = e.target.closest('[data-bill]');
      if (bl) docModal('purchases', null, +bl.dataset.bill);
      if (del && confirm(`Delete "${find(del.dataset.del).name}"?`)) { await api('DELETE', `${path}/${del.dataset.del}`); toast('Deleted'); render(); }
      if (tg) { const n = find(tg.dataset.toggle); await api('PUT', `${path}/${n.id}`, {active: !n.active}); render(); }
    });
  };
}
pages.customers = namesPage('customer');
pages.vendors = namesPage('vendor');

/* ------------------------------------------------------------------ journal entries */
pages.journals = async main => {
  const list = await api('GET', '/api/journals');
  main.innerHTML = `<div class="head"><h1>Journal Entries</h1><button class="primary" id="j-new">New journal entry</button></div>
    <div class="tbl"><table><thead><tr><th>Date</th><th>No.</th><th>Memo</th><th>Accounts</th><th class="num">Amount</th><th></th></tr></thead>
    <tbody>${list.map(j => `<tr><td class="nowrap">${j.date}</td><td>${esc(j.ref)}</td><td>${esc(j.memo)}</td><td>${esc(j.accounts)}</td>
      <td class="num">${fmt(j.total)}</td><td class="actions">${j.reconciled ? '<span class="badge done">Reconciled</span>' : ''}
      <button class="small" data-edit="${j.id}">Edit</button><button class="small danger" data-del="${j.id}">Delete</button></td></tr>`).join('') ||
      '<tr><td colspan="6" class="empty">No journal entries yet.</td></tr>'}</tbody></table></div>`;
  $('#j-new').onclick = () => journalModal();
  main.onclick = e => run(async () => {
    const ed = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]');
    if (ed) journalModal(list.find(j => j.id == ed.dataset.edit));
    if (del && confirm('Delete this journal entry?')) { await api('DELETE', '/api/transactions/' + del.dataset.del); toast('Deleted'); render(); }
  });
};

async function journalModal(j) {
  const edit = !!(j && j.id);
  j = j || {date: today(), lines: []};
  const ref = edit ? j.ref : (await api('GET', '/api/journals/next_ref')).ref;
  const line = (l = {}) => `<tr><td><select class="l-acct">${acctOptions(l.account_id)}</select></td>
    <td><input class="num l-dr" value="${l.debit ? fmt(l.debit) : ''}"></td><td><input class="num l-cr" value="${l.credit ? fmt(l.credit) : ''}"></td>
    <td><input class="l-memo" value="${esc(l.memo || '')}"></td><td><select class="l-name">${nameOptions(l.name_id)}</select></td>
    <td><button class="small" data-rm title="Remove line">&times;</button></td></tr>`;
  const lines = j.lines.length ? j.lines : [{}, {}, {}, {}];
  const m = modal(edit ? `Journal entry #${j.ref}` : 'New journal entry', `<div class="form three">
    <label>Date<input type="date" id="j-date" value="${j.date}"></label>
    <label>Journal no.<input id="j-ref" value="${esc(ref)}"></label>
    <label>Memo<input id="j-memo" value="${esc(j.memo || '')}"></label></div>
    <table class="lines" style="margin-top:12px"><thead><tr><th style="width:26%">Account</th><th class="num">Debits</th><th class="num">Credits</th><th>Description</th><th style="width:18%">Name</th><th></th></tr></thead>
    <tbody>${lines.map(line).join('')}</tbody></table>
    <button class="small" id="j-add">Add line</button>
    <div class="totals"><span>Debits <span id="j-td"></span></span><span>Credits <span id="j-tc"></span></span><span id="j-diff"></span></div>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="j-save">Save</button></div>`, true);
  const recalc = () => {
    const td = $$('.l-dr', m.el).reduce((s, i) => s + cents(i.value), 0), tc = $$('.l-cr', m.el).reduce((s, i) => s + cents(i.value), 0);
    $('#j-td', m.el).textContent = money(td); $('#j-tc', m.el).textContent = money(tc);
    $('#j-diff', m.el).innerHTML = td === tc ? '<span class="diff-ok">Balanced</span>' : `<span class="diff-bad">Out of balance by ${money(Math.abs(td - tc))}</span>`;
  };
  recalc();
  m.body.addEventListener('input', recalc);
  m.body.addEventListener('click', e => { if (e.target.closest('[data-rm]')) { e.target.closest('tr').remove(); recalc(); } });
  $('#j-add', m.el).onclick = () => $('tbody', m.el).insertAdjacentHTML('beforeend', line());
  $('#j-save', m.el).onclick = () => run(async () => {
    const body = {date: $('#j-date', m.el).value, ref: $('#j-ref', m.el).value, memo: $('#j-memo', m.el).value,
      lines: $$('tbody tr', m.el).map(tr => ({account_id: $('.l-acct', tr).value || null, debit: $('.l-dr', tr).value, credit: $('.l-cr', tr).value,
        memo: $('.l-memo', tr).value, name_id: $('.l-name', tr).value || null}))};
    try {
      await api(edit ? 'PUT' : 'POST', '/api/journals' + (edit ? '/' + j.id : ''), body);
    } catch (e) {
      if (!/already used/.test(e.message) || !confirm(e.message)) throw e;
      await api(edit ? 'PUT' : 'POST', '/api/journals' + (edit ? '/' + j.id : ''), Object.assign(body, {allow_duplicate: true}));
    }
    m.close(); toast('Journal entry saved'); render();
  });
}

/* ------------------------------------------------------------------ invoices & payments */
/* ------------------------------------------------------------------ invoices & bills (shared) */
const SIDES = {
  sales: {doc: 'invoices', pay: 'payments', name: 'customer', nameCol: 'customer_id', fk: 'invoice_id', acctCol: 'deposit_account_id',
    acctLabel: 'deposit_account', label: 'Invoice', labels: 'Invoices', title: 'Invoices & Payments', payLabel: 'Receive payment',
    payTitle: 'Payments received', acctTitle: 'Deposit to', payVerb: 'received', amountTitle: 'Amount received'},
  purchases: {doc: 'bills', pay: 'bill_payments', name: 'vendor', nameCol: 'vendor_id', fk: 'bill_id', acctCol: 'pay_account_id',
    acctLabel: 'pay_account', label: 'Bill', labels: 'Bills', title: 'Bills & Expenses', payLabel: 'Pay bills',
    payTitle: 'Bill payments', acctTitle: 'Pay from', payVerb: 'paid', amountTitle: 'Amount paid'},
};
const docState = {sales: {tab: 'docs', status: 'all'}, purchases: {tab: 'docs', status: 'all'}};
const home = () => S.meta.home_currency || 'USD';
const nameById = (kind, id) => (kind === 'customer' ? S.customers : S.vendors).find(n => n.id == id);
const curOf = (kind, id) => { const n = nameById(kind, id); return (n && n.currency) || home(); };

function docPage(key) {
  const sd = SIDES[key], st = docState[key];
  return async main => {
    let body;
    if (st.tab === 'docs') {
      const list = await api('GET', `/api/${sd.doc}?status=${st.status}`);
      body = `<div class="bar"><label>Show<select id="v-status">${[['all', 'All ' + sd.labels.toLowerCase()], ['open', 'Unpaid'], ['overdue', 'Overdue'], ['paid', 'Paid']].map(([v, l]) =>
        `<option value="${v}" ${st.status === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label></div>
        <div class="tbl"><table><thead><tr><th>No.</th><th>${sd.name === 'customer' ? 'Customer' : 'Vendor'}</th><th>Date</th><th>Due</th><th class="num">Total</th><th class="num">Balance</th><th>Status</th><th></th></tr></thead>
        <tbody>${list.map(i => `<tr><td>${esc(i.number)}</td><td>${esc(i[sd.name])}</td><td class="nowrap">${i.date}</td><td class="nowrap">${i.due_date}</td>
          <td class="num">${fmt(i.total)}${i.currency !== home() ? ' ' + i.currency : ''}</td><td class="num">${fmt(i.balance)}</td><td><span class="badge ${i.status}">${i.status}</span></td>
          <td class="actions">${i.balance > 0 ? `<button class="small primary" data-pay="${i.id}" data-name="${i[sd.nameCol]}">${key === 'sales' ? 'Receive payment' : 'Pay'}</button>` : ''}
          <button class="small" data-edit="${i.id}">Edit</button>${key === 'sales' ? `<button class="small" data-print="${i.id}">Print</button>` : ''}
          <button class="small danger" data-del="${i.id}">Delete</button></td></tr>`).join('') ||
          `<tr><td colspan="8" class="empty">No ${sd.labels.toLowerCase()}.</td></tr>`}</tbody></table></div>`;
    } else {
      const list = await api('GET', `/api/${sd.pay}`);
      body = `<div class="tbl"><table><thead><tr><th>Date</th><th>${sd.name === 'customer' ? 'Customer' : 'Vendor'}</th><th>Ref</th><th>Method</th><th>${sd.acctTitle}</th><th>Applied to</th><th class="num">Amount</th><th class="num">Unapplied</th><th></th></tr></thead>
        <tbody>${list.map(p => `<tr><td class="nowrap">${p.date}</td><td>${esc(p[sd.name])}</td><td>${esc(p.ref)}</td><td>${esc(p.method)}</td><td>${esc(p[sd.acctLabel])}</td>
          <td>${p.applications.map(a => esc(a.number ? '#' + a.number : a.date)).join(', ')}</td><td class="num">${fmt(p.amount)}${p.currency !== home() ? ' ' + p.currency : ''}</td><td class="num">${p.unapplied ? fmt(p.unapplied) : ''}</td>
          <td class="actions"><button class="small" data-pedit="${p.id}">Edit</button><button class="small danger" data-pdel="${p.id}">Delete</button></td></tr>`).join('') ||
          `<tr><td colspan="9" class="empty">No ${sd.payTitle.toLowerCase()} yet.</td></tr>`}</tbody></table></div>`;
    }
    main.innerHTML = `<div class="head"><h1>${sd.title.replace('&', '&amp;')}</h1><button id="v-pay">${sd.payLabel}</button><button class="primary" id="v-new">New ${sd.label.toLowerCase()}</button></div>
      <div class="tabs"><button data-tab="docs" class="${st.tab === 'docs' ? 'active' : ''}">${sd.labels}</button>
      <button data-tab="pays" class="${st.tab === 'pays' ? 'active' : ''}">${sd.payTitle}</button></div>${body}`;
    $$('[data-tab]', main).forEach(b => b.onclick = () => { st.tab = b.dataset.tab; render(); });
    if ($('#v-status')) $('#v-status').onchange = e => { st.status = e.target.value; render(); };
    $('#v-new').onclick = () => docModal(key);
    $('#v-pay').onclick = () => payModal(key, {});
    main.onclick = e => run(async () => {
      const t = e.target, g = k => t.closest(`[data-${k}]`);
      if (g('edit')) docModal(key, +g('edit').dataset.edit);
      if (g('print')) printInvoice(+g('print').dataset.print);
      if (g('pay')) payModal(key, {name_id: +g('pay').dataset.name, doc_id: +g('pay').dataset.pay});
      if (g('pedit')) payModal(key, {payment_id: +g('pedit').dataset.pedit});
      if (g('del') && confirm(`Delete this ${sd.label.toLowerCase()}?`)) { await api('DELETE', `/api/${sd.doc}/${g('del').dataset.del}`); toast('Deleted'); render(); }
      if (g('pdel') && confirm(`Delete this payment? The ${sd.labels.toLowerCase()} it paid will show as unpaid again.`)) { await api('DELETE', `/api/${sd.pay}/${g('pdel').dataset.pdel}`); toast('Deleted'); render(); }
    });
  };
}
pages.invoices = docPage('sales');
pages.bills = docPage('purchases');
const invoiceModal = (id, customerId) => docModal('sales', id, customerId);

function itemOptions(sel) {
  return '<option value=""></option>' + S.items.filter(i => i.active || i.id == sel)
    .map(i => `<option value="${i.id}" ${i.id == sel ? 'selected' : ''}>${esc(i.name)}${i.type === 'inventory' ? ` (${i.qty_on_hand} on hand)` : ''}</option>`).join('');
}

async function docModal(key, id, nameId) {
  const sd = SIDES[key], sales = key === 'sales';
  const v = id ? await api('GET', `/api/${sd.doc}/${id}`)
    : {[sd.nameCol]: nameId, date: today(), terms: 'Net 30', lines: [], number: sales ? (await api('GET', '/api/invoices/next_number')).number : '',
       tax_rate_id: null};
  const acctOk = sales ? (a => ['Income', 'Other Income'].includes(a.type))
    : (a => !['Accounts Receivable', 'Accounts Payable', 'Bank', 'Credit Card', 'Income', 'Other Income', 'Equity'].includes(a.type));
  const defAcct = sales ? (S.accounts.find(a => a.active && a.type === 'Income') || {}).id : null;
  const line = (l = {}) => `<tr><td><select class="l-item">${itemOptions(l.item_id)}</select></td><td><input class="l-desc" value="${esc(l.description || '')}"></td>
    <td><input class="num l-qty" value="${l.qty == null ? 1 : l.qty}"></td><td><input class="num l-rate" value="${l.rate == null ? '' : +(+l.rate).toFixed(4)}"></td>
    <td><input class="num l-amt" value="${l.amount == null ? '' : fmt(l.amount)}"></td>
    <td><select class="l-acct">${acctOptions(l.account_id || defAcct, a => acctOk(a) || a.id === l.account_id || a.system === 'inv')}</select></td>
    ${sales ? `<td style="text-align:center"><input type="checkbox" class="l-tax" ${l.taxable !== false ? 'checked' : ''} title="Taxable"></td>` : ''}
    <td><button class="small" data-rm title="Remove line">&times;</button></td></tr>`;
  const rates = S.taxRates.filter(r => r.active || r.id === v.tax_rate_id);
  const m = modal(id ? `${sd.label}${v.number ? ' #' + v.number : ''}` : `New ${sd.label.toLowerCase()}`, `<div class="form four">
    <label>${sales ? 'Customer' : 'Vendor'}<select id="v-name">${nameOptions(v[sd.nameCol], [sd.name])}</select></label>
    <label>${sales ? 'Invoice no.' : 'Bill no. (optional)'}<input id="v-num" value="${esc(v.number)}"></label>
    <label>${sd.label} date<input type="date" id="v-date" value="${v.date}"></label>
    <label>Terms<select id="v-terms"><option value=""></option>${S.meta.terms.map(t => `<option ${t === v.terms ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
    <label>Due date<input type="date" id="v-due" value="${v.due_date || ''}"></label>
    <label id="v-fx-wrap" style="display:none"><span id="v-fx-label">Exchange rate</span><input id="v-fx" class="num" style="width:100%" value="${v.exchange_rate && v.currency !== home() ? v.exchange_rate : ''}"></label>
    <label class="full" style="grid-column: span 2">${sales ? 'Message / memo' : 'Memo'}<input id="v-memo" value="${esc(v.memo || '')}"></label></div>
    <div class="tbl" style="margin-top:12px;border:0"><table class="lines"><thead><tr><th style="width:16%">Product / service</th><th>Description</th><th class="num" style="width:7%">Qty</th><th class="num" style="width:10%">Rate</th>
      <th class="num" style="width:11%">Amount</th><th style="width:20%">${sales ? 'Income account' : 'Category'}</th>${sales ? '<th>Tax</th>' : ''}<th></th></tr></thead>
    <tbody>${(v.lines.length ? v.lines : [{}, {}]).map(line).join('')}</tbody></table></div>
    <button class="small" id="v-add">Add line</button>
    ${sales ? `<div class="bar" style="justify-content:flex-end;margin-top:10px"><label class="inline">Sales tax<select id="v-taxrate"><option value="">No sales tax</option>
      ${rates.map(r => `<option value="${r.id}" data-rate="${r.rate}" ${r.id === v.tax_rate_id ? 'selected' : ''}>${esc(r.name)} (${r.rate}%)</option>`).join('')}</select></label></div>` : ''}
    <div class="totals">${sales ? '<span>Subtotal <span id="v-sub"></span></span><span>Tax <span id="v-tax"></span></span>' : ''}<span>Total <span id="v-total"></span> <span id="v-cur"></span></span>
      ${id ? `<span>Paid ${fmt(v.applied)}</span><span>Balance due <span id="v-bal"></span></span>` : ''}</div>
    <div class="totals muted" id="v-homeline" style="font-weight:400"></div>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="v-save">Save</button></div>`, true);
  const cur = () => curOf(sd.name, $('#v-name', m.el).value);
  const syncCur = () => {
    const c = cur(), foreign = c !== home();
    $('#v-fx-wrap', m.el).style.display = foreign ? '' : 'none';
    $('#v-fx-label', m.el).textContent = `${home()} per 1 ${c}`;
    $('#v-cur', m.el).textContent = foreign ? c : '';
  };
  const recalc = e => {
    if (e && e.target.matches('.l-qty, .l-rate')) {
      const tr = e.target.closest('tr'), f = s => parseFloat(String($(s, tr).value).replace(/,/g, '')) || 0;
      $('.l-amt', tr).value = money(Math.round(f('.l-qty') * f('.l-rate') * 100 + 1e-7));
    }
    if (e && e.target.matches('.l-amt')) $('.l-rate', e.target.closest('tr')).value = '';
    const sub = $$('.l-amt', m.el).reduce((s, i) => s + cents(i.value), 0);
    let tax = 0;
    if (sales) {
      const o = $('#v-taxrate', m.el).selectedOptions[0], r = o && o.dataset.rate ? +o.dataset.rate : 0;
      const taxable = $$('tbody tr', m.el).filter(tr => $('.l-tax', tr).checked).reduce((s, tr) => s + cents($('.l-amt', tr).value), 0);
      tax = Math.round(taxable * r / 100);
      $('#v-sub', m.el).textContent = money(sub); $('#v-tax', m.el).textContent = money(tax);
    }
    $('#v-total', m.el).textContent = money(sub + tax);
    if ($('#v-bal', m.el)) $('#v-bal', m.el).textContent = money(sub + tax - cents(v.applied));
    const rate = parseFloat($('#v-fx', m.el).value);
    $('#v-homeline', m.el).textContent = cur() !== home() && rate > 0 ? `= ${money(Math.round((sub + tax) * rate))} ${home()}` : '';
  };
  const pickItem = tr => {
    const it = S.items.find(i => i.id == $('.l-item', tr).value);
    if (!it) return;
    $('.l-desc', tr).value = it.description || it.name;
    $('.l-rate', tr).value = sales ? it.sales_price : it.purchase_cost;
    const acc = sales ? it.income_account_id : (it.type === 'inventory' ? it.asset_account_id : it.expense_account_id);
    if (acc) $('.l-acct', tr).value = acc;
    if (sales) $('.l-tax', tr).checked = it.taxable;
    const q = parseFloat($('.l-qty', tr).value) || 1;
    $('.l-amt', tr).value = money(Math.round(q * $('.l-rate', tr).value * 100));
  };
  const due = () => {
    const days = {'Due on receipt': 0, 'Net 15': 15, 'Net 30': 30, 'Net 60': 60}[$('#v-terms', m.el).value];
    if (days == null || !$('#v-date', m.el).value) return;
    const d = new Date($('#v-date', m.el).value + 'T00:00:00'); d.setDate(d.getDate() + days); $('#v-due', m.el).value = iso(d);
  };
  if (!id) due();
  syncCur(); recalc();
  m.body.addEventListener('input', recalc);
  m.body.addEventListener('change', e => {
    if (e.target.matches('.l-item')) pickItem(e.target.closest('tr'));
    if (e.target.matches('#v-name')) syncCur();
    recalc();
  });
  $('#v-terms', m.el).onchange = () => { due(); };
  $('#v-date', m.el).onchange = due;
  m.body.addEventListener('click', e => { if (e.target.closest('[data-rm]')) { e.target.closest('tr').remove(); recalc(); } });
  $('#v-add', m.el).onclick = () => $('tbody', m.el).insertAdjacentHTML('beforeend', line());
  $('#v-save', m.el).onclick = () => run(async () => {
    const body = {[sd.nameCol]: $('#v-name', m.el).value, number: $('#v-num', m.el).value, date: $('#v-date', m.el).value,
      terms: $('#v-terms', m.el).value, due_date: $('#v-due', m.el).value, memo: $('#v-memo', m.el).value,
      lines: $$('tbody tr', m.el).map(tr => ({item_id: $('.l-item', tr).value || null, description: $('.l-desc', tr).value, qty: $('.l-qty', tr).value,
        rate: $('.l-rate', tr).value, amount: $('.l-rate', tr).value === '' ? $('.l-amt', tr).value : '', account_id: $('.l-acct', tr).value || null,
        taxable: sales ? $('.l-tax', tr).checked : false}))
        .filter(l => l.item_id || l.description || cents(l.amount) || (l.rate && cents(l.rate)))};
    if (sales) body.tax_rate_id = $('#v-taxrate', m.el).value || null;
    if (cur() !== home()) body.exchange_rate = $('#v-fx', m.el).value;
    await api(id ? 'PUT' : 'POST', `/api/${sd.doc}` + (id ? '/' + id : ''), body);
    m.close(); toast(`${sd.label} saved`); docState[key].tab = 'docs'; render();
  });
}

async function printInvoice(id) {
  const v = await api('GET', '/api/invoices/' + id);
  const c = S.customers.find(x => x.id === v.customer_id) || {};
  const w = window.open('', '_blank');
  if (!w) return toast('Allow pop-ups to print invoices', true);
  const cur = v.currency !== home() ? ' ' + v.currency : '';
  w.document.write(`<html><head><title>Invoice ${esc(v.number)}</title><style>body{font:14px Helvetica,Arial,sans-serif;margin:40px;color:#222}
    table{border-collapse:collapse;width:100%;margin-top:24px}th,td{padding:8px;border-bottom:1px solid #ddd;text-align:left}.r{text-align:right}h1{margin:0}</style></head><body>
    <h1>INVOICE</h1><p><b>${esc(S.meta.company_name)}</b></p>
    <p><b>Bill to:</b> ${esc(v.customer)}${c.company ? '<br>' + esc(c.company) : ''}${c.address ? '<br>' + esc(c.address) : ''}</p>
    <p>Invoice #: ${esc(v.number)}<br>Date: ${v.date}<br>Terms: ${esc(v.terms)}<br>Due: ${v.due_date}</p>
    <table><tr><th>Description</th><th class="r">Qty</th><th class="r">Rate</th><th class="r">Amount</th></tr>
    ${v.lines.map(l => `<tr><td>${esc(l.description)}</td><td class="r">${l.qty}</td><td class="r">${fmt(l.rate)}</td><td class="r">${fmt(l.amount)}</td></tr>`).join('')}
    ${v.tax ? `<tr><td colspan="3" class="r">Subtotal</td><td class="r">${fmt(v.subtotal)}</td></tr><tr><td colspan="3" class="r">Sales tax</td><td class="r">${fmt(v.tax)}</td></tr>` : ''}
    <tr><td colspan="3" class="r"><b>Total</b></td><td class="r"><b>${fmt(v.total)}${cur}</b></td></tr>
    <tr><td colspan="3" class="r">Payments</td><td class="r">${fmt(v.applied)}</td></tr>
    <tr><td colspan="3" class="r"><b>Balance due</b></td><td class="r"><b>${fmt(v.balance)}${cur}</b></td></tr></table>
    <p>${esc(v.memo)}</p></body></html>`);
  w.document.close(); w.print();
}

const paymentModal = opts => payModal('sales', {name_id: opts.customer_id, doc_id: opts.invoice_id, payment_id: opts.payment_id});

async function payModal(key, opts) {
  const sd = SIDES[key], sales = key === 'sales';
  const p = opts.payment_id ? await api('GET', `/api/${sd.pay}/${opts.payment_id}`) : null;
  const udf = S.accounts.find(a => a.system === 'udf');
  const defAcct = sales ? (udf && udf.id) : (S.accounts.find(a => a.active && a.type === 'Bank') || {}).id;
  const m = modal(p ? 'Edit payment' : sd.payLabel, `<div class="form three">
    <label>${sales ? 'Customer' : 'Vendor'}<select id="p-name">${nameOptions(p ? p[sd.nameCol] : opts.name_id, [sd.name])}</select></label>
    <label>Payment date<input type="date" id="p-date" value="${p ? p.date : today()}"></label>
    <label>${sd.amountTitle} <span id="p-cur"></span><input id="p-amt" class="num" style="width:100%" value="${p ? fmt(p.amount) : ''}"></label>
    <label>${sd.acctTitle}<select id="p-acct">${acctOptions(p ? p[sd.acctCol] : defAcct,
      a => sales ? (a.type === 'Bank' || a.system === 'udf') : (a.type === 'Bank' || a.type === 'Credit Card'), false)}</select></label>
    <label>Payment method<select id="p-method">${['', 'Check', 'Cash', 'Credit card', 'ACH / bank transfer', 'Other'].map(x =>
      `<option ${p && p.method === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
    <label>Reference no.<input id="p-ref" value="${esc(p ? p.ref : '')}"></label>
    <label id="p-fx-wrap" style="display:none"><span id="p-fx-label">Exchange rate</span><input id="p-fx" class="num" style="width:100%" value="${p && p.currency !== home() ? p.exchange_rate : ''}"></label>
    <label class="full" style="grid-column: span 2">Memo<input id="p-memo" value="${esc(p ? p.memo : '')}"></label></div>
    <h2>Open ${sd.labels.toLowerCase()}</h2><div id="p-docs"></div>
    <div class="totals"><span>Applied <span id="p-app"></span></span><span>Unapplied (${sales ? 'customer credit' : 'vendor credit'}) <span id="p-un"></span></span></div>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="p-save">Save payment</button></div>`, true);
  const mine = {};
  if (p) p.applications.forEach(a => { mine[a[sd.fk]] = cents(a.amount); });
  let rows = [], touched = !!p;
  const recalc = () => {
    const app = $$('.p-apply', m.el).reduce((s, i) => s + cents(i.value), 0);
    $('#p-app', m.el).textContent = money(app);
    const un = cents($('#p-amt', m.el).value) - app;
    $('#p-un', m.el).innerHTML = `<span class="${un < 0 ? 'diff-bad' : ''}">${money(un)}</span>`;
  };
  const distribute = () => {
    let left = cents($('#p-amt', m.el).value);
    $$('.p-apply', m.el).forEach(i => { const use = Math.max(0, Math.min(left, +i.dataset.open)); i.value = use ? money(use) : ''; left -= use; });
  };
  const load = async () => {
    const nid = $('#p-name', m.el).value, c = curOf(sd.name, nid), foreign = c !== home();
    $('#p-cur', m.el).textContent = foreign ? '(' + c + ')' : '';
    $('#p-fx-wrap', m.el).style.display = foreign ? '' : 'none';
    $('#p-fx-label', m.el).textContent = `${home()} per 1 ${c} today`;
    rows = [];
    if (nid) {
      const all = await api('GET', `/api/${sd.doc}?${sd.nameCol}=${nid}`);
      rows = all.map(i => Object.assign(i, {open: cents(i.balance) + (mine[i.id] || 0)})).filter(i => i.open > 0).reverse();
    }
    $('#p-docs', m.el).innerHTML = rows.length ? `<div class="tbl"><table><thead><tr><th>${sd.label}</th><th>Date</th><th>Due</th><th class="num">Original</th><th class="num">Open balance</th><th class="num">Payment</th></tr></thead>
      <tbody>${rows.map(i => `<tr><td>${i.number ? '#' + esc(i.number) : ''}</td><td>${i.date}</td><td>${i.due_date}</td><td class="num">${fmt(i.total)}</td><td class="num">${money(i.open)}</td>
      <td class="num"><input class="num p-apply" data-id="${i.id}" data-open="${i.open}" value="${mine[i.id] ? money(mine[i.id]) : ''}"></td></tr>`).join('')}</tbody></table></div>`
      : `<div class="empty">No open ${sd.labels.toLowerCase()} for this ${sd.name}. The payment will be kept as an unapplied credit.</div>`;
    if (!p && opts.doc_id && !touched) {
      const i = rows.find(x => x.id === opts.doc_id);
      if (i) { $('#p-amt', m.el).value = money(i.open); $(`.p-apply[data-id="${i.id}"]`, m.el).value = money(i.open); }
    }
    recalc();
  };
  await run(load);
  $('#p-name', m.el).onchange = () => run(load);
  $('#p-amt', m.el).oninput = () => { touched = true; distribute(); recalc(); };
  $('#p-docs', m.el).addEventListener('input', recalc);
  $('#p-save', m.el).onclick = () => run(async () => {
    const body = {[sd.nameCol]: $('#p-name', m.el).value, date: $('#p-date', m.el).value, amount: $('#p-amt', m.el).value,
      [sd.acctCol]: $('#p-acct', m.el).value, method: $('#p-method', m.el).value, ref: $('#p-ref', m.el).value, memo: $('#p-memo', m.el).value,
      applications: $$('.p-apply', m.el).filter(i => cents(i.value)).map(i => ({[sd.fk]: +i.dataset.id, amount: i.value}))};
    if (curOf(sd.name, body[sd.nameCol]) !== home()) body.exchange_rate = $('#p-fx', m.el).value;
    await api(p ? 'PUT' : 'POST', `/api/${sd.pay}` + (p ? '/' + p.id : ''), body);
    m.close(); toast('Payment saved'); render();
  });
}

/* ------------------------------------------------------------------ products & services */
let showInactiveItems = false;
pages.items = async main => {
  const list = S.items.filter(i => showInactiveItems || i.active);
  main.innerHTML = `<div class="head"><h1>Products &amp; Services</h1>
    <label class="inline"><input type="checkbox" id="i-inact" ${showInactiveItems ? 'checked' : ''}> Show inactive</label>
    <button class="primary" id="i-new">New product or service</button></div>
    <div class="tbl"><table><thead><tr><th>Name</th><th>SKU</th><th>Type</th><th class="num">Sales price</th><th class="num">Cost</th><th class="num">Qty on hand</th><th class="num">Value</th><th>Taxable</th><th></th></tr></thead>
    <tbody>${list.map(i => `<tr class="${i.active ? '' : 'muted'}"><td>${esc(i.name)}${i.active ? '' : ' <span class="badge">inactive</span>'}<div class="muted">${esc(i.description)}</div></td>
      <td>${esc(i.sku)}</td><td>${i.type_label}</td><td class="num">${fmt(i.sales_price)}</td><td class="num">${fmt(i.purchase_cost)}</td>
      <td class="num">${i.type === 'inventory' ? `<span class="${i.qty_on_hand < 0 ? 'neg' : ''}">${i.qty_on_hand}</span>` : ''}</td><td class="num">${i.type === 'inventory' ? fmt(i.value) : ''}</td>
      <td>${i.taxable ? 'Yes' : ''}</td>
      <td class="actions">${i.type === 'inventory' ? `<button class="small" data-adj="${i.id}">Adjust qty</button>` : ''}
        <button class="small" data-edit="${i.id}">Edit</button><button class="small" data-toggle="${i.id}">${i.active ? 'Make inactive' : 'Make active'}</button>
        <button class="small danger" data-del="${i.id}">Delete</button></td></tr>`).join('') ||
      '<tr><td colspan="9" class="empty">No products or services yet. Add the things you sell or buy, so invoices and bills fill in prices and accounts for you.</td></tr>'}</tbody></table></div>`;
  $('#i-inact').onchange = e => { showInactiveItems = e.target.checked; render(); };
  $('#i-new').onclick = () => itemModal();
  main.onclick = e => run(async () => {
    const g = k => e.target.closest(`[data-${k}]`), find = el => S.items.find(i => i.id == el.dataset[Object.keys(el.dataset)[0]]);
    if (g('edit')) itemModal(find(g('edit')));
    if (g('adj')) adjustModal(find(g('adj')));
    if (g('toggle')) { const i = find(g('toggle')); await api('PUT', '/api/items/' + i.id, {active: !i.active}); render(); }
    if (g('del') && confirm(`Delete "${find(g('del')).name}"?`)) { await api('DELETE', '/api/items/' + g('del').dataset.del); toast('Deleted'); render(); }
  });
};

function itemModal(it) {
  const edit = !!it;
  it = it || {type: 'service', taxable: false};
  const m = modal(edit ? 'Edit ' + it.name : 'New product or service', `<div class="form">
    <label>Type<select id="it-type" ${edit && it.type === 'inventory' ? 'disabled' : ''}>${[['service', 'Service'], ['non_inventory', 'Non-inventory product'], ['inventory', 'Inventory product (track quantity)']]
      .map(([v, l]) => `<option value="${v}" ${it.type === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
    <label>Name<input id="it-name" value="${esc(it.name || '')}"></label>
    <label>SKU (optional)<input id="it-sku" value="${esc(it.sku || '')}"></label>
    <label class="inline" style="align-self:end"><input type="checkbox" id="it-tax" ${it.taxable ? 'checked' : ''}> Taxable</label>
    <label class="full">Description on invoices and bills<input id="it-desc" value="${esc(it.description || '')}"></label>
    <label>Sales price<input id="it-price" class="num" style="width:100%" value="${it.sales_price != null ? it.sales_price : ''}"></label>
    <label>Income account<select id="it-inc">${acctOptions(it.income_account_id || (S.accounts.find(a => a.type === 'Income' && a.active) || {}).id, a => ['Income', 'Other Income'].includes(a.type), false)}</select></label>
    <label>Purchase cost<input id="it-cost" class="num" style="width:100%" value="${it.purchase_cost != null ? it.purchase_cost : ''}"></label>
    <label><span id="it-exp-label">Expense account</span><select id="it-exp"></select></label>
    <label class="inv-only">Inventory asset account<select id="it-asset">${acctOptions(it.asset_account_id || (S.accounts.find(a => a.system === 'inv') || {}).id, a => a.type === 'Other Current Asset', false)}</select></label>
    ${edit ? '' : `<label class="inv-only">Quantity on hand<input id="it-qty" class="num" style="width:100%" value="0"></label>
    <label class="inv-only">As of<input type="date" id="it-asof" value="${today()}"></label>`}</div>
    <p class="muted inv-only">Inventory is valued at average cost. When you sell, the cost moves from the inventory asset account to cost of goods sold.</p>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="it-save">Save</button></div>`);
  const sync = () => {
    const inv = $('#it-type', m.el).value === 'inventory';
    $$('.inv-only', m.el).forEach(el => { el.style.display = inv ? '' : 'none'; });
    $('#it-exp-label', m.el).textContent = inv ? 'Cost of goods sold account' : 'Expense account (for bills)';
    const cogs = (S.accounts.find(a => a.type === 'Cost of Goods Sold' && a.active) || {}).id;
    $('#it-exp', m.el).innerHTML = acctOptions(it.expense_account_id || (inv ? cogs : ''),
      a => inv ? ['Cost of Goods Sold', 'Expense'].includes(a.type) : ['Expense', 'Cost of Goods Sold', 'Other Expense', 'Other Current Asset', 'Fixed Asset'].includes(a.type), !inv);
  };
  sync();
  $('#it-type', m.el).onchange = sync;
  $('#it-save', m.el).onclick = () => run(async () => {
    const type = $('#it-type', m.el).value;
    const body = {type, name: $('#it-name', m.el).value, sku: $('#it-sku', m.el).value, description: $('#it-desc', m.el).value,
      taxable: $('#it-tax', m.el).checked, sales_price: $('#it-price', m.el).value, purchase_cost: $('#it-cost', m.el).value,
      income_account_id: $('#it-inc', m.el).value || null, expense_account_id: $('#it-exp', m.el).value || null};
    if (type === 'inventory') body.asset_account_id = $('#it-asset', m.el).value;
    if (!edit && type === 'inventory') { body.qty_on_hand = $('#it-qty', m.el).value; body.as_of = $('#it-asof', m.el).value; }
    await api(edit ? 'PUT' : 'POST', '/api/items' + (edit ? '/' + it.id : ''), body);
    m.close(); toast('Saved'); render();
  });
}

function adjustModal(it) {
  const m = modal('Adjust quantity: ' + it.name, `<p>On hand now: <b>${it.qty_on_hand}</b> &middot; value ${fmt(it.value)} &middot; average cost ${it.avg_cost == null ? '-' : fmt(it.avg_cost)}</p>
    <div class="form">
    <label>Date<input type="date" id="aj-date" value="${today()}"></label>
    <label>New quantity on hand<input id="aj-qty" class="num" style="width:100%" value="${it.qty_on_hand}"></label>
    <label>Unit cost for added stock (optional)<input id="aj-cost" class="num" style="width:100%"></label>
    <label>Adjustment account<select id="aj-acct">${acctOptions(it.expense_account_id, a => ['Cost of Goods Sold', 'Expense', 'Other Expense'].includes(a.type), false)}</select></label>
    <label class="full">Memo<input id="aj-memo" placeholder="Stock count, damaged goods, ..."></label></div>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="aj-save">Save adjustment</button></div>`);
  $('#aj-save', m.el).onclick = () => run(async () => {
    const change = (parseFloat($('#aj-qty', m.el).value) || 0) - it.qty_on_hand;
    if (!change) throw new Error('The quantity is unchanged');
    await api('POST', `/api/items/${it.id}/adjust`, {date: $('#aj-date', m.el).value, qty_change: +change.toFixed(6),
      unit_cost: $('#aj-cost', m.el).value, account_id: $('#aj-acct', m.el).value, memo: $('#aj-memo', m.el).value});
    m.close(); toast('Quantity adjusted'); render();
  });
}

/* ------------------------------------------------------------------ sales tax */
pages.salestax = async main => {
  const [pays, rep] = await Promise.all([api('GET', '/api/sales_tax/payments'),
    api('GET', `/api/reports/sales_tax?start=${today().slice(0, 4)}-01-01&end=${today()}`)]);
  const owed = rep.rows[rep.rows.length - 1].values[2];
  main.innerHTML = `<div class="head"><h1>Sales Tax</h1><button id="t-report">Sales tax report</button><button id="t-pay">Record tax payment</button><button class="primary" id="t-new">New tax rate</button></div>
    <div class="cards"><div class="card"><div class="label">Sales tax owed today</div><div class="big">${fmt(owed)}</div></div></div>
    <h2>Tax rates</h2>
    <div class="tbl"><table><thead><tr><th>Name</th><th class="num">Rate</th><th>Tax agency</th><th></th></tr></thead>
    <tbody>${S.taxRates.map(r => `<tr class="${r.active ? '' : 'muted'}"><td>${esc(r.name)}${r.active ? '' : ' <span class="badge">inactive</span>'}</td><td class="num">${r.rate}%</td><td>${esc(r.agency)}</td>
      <td class="actions"><button class="small" data-edit="${r.id}">Edit</button><button class="small" data-toggle="${r.id}">${r.active ? 'Make inactive' : 'Make active'}</button>
      <button class="small danger" data-del="${r.id}">Delete</button></td></tr>`).join('') ||
      '<tr><td colspan="4" class="empty">No tax rates yet. Add the combined rate you charge (for example "City 8.25%"), then choose it on invoices.</td></tr>'}</tbody></table></div>
    <h2>Tax payments</h2>
    <div class="tbl"><table><thead><tr><th>Date</th><th>Memo</th><th>Paid from</th><th class="num">Amount</th><th></th></tr></thead>
    <tbody>${pays.map(p => `<tr><td>${p.date}</td><td>${esc(p.memo)}</td><td>${esc((p.lines.find(l => l.credit) || {}).account || '')}</td><td class="num">${fmt(p.total)}</td>
      <td class="actions"><button class="small" data-txn="${p.id}">Open</button></td></tr>`).join('') ||
      '<tr><td colspan="5" class="empty">No tax payments recorded.</td></tr>'}</tbody></table></div>`;
  const rateForm = r => {
    r = r || {};
    const m = modal(r.id ? 'Edit tax rate' : 'New tax rate', `<div class="form">
      <label class="full">Name<input id="r-name" value="${esc(r.name || '')}" placeholder="e.g. San Jose 9.375%"></label>
      <label>Rate (%)<input id="r-rate" class="num" style="width:100%" value="${r.rate == null ? '' : r.rate}"></label>
      <label>Tax agency<input id="r-agency" value="${esc(r.agency || '')}" placeholder="e.g. CDTFA"></label></div>
      <div class="foot"><button data-close>Cancel</button><button class="primary" id="r-save">Save</button></div>`);
    $('#r-save', m.el).onclick = () => run(async () => {
      await api(r.id ? 'PUT' : 'POST', '/api/tax_rates' + (r.id ? '/' + r.id : ''), {name: $('#r-name', m.el).value, rate: $('#r-rate', m.el).value, agency: $('#r-agency', m.el).value});
      m.close(); toast('Saved'); render();
    });
  };
  $('#t-new').onclick = () => rateForm();
  $('#t-report').onclick = () => { location.hash = '#reports?type=sales_tax'; };
  $('#t-pay').onclick = () => liabilityModal('Record sales tax payment', '/api/sales_tax/payments', owed);
  main.onclick = e => run(async () => {
    const g = k => e.target.closest(`[data-${k}]`);
    if (g('txn')) return txnModal(+g('txn').dataset.txn);
    if (g('edit')) rateForm(S.taxRates.find(r => r.id == g('edit').dataset.edit));
    if (g('toggle')) { const r = S.taxRates.find(x => x.id == g('toggle').dataset.toggle); await api('PUT', '/api/tax_rates/' + r.id, {active: !r.active}); render(); }
    if (g('del') && confirm('Delete this tax rate?')) { await api('DELETE', '/api/tax_rates/' + g('del').dataset.del); render(); }
  });
};

function liabilityModal(title, url, owed) {
  const m = modal(title, `<div class="form">
    <label>Payment date<input type="date" id="l-date" value="${today()}"></label>
    <label>Amount<input id="l-amt" class="num" style="width:100%" value="${owed > 0 ? fmt(owed) : ''}"></label>
    <label>Paid from<select id="l-bank">${acctOptions((S.accounts.find(a => a.type === 'Bank' && a.active) || {}).id, a => a.type === 'Bank' || a.type === 'Credit Card', false)}</select></label>
    <label>Reference no.<input id="l-ref"></label>
    <label class="full">Memo<input id="l-memo"></label></div>
    <p class="muted">Amount owed now: ${fmt(owed)}</p>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="l-save">Save payment</button></div>`);
  $('#l-save', m.el).onclick = () => run(async () => {
    await api('POST', url, {date: $('#l-date', m.el).value, amount: $('#l-amt', m.el).value, bank_account_id: $('#l-bank', m.el).value,
      ref: $('#l-ref', m.el).value, memo: $('#l-memo', m.el).value});
    m.close(); toast('Payment recorded'); render();
  });
}

/* ------------------------------------------------------------------ payroll */
const pay = {tab: 'paychecks'};
pages.payroll = async main => {
  const [emps, checks, liab] = await Promise.all([api('GET', '/api/employees?include_inactive=1'), api('GET', '/api/paychecks'),
    api('GET', '/api/payroll/liability_payments')]);
  const pl = S.accounts.find(a => a.system === 'pl'), owed = pl ? pl.balance : 0;
  let body;
  if (pay.tab === 'paychecks') {
    body = `<div class="tbl"><table><thead><tr><th>Pay date</th><th>Employee</th><th>Check no.</th><th class="num">Gross</th><th class="num">Taxes &amp; deductions</th><th class="num">Net pay</th><th class="num">Employer taxes</th><th></th></tr></thead>
      <tbody>${checks.map(c => `<tr><td class="nowrap">${c.pay_date}</td><td>${esc(c.employee)}</td><td>${esc(c.check_no)}</td><td class="num">${fmt(c.gross)}</td>
        <td class="num">${fmt(c.gross - c.net)}</td><td class="num">${fmt(c.net)}</td><td class="num">${fmt(c.employer_taxes)}</td>
        <td class="actions">${c.reconciled ? '<span class="badge done">Reconciled</span>' : ''}<button class="small" data-pc="${c.id}">Edit</button><button class="small danger" data-pcdel="${c.id}">Delete</button></td></tr>`).join('') ||
        '<tr><td colspan="8" class="empty">No paychecks yet. Add your employees, then click Run payroll.</td></tr>'}</tbody></table></div>`;
  } else if (pay.tab === 'employees') {
    body = `<div class="bar"><button class="primary" id="e-new">New employee</button></div>
      <div class="tbl"><table><thead><tr><th>Name</th><th>Pay</th><th class="num">Federal W/H %</th><th class="num">State W/H %</th><th class="num">Gross this year</th><th></th></tr></thead>
      <tbody>${emps.map(e => `<tr class="${e.active ? '' : 'muted'}"><td>${esc(e.name)}${e.active ? '' : ' <span class="badge">inactive</span>'}<div class="muted">${esc(e.email)}</div></td>
        <td>${fmt(e.pay_rate)} ${e.pay_type === 'hourly' ? 'per hour' : 'per paycheck'}</td><td class="num">${e.fed_pct}</td><td class="num">${e.state_pct}</td><td class="num">${fmt(e.ytd_gross)}</td>
        <td class="actions"><button class="small" data-eedit="${e.id}">Edit</button><button class="small" data-etoggle="${e.id}">${e.active ? 'Make inactive' : 'Make active'}</button>
        <button class="small danger" data-edel="${e.id}">Delete</button></td></tr>`).join('') ||
        '<tr><td colspan="6" class="empty">No employees yet.</td></tr>'}</tbody></table></div>`;
  } else {
    body = `<div class="cards"><div class="card"><div class="label">Payroll liabilities owed</div><div class="big">${fmt(owed)}</div></div></div>
      <div class="bar"><button class="primary" id="lp-new">Pay payroll liabilities</button><button id="lp-set">Tax rates &amp; wage bases</button></div>
      <div class="tbl"><table><thead><tr><th>Date</th><th>Memo</th><th class="num">Amount</th><th></th></tr></thead>
      <tbody>${liab.map(p => `<tr><td>${p.date}</td><td>${esc(p.memo)}</td><td class="num">${fmt(p.total)}</td><td class="actions"><button class="small" data-txn="${p.id}">Open</button></td></tr>`).join('') ||
        '<tr><td colspan="4" class="empty">No liability payments yet.</td></tr>'}</tbody></table></div>`;
  }
  main.innerHTML = `<div class="head"><h1>Payroll</h1><button id="pr-report">Payroll summary</button><button class="primary" id="pr-run">Run payroll</button></div>
    <div class="note warn">Social Security, Medicare, FUTA and state unemployment are calculated from the rates in <b>Liabilities &gt; Tax rates</b>.
      Federal and state income tax withholding uses each employee's percentage; check it against IRS Publication 15-T and your state's tables, and type the exact amount on the paycheck if needed.</div>
    <div class="tabs">${[['paychecks', 'Paychecks'], ['employees', 'Employees'], ['liabilities', 'Liabilities']].map(([k, l]) =>
      `<button data-tab="${k}" class="${pay.tab === k ? 'active' : ''}">${l}</button>`).join('')}</div>${body}`;
  $$('[data-tab]', main).forEach(b => b.onclick = () => { pay.tab = b.dataset.tab; render(); });
  $('#pr-run').onclick = () => emps.some(e => e.active) ? paycheckModal(emps) : toast('Add an employee first', true);
  $('#pr-report').onclick = () => { location.hash = '#reports?type=payroll_summary'; };
  if ($('#e-new')) $('#e-new').onclick = () => employeeModal();
  if ($('#lp-new')) $('#lp-new').onclick = () => liabilityModal('Pay payroll liabilities', '/api/payroll/liability_payments', owed);
  if ($('#lp-set')) $('#lp-set').onclick = payrollSettingsModal;
  main.onclick = e => run(async () => {
    const g = k => e.target.closest(`[data-${k}]`);
    if (g('txn')) return txnModal(+g('txn').dataset.txn);
    if (g('pc')) paycheckModal(emps, await api('GET', '/api/paychecks/' + g('pc').dataset.pc));
    if (g('pcdel') && confirm('Delete this paycheck?')) { await api('DELETE', '/api/paychecks/' + g('pcdel').dataset.pcdel); toast('Deleted'); render(); }
    if (g('eedit')) employeeModal(emps.find(x => x.id == g('eedit').dataset.eedit));
    if (g('etoggle')) { const x = emps.find(y => y.id == g('etoggle').dataset.etoggle); await api('PUT', '/api/employees/' + x.id, {active: !x.active}); render(); }
    if (g('edel') && confirm('Delete this employee?')) { await api('DELETE', '/api/employees/' + g('edel').dataset.edel); render(); }
  });
};

function employeeModal(e) {
  e = e || {pay_type: 'hourly', fed_pct: 0, state_pct: 0};
  const m = modal(e.id ? 'Edit employee' : 'New employee', `<div class="form">
    <label class="full">Name<input id="e-name" value="${esc(e.name || '')}"></label>
    <label>Email<input id="e-email" value="${esc(e.email || '')}"></label><label>Phone<input id="e-phone" value="${esc(e.phone || '')}"></label>
    <label class="full">Address<input id="e-address" value="${esc(e.address || '')}"></label>
    <label>Pay type<select id="e-type"><option value="hourly" ${e.pay_type === 'hourly' ? 'selected' : ''}>Hourly</option><option value="salary" ${e.pay_type === 'salary' ? 'selected' : ''}>Salary (per paycheck)</option></select></label>
    <label>Pay rate<input id="e-rate" class="num" style="width:100%" value="${e.pay_rate == null ? '' : e.pay_rate}"></label>
    <label>Federal income tax withholding (% of gross)<input id="e-fed" class="num" style="width:100%" value="${e.fed_pct}"></label>
    <label>State income tax withholding (% of gross)<input id="e-state" class="num" style="width:100%" value="${e.state_pct}"></label></div>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="e-save">Save</button></div>`);
  $('#e-save', m.el).onclick = () => run(async () => {
    const body = {name: $('#e-name', m.el).value, email: $('#e-email', m.el).value, phone: $('#e-phone', m.el).value, address: $('#e-address', m.el).value,
      pay_type: $('#e-type', m.el).value, pay_rate: $('#e-rate', m.el).value, fed_pct: $('#e-fed', m.el).value, state_pct: $('#e-state', m.el).value};
    await api(e.id ? 'PUT' : 'POST', '/api/employees' + (e.id ? '/' + e.id : ''), body);
    m.close(); toast('Saved'); pay.tab = 'employees'; render();
  });
}

const PAY_FIELDS = [['fed_wh', 'Federal income tax'], ['state_wh', 'State income tax'], ['ss_ee', 'Social Security'], ['med_ee', 'Medicare'], ['other_ded', 'Other deductions']];
const ER_FIELDS = [['ss_er', 'Social Security'], ['med_er', 'Medicare'], ['futa', 'FUTA'], ['suta', 'State unemployment']];
async function paycheckModal(emps, pc) {
  const active = emps.filter(e => e.active || (pc && e.id === pc.employee_id));
  const bank = (S.accounts.find(a => a.type === 'Bank' && a.active) || {}).id;
  const m = modal(pc ? 'Edit paycheck' : 'Run payroll', `<div class="form four">
    <label>Employee<select id="pc-emp">${active.map(e => `<option value="${e.id}" ${pc && pc.employee_id === e.id ? 'selected' : ''}>${esc(e.name)}</option>`).join('')}</select></label>
    <label>Pay date<input type="date" id="pc-date" value="${pc ? pc.pay_date : today()}"></label>
    <label>Period start<input type="date" id="pc-ps" value="${pc ? pc.period_start : ''}"></label>
    <label>Period end<input type="date" id="pc-pe" value="${pc ? pc.period_end : ''}"></label>
    <label>Hours<input id="pc-hours" class="num" style="width:100%" value="${pc ? pc.hours : ''}"></label>
    <label>Gross pay<input id="pc-gross" class="num" style="width:100%" value="${pc ? fmt(pc.gross) : ''}"></label>
    <label>Pay from<select id="pc-bank">${acctOptions(pc ? pc.bank_account_id : bank, a => a.type === 'Bank', false)}</select></label>
    <label>Check no.<input id="pc-chk" value="${esc(pc ? pc.check_no : '')}"></label></div>
    <div class="form" style="margin-top:14px">
      <div><h2 style="margin-top:0">Employee taxes &amp; deductions</h2>${PAY_FIELDS.map(([k, l]) => `<label class="inline" style="justify-content:space-between;margin:4px 0">${l}<input class="num pc-amt" data-k="${k}" value="${pc ? fmt(pc[k]) : ''}"></label>`).join('')}
        <label class="inline" style="justify-content:space-between;margin-top:10px;font-weight:600">Net pay <span id="pc-net"></span></label></div>
      <div><h2 style="margin-top:0">Employer taxes</h2>${ER_FIELDS.map(([k, l]) => `<label class="inline" style="justify-content:space-between;margin:4px 0">${l}<input class="num pc-amt" data-k="${k}" value="${pc ? fmt(pc[k]) : ''}"></label>`).join('')}</div>
    </div>
    <p class="muted">Amounts are calculated when you change the employee, date, hours or gross pay. You can type over any of them.</p>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="pc-save">Save paycheck</button></div>`, true);
  const emp = () => emps.find(e => e.id == $('#pc-emp', m.el).value);
  const netCalc = () => {
    const g = cents($('#pc-gross', m.el).value);
    const ded = PAY_FIELDS.reduce((s, [k]) => s + cents($(`.pc-amt[data-k="${k}"]`, m.el).value), 0);
    $('#pc-net', m.el).innerHTML = `<span class="${g - ded < 0 ? 'diff-bad' : ''}">${money(g - ded)}</span>`;
  };
  const calc = async (fromGross) => {
    const e = emp(); if (!e) return;
    if (!fromGross && e.pay_type === 'hourly' && $('#pc-hours', m.el).value) $('#pc-gross', m.el).value = '';
    const body = {employee_id: e.id, pay_date: $('#pc-date', m.el).value, hours: $('#pc-hours', m.el).value, gross: $('#pc-gross', m.el).value || null};
    if (pc) body.paycheck_id = pc.id;
    if (!body.gross && e.pay_type === 'hourly' && !body.hours) return;
    const c = await api('POST', '/api/payroll/calculate', body);
    $('#pc-gross', m.el).value = fmt(c.gross);
    [...PAY_FIELDS, ...ER_FIELDS].forEach(([k]) => { $(`.pc-amt[data-k="${k}"]`, m.el).value = fmt(c[k]); });
    netCalc();
  };
  if (!pc) run(() => calc(false));
  netCalc();
  $('#pc-emp', m.el).onchange = () => run(() => calc(false));
  $('#pc-date', m.el).onchange = () => run(() => calc(true));
  $('#pc-hours', m.el).onchange = () => run(() => calc(false));
  $('#pc-gross', m.el).onchange = () => run(() => calc(true));
  m.body.addEventListener('input', e => { if (e.target.matches('.pc-amt')) netCalc(); });
  $('#pc-save', m.el).onclick = () => run(async () => {
    const body = {employee_id: $('#pc-emp', m.el).value, pay_date: $('#pc-date', m.el).value, period_start: $('#pc-ps', m.el).value,
      period_end: $('#pc-pe', m.el).value, hours: $('#pc-hours', m.el).value, gross: $('#pc-gross', m.el).value,
      bank_account_id: $('#pc-bank', m.el).value, check_no: $('#pc-chk', m.el).value};
    $$('.pc-amt', m.el).forEach(i => { body[i.dataset.k] = i.value === '' ? '0' : i.value; });
    await api(pc ? 'PUT' : 'POST', '/api/paychecks' + (pc ? '/' + pc.id : ''), body);
    m.close(); toast('Paycheck saved'); pay.tab = 'paychecks'; render();
  });
}

async function payrollSettingsModal() {
  const s = await api('GET', '/api/payroll/settings');
  const f = [['ss_rate', 'Social Security rate (%)'], ['ss_wage_base', 'Social Security wage base'], ['medicare_rate', 'Medicare rate (%)'],
    ['addl_medicare_rate', 'Additional Medicare rate (%)'], ['addl_medicare_threshold', 'Additional Medicare threshold'],
    ['futa_rate', 'FUTA rate (%)'], ['futa_wage_base', 'FUTA wage base'], ['suta_rate', 'State unemployment rate (%)'], ['suta_wage_base', 'State unemployment wage base']];
  const m = modal('Payroll tax rates', `<div class="form">${f.map(([k, l]) => `<label>${l}<input class="num ps" data-k="${k}" style="width:100%" value="${s[k]}"></label>`).join('')}
    <label>Wages expense account<select id="ps-wage">${acctOptions(s.wage_account_id, a => ['Expense', 'Cost of Goods Sold'].includes(a.type), false)}</select></label></div>
    <p class="muted">Check these each January: wage bases and your state unemployment rate change yearly.</p>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="ps-save">Save</button></div>`);
  $('#ps-save', m.el).onclick = () => run(async () => {
    const body = {wage_account_id: $('#ps-wage', m.el).value};
    $$('.ps', m.el).forEach(i => { body[i.dataset.k] = i.value; });
    await api('PUT', '/api/payroll/settings', body); m.close(); toast('Saved');
  });
}

/* ------------------------------------------------------------------ reconcile */
pages.reconcile = async (main, params) => {
  if (params.id) return reconcileScreen(main, +params.id);
  const [accts, hist] = await Promise.all([api('GET', '/api/reconcile/accounts'), api('GET', '/api/reconciliations')]);
  main.innerHTML = `<div class="head"><h1>Reconcile</h1></div>
    <div class="tbl"><table><thead><tr><th>Account</th><th>Type</th><th class="num">Balance in books</th><th>Last reconciled</th><th class="num">Last statement balance</th><th></th></tr></thead>
    <tbody>${accts.map(a => `<tr><td>${esc(a.name)}</td><td>${a.type}</td><td class="num">${amt(a.balance)}</td><td>${a.last_statement_date || '<span class="muted">Never</span>'}</td>
      <td class="num">${a.last_ending_balance == null ? '' : fmt(a.last_ending_balance)}</td>
      <td class="actions">${a.open_reconciliation_id ? `<a href="#reconcile?id=${a.open_reconciliation_id}"><button class="small primary">Resume reconciling</button></a>`
        : `<button class="small primary" data-start="${a.account_id}">Reconcile</button>`}</td></tr>`).join('') ||
      '<tr><td colspan="6" class="empty">Add a Bank or Credit Card account first.</td></tr>'}</tbody></table></div>
    <h2>History</h2>
    <div class="tbl"><table><thead><tr><th>Account</th><th>Statement date</th><th class="num">Beginning balance</th><th class="num">Ending balance</th><th>Status</th><th></th></tr></thead>
    <tbody>${hist.map(r => `<tr><td>${esc(r.account)}</td><td>${r.statement_date}</td><td class="num">${fmt(r.beginning_balance)}</td><td class="num">${fmt(r.ending_balance)}</td>
      <td><span class="badge ${r.status}">${r.status === 'done' ? 'Reconciled' : 'In progress'}</span>${r.opening_balance ? ' <span class="muted">opening balance</span>' : ''}</td>
      <td class="actions">${r.status === 'done' ? `<button class="small" data-report="${r.id}">Report</button>` : `<a href="#reconcile?id=${r.id}">Resume</a>`}
      ${r.can_undo ? `<button class="small danger" data-undo="${r.id}">${r.status === 'done' ? 'Undo' : 'Cancel'}</button>` : ''}</td></tr>`).join('') ||
      '<tr><td colspan="6" class="empty">No reconciliations yet.</td></tr>'}</tbody></table></div>`;
  main.onclick = e => run(async () => {
    const st = e.target.closest('[data-start]'), rp = e.target.closest('[data-report]'), un = e.target.closest('[data-undo]');
    if (st) startRecModal(accts.find(a => a.account_id == st.dataset.start));
    if (rp) recReport(+rp.dataset.report);
    if (un && confirm('Undo this reconciliation? Its transactions will become unreconciled.')) { await api('DELETE', '/api/reconciliations/' + un.dataset.undo); toast('Reconciliation undone'); render(); }
  });
};

function startRecModal(a, rec) {
  const cc = a.type === 'Credit Card';
  const m = modal(`Reconcile ${a.name}`, `<div class="form">
    <label>Beginning balance<input disabled value="${fmt(rec ? rec.beginning_balance : a.beginning_balance)}"></label><span></span>
    <label>Statement ending date<input type="date" id="rc-date" value="${rec ? rec.statement_date : ''}"></label>
    <label>Statement ending balance${cc ? ' (amount owed)' : ''}<input id="rc-end" class="num" style="width:100%" value="${rec ? rec.ending_balance : ''}"></label></div>
    <p class="muted">Enter the ending date and balance exactly as printed on the ${cc ? 'credit card' : 'bank'} statement.</p>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="rc-go">${rec ? 'Save' : 'Start reconciling'}</button></div>`);
  $('#rc-go', m.el).onclick = () => run(async () => {
    const body = {account_id: a.account_id, statement_date: $('#rc-date', m.el).value, ending_balance: $('#rc-end', m.el).value};
    const r = rec ? await api('PUT', '/api/reconciliations/' + rec.id, body) : await api('POST', '/api/reconciliations', body);
    m.close(); location.hash = '#reconcile?id=' + r.id; if (rec) render();
  });
}

async function reconcileScreen(main, id) {
  let rec = await api('GET', '/api/reconciliations/' + id);
  if (rec.status === 'done') { location.hash = '#reconcile'; return; }
  const cc = rec.account_type === 'Credit Card';
  const outL = cc ? 'Charges' : 'Payments', inL = cc ? 'Payments & credits' : 'Deposits';
  const draw = () => {
    const ok = Math.round(rec.difference * 100) === 0;
    main.innerHTML = `<div class="head"><h1>Reconcile ${esc(rec.account)} &middot; statement ending ${rec.statement_date}</h1>
      <button id="rc-edit">Edit info</button><button id="rc-later">Save for later</button><button class="primary" id="rc-finish" ${ok ? '' : 'disabled'}>Finish now</button></div>
      <div class="recon-sum">
        <div class="card"><div class="label">Statement ending balance</div><div class="big">${fmt(rec.ending_balance)}</div></div>
        <div class="card"><div class="label">Beginning balance</div><div class="big">${fmt(rec.beginning_balance)}</div></div>
        <div class="card"><div class="label">${rec.cleared_payment_count} ${outL} cleared</div><div class="big">${fmt(rec.cleared_payments)}</div></div>
        <div class="card"><div class="label">${rec.cleared_deposit_count} ${inL} cleared</div><div class="big">${fmt(rec.cleared_deposits)}</div></div>
        <div class="card"><div class="label">Difference (cleared balance ${fmt(rec.cleared_balance)})</div><div class="big ${ok ? 'diff-ok' : 'diff-bad'}">${fmt(rec.difference)}</div></div>
      </div>
      <div class="bar"><button class="small" id="rc-all">Select all</button><button class="small" id="rc-none">Clear all</button>
        <span class="muted">Tick each line that appears on the statement. Finish when the difference is 0.00. Lines that came from an imported statement start ticked.</span></div>
      <div class="tbl"><table><thead><tr><th></th><th>Date</th><th>Type</th><th>No.</th><th>Name</th><th>Memo</th><th class="num">${outL}</th><th class="num">${inL}</th></tr></thead>
      <tbody>${rec.lines.map(l => `<tr class="click" data-line="${l.line_id}"><td><input type="checkbox" ${l.cleared ? 'checked' : ''}></td><td class="nowrap">${l.date}</td>
        <td>${esc(l.type)}</td><td>${esc(l.ref)}</td><td>${esc(l.name || '')}</td><td>${esc(l.memo || '')}</td>
        <td class="num">${l.payment == null ? '' : fmt(l.payment)}</td><td class="num">${l.deposit == null ? '' : fmt(l.deposit)}</td></tr>`).join('') ||
        '<tr><td colspan="8" class="empty">No unreconciled transactions on or before the statement date.</td></tr>'}</tbody></table></div>`;
    const toggle = (ids, cleared) => run(async () => { rec = await api('POST', `/api/reconciliations/${id}/toggle`, {line_ids: ids, cleared}); draw(); });
    $$('tr[data-line]', main).forEach(tr => tr.onclick = () => toggle([+tr.dataset.line], !rec.lines.find(l => l.line_id == tr.dataset.line).cleared));
    $('#rc-all').onclick = () => toggle(rec.lines.map(l => l.line_id), true);
    $('#rc-none').onclick = () => toggle(rec.lines.map(l => l.line_id), false);
    $('#rc-later').onclick = () => { location.hash = '#reconcile'; };
    $('#rc-edit').onclick = () => startRecModal({account_id: rec.account_id, name: rec.account, type: rec.account_type}, rec);
    $('#rc-finish').onclick = () => run(async () => {
      await api('POST', `/api/reconciliations/${id}/finish`); toast('Reconciled'); location.hash = '#reconcile'; setTimeout(() => recReport(id), 300);
    });
  };
  main.onclick = null;
  draw();
}

async function recReport(id) {
  const r = await api('GET', '/api/reconciliations/' + id);
  const cc = r.account_type === 'Credit Card';
  const tbl = (rows) => rows.length ? `<div class="tbl"><table><thead><tr><th>Date</th><th>Type</th><th>No.</th><th>Name</th><th class="num">Amount</th></tr></thead><tbody>
    ${rows.map(l => `<tr class="click" data-txn="${l.txn_id}"><td>${l.date}</td><td>${esc(l.type)}</td><td>${esc(l.ref)}</td><td>${esc(l.name || l.memo || '')}</td><td class="num">${amt(l.amount)}</td></tr>`).join('')}
    </tbody></table></div>` : '<div class="muted">None</div>';
  const m = modal(`Reconciliation report: ${r.account}, statement ending ${r.statement_date}`, `
    ${r.opening_balance ? '<div class="note">Opening balance entered when the account was created. It counts as reconciled, so the first statement starts from it.</div>' : ''}
    ${r.snapshot ? `<p class="muted">As of when it was reconciled (${esc(localTime(r.completed_at))}). Later changes are not included.</p>` : ''}
    <div class="tbl"><table><tbody>
      <tr><td>Statement beginning balance</td><td class="num">${fmt(r.beginning_balance)}</td></tr>
      <tr><td>${cc ? 'Charges' : 'Checks and payments'} cleared (${r.cleared_payment_count})</td><td class="num">${fmt(cc ? r.cleared_payments : -r.cleared_payments)}</td></tr>
      <tr><td>${cc ? 'Payments and credits' : 'Deposits and other credits'} cleared (${r.cleared_deposit_count})</td><td class="num">${fmt(cc ? -r.cleared_deposits : r.cleared_deposits)}</td></tr>
      <tr class="sum"><td>Statement ending balance</td><td class="num">${fmt(r.ending_balance)}</td></tr>
      <tr><td>Uncleared transactions as of ${r.statement_date}</td><td class="num">${fmt(r.uncleared_total)}</td></tr>
      <tr class="sum"><td>Register balance as of ${r.statement_date}</td><td class="num">${fmt(r.register_balance)}</td></tr>
    </tbody></table></div>
    <h2>Cleared transactions</h2>${tbl(r.lines)}<h2>Uncleared transactions as of ${r.statement_date}</h2>${tbl(r.uncleared)}
    <div class="foot"><button data-close>Close</button></div>`, true);
  m.body.addEventListener('click', e => { const t = e.target.closest('[data-txn]'); if (t) run(() => txnModal(+t.dataset.txn)); });
}

/* ------------------------------------------------------------------ reports */
const REPORTS = {
  profit_loss: {label: 'Profit and Loss', range: true, cols: true},
  balance_sheet: {label: 'Balance Sheet', range: true, cols: true},
  cash_flow: {label: 'Statement of Cash Flows', range: true, cols: true},
  transaction_detail: {label: 'Transaction Detail by Account', range: true, account: true, basis: true},
  trial_balance: {label: 'Trial Balance', asof: true, basis: true},
  ar_aging: {label: 'A/R Aging Summary', asof: true},
  ap_aging: {label: 'A/P Aging Summary', asof: true},
  sales_tax: {label: 'Sales Tax Liability', range: true},
  inventory_valuation: {label: 'Inventory Valuation Summary', asof: true},
  payroll_summary: {label: 'Payroll Summary', range: true},
};
['profit_loss', 'balance_sheet', 'cash_flow'].forEach(k => { REPORTS[k].basis = true; });
const rep = {type: 'profit_loss', preset: 'ytd', start: '', end: '', columns: 'total', compare: 'none', account_id: '', basis: 'accrual'};
function presetRange(p) {
  const t = new Date(), y = t.getFullYear(), mo = t.getMonth(), fm = (S.meta.fiscal_year_start_month || 1) - 1;
  const fy = mo >= fm ? y : y - 1;
  const q = Math.floor(((mo - fm + 12) % 12) / 3), qs = new Date(fy, fm + q * 3, 1);
  switch (p) {
    case 'this_month': return [new Date(y, mo, 1), new Date(y, mo + 1, 0)];
    case 'last_month': return [new Date(y, mo - 1, 1), new Date(y, mo, 0)];
    case 'this_quarter': return [qs, new Date(qs.getFullYear(), qs.getMonth() + 3, 0)];
    case 'last_quarter': return [new Date(qs.getFullYear(), qs.getMonth() - 3, 1), new Date(qs.getFullYear(), qs.getMonth(), 0)];
    case 'this_year': return [new Date(fy, fm, 1), new Date(fy + 1, fm, 0)];
    case 'last_year': return [new Date(fy - 1, fm, 1), new Date(fy, fm, 0)];
    case 'ytd': return [new Date(fy, fm, 1), t];
    default: return null;
  }
}
pages.reports = async (main, params) => {
  if (params.type && REPORTS[params.type]) { rep.type = params.type; rep.account_id = params.account_id || ''; history.replaceState(null, '', '#reports'); }
  if (!rep.start) { const r = presetRange(rep.preset); rep.start = iso(r[0]); rep.end = iso(r[1]); }
  const def = REPORTS[rep.type];
  const opt = (list, cur) => list.map(([v, l]) => `<option value="${v}" ${cur === v ? 'selected' : ''}>${l}</option>`).join('');
  main.innerHTML = `<div class="head"><h1>Reports</h1><button id="rp-csv">Export CSV</button><button id="rp-print">Print</button></div>
    <div class="bar">
      <label>Report<select id="rp-type">${opt(Object.entries(REPORTS).map(([k, v]) => [k, v.label]), rep.type)}</select></label>
      <label>Period<select id="rp-preset">${opt([['this_month', 'This month'], ['last_month', 'Last month'], ['this_quarter', 'This quarter'], ['last_quarter', 'Last quarter'],
        ['ytd', 'This year to date'], ['this_year', 'This year'], ['last_year', 'Last year'], ['custom', 'Custom']], rep.preset)}</select></label>
      ${def.asof ? '' : `<label>From<input type="date" id="rp-start" value="${rep.start}"></label>`}
      <label>${def.asof ? 'As of' : 'To'}<input type="date" id="rp-end" value="${rep.end}"></label>
      ${def.cols ? `<label>Columns<select id="rp-cols">${opt([['total', 'Total only'], ['month', 'Months'], ['quarter', 'Quarters'], ['year', 'Years']], rep.columns)}</select></label>
      <label>Compare to<select id="rp-cmp">${opt([['none', 'Nothing'], ['pp', 'Previous period'], ['py', 'Previous year']], rep.compare)}</select></label>` : ''}
      ${def.basis ? `<label>Basis<select id="rp-basis">${opt([['accrual', 'Accrual'], ['cash', 'Cash']], rep.basis)}</select></label>` : ''}
      ${def.account ? `<label>Account<select id="rp-acct"><option value="">All accounts</option>${acctOptions(rep.account_id, null, false)}</select></label>` : ''}
      <button class="primary" id="rp-run" style="align-self:flex-end">Run report</button>
    </div><div id="rp-out"></div>`;
  const read = () => {
    rep.type = $('#rp-type').value; rep.preset = $('#rp-preset').value;
    if ($('#rp-start')) rep.start = $('#rp-start').value;
    rep.end = $('#rp-end').value;
    if ($('#rp-cols')) { rep.columns = $('#rp-cols').value; rep.compare = $('#rp-cmp').value; }
    if ($('#rp-acct')) rep.account_id = $('#rp-acct').value;
    if ($('#rp-basis')) rep.basis = $('#rp-basis').value;
  };
  $('#rp-type').onchange = () => { read(); render(); };
  $('#rp-preset').onchange = () => { read(); const r = presetRange(rep.preset); if (r) { rep.start = iso(r[0]); rep.end = iso(r[1]); } render(); };
  ['#rp-start', '#rp-end'].forEach(s => { if ($(s)) $(s).onchange = () => { $('#rp-preset').value = 'custom'; }; });
  let data = null;
  const go = () => run(async () => {
    read();
    const q = new URLSearchParams({end: rep.end});
    if (!def.asof) q.set('start', rep.start);
    if (def.cols) { q.set('columns', rep.columns); q.set('compare', rep.compare); }
    if (def.account && rep.account_id) q.set('account_id', rep.account_id);
    if (def.basis) q.set('basis', rep.basis);
    data = await api('GET', `/api/reports/${rep.type}?${q}`);
    $('#rp-out').innerHTML = rep.type === 'transaction_detail' ? detailHtml(data) : reportHtml(data);
  });
  $('#rp-run').onclick = go;
  $('#rp-print').onclick = () => window.print();
  $('#rp-csv').onclick = () => { if (data) downloadCsv(data); };
  $('#rp-out').onclick = e => run(async () => {
    const d = e.target.closest('a.drill'), t = e.target.closest('[data-txn]');
    if (t) return txnModal(+t.dataset.txn);
    if (!d) return;
    const row = data.rows[+d.dataset.r], col = data.columns[+d.dataset.c];
    drillModal(`${row.label.trim()} · ${col.label}`, row.drill[+d.dataset.c], row.values[+d.dataset.c]);
  });
  await go();
};

function reportHtml(d) {
  const head = `<div class="rhead"><div class="co">${esc(d.company)}</div><div class="ti">${esc(d.title)}</div><div>${esc(d.subtitle)}</div><div class="muted">${esc(d.basis || '')}</div></div>`;
  const rows = d.rows.map((r, ri) => `<tr class="${r.kind}"><td style="padding-left:${10 + r.level * 20}px">${esc(r.label)}</td>${d.columns.map((c, ci) => {
    const v = r.values ? r.values[ci] : null;
    if (v == null) return '<td class="num"></td>';
    const text = c.kind === 'pct' ? `${v.toFixed(1)}%` : c.kind === 'number' ? (+v).toLocaleString() : fmt(v);
    const cls = v < 0 ? 'neg' : '';
    return r.drill && r.drill[ci] ? `<td class="num"><a class="drill ${cls}" data-r="${ri}" data-c="${ci}" title="Click for the transactions behind this number">${text}</a></td>`
      : `<td class="num ${cls}">${text}</td>`;
  }).join('')}</tr>`).join('');
  return `<div class="report">${head}<table><thead><tr><th></th>${d.columns.map(c => `<th class="num">${esc(c.label)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>
    <p class="muted noprint" style="text-align:center;margin-top:14px">Click any amount to see the transactions that add up to it.</p></div>`;
}

function detailHtml(d) {
  const head = `<div class="rhead"><div class="co">${esc(d.company)}</div><div class="ti">${esc(d.title)}</div><div>${esc(d.subtitle)}</div></div>`;
  const body = d.groups.map(g => `<tr class="grp"><td colspan="6">${esc(g.account)} <span class="muted">(${g.type})</span></td>
      <td class="num" colspan="3">${g.beginning_balance == null ? '' : 'Beginning balance ' + fmt(g.beginning_balance)}</td></tr>
    ${g.rows.map(r => `<tr class="click" data-txn="${r.txn_id}"><td class="nowrap">${r.date}</td><td>${esc(r.type)}</td><td>${esc(r.ref)}</td><td>${esc(r.name || '')}</td><td>${esc(r.memo || '')}</td>
      <td>${esc(r.split)}</td><td class="num">${r.debit ? fmt(r.debit) : ''}</td><td class="num">${r.credit ? fmt(r.credit) : ''}</td><td class="num">${amt(r.balance)}</td></tr>`).join('')}
    <tr class="sum"><td colspan="6">Total ${esc(g.account)} &middot; net change ${fmt(g.net_change)}</td><td class="num">${fmt(g.total_debit)}</td><td class="num">${fmt(g.total_credit)}</td><td class="num">${fmt(g.ending_balance)}</td></tr>`).join('');
  return `<div class="report">${head}<table style="width:100%"><thead><tr><th style="text-align:left">Date</th><th style="text-align:left">Type</th><th style="text-align:left">No.</th><th style="text-align:left">Name</th>
    <th style="text-align:left">Memo</th><th style="text-align:left">Split</th><th class="num">Debit</th><th class="num">Credit</th><th class="num">Balance</th></tr></thead>
    <tbody>${body || '<tr><td colspan="9" class="empty">No transactions in this period.</td></tr>'}</tbody></table></div>`;
}

async function drillModal(title, drill, cellValue) {
  let groups, total;
  if (drill.items) {
    groups = [{account: 'Open items', rows: drill.items.map(i => Object.assign({split: ''}, i)), total: null}];
    total = drill.items.reduce((s, i) => s + cents(i.amount), 0) / 100;
  } else { const d = await api('POST', '/api/reports/drill', drill); groups = d.groups; total = d.total; }
  const ties = Math.round(total * 100) === Math.round(cellValue * 100);
  const many = groups.length > 1;
  const m = modal(`Breakdown: ${title}`, `
    <div class="note">${groups.reduce((s, g) => s + g.rows.length, 0)} transaction(s) add up to <b>${fmt(total)}</b>
      ${ties ? '&mdash; matches the report amount.' : `<span class="diff-bad">(report shows ${fmt(cellValue)})</span>`} Click a line to open the transaction.</div>
    <div class="tbl" style="max-height:60vh"><table><thead><tr><th>Date</th><th>Type</th><th>No.</th><th>Name</th><th>Memo</th><th>Split</th><th class="num">Amount</th></tr></thead><tbody>
    ${groups.map(g => `${many || g.total != null ? `<tr class="grp"><td colspan="7">${esc(g.account)}</td></tr>` : ''}
      ${g.rows.map(r => `<tr class="click" data-txn="${r.txn_id}"><td class="nowrap">${r.date}</td><td>${esc(r.type)}</td><td>${esc(r.ref)}</td><td>${esc(r.name || '')}</td>
        <td>${esc(r.memo || '')}</td><td>${esc(r.split || '')}</td><td class="num">${amt(r.amount)}</td></tr>`).join('')}
      ${g.total != null ? `<tr class="sum"><td colspan="6">Total ${esc(g.account)}</td><td class="num">${fmt(g.total)}</td></tr>` : ''}`).join('')}
    <tr class="grandsum"><td colspan="6">TOTAL</td><td class="num">${fmt(total)}</td></tr></tbody></table></div>
    <div class="foot"><button data-close>Close</button></div>`, true);
  m.body.addEventListener('click', e => { const t = e.target.closest('[data-txn]'); if (t) run(() => txnModal(+t.dataset.txn)); });
}

function downloadCsv(d) {
  const q = v => `"${String(v == null ? '' : v).replace(/"/g, '""')}"`;
  let lines = [[d.company], [d.title], [d.subtitle], []];
  if (d.groups) {
    lines.push(['Account', 'Date', 'Type', 'No.', 'Name', 'Memo', 'Split', 'Debit', 'Credit', 'Balance']);
    for (const g of d.groups) {
      lines.push([g.account, '', 'Beginning balance', '', '', '', '', '', '', g.beginning_balance]);
      for (const r of g.rows) lines.push([g.account, r.date, r.type, r.ref, r.name, r.memo, r.split, r.debit, r.credit, r.balance]);
      lines.push(['Total ' + g.account, '', '', '', '', '', '', g.total_debit, g.total_credit, g.ending_balance]);
    }
  } else {
    lines.push([''].concat(d.columns.map(c => c.label)));
    for (const r of d.rows) lines.push(['  '.repeat(r.level) + r.label].concat(r.values || []));
  }
  const blob = new Blob([lines.map(l => l.map(q).join(',')).join('\n')], {type: 'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = d.title.replace(/[^\w]+/g, '_') + '.csv'; a.click();
}
