/* Same-origin Flask API client. All account and approval decisions are server-side. */
window.PGMPApi = (function () {
  'use strict';

  const API_BASE = String(window.PGMP_API_BASE_URL || '').replace(/\/$/, '');
  function apiUrl(path) {
    return /^https?:\/\//i.test(path) ? path : API_BASE + (path.startsWith('/') ? path : '/' + path);
  }

  function token() {
    const session = window.PGMPStorage ? window.PGMPStorage.getCurrentSession() : null;
    return session && session.token ? session.token : null;
  }

  async function request(method, path, options) {
    const opts = options || {};
    const headers = Object.assign({ Accept: 'application/json' }, opts.headers || {});
    let body = opts.body;
    if (body && !(body instanceof FormData) && typeof body !== 'string') {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    const bearer = token();
    if (bearer) headers.Authorization = `Bearer ${bearer}`;

    let response;
    try {
      response = await fetch(apiUrl(path), {
        method,
        headers,
        body,
        credentials: 'same-origin',
        cache: 'no-store',
      });
    } catch (_) {
      return { ok: false, status: 0, error: 'Cannot reach the Flask server. Start it with python app.py.', networkError: true };
    }

    if (response.status === 204) return { ok: true, status: 204, data: null };
    let payload;
    try {
      const contentType = response.headers.get('Content-Type') || '';
      payload = contentType.includes('application/json') ? await response.json() : await response.text();
    } catch (_) {
      payload = null;
    }
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: payload && payload.error ? payload.error : `Request failed (${response.status}).`,
        data: payload && payload.data,
      };
    }
    return { ok: true, status: response.status, data: payload && payload.data !== undefined ? payload.data : payload };
  }

  return {
    get: (path) => request('GET', path),
    post: (path, body) => request('POST', path, { body }),
    put: (path, body) => request('PUT', path, { body }),
    del: (path) => request('DELETE', path),
    upload: (path, formData) => request('POST', path, { body: formData }),
    health: () => request('GET', '/api/health'),
    publicConfig: () => request('GET', '/api/config/public'),
    login: (email, password) => request('POST', '/api/auth/login', { body: { email, password } }),
    register: (payload) => request('POST', '/api/auth/register', { body: payload }),
    logout: () => request('POST', '/api/auth/logout', { body: {} }),
    me: () => request('GET', '/api/auth/me'),
    changePassword: (body) => request('POST', '/api/auth/change-password', { body }),
    forgotPassword: (email) => request('POST', '/api/auth/forgot-password', { body: { email } }),
    pendingApprovals: () => request('GET', '/api/approvals/pending'),
    approvalSummary: () => request('GET', '/api/approvals/summary'),
    decideApproval: (userId, decision) => request('POST', `/api/approvals/${encodeURIComponent(userId)}`, { body: { decision } }),
    listProjects: () => request('GET', '/api/projects'),
    createProject: (payload) => request('POST', '/api/projects', { body: payload }),
    updateProject: (projectId, payload) => request('PUT', `/api/projects/${encodeURIComponent(projectId)}`, { body: payload }),
    uploadProjectFile: (projectId, file) => {
      const form = new FormData(); form.append('file', file);
      return request('POST', `/api/projects/${encodeURIComponent(projectId)}/upload`, { body: form });
    },
    getProject: (projectId) => request('GET', `/api/projects/${encodeURIComponent(projectId)}`),
    reviewProject: (projectId, action, feedback) => request('POST', `/api/projects/${encodeURIComponent(projectId)}/review`, { body: { action, feedback } }),
    listNotifications: () => request('GET', '/api/notifications'),
    markNotificationRead: (notificationId) => request('PUT', `/api/notifications/${encodeURIComponent(notificationId)}`, { body: { read: true } }),
    deleteNotification: (notificationId) => request('DELETE', `/api/notifications/${encodeURIComponent(notificationId)}`),
    markAllNotificationsRead: () => request('PUT', '/api/notifications/read-all', {}),
    downloadProjectFile: async (projectId) => {
      const bearer = token();
      try {
        const response = await fetch(apiUrl(`/api/projects/${encodeURIComponent(projectId)}/download`), {
          headers: bearer ? { Authorization: `Bearer ${bearer}` } : {},
          credentials: 'same-origin',
          cache: 'no-store',
        });
        if (!response.ok) return { ok: false, error: 'Unable to download the project file.' };
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="?([^";]+)"?/i);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = match ? match[1] : 'project-file';
        document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
        return { ok: true };
      } catch (_) { return { ok: false, error: 'Cannot reach the Flask server.' }; }
    },
  };
})();
