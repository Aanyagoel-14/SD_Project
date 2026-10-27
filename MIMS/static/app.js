// MIMS - Mock Interview Management System (Team T065). Plain JavaScript, no build step.
// One screen is rendered at a time from `state` (fetched from /api/state) and `view`.

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let state = null;                 // everything the server says this user may see
let view = { page: 'Home' };      // current screen; { page: 'Slot', id } for a slot page
let authTab = 'login';            // 'login' | 'signup'
let filters = {};

const ROLE_NAMES = { Student: 'Student', Interviewer: 'Interviewer', Coordinator: 'Placement Cell Coordinator', Admin: 'Administrator' };
const DEMO = { Student: 'student@mims.demo', Interviewer: 'interviewer@mims.demo', Coordinator: 'coordinator@mims.demo', Admin: 'admin@mims.demo' };
const NAV = { Student: ['Home', 'Find slots', 'My interviews'], Interviewer: ['Home', 'My slots'], Coordinator: ['Home', 'Slots', 'Reports'], Admin: ['Home', 'Users', 'Reports'] };
const PASSWORD_RULES = [['8 or more characters', p => p.length >= 8], ['An uppercase letter', p => /[A-Z]/.test(p)], ['A lowercase letter', p => /[a-z]/.test(p)], ['A number', p => /\d/.test(p)], ['A special character', p => /[^A-Za-z0-9]/.test(p)]];
const ICONS = {
  bell: '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
  info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
  alert: '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  check: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>'
};
const icon = n => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n]}</svg>`;

// ---------- Formatting helpers ----------
const fmtDate = d => d ? new Date(d + 'T12:00:00').toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }) : '—';
const fmtTime = t => { if (!t) return ''; const [h, m] = t.split(':').map(Number); return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`; };
const timeRange = s => `${fmtTime(s.start)} – ${fmtTime(s.end)}`;
const options = (list, selected) => list.map(x => `<option ${String(selected) === String(x) ? 'selected' : ''}>${esc(x)}</option>`).join('');
const badge = (text, tone = 'grey') => `<span class="badge b-${tone}">${esc(text)}</span>`;
const SLOT_TONE = { Draft: 'grey', Allocated: 'amber', Published: 'green', Completed: 'blue', Cancelled: 'red' };
const slotBadge = s => badge(s.status === 'Published' && s.date < state.today ? 'Feedback pending' : s.status, s.status === 'Published' && s.date < state.today ? 'amber' : SLOT_TONE[s.status]);
const note = (text, tone = '') => `<div class="note ${tone}">${icon(tone === 'info' ? 'info' : tone === 'ok' ? 'check' : 'alert')}<div>${text}</div></div>`;
const empty = text => `<div class="empty">${text}</div>`;
const card = (title, body, sub = '', action = '') => `<section class="card"><div class="card-head"><div><h2>${title}</h2>${sub ? `<p>${sub}</p>` : ''}</div>${action}</div>${body}</section>`;
const stats = items => `<div class="stats">${items.map(([label, value]) => `<div class="stat"><span>${label}</span><b>${value}</b></div>`).join('')}</div>`;
const table = (head, rows, emptyText) => rows.length ? `<div class="table-wrap"><table><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>` : empty(emptyText);
const pageHead = (title, sub = '', action = '') => `<div class="page-head"><div><h1>${title}</h1>${sub ? `<p>${sub}</p>` : ''}</div>${action ? `<div>${action}</div>` : ''}</div>`;
const upcoming = s => s.date >= state.today;
const seatsLeft = s => Math.max(0, s.capacity - s.registered);

// ---------- Server calls ----------
async function api(path, data) {
  const res = await fetch('/api/' + path, { method: data ? 'POST' : 'GET', headers: data ? { 'Content-Type': 'application/json' } : {}, body: data ? JSON.stringify(data) : undefined });
  const body = await res.json();
  if (!res.ok) {
    if (res.status === 401 && path === 'state') { showLogin(); return null; }
    throw Error(body.error || 'Something went wrong.');
  }
  return body;
}
async function refresh() { try { const s = await api('state'); if (s) { state = s; render(); } } catch (e) { toast(e.message); } }
function toast(message) { const t = $('#toast'); t.textContent = message; t.style.display = 'block'; clearTimeout(window.toastTimer); window.toastTimer = setTimeout(() => t.style.display = 'none', 4000); }

// ================= Login and Create account =================
const passwordInput = (name, autocomplete) => `<div class="password"><input type="password" name="${name}" required autocomplete="${autocomplete}"><button type="button" data-toggle-pw>Show</button></div>`;
const L = (text, required = true) => `<span>${text}${required ? ' <span class="req">*</span>' : ' <span class="opt">(optional)</span>'}</span>`;

