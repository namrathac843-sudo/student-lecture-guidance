/* Server-backed lecturer project allocation and review interface. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let projects = [], tab = 'pending', me = null, acceptanceCapacity = null, busy = false;
  let pollHandle = null;

  async function load(silent = false) {
    if (busy) return;
    busy = true;
    try {
      const result = await PGMPApi.listProjects();
      if (!result.ok) { if (!silent) $('server-project-message').textContent = result.error || 'Unable to load project requests.'; return; }
      projects = result.data.projects || [];
      acceptanceCapacity = result.data.acceptanceCapacity || null;
      render();
      const cap = acceptanceCapacity;
      if (cap) {
        $('server-project-capacity').innerHTML = `<strong>Active Projects: ${cap.accepted}/4</strong> &nbsp;·&nbsp; <strong>Available Slots: ${cap.remaining}</strong>${cap.remaining === 0 ? '<p role="alert">Maximum project allocation reached (4/4). Complete an existing project to accept another project.</p>' : ''}`;
        $('server-project-capacity').setAttribute('aria-label', `Active projects ${cap.accepted} of 4, available slots ${cap.remaining}`);
      }
      if (!silent) $('server-project-message').textContent = '';
      const notifications = await PGMPApi.listNotifications();
      if (notifications.ok) {
        const count = notifications.data.unreadCount || 0;
        [ $('shell-bell-dot'), document.querySelector('[data-nav-badge]') ].forEach(el => {
          if (!el) return;
          el.textContent = count > 99 ? '99+' : String(count);
          el.hidden = count === 0;
        });
      }
    } finally { busy = false; }
  }

  function date(value) { return value ? esc(new Date(value).toLocaleString()) : '—'; }
  function projectCard(p) {
    const student = p.student || {};
    const pending = ['Pending','Submitted','Under Review','Changes Requested'].includes(p.status);
    const atCapacity = me.role === 'lecturer' && acceptanceCapacity && acceptanceCapacity.remaining <= 0;
    const mine = me.role === 'admin' || p.guideId === me.id;
    const acceptedBy = p.acceptedByName || p.guideName || p.guide?.fullName || 'another lecturer';
    const active = p.status === 'Accepted' && mine;
    const completed = p.status === 'Completed' && mine;
    const showReview = me.role === 'lecturer' && pending;
    return `<article class="card" style="margin:1rem 0;padding:1rem">
      <div style="display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap"><h3>${esc(p.title)}</h3><strong>${esc(p.status)}</strong></div>
      <p><b>Student:</b> ${esc(student.fullName || p.studentId || '—')} · <b>USN:</b> ${esc(student.usn || '—')}</p>
      <p><b>Department:</b> ${esc(p.department || student.department || '—')}</p>
      <p><b>Description:</b> ${esc(p.description || '—')}</p>
      <p><b>Technologies:</b> ${esc(p.technologies || 'Not specified')}</p>
      <p><b>Submitted:</b> ${date(p.submittedAt || p.createdAt)}</p>
      ${p.fileName ? `<p><b>Document:</b> ${esc(p.fileName)} · ${Number(p.fileSize || 0).toLocaleString()} bytes</p>` : '<p><b>Document:</b> Not attached</p>'}
      ${active || completed ? `<p><b>Progress:</b> ${Number(p.progress || 0)}% <span class="muted">· Accepted: ${date(p.acceptedAt)}</span></p><div style="height:8px;background:var(--border,#ddd);border-radius:8px;overflow:hidden"><span style="display:block;height:8px;width:${Math.max(0,Math.min(100,Number(p.progress||0)))}%;background:var(--primary,#2563eb)"></span></div>` : ''}
      ${p.status === 'Accepted' && !mine ? `<p class="muted">Already accepted by ${esc(acceptedBy)}. Acceptance is closed.</p>` : ''}
      ${p.status === 'Rejected' ? `<p><b>Rejected by:</b> ${esc(p.rejectedByName || 'Lecturer')} · ${date(p.rejectedAt)}</p><p><b>Reason:</b> ${esc(p.rejectionReason || 'No reason supplied')}</p>` : ''}
      ${completed ? `<p><b>Completed on:</b> ${date(p.completedAt)} · This project is retained in Project History and no longer uses an active slot.</p>` : ''}
      <div style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-top:.75rem">
        ${p.fileName ? `<button class="btn btn-outline btn-sm" data-download="${esc(p.id)}">Download document</button>` : ''}
        ${showReview ? `<button class="btn btn-primary btn-sm" data-accept="${esc(p.id)}" ${atCapacity ? 'disabled aria-disabled="true" title="Maximum project allocation reached (4/4). Complete an existing project to accept another project."' : ''}>Accept</button><button class="btn btn-danger-outline btn-sm" data-reject="${esc(p.id)}">Reject</button>` : ''}
        ${active ? `<label class="muted" for="progress-${esc(p.id)}">Progress</label><input id="progress-${esc(p.id)}" type="number" min="0" max="100" step="5" value="${Math.max(0,Math.min(100,Number(p.progress||0)))}" style="width:5rem"><button class="btn btn-outline btn-sm" data-progress="${esc(p.id)}">Save progress</button><button class="btn btn-primary btn-sm" data-complete="${esc(p.id)}">Mark Completed</button>` : ''}
      </div>
    </article>`;
  }

  function render() {
    const list = projects.filter(p => {
      if (tab === 'pending') return ['Pending','Submitted','Under Review','Changes Requested'].includes(p.status);
      if (tab === 'accepted') return p.status === 'Accepted' && (me.role === 'admin' || p.guideId === me.id);
      if (tab === 'completed') return p.status === 'Completed' && (me.role === 'admin' || p.guideId === me.id);
      if (tab === 'rejected') return p.status === 'Rejected';
      return true;
    });
    $('server-project-list').innerHTML = list.length ? list.map(projectCard).join('') : '<p class="muted">No projects in this section.</p>';
    $('server-project-count').textContent = `${list.length} project(s)`;
  }

  async function decide(id, action) {
    let feedback = '';
    if (action === 'reject') {
      feedback = window.prompt('Enter the rejection reason (required):') || '';
      if (!feedback.trim()) { $('server-project-message').textContent = 'A rejection reason is required.'; return; }
      if (!window.confirm('Reject this project request and notify the student?')) return;
    } else if (!window.confirm('Accept this request and become its assigned guide?')) return;
    const result = await PGMPApi.reviewProject(id, action, feedback.trim());
    if (!result.ok) { $('server-project-message').textContent = result.error || 'Unable to update project.'; await load(true); return; }
    $('server-project-message').textContent = result.data.message || 'Project updated.';
    await load(true);
  }

  async function updateProgress(id) {
    const input = $(`progress-${id}`);
    const value = Number(input?.value);
    if (!Number.isInteger(value) || value < 0 || value > 100) { $('server-project-message').textContent = 'Progress must be a whole number from 0 to 100.'; return; }
    const result = await PGMPApi.updateProject(id, { progress: value });
    $('server-project-message').textContent = result.ok ? (result.data.message || 'Progress updated.') : (result.error || 'Progress update failed.');
    await load(true);
  }

  async function completeProject(id) {
    if (!window.confirm('Mark this project as Completed? This releases one active allocation slot.')) return;
    const result = await PGMPApi.updateProject(id, { status: 'Completed', progress: 100 });
    $('server-project-message').textContent = result.ok ? (result.data.message || 'Project completed; one slot is now available.') : (result.error || 'Could not complete the project.');
    await load(true);
  }

  document.addEventListener('DOMContentLoaded', async () => {
    if (document.body.dataset.page !== 'projects') return;
    const meResult = await PGMPApi.me();
    if (!meResult.ok) return;
    me = meResult.data.user;
    if (!['lecturer','admin'].includes(me.role)) return;
    $('btn-new-project')?.setAttribute('hidden', 'hidden');
    ['.filter-bar','.list-toolbar','#proj-grid','#proj-table-wrap','#proj-empty'].forEach(sel => document.querySelectorAll(sel).forEach(el => el.hidden = true));
    const host = document.createElement('section');
    host.id = 'server-project-review';
    host.innerHTML = `<section class="card" style="padding:1rem;margin:1rem 0">
      <h2>${me.role === 'admin' ? 'Project Allocation Overview' : 'Project Requests & Allocation'}</h2>
      <p class="muted">All pending requests are visible to lecturers. The first successful acceptance assigns the guide. Only active Accepted projects count toward the four-project limit. Completed projects stay in Project History.</p>
      <p id="server-project-capacity" class="muted" aria-live="polite"></p><p id="server-project-message" role="status" aria-live="polite"></p>
      <div style="display:flex;gap:.5rem;flex-wrap:wrap;margin:.75rem 0">
        <button class="btn btn-outline" data-tab="pending">Pending Requests</button>
        <button class="btn btn-outline" data-tab="accepted">Accepted Projects</button>
        <button class="btn btn-outline" data-tab="completed">Completed / History</button>
        <button class="btn btn-outline" data-tab="rejected">Rejected Requests</button>
        <button class="btn btn-ghost" id="server-project-refresh">Refresh</button>
      </div><p id="server-project-count"></p><div id="server-project-list" aria-live="polite"></div>
    </section>`;
    $('page-content').appendChild(host);
    host.addEventListener('click', async e => {
      const t = e.target.closest('[data-tab]'); if (t) { tab = t.dataset.tab; render(); return; }
      const a = e.target.closest('[data-accept]'); const r = e.target.closest('[data-reject]');
      const d = e.target.closest('[data-download]'); const pr = e.target.closest('[data-progress]'); const c = e.target.closest('[data-complete]');
      if (a) await decide(a.dataset.accept, 'accept');
      if (r) await decide(r.dataset.reject, 'reject');
      if (pr) await updateProgress(pr.dataset.progress);
      if (c) await completeProject(c.dataset.complete);
      if (d) { const result = await PGMPApi.downloadProjectFile(d.dataset.download); if (!result.ok) $('server-project-message').textContent = result.error; }
    });
    $('server-project-refresh').addEventListener('click', () => load());
    await load();
    pollHandle = window.setInterval(() => load(true), 8000);
    window.addEventListener('beforeunload', () => { if (pollHandle) clearInterval(pollHandle); });
  });
})();
