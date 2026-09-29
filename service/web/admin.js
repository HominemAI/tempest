'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { csrf: '', admin: null, accounts: [], activeTab: 'accounts', scope: { account_id: '', user_id: '' }, observabilityTimer: null, observabilityBusy: false };
  const authView = $('auth-view');
  const adminView = $('admin-view');

  async function request(path, { method = 'GET', body } = {}) {
    const headers = { Accept: 'application/json' };
    const options = { method, headers, credentials: 'same-origin', cache: 'no-store' };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      headers['X-CSRF-Token'] = state.csrf;
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    let data = {};
    try { data = await response.json(); } catch { /* A non-JSON error gets a useful fallback below. */ }
    if (!response.ok) {
      const error = new Error(data.error || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function setStatus(id, message, kind = '') {
    const node = $(id);
    node.textContent = message || '';
    node.className = `status${kind ? ` ${kind}` : ''}`;
  }

  function cell(row, value, className = '') {
    const td = document.createElement('td');
    if (className) td.className = className;
    td.textContent = display(value);
    row.append(td);
    return td;
  }

  function display(value, fallback = '—') {
    if (value === null || value === undefined || value === '') return fallback;
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  function asDate(value) {
    if (!value) return null;
    const timestamp = typeof value === 'number' || (typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value))
      ? Number(value) * 1000 : value;
    const parsed = new Date(timestamp);
    return Number.isNaN(parsed.valueOf()) ? null : parsed;
  }

  function date(value) {
    if (!value) return '—';
    const parsed = asDate(value);
    if (!parsed) return String(value);
    return parsed.toISOString().replace('T', ' ').replace('.000Z', ' UTC');
  }

  function listFrom(data, key) {
    const value = data?.[key] ?? data;
    return Array.isArray(value) ? value : [];
  }

  function setAuthMode(setupRequired) {
    $('login-form').hidden = setupRequired;
    $('setup-form').hidden = !setupRequired;
    $('auth-heading').textContent = setupRequired ? 'Set up admin access' : 'Admin sign in';
    $('auth-intro').textContent = setupRequired
      ? 'Create the first administrator to manage API access.'
      : 'Sign in to manage Tempest accounts, keys, limits, and usage.';
  }

  function showAuth(message = '', kind = '') {
    clearInterval(state.observabilityTimer);
    state.csrf = '';
    state.admin = null;
    adminView.hidden = true;
    authView.hidden = false;
    $('admin-identity').hidden = true;
    $('logout-button').hidden = true;
    if (message) setStatus('auth-status', message, kind);
  }

  async function enterWorkspace(principal) {
    if (!principal || principal.kind !== 'admin' || !principal.csrf) {
      throw new Error('This session does not have administrator access. Sign in with an admin account.');
    }
    state.admin = principal;
    state.csrf = principal.csrf;
    authView.hidden = true;
    adminView.hidden = false;
    $('admin-identity').textContent = principal.username || 'Administrator';
    $('admin-identity').hidden = false;
    $('logout-button').hidden = false;
    setStatus('workspace-status', 'Loading admin data…');
    try { await loadWorkspace(); }
    catch (error) { showWorkspaceError(error); }
  }

  async function initialize() {
    setStatus('auth-status', 'Checking admin access…');
    try {
      const status = await request('/auth/status');
      setAuthMode(Boolean(status.setup_required));
      if (status.setup_required) {
        setStatus('auth-status', 'First-time setup is required.');
        return;
      }
      try {
        const principal = await request('/auth/me');
        await enterWorkspace(principal);
      } catch (error) {
        if (error.status !== 401) throw error;
        setStatus('auth-status', 'Sign in to continue.');
      }
    } catch (error) {
      setAuthMode(false);
      setStatus('auth-status', `Admin service unavailable: ${error.message}`, 'error');
    }
  }

  async function authenticate(path, payload) {
    setStatus('auth-status', 'Signing in…');
    const form = path === '/auth/setup' ? $('setup-form') : $('login-form');
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      await request(path, { method: 'POST', body: payload });
      const principal = await request('/auth/me');
      await enterWorkspace(principal);
    } catch (error) {
      setStatus('auth-status', error.message, 'error');
    } finally {
      submit.disabled = false;
    }
  }

  $('login-form').addEventListener('submit', (event) => {
    event.preventDefault();
    authenticate('/auth/login', {
      username: $('login-username').value,
      password: $('login-password').value,
    });
  });
  $('setup-form').addEventListener('submit', (event) => {
    event.preventDefault();
    authenticate('/auth/setup', {
      username: $('setup-username').value,
      password: $('setup-password').value,
      bootstrap_token: $('setup-token').value,
    });
  });

  $('logout-button').addEventListener('click', async () => {
    $('logout-button').disabled = true;
    try {
      await request('/auth/logout', { method: 'POST', body: {} });
      showAuth('You have signed out.');
      setAuthMode(false);
      $('login-form').reset();
    } catch (error) {
      setStatus('workspace-status', error.message, 'error');
    } finally {
      $('logout-button').disabled = false;
    }
  });

  function selectTab(name, focus = false) {
    state.activeTab = name;
    for (const key of ['accounts', 'keys', 'limits', 'observability']) {
      const active = key === name;
      const tab = $(`tab-${key}`);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      $(`panel-${key}`).hidden = !active;
      if (active && focus) tab.focus();
    }
    if (name === 'limits') loadScopeData().catch(showWorkspaceError);
    if (name === 'observability') {
      loadObservability().catch(showWorkspaceError);
      clearInterval(state.observabilityTimer);
      state.observabilityTimer = setInterval(() => {
        if (!document.hidden && !state.observabilityBusy && state.activeTab === 'observability') loadObservability().catch(showWorkspaceError);
      }, 15000);
    } else clearInterval(state.observabilityTimer);
  }

  for (const key of ['accounts', 'keys', 'limits', 'observability']) {
    $(`tab-${key}`).addEventListener('click', () => selectTab(key));
    $(`tab-${key}`).addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const keys = ['accounts', 'keys', 'limits', 'observability'];
      const index = keys.indexOf(key);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? 3
        : (index + (event.key === 'ArrowRight' ? 1 : 3)) % 4;
      selectTab(keys[next], true);
    });
  }

  function updateAccountOptions() {
    for (const id of ['key-account', 'limit-account']) {
      const select = $(id);
      const selected = select.value;
      select.replaceChildren(new Option(id === 'limit-account' ? 'Default limits' : 'Select an account', ''));
      for (const account of state.accounts) {
        const option = new Option(display(account.name, account.id), account.id);
        select.add(option);
      }
      if (state.accounts.some((account) => String(account.id) === selected)) select.value = selected;
    }
  }

  async function loadAccounts() {
    const data = await request('/admin/accounts');
    state.accounts = listFrom(data, 'accounts');
    const body = $('accounts-body');
    body.replaceChildren();
    if (!state.accounts.length) {
      const row = body.insertRow();
      const td = row.insertCell(); td.colSpan = 4; td.className = 'empty-cell'; td.textContent = 'No API accounts yet. Create one to issue account-scoped keys.';
    }
    for (const account of state.accounts) {
      const row = body.insertRow();
      cell(row, account.name || 'Unnamed account');
      cell(row, account.id, 'table-id');
      cell(row, date(account.created_at));
      cell(row, account.status || (account.disabled_at ? 'Disabled' : 'Active'));
    }
    updateAccountOptions();
  }

  $('account-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button');
    button.disabled = true;
    try {
      await request('/admin/accounts', { method: 'POST', body: { name: $('account-name').value.trim() } });
      form.reset();
      await loadAccounts();
      setStatus('workspace-status', 'Account created.', 'success');
    } catch (error) { showWorkspaceError(error); }
    finally { button.disabled = false; }
  });

  function scopeNames(scopes) {
    if (!Array.isArray(scopes)) return display(scopes);
    return scopes.map((scope) => scope === 'create_description' ? 'Creation' : scope).join(', ') || 'No scopes';
  }

  function accountName(id) {
    if (!id) return 'Admin';
    return state.accounts.find((account) => String(account.id) === String(id))?.name || String(id);
  }

  async function loadKeys() {
    const data = await request('/admin/keys');
    const keys = listFrom(data, 'keys');
    const body = $('keys-body');
    body.replaceChildren();
    if (!keys.length) {
      const row = body.insertRow(); const td = row.insertCell();
      td.colSpan = 9; td.className = 'empty-cell'; td.textContent = 'No API keys yet. Create a key when an account or service is ready to connect.';
    }
    for (const key of keys) {
      const row = body.insertRow();
      cell(row, key.name || 'Unnamed key');
      cell(row, key.kind || (key.account_id ? 'account' : 'admin'));
      cell(row, `${accountName(key.account_id)}${key.user_id ? ` · ${key.user_id}` : ''}`);
      cell(row, scopeNames(key.scopes));
      cell(row, date(key.created_at));
      cell(row, key.expires_at == null ? 'Never' : date(key.expires_at));
      cell(row, date(key.last_used_at));
      const expiresAt = asDate(key.expires_at);
      const status = key.revoked_at ? 'Revoked' : expiresAt && expiresAt < new Date() ? 'Expired' : 'Active';
      cell(row, status, `state-${status.toLowerCase()}`);
      const actionCell = row.insertCell();
      if (status === 'Active') {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'table-action'; button.textContent = 'Revoke';
        button.setAttribute('aria-label', `Revoke ${key.name || 'API key'}`);
        button.addEventListener('click', () => revokeKey(key));
        actionCell.append(button);
      } else actionCell.textContent = '—';
    }
  }

  function showSecret(token) {
    $('new-key-value').textContent = token;
    $('new-key').hidden = false;
    $('copy-status').textContent = '';
    $('dismiss-key').focus();
  }

  function clearSecret() {
    $('new-key-value').textContent = '';
    $('new-key').hidden = true;
    $('copy-status').textContent = '';
  }

  $('key-kind').addEventListener('change', () => {
    const accountKey = $('key-kind').value === 'account';
    $('key-account-wrap').hidden = !accountKey;
    $('key-account').required = accountKey;
  });
  $('key-unlimited').addEventListener('change', () => {
    $('key-expiry').disabled = $('key-unlimited').checked;
  });
  $('key-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const kind = $('key-kind').value;
    const scopes = [...form.querySelectorAll('input[name="scope"]:checked')].map((input) => input.value);
    if (!scopes.length) { setStatus('workspace-status', 'Choose at least one API scope.', 'error'); return; }
    if (kind === 'account' && !$('key-account').value) { $('key-account').focus(); return; }
    const payload = { kind, name: $('key-name').value.trim(), scopes };
    if (kind === 'account') payload.account_id = $('key-account').value;
    if ($('key-user').value.trim()) payload.user_id = $('key-user').value.trim();
    if ($('key-unlimited').checked) payload.expires_days = -1;
    else if ($('key-expiry').value !== '') payload.expires_days = Number($('key-expiry').value);
    button.disabled = true; clearSecret();
    try {
      const result = await request('/admin/keys', { method: 'POST', body: payload });
      if (!result.token) throw new Error('The key was created, but the response did not include its one-time token. Contact an administrator before creating another key.');
      form.reset();
      $('key-unlimited').dispatchEvent(new Event('change'));
      $('key-kind').dispatchEvent(new Event('change'));
      showSecret(result.token);
      await loadKeys();
      setStatus('workspace-status', 'API key created. Copy its raw value now.', 'success');
    } catch (error) { showWorkspaceError(error); }
    finally { button.disabled = false; }
  });

  $('copy-key').addEventListener('click', async () => {
    const value = $('new-key-value').textContent;
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      $('copy-status').textContent = 'Copied to clipboard.';
    } catch {
      $('copy-status').textContent = 'Clipboard access is unavailable. Select and copy the key above.';
    }
  });
  $('dismiss-key').addEventListener('click', clearSecret);

  async function revokeKey(key) {
    if (!window.confirm(`Revoke “${key.name || 'this API key'}”? Requests using it will stop authenticating.`)) return;
    try {
      await request(`/admin/keys/${encodeURIComponent(key.id)}/revoke`, { method: 'POST', body: {} });
      await loadKeys();
      setStatus('workspace-status', 'API key revoked.', 'success');
    } catch (error) { showWorkspaceError(error); }
  }

  function selectedScope() {
    const account_id = $('limit-account').value;
    const user_id = $('limit-user').value.trim();
    return { account_id, user_id };
  }

  function scopeQuery(scope) {
    const params = new URLSearchParams();
    if (scope.account_id) params.set('account_id', scope.account_id);
    if (scope.user_id) params.set('user_id', scope.user_id);
    const query = params.toString();
    return query ? `?${query}` : '';
  }

  function scopeLabel(scope) {
    if (!scope.account_id) return 'Default limits';
    return `${accountName(scope.account_id)}${scope.user_id ? ` · user ${scope.user_id}` : ''} limits`;
  }

  function quota(value) {
    if (value === -1 || value === '-1') return 'Unlimited';
    if (value === 0 || value === '0') return 'Disabled (0)';
    if (value === null || value === undefined || value === '') return 'Inherit';
    return display(value);
  }

  function fillLimits(data, scope) {
    const values = data.overrides || data.override || data;
    const inherited = data.inherited || {};
    const text = values.text;
    const creation = values.create_description ?? values.creation;
    $('limits-heading').textContent = scopeLabel(scope);
    const inheritedText = inherited.text;
    const inheritedCreation = inherited.create_description ?? inherited.creation;
    $('limits-caption').textContent = scope.account_id
      ? `Set an override for this scope. Leave blank to inherit from its parent (text: ${quota(inheritedText)} · creation: ${quota(inheritedCreation)}).`
      : 'Set the fallback request allowance for each API scope.';
    $('limit-text').value = text === null || text === undefined ? '' : String(text);
    $('limit-creation').value = creation === null || creation === undefined ? '' : String(creation);
    $('limit-text').placeholder = scope.account_id ? 'Inherit' : 'Required';
    $('limit-creation').placeholder = scope.account_id ? 'Inherit' : 'Required';
    $('limit-text').required = !scope.account_id;
    $('limit-creation').required = !scope.account_id;
  }

  function showUsage(data) {
    $('usage-day').textContent = data.day ? `UTC day: ${data.day}` : 'Usage for the selected UTC day.';
    const used = data.used || {};
    const text = used.text ?? data.text;
    const creation = used.create_description ?? used.creation ?? data.create_description ?? data.creation;
    $('usage-text').textContent = display(text, '0');
    $('usage-creation').textContent = display(creation, '0');
    const accountUsed = data.account_used;
    $('account-usage-row').hidden = !accountUsed;
    if (accountUsed) {
      $('usage-account').textContent = `Text: ${display(accountUsed.text, '0')} · Creation: ${display(accountUsed.create_description ?? accountUsed.creation, '0')}`;
    }
    const limits = data.limits || data.account_limits || {};
    $('usage-effective').textContent = `Text: ${quota(limits.text)} · Creation: ${quota(limits.create_description ?? limits.creation)}`;
  }

  async function loadScopeData() {
    state.scope = selectedScope();
    if (state.scope.user_id && !state.scope.account_id) {
      throw new Error('Select an account before entering a user ID.');
    }
    const query = scopeQuery(state.scope);
    $('usage-text').textContent = 'Loading…';
    $('usage-creation').textContent = 'Loading…';
    const [limits, usage] = await Promise.all([
      request(`/admin/limits${query}`),
      request(`/admin/usage${query}`),
    ]);
    fillLimits(limits, state.scope);
    showUsage(usage);
  }

  async function loadObservability() {
    if (state.observabilityBusy) return;
    state.observabilityBusy = true;
    $('observability-refresh').disabled = true;
    try {
      const data = await request('/admin/observability/inference');
      $('inference-average').textContent = data.average_ms == null ? 'No inference calls recorded yet' : `${Number(data.average_ms).toFixed(1)} ms`;
      $('inference-count').textContent = `${data.sample_count} / ${data.window_limit} recent calls`;
      $('server-turn-average').textContent = data.request_average_ms == null ? 'No server turns recorded yet' : `${Number(data.request_average_ms).toFixed(1)} ms`;
      $('server-turn-count').textContent = `${data.request_sample_count || 0} / ${data.window_limit} recent turns`;
      $('db-connect-average').textContent = data.database_pool_wait_average_ms == null ? 'No database timing yet' : `${Number(data.database_pool_wait_average_ms).toFixed(1)} ms`;
      $('db-setup-average').textContent = data.database_setup_average_ms == null ? '—' : `${Number(data.database_setup_average_ms).toFixed(1)} ms`;
      $('db-query-average').textContent = data.database_query_average_ms == null ? '—' : `${Number(data.database_query_average_ms).toFixed(1)} ms`;
      $('db-execute-average').textContent = data.database_execute_average_ms == null ? '—' : `${Number(data.database_execute_average_ms).toFixed(1)} ms`;
      $('db-fetch-average').textContent = data.database_fetch_average_ms == null ? '—' : `${Number(data.database_fetch_average_ms).toFixed(1)} ms`;
      $('db-query-count-average').textContent = data.database_query_count_average == null ? '—' : Number(data.database_query_count_average).toFixed(1);
      $('db-sample-count').textContent = `${data.database_sample_count || 0} / ${data.window_limit} recent turns`;
      const pool = data.database_pool || {};
      $('db-pool-status').textContent = Number(pool.pool_max) > 0
        ? `${pool.pool_size || 0}/${pool.pool_max} open · ${pool.pool_available || 0} idle · ${pool.requests_waiting || 0} waiting · ${pool.requests_errors || 0} checkout errors`
        : 'SQLite: no connection pool';
      $('inference-latest').textContent = data.latest_at ? date(data.latest_at) : '—';
      const body = $('inference-observations'); body.replaceChildren();
      const observations = Array.isArray(data.observations) ? data.observations : [];
      if (!observations.length) {
        const row = body.insertRow(); const td = row.insertCell(); td.colSpan = 4; td.className = 'empty-cell';
        td.textContent = 'No model inference calls have been recorded yet.';
      }
      for (const observation of observations) {
        const row = body.insertRow();
        cell(row, date(observation.occurred_at));
        cell(row, `${Number(observation.duration_ms).toFixed(1)} ms`);
        cell(row, observation.model_id);
        cell(row, observation.personality_id, 'table-id');
      }
      const databaseBody = $('database-observations'); databaseBody.replaceChildren();
      const databaseObservations = Array.isArray(data.database_observations) ? data.database_observations : [];
      if (!databaseObservations.length) {
        const row = databaseBody.insertRow(); const td = row.insertCell(); td.colSpan = 7; td.className = 'empty-cell';
        td.textContent = 'No database timing samples yet. New event turns will populate this table.';
      }
      for (const observation of databaseObservations) {
        const row = databaseBody.insertRow();
        cell(row, date(observation.occurred_at));
        cell(row, `${Number(observation.pool_wait_ms).toFixed(1)} ms`);
        cell(row, `${Number(observation.setup_ms).toFixed(1)} ms`);
        cell(row, `${Number(observation.execute_ms).toFixed(1)} ms`);
        cell(row, `${Number(observation.fetch_ms).toFixed(1)} ms`);
        cell(row, String(observation.query_count));
        cell(row, observation.personality_id, 'table-id');
      }
      $('observability-status').textContent = `Showing ${observations.length} recent model predictions and ${databaseObservations.length} database timing samples. Query wait is SQL execution plus result fetching; each rolling window holds at most ${data.window_limit} turns.`;
    } finally {
      state.observabilityBusy = false;
      $('observability-refresh').disabled = false;
    }
  }

  $('observability-refresh').addEventListener('click', () => loadObservability().catch(showWorkspaceError));

  $('scope-select-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    await loadScopeData().catch(showWorkspaceError);
  });
  $('limits-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const textValue = $('limit-text').value;
    const creationValue = $('limit-creation').value;
    const values = { text: textValue, create_description: creationValue };
    for (const [key, value] of Object.entries(values)) {
      if (value === '' && !state.scope.account_id) { setStatus('workspace-status', 'Enter a number for both default limits.', 'error'); return; }
      if (value !== '' && (!Number.isInteger(Number(value)) || Number(value) < -1)) {
        setStatus('workspace-status', 'Limits must be −1 (unlimited), 0 (disabled), or a positive whole number.', 'error');
        $(`limit-${key === 'create_description' ? 'creation' : 'text'}`).focus();
        return;
      }
    }
    const payload = {
      ...(state.scope.account_id ? { account_id: state.scope.account_id } : {}),
      ...(state.scope.user_id ? { user_id: state.scope.user_id } : {}),
      text: textValue === '' ? null : Number(textValue),
      create_description: creationValue === '' ? null : Number(creationValue),
    };
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await request('/admin/limits', { method: 'POST', body: payload });
      await loadScopeData();
      setStatus('workspace-status', 'Limits saved.', 'success');
    } catch (error) { showWorkspaceError(error); }
    finally { button.disabled = false; }
  });

  async function loadWorkspace() {
    clearSecret();
    await loadAccounts();
    await loadKeys();
    if (state.activeTab === 'limits') await loadScopeData();
    if (state.activeTab === 'observability') await loadObservability();
    setStatus('workspace-status', 'Admin data is up to date.');
  }

  function showWorkspaceError(error) {
    if (error.status === 401) {
      showAuth('Your admin session has expired. Sign in again.');
      setAuthMode(false);
      return;
    }
    setStatus('workspace-status', error.message || 'Admin request failed. Please retry.', 'error');
  }

  $('refresh-button').addEventListener('click', async () => {
    $('refresh-button').disabled = true;
    try { await loadWorkspace(); }
    catch (error) { showWorkspaceError(error); }
    finally { $('refresh-button').disabled = false; }
  });

  initialize();
})();