function signupFields(role) {
  if (!role) return `<p class="hint">Choose a user type to see the fields you need to fill in.</p>`;
  const common = `<div class="grid-3"><label>${L('First name')}<input name="first" required maxlength="50" autocomplete="given-name"></label><label>${L('Middle name', false)}<input name="middle" maxlength="50" autocomplete="additional-name"></label><label>${L('Last name')}<input name="last" required maxlength="50" autocomplete="family-name"></label></div>
    <div class="grid-2"><label>${L('Email')}<input type="email" name="email" required autocomplete="email"></label><label>${L('Mobile number')}<input name="phone" required inputmode="numeric" maxlength="13" placeholder="10-digit number"></label></div>`;
  const byRole = {
    Student: `<div class="form-section">Academic details</div>
      <div class="grid-2"><label>${L('Roll number')}<input name="roll" required maxlength="20" placeholder="24UCS119"></label><label>${L('Branch')}<select name="branch" required><option value="">Select branch</option>${options(['CSE', 'CCE', 'ECE', 'ME'])}</select></label></div>
      <div class="grid-2"><label>${L('Batch (graduation year)')}<input type="number" name="batch" required min="2020" max="2040" value="2028"></label><label>${L('CGPA')}<input type="number" name="cgpa" required min="0" max="10" step="0.01" placeholder="8.40"></label></div>`,
    Interviewer: `<div class="form-section">Professional details</div>
      <div class="grid-2"><label>${L('Employee / staff ID')}<input name="employee_id" required maxlength="20"></label><label>${L('Designation')}<input name="designation" required maxlength="100" placeholder="Assistant Professor, CSE"></label></div>
      <div class="grid-2"><label>${L('Domain of expertise')}<select name="domain" required><option value="">Select domain</option>${options(['Software Engineering', 'Data Science', 'Core Electronics', 'HR & Behavioural', 'Consulting & Case'])}</select></label><label>${L('Years of experience')}<input type="number" name="experience" required min="0" max="60"></label></div>`,
    Coordinator: `<div class="form-section">Staff details</div>
      <div class="grid-2"><label>${L('Employee ID')}<input name="employee_id" required maxlength="20"></label><label>${L('Department')}<input name="department" required maxlength="100" value="Placement Cell"></label></div>`
  };
  const approval = role === 'Student' ? '' : note('Interviewer and coordinator accounts are activated after the administrator approves them.', 'info');
  return `${common}${byRole[role]}<div class="form-section">Password</div>
    <div class="grid-2"><label>${L('Password')}${passwordInput('password', 'new-password')}</label><label>${L('Confirm password')}${passwordInput('confirm', 'new-password')}</label></div>
    <ul class="rules" id="rules">${PASSWORD_RULES.map(([t]) => `<li>${t}</li>`).join('')}</ul>${approval}`;
}

function showLogin(message = '') {
  state = null;
  const userTypes = (list, sel) => `<option value="">Please select</option>${list.map(r => `<option value="${r}" ${r === sel ? 'selected' : ''}>${ROLE_NAMES[r]}</option>`).join('')}`;
  const body = authTab === 'login'
    ? `<h1>Log in</h1><p>Choose your user type and enter your details.</p>
       ${message}
       <form id="auth" class="form" novalidate>
         <label>${L('User type')}<select name="role" id="role" required>${userTypes(['Student', 'Interviewer', 'Coordinator', 'Admin'])}</select><span class="hint" id="demo-hint"></span></label>
         <label>${L('Email')}<input type="email" name="email" required autocomplete="username"></label>
         <label>${L('Password')}${passwordInput('password', 'current-password')}</label>
         <div class="error" id="auth-error" role="alert"></div>
         <button class="full">Log in</button>
       </form>`
    : `<h1>Create an account</h1><p>The details we ask for depend on your user type.</p>
       <form id="auth" class="form" novalidate>
         <label>${L('User type')}<select name="role" id="role" required>${userTypes(['Student', 'Interviewer', 'Coordinator'], filters.signupRole)}</select><span class="hint">Administrator accounts cannot be created here.</span></label>
         <div id="role-fields" class="form">${signupFields(filters.signupRole)}</div>
         <div class="error" id="auth-error" role="alert"></div>
         <button class="full">Create account</button>
       </form>`;
  $('#app').innerHTML = `<div class="auth">
    <div class="brand"><span class="mark">M</span><span>Mock Interview Management System<small>LNMIIT Jaipur · Placement Cell</small></span></div>
    <div class="auth-card ${authTab === 'signup' ? 'wide' : ''}">
      <div class="auth-tabs"><button type="button" data-auth="login" class="${authTab === 'login' ? 'active' : ''}">Log in</button><button type="button" data-auth="signup" class="${authTab === 'signup' ? 'active' : ''}">Create account</button></div>
      <div class="auth-body">${body}</div>
    </div>
    <p class="auth-foot">CS314 Software Development Laboratory · Team T065 · Local prototype</p></div>`;
  bindAuth();
}

function bindAuth() {
  $$('[data-auth]').forEach(b => b.onclick = () => { authTab = b.dataset.auth; showLogin(); });
  const bindPasswords = () => {
    $$('[data-toggle-pw]').forEach(b => b.onclick = () => { const i = b.previousElementSibling, show = i.type === 'password'; i.type = show ? 'text' : 'password'; b.textContent = show ? 'Hide' : 'Show'; });
    const p = $('#auth [name=password]'), rules = $('#rules');
    if (p && rules) p.oninput = () => [...rules.children].forEach((li, i) => li.classList.toggle('ok', PASSWORD_RULES[i][1](p.value)));
  };
  bindPasswords();
  $('#role').onchange = e => {
    if (authTab === 'login') { $('#demo-hint').innerHTML = e.target.value ? `Demo account: <b>${DEMO[e.target.value]}</b> · password <b>Demo@123</b>` : ''; return; }
    filters.signupRole = e.target.value; $('#role-fields').innerHTML = signupFields(e.target.value); bindPasswords();
  };
  $('#auth').onsubmit = async e => {
    e.preventDefault();
    const form = e.target, err = $('#auth-error'), button = form.querySelector('button.full'), d = Object.fromEntries(new FormData(form));
    err.textContent = '';
    const missing = [...form.querySelectorAll('[required]')].find(i => !i.value.trim());
    if (missing) { missing.focus(); return err.textContent = missing.name === 'role' ? 'Please select a user type.' : 'Please fill in all required fields.'; }
    if (authTab === 'signup') {
      if (PASSWORD_RULES.some(([, ok]) => !ok(d.password))) return err.textContent = 'The password does not meet all the rules shown.';
      if (d.password !== d.confirm) return err.textContent = 'The two passwords do not match.';
    }
    button.disabled = true;
    try {
      const res = await api(authTab === 'login' ? 'login' : 'signup', d);
      if (res.pending) { authTab = 'login'; filters = {}; return showLogin(note('Your account was created and is waiting for administrator approval. You will be able to log in once it is approved.', 'ok')); }
      view = { page: 'Home' }; filters = {}; await refresh();
    } catch (x) { err.textContent = x.message; } finally { button.disabled = false; }
  };
}

// ================= Shell =================
function go(page, extra = {}) { view = { page, ...extra }; render(); window.scrollTo(0, 0); }

