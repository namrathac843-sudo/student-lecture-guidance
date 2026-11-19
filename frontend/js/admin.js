(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  let pendingUsers = [];

  function showMessage(type, message) {
    const box = $('admin-alert');
    box.className = `alert is-${type}`;
    box.textContent = message;
    box.hidden = false;
  }

  function formatDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
  }

  function renderPending() {
    const filter = $('admin-role-filter').value;
    const rows = pendingUsers.filter(user => filter === 'all' || user.role === filter);
    const body = $('admin-pending-body');
    $('admin-empty').hidden = rows.length > 0;
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty-cell">No pending requests for this filter.</td></tr>';
      return;
    }
    body.innerHTML = rows.map(user => `
      <tr data-user-id="${esc(user.id)}">
        <td><span class="person-name">${esc(user.fullName)}</span><span class="secondary">${esc(user.collegeName || '')}</span></td>
        <td>${esc(user.email)}${user.usn ? `<span class="secondary">${esc(user.usn)}</span>` : ''}</td>
        <td>${esc(user.role)}</td>
        <td>${esc(user.department || '—')}</td>
        <td>${esc(formatDate(user.createdAt))}</td>
        <td><div class="actions"><button class="btn btn-primary btn-sm" data-decision="Approved" type="button">Approve</button><button class="btn btn-danger-outline btn-sm" data-decision="Rejected" type="button">Reject</button></div></td>
      </tr>`).join('');
  }

  async function loadData() {
    $('admin-refresh').disabled = true;
    const [summary, queue] = await Promise.all([PGMPApi.approvalSummary(), PGMPApi.pendingApprovals()]);
    $('admin-refresh').disabled = false;
    if (!summary.ok || !queue.ok) {
      showMessage('error', summary.error || queue.error || 'Could not load the admin dashboard.');
      return;
    }
    const stats = summary.data;
    $('stat-total').textContent = stats.totalUsers;
    $('stat-pending').textContent = stats.pending;
    $('stat-approved').textContent = stats.approved;
    $('stat-rejected').textContent = stats.rejected;
    pendingUsers = queue.data.users || [];
    renderPending();
  }

  async function init() {
    const session = PGMPStorage.getCurrentSession();
    if (!session?.token) { window.location.replace('admin-login.html'); return; }
    const me = await PGMPApi.me();
    if (!me.ok || me.data?.user?.role !== 'admin') {
      PGMPStorage.clearCurrentSession();
      window.location.replace('admin-login.html');
      return;
    }
    PGMPStorage.users.upsert(me.data.user);
    document.body.classList.add('auth-verified');
    $('admin-user-name').textContent = me.data.user.fullName;
    $('admin-refresh').addEventListener('click', loadData);
    $('admin-role-filter').addEventListener('change', renderPending);
    $('admin-logout').addEventListener('click', () => PGMPAuth.logout());
    $('admin-pending-body').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-decision]');
      if (!button) return;
      const row = button.closest('tr[data-user-id]');
      if (!row) return;
      const userId = row.dataset.userId;
      const decision = button.dataset.decision;
      const name = pendingUsers.find(user => user.id === userId)?.fullName || 'this account';
      if (!window.confirm(`${decision === 'Approved' ? 'Approve' : 'Reject'} ${name}?`)) return;
      button.disabled = true;
      const result = await PGMPApi.decideApproval(userId, decision);
      if (!result.ok) {
        showMessage('error', result.error || 'Could not update the account.');
        button.disabled = false;
        return;
      }
      showMessage('success', result.data.message);
      await loadData();
    });
    await loadData();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
