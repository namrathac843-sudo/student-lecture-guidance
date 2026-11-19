(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  let users = [];

  function message(type, value) {
    const box = $('lecturer-alert');
    box.className = `alert is-${type}`;
    box.textContent = value;
    box.hidden = false;
  }

  function render() {
    $('lecturer-pending-count').textContent = `${users.length} pending`;
    $('lecturer-empty').hidden = users.length > 0;
    const body = $('lecturer-pending-body');
    if (!users.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty-cell">No pending student requests.</td></tr>';
      return;
    }
    body.innerHTML = users.map(user => `
      <tr data-user-id="${esc(user.id)}">
        <td><span class="person-name">${esc(user.fullName)}</span><span class="secondary">${esc(user.collegeName || '')}</span></td>
        <td>${esc(user.email)}</td>
        <td>${esc(user.usn || '—')}</td>
        <td>${esc(user.department || '—')}</td>
        <td>${esc(user.createdAt ? new Date(user.createdAt).toLocaleDateString() : '—')}</td>
        <td><div class="actions"><button class="btn btn-primary btn-sm" data-decision="Approved" type="button">Approve</button><button class="btn btn-danger-outline btn-sm" data-decision="Rejected" type="button">Reject</button></div></td>
      </tr>`).join('');
  }

  async function loadQueue() {
    $('lecturer-refresh').disabled = true;
    const result = await PGMPApi.pendingApprovals();
    $('lecturer-refresh').disabled = false;
    if (!result.ok) { message('error', result.error || 'Unable to load pending students.'); return; }
    users = result.data.users || [];
    render();
  }

  async function init() {
    const me = await PGMPApi.me();
    if (!me.ok || !['lecturer', 'admin'].includes(me.data?.user?.role)) return;
    if (me.data.user.role === 'admin') { window.location.replace('admin.html'); return; }
    $('lecturer-refresh').addEventListener('click', loadQueue);
    $('lecturer-pending-body').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-decision]');
      const row = button?.closest('tr[data-user-id]');
      if (!button || !row) return;
      const user = users.find(item => item.id === row.dataset.userId);
      const decision = button.dataset.decision;
      if (!window.confirm(`${decision === 'Approved' ? 'Approve' : 'Reject'} ${user?.fullName || 'this student'}?`)) return;
      button.disabled = true;
      const result = await PGMPApi.decideApproval(row.dataset.userId, decision);
      if (!result.ok) { message('error', result.error || 'Could not update this account.'); button.disabled = false; return; }
      message('success', result.data.message);
      await loadQueue();
    });
    await loadQueue();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