function render() {
  if (!state) return;
  const role = state.user.role, unread = state.notifications.filter(n => n.unread).length;
  $('#app').innerHTML = `<header class="topbar"><div class="topbar-inner">
      <div class="brand"><span class="mark">M</span><span>MIMS<small>${ROLE_NAMES[role]}</small></span></div>
      <nav class="tabs-nav">${NAV[role].map(p => `<button data-go="${p}" class="${view.page === p || (view.page === 'Slot' && view.from === p) ? 'active' : ''}">${p}</button>`).join('')}</nav>
      <div class="top-right">
        <button class="bell" data-go="Notifications" title="Notifications" aria-label="Notifications">${icon('bell')}${unread ? `<span class="count">${unread}</span>` : ''}</button>
        <div class="who"><strong>${esc(state.user.name)}</strong><span>${esc(state.user.email)}</span></div>
        <button class="btn-light btn-sm" id="logout">Log out</button>
      </div></div></header>
    <main class="container">${screen()}<footer class="foot"><span>MIMS · Mock Interview Management System</span><span>Team T065 · LNMIIT Jaipur · Local prototype</span></footer></main>`;
  bind();
}

function screen() {
  const role = state.user.role;
  if (view.page === 'Notifications') return notificationsPage();
  if (view.page === 'Slot') return slotPage();
  if (view.page === 'Reports') return reportsPage();
  return ({ Student: studentPage, Interviewer: interviewerPage, Coordinator: coordinatorPage, Admin: adminPage })[role]();
}

function notificationsPage() {
  const n = state.notifications;
  return pageHead('Notifications', 'Confirmations, reminders, changes and feedback alerts.', n.some(x => x.unread) ? '<button class="btn-light" id="read-all">Mark all as read</button>' : '')
    + card('All notifications', n.length ? n.map(x => `<div class="notif ${x.unread ? '' : 'read'}"><span class="dot"></span><div><b>${esc(x.type)}</b> · ${esc(x.message)}<small>${esc(x.created)} UTC</small></div></div>`).join('') : empty('You have no notifications.'));
}

// ================= Student =================
function studentPage() {
  const u = state.user, regs = state.registrations;
  const active = regs.filter(r => r.status === 'Registered' && r.date >= state.today && r.slot_status === 'Published');
  const scored = regs.filter(r => r.score != null);
  const avg = scored.length ? (scored.reduce((t, r) => t + r.score, 0) / scored.length).toFixed(1) : '—';
  if (view.page === 'Home') {
    const openForMe = state.slots.filter(s => s.open && !s.ineligible && !(s.mine && s.mine.status === 'Registered') && seatsLeft(s));
    return pageHead(`Welcome, ${esc(u.name.split(' ')[0])}`, `${esc(u.roll)} · ${esc(u.branch)} · Batch ${esc(u.batch)} · CGPA ${esc(u.cgpa)}`, '<button data-go="Find slots">Find a slot</button>')
      + stats([['Upcoming interviews', active.length], ['Slots you can join', openForMe.length], ['Interviews attended', regs.filter(r => r.attendance === 'Present').length], ['Average score', scored.length ? `${avg} / ${state.score_max}` : '—']])
      + card('Your upcoming interviews', active.length ? active.map(r => `<div class="list-row"><div><b>${esc(r.domain)}</b> · slot #${r.slot}<small>${fmtDate(r.date)}, ${timeRange(r)} · ${esc(r.mode)} · ${esc(r.venue)}</small></div><button class="btn-light btn-sm" data-slot="${r.slot}">Open</button></div>`).join('') : empty('You have no upcoming interviews. <button class="link" data-go="Find slots">Find a slot</button>'))
      + card('Latest feedback', scored.length ? scored.slice(0, 3).map(r => `<div class="list-row"><div><b>${esc(r.domain)}</b> · ${fmtDate(r.date)} · ${esc(r.interviewer_name)}<small>${esc(r.comments)}</small></div>${badge(`${r.score} / ${state.score_max}`, 'blue')}</div>`).join('') : empty('Feedback appears here after your interviews.'));
  }
  if (view.page === 'Find slots') {
    const q = (filters.q || '').toLowerCase();
    const list = state.slots.filter(s => s.status === 'Published' && upcoming(s) && (!filters.domain || s.domain === filters.domain) && (!q || `${s.domain} ${s.interviewer_name} ${s.venue}`.toLowerCase().includes(q)));
    return pageHead('Find slots', 'Published mock interview slots. Open a slot to see the details and register.')
      + `<div class="toolbar"><input id="f-q" placeholder="Search domain, interviewer or venue" value="${esc(filters.q || '')}"><select id="f-domain"><option value="">All domains</option>${options(state.domains, filters.domain)}</select></div>`
      + (list.length ? `<div class="slot-grid">${list.map(slotCard).join('')}</div>` : card('No slots', empty('No published slots match your search.')));
  }
  if (view.page === 'My interviews') {
    return pageHead('My interviews', 'Every slot you registered for, with attendance and feedback.')
      + stats([['Registered (upcoming)', active.length], ['Attended', regs.filter(r => r.attendance === 'Present').length], ['Feedback received', scored.length], ['Average score', scored.length ? `${avg} / ${state.score_max}` : '—']])
      + card('Registrations', table(['Slot', 'Date and time', 'Interviewer', 'Status', 'Score', ''], regs.map(r => {
          const status = r.status === 'Cancelled' ? badge('Cancelled', 'red') : r.attendance === 'Absent' ? badge('Absent', 'red') : r.attendance === 'Present' ? badge('Attended', 'green') : badge('Registered', 'blue');
          return `<tr><td><b>${esc(r.domain)}</b><small>Slot #${r.slot} · ${esc(r.mode)}</small></td><td>${fmtDate(r.date)}<small>${timeRange(r)}</small></td><td>${esc(r.interviewer_name || '—')}</td><td>${status}</td><td>${r.score != null ? `<b>${r.score} / ${state.score_max}</b>` : r.attendance === 'Present' ? '<small>Awaiting feedback</small>' : '—'}</td>
            <td><div class="actions">${r.score != null ? `<button class="btn-light btn-sm" data-view-feedback="${r.id}">View feedback</button>` : ''}<button class="btn-light btn-sm" data-slot="${r.slot}">Open slot</button></div></td></tr>`;
        }), 'You have not registered for any slot yet.'));
  }
  return '';
}

