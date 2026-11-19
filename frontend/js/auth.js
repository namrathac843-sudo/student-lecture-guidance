/* Registration and login use the Flask API. Local Storage is only a UI cache. */
window.PGMPAuth = (function () {
  'use strict';
  const S = () => window.PGMPStorage;
  const V = () => window.PGMPValidators;
  const API = () => window.PGMPApi;
  const $ = (id) => document.getElementById(id);

  function showAlert(box, type, message) {
    if (!box) return;
    box.className = `alert is-${type}`;
    box.textContent = message;
    box.hidden = false;
  }

  function splitCSV(value) {
    return String(value || '').split(',').map(part => part.trim()).filter(Boolean);
  }

  function wirePasswordToggles(root) {
    root.querySelectorAll('[data-toggle-password]').forEach(button => {
      button.addEventListener('click', () => {
        const input = $(button.dataset.togglePassword);
        if (!input) return;
        const reveal = input.type === 'password';
        input.type = reveal ? 'text' : 'password';
        button.textContent = reveal ? 'Hide' : 'Show';
        button.setAttribute('aria-label', reveal ? 'Hide password' : 'Show password');
      });
    });
  }

  function initRegisterPage() {
    const form = $('register-form');
    if (!form) return;
    const roleInput = $('reg-role');
    const alertBox = $('register-alert');
    const tabs = [...document.querySelectorAll('.role-tab')];
    const sections = [...document.querySelectorAll('[data-role-section]')];
    const configReady = API().publicConfig().then(result => {
      if (result.ok) V().configure(result.data);
    }).catch(() => {});

    function setRole(role) {
      roleInput.value = role;
      tabs.forEach(tab => {
        const active = tab.dataset.role === role;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', String(active));
      });
      sections.forEach(section => { section.hidden = section.dataset.roleSection !== role; });
      const hint = $('email-hint');
      if (hint) {
        hint.textContent = role === 'student'
          ? `Student email must match your USN (for example 1ga21cs001@${V().getCollegeEmailDomain()}).`
          : 'Use the email address assigned to you by your institution (or the address permitted by your college).';
      }
    }
    tabs.forEach(tab => tab.addEventListener('click', () => setRole(tab.dataset.role)));
    setRole('student');
    configReady.then(() => setRole(roleInput.value));
    wirePasswordToggles(form);
    form.querySelectorAll('input,select,textarea').forEach(field => {
      field.addEventListener('input', () => V().clearFieldError(field.id));
      field.addEventListener('change', () => V().clearFieldError(field.id));
    });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      V().clearAllErrors(form);
      alertBox.hidden = true;
      const role = roleInput.value;
      const data = {
        fullName: $('reg-fullname').value.trim(),
        email: $('reg-email').value.trim().toLowerCase(),
        collegeName: $('reg-college').value.trim(),
        department: $('reg-department').value,
        role,
        password: $('reg-password').value,
        confirmPassword: $('reg-confirm').value,
        usn: '',
      };
      if (role === 'student') {
        data.usn = $('reg-usn').value.trim().toUpperCase();
        data.semester = $('reg-semester').value;
        data.skills = splitCSV($('reg-skills').value);
      } else if (role === 'lecturer') {
        data.designation = $('reg-designation').value.trim();
        data.experienceYears = $('reg-experience').value;
        data.usn = $('reg-usn-lecturer').value.trim().toUpperCase();
        data.expertise = splitCSV($('reg-expertise').value);
      } else if (role === 'alumni') {
        data.graduationDepartment = $('reg-grad-dept').value.trim();
        data.graduationYear = $('reg-grad-year').value;
        data.company = $('reg-company').value.trim();
        data.jobDesignation = $('reg-job').value.trim();
        data.expertise = splitCSV($('reg-expertise-alumni').value);
      }

      await configReady;
      const validation = V().validateRegistration(data, role);
      if (!validation.ok) {
        Object.entries(validation.errors).forEach(([field, message]) => V().setFieldError(field, message));
        showAlert(alertBox, 'error', 'Please correct the highlighted fields.');
        const first = $(Object.keys(validation.errors)[0]);
        first?.focus();
        return;
      }
      if (!$('reg-terms').checked) {
        showAlert(alertBox, 'error', 'Please accept the registration notice.');
        return;
      }

      const submit = $('register-submit');
      submit.disabled = true;
      submit.textContent = 'Submitting…';
      const result = await API().register(data);
      submit.disabled = false;
      submit.textContent = 'Create account';
      if (!result.ok) {
        showAlert(alertBox, 'error', result.error || 'Registration failed.');
        return;
      }
      if (result.data && result.data.user) S().users.upsert(result.data.user);
      showAlert(alertBox, 'success', 'Registration submitted. Your account is pending approval. You can sign in after an administrator or lecturer approves it.');
      form.reset();
      setRole('student');
      setTimeout(() => { window.location.href = 'login.html?registered=1'; }, 1800);
    });
  }

  function initLoginPage() { initLoginForm(false); }
  function initAdminLoginPage() { initLoginForm(true); }

  function initLoginForm(adminOnly) {
    const form = $('login-form');
    if (!form) return;
    const alertBox = $('login-alert');
    const emailInput = $('login-email');
    const remember = $('login-remember');
    const remembered = localStorage.getItem('pgmp_remembered_email');
    if (remembered && emailInput && remember) {
      emailInput.value = remembered;
      remember.checked = true;
    }
    wirePasswordToggles(form);

    const params = new URLSearchParams(window.location.search);
    if (!adminOnly && params.get('registered') === '1') {
      showAlert(alertBox, 'success', 'Registration received. Wait for approval before signing in.');
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      V().clearAllErrors(form);
      alertBox.hidden = true;
      const email = emailInput.value.trim().toLowerCase();
      const password = $('login-password').value;
      if (!email) { V().setFieldError('login-email', 'Email is required.'); return; }
      if (!V().isEmail(email)) { V().setFieldError('login-email', 'Enter a valid email address.'); return; }
      if (!password) { V().setFieldError('login-password', 'Password is required.'); return; }

      const submit = $('login-submit');
      submit.disabled = true;
      submit.textContent = 'Signing in…';
      const result = await API().login(email, password);
      submit.disabled = false;
      submit.textContent = 'Log in';
      if (!result.ok) {
        showAlert(alertBox, result.status === 403 ? 'warning' : 'error', result.error || 'Sign in failed.');
        return;
      }
      const user = result.data.user;
      if (adminOnly && user.role !== 'admin') {
        showAlert(alertBox, 'error', 'This sign-in page is for administrators only. Use the regular login page.');
        // Revoke the issued session immediately; do not keep a token for the wrong role.
        S().setCurrentSession({ userId: user.id, token: result.data.token, loggedInAt: new Date().toISOString() });
        await API().logout();
        S().clearCurrentSession();
        return;
      }
      if (!adminOnly && user.role === 'admin') {
        S().setCurrentSession({ userId: user.id, token: result.data.token, loggedInAt: new Date().toISOString() });
        S().users.upsert(user);
        window.location.href = 'admin.html';
        return;
      }
      S().users.upsert(user);
      S().setCurrentSession({ userId: user.id, token: result.data.token, loggedInAt: new Date().toISOString() });
      if (remember) {
        if (remember.checked) localStorage.setItem('pgmp_remembered_email', email);
        else localStorage.removeItem('pgmp_remembered_email');
      }
      showAlert(alertBox, 'success', 'Sign in successful. Opening your workspace…');
      const requested = params.get('next');
      const safeNext = requested && /^[a-z0-9-]+\.html$/i.test(requested) ? requested : 'dashboard.html';
      if (user.role === 'admin') window.location.href = 'admin.html';
      else window.location.href = user.role === 'lecturer' ? 'approvals.html' : safeNext;
    });

    // Password recovery is intentionally routed to the administrator until
    // email delivery is configured. Reset tokens are never exposed in-browser.
    const openForgot = $('open-forgot');
    const panel = $('forgot-panel');
    const closeForgot = $('close-forgot');
    const forgotForm = $('forgot-form');
    if (openForgot && panel) {
      openForgot.addEventListener('click', (event) => {
        event.preventDefault();
        panel.hidden = false;
        openForgot.hidden = true;
      });
    }
    closeForgot?.addEventListener('click', () => {
      panel.hidden = true;
      if (openForgot) openForgot.hidden = false;
    });
    forgotForm?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const email = $('forgot-email').value.trim().toLowerCase();
      const result = await API().forgotPassword(email);
      showAlert(alertBox, result.ok ? 'warning' : 'error', result.ok ? result.data.message : result.error);
    });
  }

  async function requireAuth() {
    const session = S().getCurrentSession();
    if (!session || !session.token) {
      redirectToLogin();
      return null;
    }
    const result = await API().me();
    if (!result.ok || !result.data || !result.data.user) {
      S().clearCurrentSession();
      redirectToLogin();
      return null;
    }
    S().users.upsert(result.data.user);
    return result.data.user;
  }

  function redirectToLogin() {
    const next = encodeURIComponent(window.location.pathname.split('/').pop() || 'dashboard.html');
    window.location.replace(`login.html?next=${next}`);
  }

  async function logout() {
    try { if (S().getCurrentSession()?.token) await API().logout(); } catch (_) { /* local sign-out still proceeds */ }
    S().clearCurrentSession();
    window.location.href = 'login.html?loggedout=1';
  }

  return {
    initRegisterPage,
    initLoginPage,
    initAdminLoginPage,
    requireAuth,
    logout,
    splitCSV,
    showAlert,
  };
})();
