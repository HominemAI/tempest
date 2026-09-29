'use strict';
// Shared session bootstrap. No tokens or turn histories enter browser storage.
window.TEMPEST_HOSTED = true;
window.TempestHosted = (() => {
  const originalFetch = window.fetch.bind(window);
  const ready = originalFetch('/auth/me', {credentials: 'same-origin'}).then(async response => {
    const value = await response.json();
    if (!response.ok || typeof value.csrf !== 'string') throw Error(value.error || 'Sign in from Administration, then reload this page.');
    return value;
  });
  // Register a rejection handler now; callers still receive the original failure.
  ready.catch(error => {
    const status = document.getElementById('live-status') || document.getElementById('status');
    if (status) status.textContent = error.message;
  });
  window.fetch = async (input, init = {}) => {
    const request = input instanceof Request ? input : null;
    const url = new URL(request ? request.url : input, location.href);
    if (url.origin !== location.origin) throw Error('Hosted requests must use this server.');
    const session = await ready;
    const headers = new Headers(init.headers || request?.headers);
    const method = String(init.method || request?.method || 'GET').toUpperCase();
    if (!['GET', 'HEAD'].includes(method)) headers.set('X-CSRF-Token', session.csrf);
    return originalFetch(input, {...init, headers, credentials: 'same-origin'});
  };
  async function api(path, payload) {
    const response = await fetch(path, payload === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    });
    const value = await response.json();
    if (!response.ok) {
      const error = Error(value.error || 'Request failed. Reload the saved personality and retry.');
      error.status = response.status;
      throw error;
    }
    return value;
  }

  function mount() {
    let active = null, instances = [], activeTurns = [], operation = null, pendingEvent = null, pendingCreate = null;
    let relationshipUsers = [], relationshipNodes = [];
    let searchTimer;
    const randomUnavailable = 'Random playback is unavailable until an administrator configures an event pool. Enter a manual event below.';
    const setStatus = message => { $('live-status').textContent = message; };
    function controls() {
      const busy = Boolean(operation);
      $('manual-event-submit').disabled = busy;
      $('saved-personality-load').disabled = busy || !$('saved-personality-select').value || Boolean(pendingEvent);
      $('save-simulation').disabled = busy || !active || Boolean(pendingEvent);
      $('live-reset').disabled = busy || Boolean(pendingEvent);
      $('live-toggle').disabled = true;
      $('random-event-once').disabled = true;
      $('live-toggle').title = randomUnavailable;
      $('random-event-once').title = randomUnavailable;
      for (const id of ['live-start', 'live-seed', 'random-seed']) $(id).disabled = busy || Boolean(pendingEvent);
      $('manual-event').disabled = busy || Boolean(pendingEvent);
      $('event-actor').disabled = busy || Boolean(pendingEvent);
      for (const id of ['saved-personality-select','history-session','history-refresh']) $(id).disabled = busy;
      $('manual-event-submit').textContent = pendingEvent ? 'Retry pending event' : 'Apply event';
      // Configuration must remain unchanged while a turn awaits replay.
      for (const input of document.querySelectorAll('#category-controls input,#pressure-controls input,#recovery-sliders input,#multiplier-sliders input,#recovery-master,#pressure-enabled,#long-enabled')) input.disabled = busy || Boolean(pendingEvent);
      for (const id of ['category-reset','recovery-reset','multiplier-reset']) $(id).disabled = busy || Boolean(pendingEvent);
      $('relationships-enabled').disabled = busy || Boolean(pendingEvent) || !relationshipData;
    }
    // Legacy visibility listeners remain useful, but their control updates must
    // never re-enable local playback or alter server state.
    liveControls = controls;
    function configuration() {
      const config = simulationSettings();
      delete config.version;
      delete config.relationshipVersion;
      const seed = $('live-seed');
      if (!seed.value.trim() || !seed.checkValidity() || !Number.isInteger(config.seed)) throw Error('Seed must be an integer between 0 and 4294967295.');
      for (const {number} of categoryInputs) if (!number.checkValidity()) throw Error('Correct the category effect values before saving.');
      return config;
    }
    function validateState(value) {
      if (typeof value.id !== 'string' || !Number.isInteger(value.turn) || !validVector(value.current, 23, -10, 10) || !validVector(value.baseline, 23, -10, 10) ||
          !['original', 'adapted', 'temporary'].every(key => validVector(value.pressure?.[key], 23, -10, 10)) ||
          !['pressure', 'duration', 'intensity'].every(key => validVector(value.pressure?.[key], 23, -1e12, 1e12))) throw Error('The server returned an invalid personality state. Reload to retry.');
    }
    function showState(value, delta = Array(23).fill(0)) {
      validateState(value);
      active = value;
      loadedPersonality = value;
      liveState = [...value.current];
      liveBaseline = [...value.baseline];
      pressureState = structuredClone(value.pressure);
      liveCount = value.turn;
      liveSeed = value.settings.seed;
      savedVector.splice(0, 23, ...value.baseline);
      restoreSettings({...value.settings, relationshipVersion: value.relationship_version});
      $('live-count').textContent = String(value.turn);
      const savedOption = $('live-start').querySelector('[value=saved]');
      savedOption.disabled = false;
      savedOption.textContent = value.name || value.id;
      $('live-start').value = 'saved';
      drawLive(liveState, delta);
      drawPressure();
      const url = new URL(location.href);
      url.searchParams.set('instance', value.id);
      history.replaceState(null, '', url);
      controls();
    }
    function turnView(value) {
      const predictions = value.predictions || [];
      const events = predictions.map(prediction => prediction.event);
      return {
        session: value.id, personality_id: value.id, turn: value.turn, timestamp: '',
        event: events.join('\n'), dimensions: liveNames, seed: value.settings?.seed || 0,
        before: value.before, model_input: value.model_input, categories: value.delta_categories || predictions[0]?.categories || Array(23).fill(0),
        event_delta: value.combined_event_effect, net_delta: value.net_delta, after: value.current,
        baselines_before: value.pressure_before, baselines_after: value.pressure, settings: value.settings,
        delta_text: value.delta_text, server_response: value,
      };
    }
    function renderInstances() {
      const select = $('saved-personality-select'), selected = select.value;
      const query = $('saved-personality-search').value.trim().toLowerCase();
      const matches = instances.filter(value => `${value.name} ${value.id} ${value.account_id || ''} ${value.user_id || ''}`.toLowerCase().includes(query));
      select.replaceChildren(new Option(matches.length ? 'Choose a saved personality' : 'No matching personalities', ''));
      for (const value of matches) select.add(new Option(`${value.name || 'Unnamed personality'} · ${value.turn} turns · ${value.id}`, value.id));
      select.value = matches.some(value => value.id === selected) ? selected : '';
      const historySelect = $('history-session'), historySelected = historySelect.value || active?.id;
      historySelect.replaceChildren(new Option('Choose a personality', ''));
      for (const value of instances) historySelect.add(new Option(`${value.name || 'Unnamed personality'} · ${value.turn} turns`, value.id));
      historySelect.value = historySelected || '';
      $('saved-personality-status').textContent = `${matches.length} matching saved personalities. Loading continues from the saved server state.`;
      controls();
    }
    async function refreshInstances() {
      const value = await api('/admin/instances');
      if (!Array.isArray(value.instances)) throw Error('Saved personalities could not be read.');
      instances = value.instances;
      renderInstances();
    }
    async function readHistory(identifier) {
      let after = 0;
      const values = [];
      for (let page = 0; page < 100; page++) {
        const value = await api(`/personalities/${encodeURIComponent(identifier)}/history?after=${after}`);
        if (!Array.isArray(value.turns)) throw Error('Saved history could not be read.');
        values.push(...value.turns.map(turnView));
        if (value.turns.length < 100) return {turns: values, limited: false};
        const next = value.turns[value.turns.length - 1].turn;
        if (!Number.isInteger(next) || next <= after) throw Error('Saved history pagination did not advance.');
        after = next;
      }
      return {turns: values, limited: true};
    }
    function showHistory(values, limited = false) {
      historyTurns = values;
      $('history-turn').replaceChildren();
      values.forEach((value, index) => $('history-turn').add(new Option('Turn ' + value.turn, String(index))));
      if (values.length) $('history-turn').value = String(values.length - 1);
      showHistoryTurn();
      $('history-status').textContent = limited ? 'First 10,000 saved turns loaded. Export contains these loaded turns.' : `${values.length} saved turns loaded from the server.`;
    }
    async function load(identifier) {
      await relationshipsReady;
      const state = await api('/admin/instances/' + encodeURIComponent(identifier));
      const saved = await readHistory(identifier);
      pendingEvent = null;
      pendingCreate = null;
      showState(state);
      $('saved-personality-select').value = identifier;
      $('live-history').replaceChildren();
      activeTurns = saved.turns;
      [...saved.turns].reverse().forEach(value => appendEventLog(value, false));
      showHistory(saved.turns, saved.limited);
      $('live-event-text').textContent = saved.turns.at(-1)?.event || 'No events have been applied to this saved personality.';
      await refreshRelationships();
      setStatus(`Loaded ${state.name || state.id} · turn ${state.turn}. Events and settings save to the server.`);
    }
    async function saveSettings() {
      if (!active) throw Error('Load a personality or create a new instance first.');
      const settings = configuration();
      if (Object.keys(settings).every(key => JSON.stringify(settings[key]) === JSON.stringify(active.settings[key]))) return active;
      const value = await api('/admin/instances/' + encodeURIComponent(active.id), {settings, expected_turn: active.turn});
      showState(value);
      return value;
    }
    function seededBaseline(seed) {
      let state = seed >>> 0;
      return Array.from({length: 23}, () => {
        state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
        return Math.round(((state / 2 ** 32) * 4 - 2) * 100) / 100;
      });
    }
    async function create() {
      if (!pendingCreate) {
        const settings = configuration(), source = $('live-start').value;
        pendingCreate = {
          baseline: source === 'saved' && active ? [...active.baseline] : source === 'neutral' ? Array(23).fill(0) : seededBaseline(settings.seed),
          settings, name: source === 'saved' && active ? `${active.name || 'Personality'} copy`.slice(0, 180) : `${source === 'neutral' ? 'Neutral' : 'Random'} baseline · ${settings.seed}`,
          idempotency_key: crypto.randomUUID(),
        };
      }
      const value = await api('/admin/instances', pendingCreate);
      pendingCreate = null;
      pendingEvent = null;
      showState(value);
      $('live-history').replaceChildren();
      activeTurns = [];
      showHistory([]);
      $('live-event-text').textContent = 'New personality saved. Enter an event to apply its first turn.';
      await refreshInstances();
      $('saved-personality-select').value = value.id;
      await refreshRelationships();
      return value;
    }
    async function run(name, work) {
      if (operation) return;
      operation = name;
      controls();
      setStatus(name + '…');
      try { await work(); }
      catch (error) {
        if (error.status === 409) {
          pendingEvent = null;
          pendingCreate = null;
          setStatus(error.message + ' Reload the saved personality before applying another event.');
        } else if (error.status >= 400 && error.status < 500) {
          pendingEvent = null;
          pendingCreate = null;
          setStatus(error.message + ' Correct the request or reload the saved personality to retry.');
        } else if (pendingEvent) setStatus(error.message + ' Use Retry pending event to retrieve the saved result safely.');
        else setStatus(error.message + (pendingCreate ? ' Retry New saved instance to retrieve the same instance.' : ''));
      } finally { operation = null; controls(); }
    }
    async function applyEvent() {
      if (!pendingEvent) {
        const event = $('manual-event').value.trim();
        if (!event) return;
        if (!active) await create();
        await saveSettings();
        const actor = $('event-actor').value.trim();
        pendingEvent = {identifier: active.id, payload: {
          events: [actor ? {text: event, user_id: actor} : event],
          expected_turn: active.turn, idempotency_key: crypto.randomUUID(),
        }};
      }
      const started = performance.now();
      const value = await api(`/personalities/${encodeURIComponent(pendingEvent.identifier)}/events`, pendingEvent.payload);
      pendingEvent = null;
      updateEventMotion(started);
      showState(value, value.net_delta);
      const turn = turnView(value);
      appendEventLog(turn);
      activeTurns.push(turn);
      showHistory(activeTurns);
      $('history-session').value = value.id;
      $('live-event-text').textContent = turn.event;
      selectEventTab(false);
      $('manual-event').value = '';
      await refreshRelationships();
      const item = instances.find(instance => instance.id === value.id);
      if (item) item.turn = value.turn;
      renderInstances();
      setStatus(`Saved turn ${value.turn} · ${Math.round(performance.now() - started)} ms. State, baselines, and history come from the server.`);
    }
    function addRelationshipChart() {
      const actorLabel = document.createElement('label');
      actorLabel.textContent = 'Event actor user ID (optional)';
      const actor = document.createElement('input');
      actor.id = 'event-actor'; actor.type = 'text'; actor.maxLength = 128;
      actor.placeholder = 'Leave blank for an untagged event';
      actorLabel.append(actor);
      $('manual-event').after(actorLabel);
      const section = document.createElement('section');
      section.setAttribute('aria-labelledby', 'user-delta-heading');
      section.innerHTML = '<h2 id="user-delta-heading">Per-user accumulated event deltas</h2><p>Each tagged event changes the global personality once and accumulates its event effect for that actor. These per-user deltas do not feed back into global inference, recovery, or baseline shifts.</p><label>Event actor<select id="relationship-user"><option value="">No tagged users yet</option></select></label><p id="relationship-user-status" role="status">Apply an event with an actor user ID to begin.</p>';
      const region = chartScroll.cloneNode(true);
      region.setAttribute('aria-label', 'Per-user event-delta chart, scroll horizontally for all 23 states');
      region.querySelector('#live-bars').id = 'relationship-bars';
      region.querySelectorAll('.live-baseline,.live-change').forEach(node => node.remove());
      relationshipNodes = [...region.querySelectorAll('.live-column')].map((column, index) => {
        const node = {bar: column.querySelector('.live-bar'), button: column.querySelector('.live-emoji'), value: column.querySelector('.live-value')};
        const inspect = () => showStatTooltip(node.button, liveNames[index]);
        node.button.onmouseenter = inspect; node.button.onfocus = inspect; node.button.onclick = inspect;
        node.button.onmouseleave = hideStatTooltip; node.button.onblur = hideStatTooltip;
        return node;
      });
      section.append(region);
      $('live-event-text').closest('.live-event').before(section);
      $('relationship-user').onchange = drawRelationship;
      drawRelationship();
    }
    function drawRelationship() {
      const identifier = $('relationship-user').value;
      const user = relationshipUsers.find(value => value.user_id === identifier);
      const delta = user?.delta || Array(23).fill(0);
      drawLive(delta, Array(23).fill(0), relationshipNodes, null, []);
      relationshipNodes.forEach((node, index) => {
        node.button.dataset.detail = `${liveNames[index]}: ${delta[index].toFixed(2)} accumulated event delta${user ? ' · user ' + user.user_id : ''}`;
        node.button.setAttribute('aria-label', node.button.dataset.detail);
      });
      $('relationship-user-status').textContent = user ? `${user.user_id} · server accumulated deltas through turn ${user.turn}. Values are clamped to −10…+10.` : 'No tagged users for this personality. Enter an actor user ID with an event to begin.';
    }
    async function refreshRelationships() {
      if (!active) return;
      const value = await api(`/personalities/${encodeURIComponent(active.id)}/status/users`);
      if (!Array.isArray(value.users) || value.users.some(user => typeof user.user_id !== 'string' || !validVector(user.delta, 23, -10, 10))) throw Error('Per-user deltas could not be read from the server.');
      relationshipUsers = value.users;
      const selected = $('relationship-user').value;
      $('relationship-user').replaceChildren(new Option(value.users.length ? 'Choose an event actor' : 'No tagged users yet', ''));
      for (const user of value.users) $('relationship-user').add(new Option(user.user_id, user.user_id));
      $('relationship-user').value = value.users.some(user => user.user_id === selected) ? selected : value.users[0]?.user_id || '';
      drawRelationship();
    }

    addRelationshipChart();
    $('event-details-close').onclick = () => { $('event-turn-details').hidden = true; };
    $('manual-event-form').onsubmit = event => { event.preventDefault(); run('Applying saved event', applyEvent); };
    $('live-reset').onclick = () => run('Creating saved instance', async () => { await create(); setStatus('New instance saved. Earlier personalities and their history remain available.'); });
    $('live-toggle').onclick = () => setStatus(randomUnavailable);
    $('random-event-once').onclick = () => setStatus(randomUnavailable);
    $('save-simulation').onclick = () => run('Saving settings', async () => { await saveSettings(); setStatus('Settings saved. Current state and original baseline are retained.'); });
    $('live-start').onchange = () => setStatus('Starting personality selection applies when you create a New saved instance.');
    $('random-seed').onclick = () => { $('live-seed').value = crypto.getRandomValues(new Uint32Array(1))[0]; setStatus('Seed chosen. Save settings or create a new instance to persist it.'); };
    $('saved-personality-search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(renderInstances, 150); };
    $('saved-personality-select').onchange = controls;
    $('saved-personality-load').onclick = () => run('Loading saved personality', () => load($('saved-personality-select').value));
    $('history-refresh').onclick = () => run('Refreshing saved history', async () => {
      await refreshInstances();
      if (active) { const saved = await readHistory(active.id); activeTurns = saved.turns; showHistory(saved.turns, saved.limited); }
      setStatus('Saved personalities and history refreshed.');
    });
    $('history-session').onchange = () => run('Loading history', async () => {
      const identifier = $('history-session').value;
      if (!identifier) { showHistory([]); return; }
      const saved = await readHistory(identifier);
      showHistory(saved.turns, saved.limited);
      setStatus('Saved history loaded. The global chart still shows the active personality.');
    });
    $('history-export').onclick = () => {
      if (!historyTurns.length) return;
      const blob = new Blob([JSON.stringify(historyTurns.map(turn => turn.server_response), null, 2)], {type: 'application/json'});
      const url = URL.createObjectURL(blob), anchor = document.createElement('a');
      anchor.href = url; anchor.download = `tempest-history-${historyTurns[0].session}.json`; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    controls();
    run('Loading hosted personalities', async () => {
      await ready;
      await refreshInstances();
      const identifier = new URL(location.href).searchParams.get('instance');
      if (identifier) await load(identifier);
      else setStatus('Ready. Select a saved personality or enter an event to create a saved instance. ' + randomUnavailable);
    });
  }
  return {ready, api, mount};
})();