function studentSlotStatus(s) {
  if (s.mine && s.mine.status === 'Registered') return ['Registered', 'blue'];
  if (s.status !== 'Published') return [s.status, SLOT_TONE[s.status]];
  if (s.ineligible) return ['Not eligible', 'grey'];
  if (!s.open) return ['Registration closed', 'grey'];
  if (!seatsLeft(s)) return ['Full', 'red'];
  return ['Open', 'green'];
}

function slotCard(s) {
  const [label, tone] = studentSlotStatus(s);
  return `<article class="slot-card"><header><div><h2>${esc(s.domain)}</h2><small>Slot #${s.id} · ${esc(s.mode)}</small></div>${badge(label, tone)}</header>
    <dl><div><dt>Date</dt><dd>${fmtDate(s.date)}</dd></div><div><dt>Time</dt><dd>${timeRange(s)}</dd></div><div><dt>Interviewer</dt><dd>${esc(s.interviewer_name)}</dd></div><div><dt>Seats left</dt><dd>${seatsLeft(s)} of ${s.capacity}</dd></div><div><dt>Registration closes</dt><dd>${fmtDate(s.reg_end)}</dd></div></dl>
    ${s.ineligible ? `<p class="why">${esc(s.ineligible)}</p>` : ''}
    <footer><button class="${label === 'Open' ? '' : 'btn-light'} full" data-slot="${s.id}">${label === 'Open' ? 'View and register' : 'View details'}</button></footer></article>`;
}

// ================= Interviewer =================
function interviewerPage() {
  const slots = state.slots.filter(s => s.status !== 'Cancelled'), regs = state.registrations;
  const pending = regs.filter(r => r.attendance === 'Present' && r.score == null);
  const unmarked = regs.filter(r => !r.attendance && r.date <= state.today);
  const next = slots.filter(s => upcoming(s) && s.status === 'Published');
  const row = s => `<tr class="clickable" data-slot="${s.id}"><td><b>${esc(s.domain)}</b><small>Slot #${s.id}</small></td><td>${fmtDate(s.date)}<small>${timeRange(s)}</small></td><td>${esc(s.mode)}<small>${esc(s.venue || '—')}</small></td><td>${s.registered} / ${s.capacity}</td><td>${slotBadge(s)}</td><td><div class="actions"><button class="btn-light btn-sm" data-slot="${s.id}">Open</button></div></td></tr>`;
  const head = ['Slot', 'Date and time', 'Mode / venue', 'Students', 'Status', ''];
  if (view.page === 'Home') {
    return pageHead(`Welcome, ${esc(state.user.name)}`, `${esc(state.user.designation)} · ${esc(state.user.domain)}`)
      + stats([['Upcoming slots', next.length], ['Attendance to mark', unmarked.length], ['Feedback to give', pending.length], ['Students evaluated', regs.filter(r => r.score != null).length]])
      + (pending.length || unmarked.length ? note(`You have <b>${unmarked.length}</b> attendance entr${unmarked.length === 1 ? 'y' : 'ies'} and <b>${pending.length}</b> feedback form${pending.length === 1 ? '' : 's'} pending. Open the slot to complete them.`) : '')
      + card('Slots that need action', table(head, slots.filter(s => s.status === 'Published' && s.date <= state.today).map(row), 'Nothing pending.'))
      + card('Upcoming slots', table(head, next.map(row), 'No upcoming slots are allocated to you.'));
  }
  return pageHead('My slots', 'Slots the placement cell allocated to you. Open a slot to see students, mark attendance and give feedback.')
    + card('Upcoming', table(head, slots.filter(s => upcoming(s) && s.status !== 'Completed').map(row), 'No upcoming slots.'))
    + card('Past', table(head, slots.filter(s => !upcoming(s) || s.status === 'Completed').reverse().map(row), 'No past slots.'));
}

// ================= Coordinator =================
function coordinatorPage() {
  const slots = state.slots, regs = state.registrations;
  const row = s => `<tr class="clickable" data-slot="${s.id}"><td><b>${esc(s.domain)}</b><small>Slot #${s.id}</small></td><td>${fmtDate(s.date)}<small>${timeRange(s)}</small></td><td>${esc(s.interviewer_name || '—')}</td><td>${s.registered} / ${s.capacity}</td><td>${slotBadge(s)}</td><td><div class="actions"><button class="btn-light btn-sm" data-slot="${s.id}">Open</button></div></td></tr>`;
  const head = ['Slot', 'Date and time', 'Interviewer', 'Registered', 'Status', ''];
  if (view.page === 'Home') {
    const todo = slots.filter(s => ['Draft', 'Allocated'].includes(s.status));
    return pageHead(`Welcome, ${esc(state.user.name)}`, `${esc(state.user.department)} · create slots, allocate interviewers and publish them to students.`, '<button id="new-slot">Create slot</button>')
      + stats([['Drafts (no interviewer)', slots.filter(s => s.status === 'Draft').length], ['Ready to publish', slots.filter(s => s.status === 'Allocated').length], ['Open for registration', slots.filter(s => s.open).length], ['Students registered', regs.filter(r => r.status === 'Registered').length]])
      + card('Needs your action', todo.length ? todo.map(s => `<div class="list-row"><div><b>${esc(s.domain)}</b> · slot #${s.id} · ${fmtDate(s.date)}, ${timeRange(s)}<small>${s.status === 'Draft' ? 'Next step: allocate an interviewer.' : `Interviewer ${esc(s.interviewer_name)} allocated. Next step: publish.`}</small></div><button class="btn-sm" data-slot="${s.id}">${s.status === 'Draft' ? 'Allocate' : 'Publish'}</button></div>`).join('') : empty('Nothing waiting. All slots are published.'))
      + card('Upcoming published slots', table(head, slots.filter(s => s.status === 'Published' && upcoming(s)).map(row), 'No upcoming published slots.'));
  }
  const tabs = ['All', 'Draft', 'Allocated', 'Published', 'Completed', 'Cancelled'], f = filters.status || 'All';
  return pageHead('Slots', 'Every mock interview slot. Open a slot to edit, allocate, publish or cancel it.', '<button id="new-slot">Create slot</button>')
    + `<div class="toolbar"><div class="segmented">${tabs.map(t => `<button data-status="${t}" class="${f === t ? 'active' : ''}">${t} (${t === 'All' ? slots.length : slots.filter(s => s.status === t).length})</button>`).join('')}</div></div>`
    + card(`${f} slots`, table(head, slots.filter(s => f === 'All' || s.status === f).map(row), 'No slots in this list.'));
}

