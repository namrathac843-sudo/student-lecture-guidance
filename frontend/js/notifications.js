/* Database-backed notification centre. */
window.PGMPNotifications = (function () {
  'use strict';
  const el = id => document.getElementById(id);
  const esc = v => window.PGMP.escapeHtml(v);
  let records = [];

  function icon(type) {
    return ({project:'📁',mentoring:'🎓',session:'📅',message:'💬',account:'⚙'})[type] || '🔔';
  }
  async function renderPage() {
    const result = await PGMPApi.listNotifications();
    if (!result.ok) { el('notification-list').textContent = result.error; return; }
    records = result.data.notifications || [];
    const q = (el('notif-search')?.value || '').trim().toLowerCase();
    const type = el('notif-type')?.value || '';
    const read = el('notif-read')?.value || '';
    let list = records.filter(n =>
      (!q || `${n.title} ${n.message}`.toLowerCase().includes(q)) &&
      (!type || n.type === type) &&
      (!read || (read === 'unread' ? !n.read : !!n.read))
    );
    el('notif-count').textContent = `${list.length} notification(s) · ${result.data.unreadCount || 0} unread`;
    el('notif-empty').hidden = list.length > 0;
    el('notification-list').innerHTML = list.map(n => `
      <li class="notification-item ${n.read ? '' : 'is-unread'}" data-id="${esc(n.id)}">
        <span class="notification-icon">${icon(n.type)}</span>
        <div class="notification-body"><strong>${esc(n.title)}</strong>
          <p>${esc(n.message)}</p>
          <span class="notification-meta">${n.createdAt ? esc(new Date(n.createdAt).toLocaleString()) : ''}</span>
        </div>
        <div class="notification-actions">
          ${n.read ? '' : `<button type="button" data-read="${esc(n.id)}" title="Mark as read">●</button>`}
          <button type="button" data-delete="${esc(n.id)}" title="Delete">✕</button>
        </div>
      </li>`).join('');
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (document.body.dataset.page !== 'notifications') return;
    ['notif-search','notif-type','notif-read'].forEach(id => {
      el(id)?.addEventListener('input', renderPage);
      el(id)?.addEventListener('change', renderPage);
    });
    el('notif-clear-filters')?.addEventListener('click', () => {
      ['notif-search','notif-type','notif-read'].forEach(id => { if (el(id)) el(id).value = ''; });
      renderPage();
    });
    el('btn-mark-all-read')?.addEventListener('click', async () => {
      const r = await PGMPApi.markAllNotificationsRead();
      if (!r.ok) window.PGMP.toast({type:'error',title:r.error});
      await renderPage();
    });
    el('notification-list')?.addEventListener('click', async e => {
      const read = e.target.closest('[data-read]');
      const del = e.target.closest('[data-delete]');
      const item = e.target.closest('[data-id]');
      if (read) { await PGMPApi.markNotificationRead(read.dataset.read); await renderPage(); return; }
      if (del) { await PGMPApi.deleteNotification(del.dataset.delete); await renderPage(); return; }
      if (item) {
        const n = records.find(x => x.id === item.dataset.id);
        if (n && !n.read) await PGMPApi.markNotificationRead(n.id);
        if (n?.link) window.location.href = n.link;
        else await renderPage();
      }
    });
    renderPage();
    window.setInterval(renderPage, 10000);
  });
  return {renderPage};
})();
