'use strict';
(function (root) {
  const APPS = Object.freeze({melange: 'Melange', mentat: 'Mentat', terrier: 'Terrier'});
  const PHASES = new Set(['needs_request', 'pending', 'needs_password', 'locked', 'ready', 'revoked', 'terrier_signin', 'error']);
  const REQUESTS = new Set(['pending', 'approved', 'used', 'denied', 'expired']);
  const FIELDS = new Set(['appId', 'appName', 'phase', 'accountIdentity', 'requestStatus', 'requestExpiresAt', 'requestCleanupPending', 'canRequestAgain']);
  const GENERIC_ERROR = 'This action could not be completed. Check your connection or try again later.';
  function validateState(value, expectedApp) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !FIELDS.has(key)) || !Object.hasOwn(APPS, value.appId) || value.appName !== APPS[value.appId] || !PHASES.has(value.phase) || (expectedApp && value.appId !== expectedApp)) throw new Error('Unsupported access state');
    if (value.accountIdentity !== undefined && (typeof value.accountIdentity !== 'string' || !/^[A-Za-z0-9_@.+-]{1,150}$/.test(value.accountIdentity))) throw new Error('Unsupported account identity');
    if (value.requestStatus !== undefined && !REQUESTS.has(value.requestStatus)) throw new Error('Unsupported request state');
    if (value.requestExpiresAt !== undefined && (!Number.isSafeInteger(value.requestExpiresAt) || value.requestExpiresAt < 946684800 || value.requestExpiresAt > 253402300799)) throw new Error('Unsupported request expiry');
    for (const key of ['requestCleanupPending', 'canRequestAgain']) if (value[key] !== undefined && typeof value[key] !== 'boolean') throw new Error('Unsupported access state');
    if (value.appId === 'terrier' && ['needs_password', 'locked'].includes(value.phase)) throw new Error('Use Terrier sign-in');
    if (value.appId !== 'terrier' && value.phase === 'terrier_signin') throw new Error('Unsupported sign-in state');
    if (value.canRequestAgain && !['revoked', 'pending'].includes(value.phase)) throw new Error('Unsupported request replacement');
    if (value.canRequestAgain && value.phase === 'pending' && !['used', 'denied', 'expired'].includes(value.requestStatus)) throw new Error('Active request cannot be replaced');
    return Object.freeze({...value});
  }
  function displayName(value) {
    if (typeof value !== 'string' || value.trim() !== value || Array.from(value).length < 1 || Array.from(value).length > 80 || /[\x00-\x1f\x7f]/.test(value)) throw new Error('Enter a label of 1 to 80 characters without leading or trailing spaces.');
    return value;
  }
  function activationKey(value) {
    if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{43,86}$/.test(value)) throw new Error('Enter the complete approval key supplied by your administrator.');
    return value;
  }
  function password(value) {
    if (typeof value !== 'string' || Array.from(value).length < 12 || new TextEncoder().encode(value).length > 1024 || value.includes('\0')) throw new Error('Use at least 12 characters and at most 1024 UTF-8 bytes.');
    return value;
  }
  function mount(document, api, windowObject) {
    const ids = ['app-name', 'description', 'account', 'notice', 'request-form', 'display-name', 'request-submit', 'pending-panel', 'request-status', 'request-expiry', 'poll', 'activation-form', 'activation-key', 'password-form', 'password-label', 'password', 'confirm-panel', 'password-confirm', 'password-submit', 'ready-panel', 'open', 'lock', 'retry', 'status'];
    const el = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
    if (ids.some(id => !el[id])) throw new Error('Access UI is incomplete');
    let state = null, appId = null, busy = false, disposed = false, pollTimer = null, unsubscribe;
    const secretFields = ['activation-key', 'password', 'password-confirm'];
    const clearSecrets = () => { for (const id of secretFields) el[id].value = ''; };
    const status = value => { el.status.textContent = value; };
    function lockControls() { for (const element of document.querySelectorAll('button, input')) element.disabled = busy || !state; if (pollTimer) el.poll.disabled = true; if (!el.retry.hidden) el.retry.disabled = busy; }
    function render(raw) {
      const next = validateState(raw, appId);
      if (!state || state.phase !== next.phase || state.accountIdentity !== next.accountIdentity) clearSecrets();
      state = next; appId = next.appId;
      el['app-name'].textContent = next.appName; document.title = next.appName + ' access';
      const logo=document.getElementById('app-logo');if(logo){logo.src='icons/'+next.appId+'.ico';logo.hidden=false;document.body.dataset.app=next.appId;}
      const messages = {
        needs_request: 'Request administrator approval for this installation. Connect to the internet or the configured local network to reach your trusted administrator server.',
        pending: 'This installation is waiting for administrator approval.',
        needs_password: 'This installation is approved. Choose your local password, then sign in.',
        locked: 'Sign in with your local password to open ' + next.appName + '.',
        ready: 'You are signed in. Your local workspace is ready to open.',
        revoked: 'Administrator access has been revoked. Your local data remains on this computer.',
        terrier_signin: 'This installation is approved. Continue to Terrier to set up or sign in to its existing local account.',
        error: 'Access could not be checked. Your workspace has not been opened.'
      };
      el.description.textContent = messages[next.phase];
      el.account.hidden = !next.accountIdentity; el.account.textContent = next.accountIdentity ? 'Local account: ' + next.accountIdentity : '';
      el.notice.hidden = !next.requestCleanupPending; el.notice.textContent = next.requestCleanupPending ? 'Approval is saved. Background cleanup will be retried; you do not need another approval key.' : '';
      el['request-form'].hidden = !(next.phase === 'needs_request' || next.canRequestAgain);
      el['request-submit'].textContent = next.canRequestAgain ? 'Request access again' : 'Request access';
      el['pending-panel'].hidden = next.phase !== 'pending';
      const requestMessages = {pending: 'Request sent to the administrator laptop. Approval is pending.', approved: 'Request approved. Ask the administrator for the one-time approval code.', used: 'Activation was attempted. Retry the same code while recovery remains available.', denied: 'This request was declined. You can send a new request when ready.', expired: 'This request expired. Send a new request for administrator review.'};
      el['request-status'].textContent = requestMessages[next.requestStatus] || 'Check for the latest approval status.';
      if (next.canRequestAgain && next.requestStatus === 'used') el['request-status'].textContent = 'Activation recovery expired. Send a new request for administrator review.';
      el['request-expiry'].textContent = next.requestExpiresAt ? 'Request or recovery deadline: ' + new Date(next.requestExpiresAt * 1000).toLocaleString() : '';
      el['activation-form'].hidden = !(next.phase === 'pending' && ['approved', 'used'].includes(next.requestStatus) && !next.canRequestAgain);
      el['password-form'].hidden = !['needs_password', 'locked'].includes(next.phase);
      el['confirm-panel'].hidden = next.phase !== 'needs_password'; el['password-confirm'].required = next.phase === 'needs_password';
      el.password.autocomplete = next.phase === 'needs_password' ? 'new-password' : 'current-password';
      el['password-label'].textContent = next.phase === 'needs_password' ? 'Choose a password' : 'Password';
      el['password-submit'].textContent = next.phase === 'needs_password' ? 'Save password' : 'Sign in';
      el['ready-panel'].hidden = !['ready', 'terrier_signin'].includes(next.phase);
      el.open.textContent = next.phase === 'terrier_signin' ? 'Continue to Terrier sign-in' : 'Open ' + next.appName;
      el.lock.hidden = next.phase !== 'ready'; el.retry.hidden = next.phase !== 'error'; lockControls();
    }
    function failClosed() {
      clearSecrets(); state = null;
      for (const id of ['request-form', 'pending-panel', 'activation-form', 'password-form', 'ready-panel']) el[id].hidden = true;
      el.retry.hidden = false; el.retry.disabled = false; lockControls(); el.retry.disabled = false; status(GENERIC_ERROR);
    }
    async function act(method, value) {
      if (busy || disposed) return;
      busy = true; clearSecrets(); status('Working…'); lockControls();
      try {
        const result = value === undefined ? await api[method]() : await api[method](value);
        if (!disposed) { try { render(result); status(method === 'setPassword' ? 'Password saved. Sign in to continue.' : ''); } catch { failClosed(); } }
      } catch { if (!disposed) { if (!state) failClosed(); else status(GENERIC_ERROR); } }
      finally { busy = false; if (!disposed) lockControls(); }
    }
    function submit(id, callback) { el[id].addEventListener('submit', event => { event.preventDefault(); if (busy || !state) return; try { callback(); } catch (error) { clearSecrets(); status(error.message); } }); }
    submit('request-form', () => { if (state.phase !== 'needs_request' && !state.canRequestAgain) return; return act('request', {displayName: displayName(el['display-name'].value)}); });
    submit('activation-form', () => { if (el['activation-form'].hidden) return; return act('activate', {activationKey: activationKey(el['activation-key'].value)}); });
    submit('password-form', () => {
      if (!['needs_password', 'locked'].includes(state.phase)) return;
      const value = password(el.password.value);
      if (state.phase === 'needs_password' && value !== el['password-confirm'].value) throw new Error('The passwords do not match. Enter them again.');
      return act(state.phase === 'needs_password' ? 'setPassword' : 'unlock', {password: value});
    });
    el.poll.addEventListener('click', () => {
      if (busy || !state || state.phase !== 'pending' || pollTimer) return;
      pollTimer = windowObject.setTimeout(() => { pollTimer = null; if (!disposed) lockControls(); }, 15000);
      void act('poll');
    });
    el.open.addEventListener('click', () => { if (state && ['ready', 'terrier_signin'].includes(state.phase)) void act('open'); });
    el.lock.addEventListener('click', () => { if (state?.phase === 'ready') void act('lock'); });
    el.retry.addEventListener('click', () => { if (!busy) void act('info'); });
    function dispose() { disposed = true; clearSecrets(); if (pollTimer) windowObject.clearTimeout(pollTimer); unsubscribe?.(); }
    windowObject.addEventListener('pagehide', dispose, {once: true});
    const required = ['info', 'request', 'poll', 'activate', 'setPassword', 'unlock', 'open', 'lock', 'onStatus'];
    if (!api || required.some(key => typeof api[key] !== 'function')) { failClosed(); return {dispose, ready: Promise.resolve()}; }
    unsubscribe = api.onStatus(value => { if (disposed) return; try { render(value); status(''); } catch { failClosed(); } });
    const ready = act('info');
    return {dispose, ready};
  }
  const exported = {validateState, displayName, activationKey, password, mount};
  if (typeof module === 'object' && module.exports) module.exports = exported;
  else root.addEventListener('DOMContentLoaded', () => mount(root.document, root.desktopAccess, root), {once: true});
})(typeof window === 'object' ? window : globalThis);