// ================= Administrator =================
function adminPage() {
  const users = state.users, pending = users.filter(u => u.status === 'Pending');
  const details = u => u.role === 'Student' ? `${u.roll} · ${u.branch} · ${u.batch} · CGPA ${u.cgpa}` : u.role === 'Interviewer' ? `${u.designation} · ${u.domain} · ${u.experience} yrs` : u.role === 'Coordinator' ? `${u.employee_id} · ${u.department}` : '';
  if (view.page === 'Home') {
    return pageHead('Administration', 'Approve new accounts, manage users and watch system activity.')
      + stats([['User accounts', users.length], ['Waiting for approval', pending.length], ['Interview slots', state.slots.length], ['Active registrations', state.registrations.filter(r => r.status === 'Registered').length]])
      + `<div class="split">`
      + card('Waiting for approval', pending.length ? pending.map(u => `<div class="list-row"><div><b>${esc(u.name)}</b> · ${ROLE_NAMES[u.role]}<small>${esc(u.email)} · ${esc(details(u))}</small></div><div class="actions" style="display:flex;gap:6px"><button class="btn-sm" data-user="${u.id}" data-set="Active">Approve</button><button class="btn-danger btn-sm" data-user="${u.id}" data-set="Inactive">Reject</button></div></div>`).join('') : empty('No accounts are waiting.'), 'Interviewer and coordinator sign-ups.')
      + card('Recent activity', state.audit.length ? state.audit.map(a => `<div class="list-row"><div>${esc(a.name)} · ${esc(a.action)}<small>${esc(a.created)} UTC</small></div></div>`).join('') : empty('No activity yet.'), 'From the audit log.')
      + `</div>`;
  }
  const f = filters.role || 'All', q = (filters.q || '').toLowerCase();
  const list = users.filter(u => (f === 'All' || u.role === f) && (!q || `${u.name} ${u.email}`.toLowerCase().includes(q)));
  return pageHead('Users', 'Approve, deactivate or change the role of an account.')
    + `<div class="toolbar"><div class="segmented">${['All', ...state.roles].map(r => `<button data-role-tab="${r}" class="${f === r ? 'active' : ''}">${r === 'All' ? 'All' : ROLE_NAMES[r]}</button>`).join('')}</div><input id="f-q" placeholder="Search name or email" value="${esc(filters.q || '')}"></div>`
    + card(`${list.length} account${list.length === 1 ? '' : 's'}`, table(['Name', 'Details', 'Role', 'Status', ''], list.map(u => {
        const me = u.id === state.user.id, tone = { Active: 'green', Pending: 'amber', Inactive: 'red' }[u.status];
        return `<tr><td><b>${esc(u.name)}</b><small>${esc(u.email)} · ${esc(u.phone || '')}</small></td><td><small>${esc(details(u))}</small></td>
          <td>${me ? ROLE_NAMES[u.role] : `<select data-change-role="${u.id}" style="width:auto;padding:5px 8px">${state.roles.map(r => `<option value="${r}" ${r === u.role ? 'selected' : ''}>${ROLE_NAMES[r]}</option>`).join('')}</select>`}</td>
          <td>${badge(u.status, tone)}</td>
          <td><div class="actions">${me ? '<small>You</small>' : u.status === 'Pending' ? `<button class="btn-sm" data-user="${u.id}" data-set="Active">Approve</button><button class="btn-danger btn-sm" data-user="${u.id}" data-set="Inactive">Reject</button>` : u.status === 'Active' ? `<button class="btn-danger btn-sm" data-user="${u.id}" data-set="Inactive">Deactivate</button>` : `<button class="btn-light btn-sm" data-user="${u.id}" data-set="Active">Activate</button>`}</div></td></tr>`;
      }), 'No users match.'));
}

