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

const S = {accounts: [], customers: [], vendors: [], meta: {}};

async function api(method, url, body) {
  const res = await fetch(url, {method, headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
async function loadRefs() {
  [S.accounts, S.customers, S.vendors, S.meta] = await Promise.all([
    api('GET', '/api/accounts?include_inactive=1'), api('GET', '/api/customers?include_inactive=1'),
    api('GET', '/api/vendors?include_inactive=1'), api('GET', '/api/meta')]);
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
window.addEventListener('hashchange', render);
window.addEventListener('DOMContentLoaded', () => {
  $('#settings-btn').onclick = settingsModal;
  render();
});

function settingsModal() {
  const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const m = modal('Company settings', `<div class="form">
    <label class="full">Company name<input id="s-name" value="${esc(S.meta.company_name)}"></label>
    <label class="full">First month of fiscal year<select id="s-fy">${months.map((x, i) =>
      `<option value="${i + 1}" ${S.meta.fiscal_year_start_month === i + 1 ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
    </div><div class="foot"><button data-close>Cancel</button><button class="primary" id="s-save">Save</button></div>`);
  $('#s-save', m.el).onclick = () => run(async () => {
    await api('PUT', '/api/settings', {company_name: $('#s-name', m.el).value, fiscal_year_start_month: $('#s-fy', m.el).value});
    m.close(); toast('Saved'); render();
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
        <td class="actions"><button class="small" data-act="undo">Undo</button></td></tr>`;
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
    await api('POST', `/api/feed/${r.id}/match`, x.kind === 'invoice' ? {invoice_id: x.invoice_id} : x.kind === 'undeposited' ? {undeposited: true} : {line_id: x.line_id});
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
      <tbody>${rules.map(r => `<tr><td>${esc(r.match_text)}</td><td>${r.direction}</td><td>${esc(r.account)}</td><td>${esc(r.name || '')}</td>
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
      <div class="tbl" style="max-height:320px"><table><thead><tr><th>Date</th><th>Description</th><th class="num">${cc ? 'Charge' : 'Spent'}</th><th class="num">${cc ? 'Payment / credit' : 'Received'}</th><th></th></tr></thead>
      <tbody>${p.rows.map(r => `<tr class="${r.duplicate ? 'muted' : ''}"><td class="nowrap">${r.date}</td><td>${esc(r.description)}</td>
        <td class="num">${r.amount < 0 ? fmt(-r.amount) : ''}</td><td class="num">${r.amount > 0 ? fmt(r.amount) : ''}</td>
        <td>${r.duplicate ? 'duplicate' : ''}</td></tr>`).join('')}</tbody></table></div>`;
    $('#i-go', m.el).disabled = !p.new_count;
  });
  ['#i-file', '#i-flip', '#i-acct'].forEach(s => { $(s, m.el).onchange = preview; });
  $('#i-go', m.el).onclick = () => run(async () => {
    const r = await api('POST', '/api/feed/import', payload);
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
    ${t.type === 'Journal' || t.invoice_id || t.payment_id ? '<button id="t-edit">Edit</button>' : ''}<button data-close>Close</button></div>`, true);
  $('#t-del', m.el).onclick = () => run(async () => {
    if (!confirm('Delete this transaction?' + (t.feed_id ? ' Its statement line will go back to For review.' : ''))) return;
    await api('DELETE', '/api/transactions/' + id);
    $$('.modal-backdrop').forEach(x => x.remove()); toast('Deleted'); render();
  });
  if ($('#t-edit', m.el)) $('#t-edit', m.el).onclick = () => {
    $$('.modal-backdrop').forEach(x => x.remove());
    if (t.invoice_id) invoiceModal(t.invoice_id); else if (t.payment_id) paymentModal({payment_id: t.payment_id}); else journalModal(t);
  };
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
        <td>${esc(n.company)}</td><td>${esc(n.email)}</td><td>${esc(n.phone)}</td><td class="num">${amt(n.balance)}</td>
        <td class="actions">${kind === 'customer' && n.active ? `<button class="small" data-inv="${n.id}">New invoice</button>` : ''}
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
        <label class="full">Notes<textarea id="n-notes" rows="2">${esc(n.notes || '')}</textarea></label></div>
        <div class="foot"><button data-close>Cancel</button><button class="primary" id="n-save">Save</button></div>`);
      $('#n-save', m.el).onclick = () => run(async () => {
        const body = {};
        ['name', 'company', 'email', 'phone', 'address', 'notes'].forEach(f => { body[f] = $('#n-' + f, m.el).value; });
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
    await api(edit ? 'PUT' : 'POST', '/api/journals' + (edit ? '/' + j.id : ''), body);
    m.close(); toast('Journal entry saved'); render();
  });
}

/* ------------------------------------------------------------------ invoices & payments */
const inv = {tab: 'invoices', status: 'all'};
pages.invoices = async main => {
  let body;
  if (inv.tab === 'invoices') {
    const list = await api('GET', '/api/invoices?status=' + inv.status);
    body = `<div class="bar"><label>Show<select id="v-status">${[['all', 'All invoices'], ['open', 'Unpaid'], ['overdue', 'Overdue'], ['paid', 'Paid']].map(([v, l]) =>
      `<option value="${v}" ${inv.status === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label></div>
      <div class="tbl"><table><thead><tr><th>No.</th><th>Customer</th><th>Date</th><th>Due</th><th class="num">Total</th><th class="num">Balance</th><th>Status</th><th></th></tr></thead>
      <tbody>${list.map(i => `<tr><td>${esc(i.number)}</td><td>${esc(i.customer)}</td><td class="nowrap">${i.date}</td><td class="nowrap">${i.due_date}</td>
        <td class="num">${fmt(i.total)}</td><td class="num">${fmt(i.balance)}</td><td><span class="badge ${i.status}">${i.status}</span></td>
        <td class="actions">${i.balance > 0 ? `<button class="small primary" data-pay="${i.id}" data-cust="${i.customer_id}">Receive payment</button>` : ''}
        <button class="small" data-edit="${i.id}">Edit</button><button class="small" data-print="${i.id}">Print</button>
        <button class="small danger" data-del="${i.id}">Delete</button></td></tr>`).join('') ||
        '<tr><td colspan="8" class="empty">No invoices.</td></tr>'}</tbody></table></div>`;
  } else {
    const list = await api('GET', '/api/payments');
    body = `<div class="tbl"><table><thead><tr><th>Date</th><th>Customer</th><th>Ref</th><th>Method</th><th>Deposited to</th><th>Applied to</th><th class="num">Amount</th><th class="num">Unapplied</th><th></th></tr></thead>
      <tbody>${list.map(p => `<tr><td class="nowrap">${p.date}</td><td>${esc(p.customer)}</td><td>${esc(p.ref)}</td><td>${esc(p.method)}</td><td>${esc(p.deposit_account)}</td>
        <td>${p.applications.map(a => '#' + esc(a.number)).join(', ')}</td><td class="num">${fmt(p.amount)}</td><td class="num">${p.unapplied ? fmt(p.unapplied) : ''}</td>
        <td class="actions"><button class="small" data-pedit="${p.id}">Edit</button><button class="small danger" data-pdel="${p.id}">Delete</button></td></tr>`).join('') ||
        '<tr><td colspan="9" class="empty">No payments received yet.</td></tr>'}</tbody></table></div>`;
  }
  main.innerHTML = `<div class="head"><h1>Invoices &amp; Payments</h1><button id="v-pay">Receive payment</button><button class="primary" id="v-new">New invoice</button></div>
    <div class="tabs"><button data-tab="invoices" class="${inv.tab === 'invoices' ? 'active' : ''}">Invoices</button>
    <button data-tab="payments" class="${inv.tab === 'payments' ? 'active' : ''}">Payments received</button></div>${body}`;
  $$('[data-tab]', main).forEach(b => b.onclick = () => { inv.tab = b.dataset.tab; render(); });
  if ($('#v-status')) $('#v-status').onchange = e => { inv.status = e.target.value; render(); };
  $('#v-new').onclick = () => invoiceModal();
  $('#v-pay').onclick = () => paymentModal({});
  main.onclick = e => run(async () => {
    const t = e.target, g = k => t.closest(`[data-${k}]`);
    if (g('edit')) invoiceModal(+g('edit').dataset.edit);
    if (g('print')) printInvoice(+g('print').dataset.print);
    if (g('pay')) paymentModal({customer_id: +g('pay').dataset.cust, invoice_id: +g('pay').dataset.pay});
    if (g('pedit')) paymentModal({payment_id: +g('pedit').dataset.pedit});
    if (g('del') && confirm('Delete this invoice?')) { await api('DELETE', '/api/invoices/' + g('del').dataset.del); toast('Deleted'); render(); }
    if (g('pdel') && confirm('Delete this payment? The invoices it paid will show as unpaid again.')) { await api('DELETE', '/api/payments/' + g('pdel').dataset.pdel); toast('Deleted'); render(); }
  });
};

async function invoiceModal(id, customerId) {
  const v = id ? await api('GET', '/api/invoices/' + id)
    : {customer_id: customerId, date: today(), terms: 'Net 30', lines: [], number: (await api('GET', '/api/invoices/next_number')).number};
  const isIncome = a => ['Income', 'Other Income'].includes(a.type);
  const defAcct = (S.accounts.find(a => a.active && a.type === 'Income') || {}).id;
  const line = (l = {}) => `<tr><td><input class="l-desc" value="${esc(l.description || '')}"></td>
    <td><input class="num l-qty" value="${l.qty == null ? 1 : l.qty}"></td><td><input class="num l-rate" value="${l.rate == null ? '' : l.rate}"></td>
    <td><input class="num l-amt" value="${l.amount == null ? '' : fmt(l.amount)}"></td>
    <td><select class="l-acct">${acctOptions(l.account_id || defAcct, a => isIncome(a) || a.id === l.account_id, false)}</select></td>
    <td><button class="small" data-rm>&times;</button></td></tr>`;
  const m = modal(id ? `Invoice #${v.number}` : 'New invoice', `<div class="form four">
    <label>Customer<select id="v-cust">${nameOptions(v.customer_id, ['customer'])}</select></label>
    <label>Invoice no.<input id="v-num" value="${esc(v.number)}"></label>
    <label>Invoice date<input type="date" id="v-date" value="${v.date}"></label>
    <label>Terms<select id="v-terms"><option value=""></option>${S.meta.terms.map(t => `<option ${t === v.terms ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
    <label>Due date<input type="date" id="v-due" value="${v.due_date || ''}"></label>
    <label class="full" style="grid-column: span 3">Message / memo<input id="v-memo" value="${esc(v.memo || '')}"></label></div>
    <table class="lines" style="margin-top:12px"><thead><tr><th>Description</th><th class="num" style="width:9%">Qty</th><th class="num" style="width:12%">Rate</th><th class="num" style="width:13%">Amount</th><th style="width:24%">Income account</th><th></th></tr></thead>
    <tbody>${(v.lines.length ? v.lines : [{}, {}]).map(line).join('')}</tbody></table>
    <button class="small" id="v-add">Add line</button>
    <div class="totals"><span>Total <span id="v-total"></span></span>${id ? `<span>Paid ${fmt(v.applied)}</span><span>Balance due <span id="v-bal"></span></span>` : ''}</div>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="v-save">Save</button></div>`, true);
  const recalc = e => {
    if (e && e.target.matches('.l-qty, .l-rate')) {
      const tr = e.target.closest('tr'), f = s => parseFloat(String($(s, tr).value).replace(/,/g, '')) || 0;
      $('.l-amt', tr).value = money(Math.round(f('.l-qty') * f('.l-rate') * 100 + 1e-7));
    }
    if (e && e.target.matches('.l-amt')) $('.l-rate', e.target.closest('tr')).value = '';   // typed amount wins; rate is derived
    const t = $$('.l-amt', m.el).reduce((s, i) => s + cents(i.value), 0);
    $('#v-total', m.el).textContent = money(t);
    if ($('#v-bal', m.el)) $('#v-bal', m.el).textContent = money(t - cents(v.applied));
  };
  const due = () => {
    const days = {'Due on receipt': 0, 'Net 15': 15, 'Net 30': 30, 'Net 60': 60}[$('#v-terms', m.el).value];
    if (days == null || !$('#v-date', m.el).value) return;
    const d = new Date($('#v-date', m.el).value + 'T00:00:00'); d.setDate(d.getDate() + days); $('#v-due', m.el).value = iso(d);
  };
  if (!id) due();
  recalc();
  m.body.addEventListener('input', recalc);
  $('#v-terms', m.el).onchange = due; $('#v-date', m.el).onchange = due;
  m.body.addEventListener('click', e => { if (e.target.closest('[data-rm]')) { e.target.closest('tr').remove(); recalc(); } });
  $('#v-add', m.el).onclick = () => $('tbody', m.el).insertAdjacentHTML('beforeend', line());
  $('#v-save', m.el).onclick = () => run(async () => {
    const body = {customer_id: $('#v-cust', m.el).value, number: $('#v-num', m.el).value, date: $('#v-date', m.el).value,
      terms: $('#v-terms', m.el).value, due_date: $('#v-due', m.el).value, memo: $('#v-memo', m.el).value,
      lines: $$('tbody tr', m.el).map(tr => ({description: $('.l-desc', tr).value, qty: $('.l-qty', tr).value, rate: $('.l-rate', tr).value,
        amount: $('.l-rate', tr).value === '' ? $('.l-amt', tr).value : '', account_id: $('.l-acct', tr).value}))};
    await api(id ? 'PUT' : 'POST', '/api/invoices' + (id ? '/' + id : ''), body);
    m.close(); toast('Invoice saved'); inv.tab = 'invoices'; render();
  });
}

async function printInvoice(id) {
  const v = await api('GET', '/api/invoices/' + id);
  const c = S.customers.find(x => x.id === v.customer_id) || {};
  const w = window.open('', '_blank');
  if (!w) return toast('Allow pop-ups to print invoices', true);
  w.document.write(`<html><head><title>Invoice ${esc(v.number)}</title><style>body{font:14px Helvetica,Arial,sans-serif;margin:40px;color:#222}
    table{border-collapse:collapse;width:100%;margin-top:24px}th,td{padding:8px;border-bottom:1px solid #ddd;text-align:left}.r{text-align:right}h1{margin:0}</style></head><body>
    <h1>INVOICE</h1><p><b>${esc(S.meta.company_name)}</b></p>
    <p><b>Bill to:</b> ${esc(v.customer)}${c.company ? '<br>' + esc(c.company) : ''}${c.address ? '<br>' + esc(c.address) : ''}</p>
    <p>Invoice #: ${esc(v.number)}<br>Date: ${v.date}<br>Terms: ${esc(v.terms)}<br>Due: ${v.due_date}</p>
    <table><tr><th>Description</th><th class="r">Qty</th><th class="r">Rate</th><th class="r">Amount</th></tr>
    ${v.lines.map(l => `<tr><td>${esc(l.description)}</td><td class="r">${l.qty}</td><td class="r">${fmt(l.rate)}</td><td class="r">${fmt(l.amount)}</td></tr>`).join('')}
    <tr><td colspan="3" class="r"><b>Total</b></td><td class="r"><b>${fmt(v.total)}</b></td></tr>
    <tr><td colspan="3" class="r">Payments</td><td class="r">${fmt(v.applied)}</td></tr>
    <tr><td colspan="3" class="r"><b>Balance due</b></td><td class="r"><b>${fmt(v.balance)}</b></td></tr></table>
    <p>${esc(v.memo)}</p></body></html>`);
  w.document.close(); w.print();
}

async function paymentModal(opts) {
  const p = opts.payment_id ? await api('GET', '/api/payments/' + opts.payment_id) : null;
  const udf = S.accounts.find(a => a.system === 'udf');
  const m = modal(p ? 'Edit payment' : 'Receive payment', `<div class="form three">
    <label>Customer<select id="p-cust">${nameOptions(p ? p.customer_id : opts.customer_id, ['customer'])}</select></label>
    <label>Payment date<input type="date" id="p-date" value="${p ? p.date : today()}"></label>
    <label>Amount received<input id="p-amt" class="num" style="width:100%" value="${p ? fmt(p.amount) : ''}"></label>
    <label>Deposit to<select id="p-dep">${acctOptions(p ? p.deposit_account_id : udf && udf.id, a => a.type === 'Bank' || a.system === 'udf', false)}</select></label>
    <label>Payment method<select id="p-method">${['', 'Check', 'Cash', 'Credit card', 'ACH / bank transfer', 'Other'].map(x =>
      `<option ${p && p.method === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
    <label>Reference no.<input id="p-ref" value="${esc(p ? p.ref : '')}"></label>
    <label class="full">Memo<input id="p-memo" value="${esc(p ? p.memo : '')}"></label></div>
    <h2>Outstanding invoices</h2><div id="p-invs"></div>
    <div class="totals"><span>Applied <span id="p-app"></span></span><span>Unapplied (customer credit) <span id="p-un"></span></span></div>
    <div class="foot"><button data-close>Cancel</button><button class="primary" id="p-save">Save payment</button></div>`, true);
  const mine = {};
  if (p) p.applications.forEach(a => { mine[a.invoice_id] = cents(a.amount); });
  let rows = [], touched = !!p;
  const recalc = () => {
    const app = $$('.p-apply', m.el).reduce((s, i) => s + cents(i.value), 0);
    $('#p-app', m.el).textContent = money(app);
    const un = cents($('#p-amt', m.el).value) - app;
    $('#p-un', m.el).innerHTML = `<span class="${un < 0 ? 'diff-bad' : ''}">${money(un)}</span>`;
  };
  const distribute = () => {      // oldest first, like QuickBooks
    let left = cents($('#p-amt', m.el).value);
    $$('.p-apply', m.el).forEach(i => { const use = Math.max(0, Math.min(left, +i.dataset.open)); i.value = use ? money(use) : ''; left -= use; });
  };
  const loadInvoices = async () => {
    const cid = $('#p-cust', m.el).value;
    rows = [];
    if (cid) {
      const all = await api('GET', '/api/invoices?customer_id=' + cid);
      rows = all.map(i => Object.assign(i, {open: cents(i.balance) + (mine[i.id] || 0)})).filter(i => i.open > 0).reverse();
    }
    $('#p-invs', m.el).innerHTML = rows.length ? `<div class="tbl"><table><thead><tr><th>Invoice</th><th>Date</th><th>Due</th><th class="num">Original</th><th class="num">Open balance</th><th class="num">Payment</th></tr></thead>
      <tbody>${rows.map(i => `<tr><td>#${esc(i.number)}</td><td>${i.date}</td><td>${i.due_date}</td><td class="num">${fmt(i.total)}</td><td class="num">${money(i.open)}</td>
      <td class="num"><input class="num p-apply" data-id="${i.id}" data-open="${i.open}" value="${mine[i.id] ? money(mine[i.id]) : ''}"></td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">No open invoices for this customer. The payment will be kept as an unapplied credit.</div>';
    if (!p && opts.invoice_id && !touched) {
      const i = rows.find(x => x.id === opts.invoice_id);
      if (i) { $('#p-amt', m.el).value = money(i.open); $(`.p-apply[data-id="${i.id}"]`, m.el).value = money(i.open); }
    }
    recalc();
  };
  await run(loadInvoices);
  $('#p-cust', m.el).onchange = () => run(loadInvoices);
  $('#p-amt', m.el).oninput = () => { touched = true; distribute(); recalc(); };
  $('#p-invs', m.el).addEventListener('input', recalc);
  $('#p-save', m.el).onclick = () => run(async () => {
    const body = {customer_id: $('#p-cust', m.el).value, date: $('#p-date', m.el).value, amount: $('#p-amt', m.el).value,
      deposit_account_id: $('#p-dep', m.el).value, method: $('#p-method', m.el).value, ref: $('#p-ref', m.el).value, memo: $('#p-memo', m.el).value,
      applications: $$('.p-apply', m.el).filter(i => cents(i.value)).map(i => ({invoice_id: +i.dataset.id, amount: i.value}))};
    await api(p ? 'PUT' : 'POST', '/api/payments' + (p ? '/' + p.id : ''), body);
    m.close(); toast('Payment saved'); render();
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
      <td><span class="badge ${r.status}">${r.status === 'done' ? 'Reconciled' : 'In progress'}</span></td>
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
  transaction_detail: {label: 'Transaction Detail by Account', range: true, account: true},
  trial_balance: {label: 'Trial Balance', asof: true},
  ar_aging: {label: 'A/R Aging Summary', asof: true},
};
const rep = {type: 'profit_loss', preset: 'ytd', start: '', end: '', columns: 'total', compare: 'none', account_id: ''};
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
      ${def.account ? `<label>Account<select id="rp-acct"><option value="">All accounts</option>${acctOptions(rep.account_id, null, false)}</select></label>` : ''}
      <button class="primary" id="rp-run" style="align-self:flex-end">Run report</button>
    </div><div id="rp-out"></div>`;
  const read = () => {
    rep.type = $('#rp-type').value; rep.preset = $('#rp-preset').value;
    if ($('#rp-start')) rep.start = $('#rp-start').value;
    rep.end = $('#rp-end').value;
    if ($('#rp-cols')) { rep.columns = $('#rp-cols').value; rep.compare = $('#rp-cmp').value; }
    if ($('#rp-acct')) rep.account_id = $('#rp-acct').value;
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
    const text = c.kind === 'pct' ? `${v.toFixed(1)}%` : fmt(v);
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