// ================= Reports (Coordinator and Administrator) =================
function reportsPage() {
  const slots = state.slots.filter(s => s.status !== 'Draft'), regs = state.registrations;
  const slotRows = slots.map(s => {
    const r = regs.filter(x => x.slot === s.id && x.status === 'Registered'), scored = r.filter(x => x.score != null);
    return [`#${s.id}`, s.domain, s.date, s.interviewer_name || '—', s.status, `${r.length} / ${s.capacity}`, r.filter(x => x.attendance === 'Present').length, r.filter(x => x.attendance === 'Absent').length, scored.length ? (scored.reduce((t, x) => t + x.score, 0) / scored.length).toFixed(1) : '—'];
  });
  const perfRows = state.performance.map(p => [p.roll, p.name, p.branch, p.batch, p.attended || 0, p.evaluated || 0, p.average ?? '—']);
  window.reports = { slots: [['Slot', 'Domain', 'Date', 'Interviewer', 'Status', 'Registered', 'Present', 'Absent', 'Average score'], ...slotRows], students: [['Roll number', 'Student', 'Branch', 'Batch', 'Interviews attended', 'Feedback received', 'Average score'], ...perfRows] };
  const render = rows => rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`);
  return pageHead('Reports', 'Registration, attendance and performance across all mock interviews.')
    + stats([['Slots run or scheduled', slots.length], ['Registrations', regs.filter(r => r.status === 'Registered').length], ['Students attended', regs.filter(r => r.attendance === 'Present').length], ['Feedback given', regs.filter(r => r.score != null).length]])
    + card('Slot summary', table(window.reports.slots[0], render(slotRows), 'No slots yet.'), 'Registrations, attendance and average score per slot (FR-039 to FR-041).', '<button class="btn-light btn-sm" data-export="slots">Export CSV</button>')
    + card('Student performance', table(window.reports.students[0], render(perfRows), 'No students yet.'), `Average of all feedback scores out of ${state.score_max} (FR-042).`, '<button class="btn-light btn-sm" data-export="students">Export CSV</button>');
}

// ================= Slot page (all roles) =================
function lifecycle(s) {
  if (s.status === 'Cancelled') return `<div class="steps cancelled"><div><i>✕</i> This slot was cancelled</div></div>`;
  const steps = ['Draft', 'Allocated', 'Published', 'Completed'], at = steps.indexOf(s.status);
  const labels = { Draft: 'Created (draft)', Allocated: 'Interviewer allocated', Published: 'Published to students', Completed: 'Interview completed' };
  return `<div class="steps">${steps.map((st, i) => `<div class="${i < at || s.status === 'Completed' ? 'done' : i === at ? 'now' : ''}"><i>${i < at || s.status === 'Completed' ? '✓' : i + 1}</i>${labels[st]}</div>`).join('')}</div>`;
}

function slotPage() {
  const s = state.slots.find(x => x.id === view.id);
  if (!s) return pageHead('Slot not found') + card('Not available', empty('This slot is not available to you.'));
  const role = state.user.role;
  const eligibility = [s.branches.length ? s.branches.join(', ') : 'All branches', s.min_cgpa ? `CGPA ${s.min_cgpa} or above` : 'No CGPA limit'].join(' · ');
  const details = `<dl class="details">
      <div><dt>Date</dt><dd>${fmtDate(s.date)}</dd></div><div><dt>Time</dt><dd>${timeRange(s)}</dd></div>
      <div><dt>Mode</dt><dd>${esc(s.mode)}</dd></div><div><dt>${s.mode === 'Online' ? 'Meeting link' : 'Venue'}</dt><dd>${esc(s.venue || 'Not set yet')}</dd></div>
      <div><dt>Interviewer</dt><dd>${esc(s.interviewer_name || 'Not allocated yet')}</dd></div><div><dt>Seats</dt><dd>${s.registered} registered of ${s.capacity}</dd></div>
      <div><dt>Registration closes</dt><dd>${fmtDate(s.reg_end)}</dd></div><div><dt>Eligibility</dt><dd>${eligibility}</dd></div>
      <div class="wide"><dt>Instructions for students</dt><dd>${esc(s.instructions || '—')}</dd></div></dl>`;
  let actions = '', notice = '';
  if (role === 'Student') {
    const mine = s.mine && s.mine.status === 'Registered';
    if (mine) { notice = note(`You are registered for this slot.${s.open ? ' You can cancel while registration is open.' : ''}`, 'ok'); if (s.open) actions = `<button class="btn-danger" data-cancel-reg="${s.mine.id}">Cancel my registration</button>`; }
    else if (s.status !== 'Published') notice = note(`This slot is ${s.status.toLowerCase()}.`, 'info');
    else if (s.ineligible) notice = note(`You cannot register: ${esc(s.ineligible)}`, 'bad');
    else if (!s.open) notice = note('Registration for this slot is closed.', 'info');
    else if (!seatsLeft(s)) notice = note('This slot is full.', 'bad');
    else actions = `<button data-register="${s.id}">Register for this slot</button>`;
  }
  if (role === 'Coordinator') {
    const btn = [];
    if (['Draft', 'Allocated'].includes(s.status) || (s.status === 'Published' && upcoming(s))) btn.push(`<button class="btn-light" data-edit="${s.id}">Edit</button>`);
    if (['Draft', 'Allocated'].includes(s.status)) btn.push(`<button class="${s.status === 'Draft' ? '' : 'btn-light'}" data-allocate="${s.id}">${s.status === 'Draft' ? 'Allocate interviewer' : 'Change interviewer'}</button>`);
    if (s.status === 'Allocated') btn.push(`<button data-publish="${s.id}">Publish</button>`);
    if (['Draft', 'Allocated'].includes(s.status) || (s.status === 'Published' && upcoming(s))) btn.push(`<button class="btn-danger" data-cancel-slot="${s.id}">Cancel slot</button>`);
    actions = btn.join(' ');
    if (s.status === 'Draft') notice = note('Next step: allocate an interviewer. A slot cannot be published without one (BR-002).', 'info');
    if (s.status === 'Allocated') notice = note(`${esc(s.interviewer_name)} is allocated. Next step: publish the slot so eligible students can register.`, 'info');
  }
  const regs = state.registrations.filter(r => r.slot === s.id && (role !== 'Coordinator' || r.status === 'Registered' || s.status === 'Cancelled'));
  let participants = '';
  if (role === 'Interviewer' || role === 'Coordinator') {
    const canMark = role === 'Interviewer' && s.date <= state.today && ['Published', 'Completed'].includes(s.status);
    if (role === 'Interviewer' && !canMark && upcoming(s) && s.date > state.today) notice = note(`Attendance and feedback open on the interview day (${fmtDate(s.date)}).`, 'info');
    participants = card(`Students (${regs.filter(r => r.status === 'Registered').length})`, table(['Student', 'Branch', 'CGPA', 'Attendance', 'Feedback', ''], regs.map(r => {
      const att = r.attendance ? badge(r.attendance, r.attendance === 'Present' ? 'green' : 'red') : r.status === 'Cancelled' ? badge('Cancelled', 'red') : '<small>Not marked</small>';
      const fb = r.score != null ? `<b>${r.score} / ${state.score_max}</b>` : r.attendance === 'Present' ? badge('Pending', 'amber') : '—';
      let act = '';
      if (canMark && r.score == null) act += `<button class="${r.attendance === 'Present' ? '' : 'btn-light'} btn-sm" data-attend="${r.id}" data-value="Present">Present</button><button class="${r.attendance === 'Absent' ? 'btn-danger' : 'btn-light'} btn-sm" data-attend="${r.id}" data-value="Absent">Absent</button>`;
      if (canMark && r.attendance === 'Present' && r.score == null) act += `<button class="btn-sm" data-feedback="${r.id}">Give feedback</button>`;
      if (r.score != null) act += `<button class="btn-light btn-sm" data-view-feedback="${r.id}">View feedback</button>`;
      return `<tr><td><b>${esc(r.student_name)}</b><small>${esc(r.roll)}</small></td><td>${esc(r.branch)}</td><td>${esc(r.cgpa)}</td><td>${att}</td><td>${fb}</td><td><div class="actions">${act}</div></td></tr>`;
    }), 'No students have registered yet.'), canMark ? 'Mark attendance, then give feedback to every student marked Present.' : '');
  }
  const from = view.from || NAV[role][1];
  return `<button class="link back" data-go="${from}">← Back to ${from}</button>`
    + pageHead(`${esc(s.domain)} <span class="muted" style="font-weight:500">· Slot #${s.id}</span>`, '', actions)
    + (role === 'Student' ? '' : lifecycle(s)) + notice
    + card('Slot details', details, '', role === 'Student' ? badge(...studentSlotStatus(s)) : slotBadge(s)) + participants;
}

// ================= Modals =================
function modal(title, body, submitLabel, onSubmit, danger = false) {
  const d = $('#modal');
  d.innerHTML = `<form id="modal-form" novalidate><div class="modal-head"><h2>${title}</h2><button type="button" data-close aria-label="Close">×</button></div>
    <div class="modal-body">${body}<div class="error" id="modal-error" role="alert" style="margin-top:12px"></div></div>
    <div class="modal-foot"><button type="button" class="btn-light" data-close>${submitLabel ? 'Cancel' : 'Close'}</button>${submitLabel ? `<button id="modal-submit" class="${danger ? 'btn-danger' : ''}">${submitLabel}</button>` : ''}</div></form>`;
  $$('[data-close]').forEach(b => b.onclick = () => d.close());
  $('#modal-form').onsubmit = async e => {
    e.preventDefault(); if (!onSubmit) return;
    $('#modal-error').textContent = '';
    const form = e.target, missing = [...form.querySelectorAll('[required]')].find(i => !i.value.trim());
    if (missing) { missing.focus(); return $('#modal-error').textContent = 'Please fill in all required fields.'; }
    const b = $('#modal-submit'); b.disabled = true;
    try { const fd = new FormData(form); await onSubmit(Object.fromEntries(fd), fd); d.close(); await refresh(); }
    catch (x) { $('#modal-error').textContent = x.message; } finally { b.disabled = false; }
  };
  if (!d.open) d.showModal();
}

function slotForm(s) {
  const v = k => s ? esc(s[k] ?? '') : '';
  const locked = s && s.status === 'Published' ? 'disabled' : '';
  return `<div class="form">${locked ? note('This slot is published. Only the venue, seats, closing date and instructions can be changed. Registered students will be notified.', 'info') : ''}
    <div class="grid-3"><label>${L('Date')}<input type="date" name="date" required min="${state.today}" value="${v('date')}" ${locked}></label><label>${L('Start time')}<input type="time" name="start" required value="${v('start')}" ${locked}></label><label>${L('End time')}<input type="time" name="end" required value="${v('end')}" ${locked}></label></div>
    <div class="grid-2"><label>${L('Domain')}<select name="domain" required ${locked}><option value="">Select domain</option>${options(state.domains, s?.domain)}</select></label><label>${L('Mode')}<select name="mode" ${locked}>${options(state.modes, s?.mode || 'Online')}</select></label></div>
    <label>${L('Venue or meeting link', false)}<input name="venue" maxlength="200" value="${v('venue')}" placeholder="Needed before publishing"></label>
    <div class="grid-2"><label>${L('Seats')}<input type="number" name="capacity" required min="1" max="200" value="${s ? s.capacity : 5}"></label><label>${L('Registration closes on', false)}<input type="date" name="reg_end" min="${state.today}" value="${v('reg_end')}"></label></div>
    <div class="form-section">Eligibility</div>
    <label><span>Branches <span class="opt">(leave all unticked to allow every branch)</span></span><div class="checks">${state.branches.map(b => `<label><input type="checkbox" name="branches" value="${b}" ${s && s.branches.includes(b) ? 'checked' : ''} ${locked}>${b}</label>`).join('')}</div></label>
    <label>${L('Minimum CGPA', false)}<input type="number" name="min_cgpa" min="0" max="10" step="0.1" value="${s ? s.min_cgpa : 0}" ${locked} style="max-width:160px"></label>
    <label>${L('Instructions for students', false)}<textarea name="instructions" maxlength="1000">${v('instructions')}</textarea></label></div>`;
}

function createSlot() {
  modal('Create slot', `<p class="muted" style="margin-bottom:14px">The slot is saved as a draft. Next you allocate an interviewer and publish it.</p>${slotForm(null)}`, 'Save draft', async (d, fd) => {
    const res = await api('slot', { ...d, branches: fd.getAll('branches') }); toast('Draft slot created.'); view = { page: 'Slot', id: res.id, from: 'Slots' };
  });
}
function editSlot(s) {
  modal(`Edit slot #${s.id}`, slotForm(s), 'Save changes', async (d, fd) => { await api('slot_update', { ...d, id: s.id, branches: fd.getAll('branches') }); toast('Slot updated.'); });
}
function allocate(s) {
  const c = s.candidates || [];
  modal(`Allocate interviewer · slot #${s.id}`, `<p class="muted">${esc(s.domain)} · ${fmtDate(s.date)}, ${timeRange(s)}</p><div style="margin-top:14px">${c.length
    ? `${note(`${c.length} interviewer${c.length > 1 ? 's are' : ' is'} available: same domain and free at this time.`, 'ok')}<label>${L('Interviewer')}<select name="interviewer" required>${c.map(i => `<option value="${i.id}" ${i.id === s.interviewer ? 'selected' : ''}>${esc(i.name)} · ${esc(i.designation)}</option>`).join('')}</select></label><p class="hint" style="margin-top:10px">The interviewer is notified as soon as you allocate.</p>`
    : note(`No ${esc(s.domain)} interviewer is free at this time. Change the slot time, or ask the administrator to approve another interviewer.`, 'bad')}</div>`,
    c.length ? 'Allocate' : '', async d => { await api('allocate', { id: s.id, ...d }); toast('Interviewer allocated. You can publish the slot now.'); });
}
function feedbackForm(r) {
  modal('Give feedback', `<dl class="details" style="border:1px solid var(--border);border-radius:8px;margin-bottom:16px"><div><dt>Student</dt><dd>${esc(r.student_name)} · ${esc(r.roll)}</dd></div><div><dt>Slot</dt><dd>#${r.slot} · ${esc(r.domain)}</dd></div></dl>
    <div class="form"><label>${L(`Score (${state.score_min} to ${state.score_max})`)}<select name="score" required><option value="">Select score</option>${Array.from({ length: state.score_max - state.score_min + 1 }, (_, i) => `<option>${state.score_min + i}</option>`).join('')}</select></label>
    <label>${L('Comments')}<textarea name="comments" required maxlength="1000" placeholder="What went well, and what the student should improve."></textarea></label>
    <p class="hint">Feedback can be submitted once. The student is notified and their performance record is updated.</p></div>`,
    'Submit feedback', async d => { await api('feedback', { id: r.id, ...d }); toast('Feedback submitted.'); });
}
function viewFeedback(r) {
  modal('Feedback', `<dl class="details" style="border:1px solid var(--border);border-radius:8px"><div><dt>Slot</dt><dd>#${r.slot} · ${esc(r.domain)}</dd></div><div><dt>Date</dt><dd>${fmtDate(r.date)}</dd></div><div><dt>Interviewer</dt><dd>${esc(r.interviewer_name)}</dd></div><div><dt>Student</dt><dd>${esc(r.student_name)}</dd></div><div class="wide"><dt>Score</dt><dd style="font-size:22px">${r.score} / ${state.score_max}</dd></div><div class="wide"><dt>Comments</dt><dd>${esc(r.comments)}</dd></div></dl>`, '', null);
}
const confirmAction = (title, text, label, fn) => modal(title, `<p>${text}</p>`, label, fn, true);

// ================= Events =================
function bind() {
  const on = (sel, fn) => $$(sel).forEach(el => el.onclick = e => { e.stopPropagation(); fn(el); });
  const slotById = id => state.slots.find(s => s.id === Number(id));
  const regById = id => state.registrations.find(r => r.id === Number(id));
  on('[data-go]', b => go(b.dataset.go));
  on('[data-slot]', b => go('Slot', { id: Number(b.dataset.slot), from: view.page === 'Slot' ? view.from : view.page }));
  $('#logout').onclick = async () => { await api('logout', {}); authTab = 'login'; showLogin(); };
  on('#read-all', async () => { await api('read', {}); await refresh(); });
  on('#new-slot', createSlot);
  on('[data-edit]', b => editSlot(slotById(b.dataset.edit)));
  on('[data-allocate]', b => allocate(slotById(b.dataset.allocate)));
  on('[data-publish]', b => modal('Publish slot', `<p>Eligible students will see slot #${b.dataset.publish} and can register until the closing date.</p>`, 'Publish', async () => { await api('publish', { id: Number(b.dataset.publish) }); toast('Slot published.'); }));
  on('[data-cancel-slot]', b => confirmAction('Cancel slot', 'All registered students and the interviewer will be notified, and every registration will be cancelled. This cannot be undone.', 'Cancel slot', async () => { await api('cancel_slot', { id: Number(b.dataset.cancelSlot) }); toast('Slot cancelled.'); }));
  on('[data-register]', b => { const s = slotById(b.dataset.register); modal('Confirm registration', `<p>Register for <b>${esc(s.domain)}</b> on <b>${fmtDate(s.date)}, ${timeRange(s)}</b> with ${esc(s.interviewer_name)}?</p><p class="muted" style="margin-top:8px">${esc(s.instructions || '')}</p>`, 'Register', async () => { await api('register_slot', { slot: s.id }); toast('You are registered. A confirmation is in your notifications.'); }); });
  on('[data-cancel-reg]', b => confirmAction('Cancel registration', 'Your seat will be released for other students.', 'Cancel registration', async () => { await api('cancel_registration', { id: Number(b.dataset.cancelReg) }); toast('Registration cancelled.'); }));
  on('[data-attend]', async b => { try { await api('attendance', { id: Number(b.dataset.attend), status: b.dataset.value }); await refresh(); toast(`Marked ${b.dataset.value.toLowerCase()}.`); } catch (e) { toast(e.message); } });
  on('[data-feedback]', b => feedbackForm(regById(b.dataset.feedback)));
  on('[data-view-feedback]', b => viewFeedback(regById(b.dataset.viewFeedback)));
  on('[data-status]', b => { filters.status = b.dataset.status; render(); });
  on('[data-role-tab]', b => { filters.role = b.dataset.roleTab; render(); });
  on('[data-user]', async b => { try { await api('user', { id: Number(b.dataset.user), status: b.dataset.set }); await refresh(); toast('Account updated.'); } catch (e) { toast(e.message); } });
  $$('[data-change-role]').forEach(sel => sel.onchange = async () => { try { await api('user', { id: Number(sel.dataset.changeRole), role: sel.value }); await refresh(); toast('Role changed.'); } catch (e) { toast(e.message); render(); } });
  on('[data-export]', b => {
    const rows = window.reports[b.dataset.export], cell = x => '"' + String(x ?? '').replace(/^[=+@-]/, "'$&").replace(/"/g, '""') + '"';
    const url = URL.createObjectURL(new Blob([rows.map(r => r.map(cell).join(',')).join('\r\n')], { type: 'text/csv' }));
    const a = document.createElement('a'); a.href = url; a.download = `mims-${b.dataset.export}-report.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  if ($('#f-q')) $('#f-q').oninput = e => { filters.q = e.target.value; const pos = e.target.selectionStart; render(); const i = $('#f-q'); i.focus(); i.setSelectionRange(pos, pos); };
  if ($('#f-domain')) $('#f-domain').onchange = e => { filters.domain = e.target.value; render(); };
}

refresh();
