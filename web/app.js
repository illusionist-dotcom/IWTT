/* IWTT v0.1 — local-first Thought Vault UI */
(() => {
  const app = document.getElementById('app-view');
  const state = {
    view: 'capture',
    parentView: 'vault',
    thoughtId: null,
    query: '',
    includeGraveyard: true,
    vaultType: 'ALL',
    searchTimer: null,
    renderToken: 0,
  };

  const TYPE_NAMES = { THOUGHT: 'Thought', EXPERIMENT: 'Experiment', INVENTION: 'Invention', PROJECT: 'Project' };
  const RELATION_TYPES = ['INSPIRED_BY', 'LED_TO', 'RELATED_TO', 'CONTRADICTS', 'SUPPORTS', 'DEPENDS_ON', 'PART_OF', 'SUPERSEDES', 'REVIVES', 'TESTS', 'IMPLEMENTED_BY'];

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const attr = esc;
  const words = (value, max = 190) => {
    const text = String(value ?? '').trim();
    if (text.length <= max) return text;
    return `${text.slice(0, max).trimEnd()}…`;
  };
  const date = (value, includeTime = false) => {
    if (!value) return '—';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleDateString(undefined, includeTime ? { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' } : { month: 'short', day: 'numeric', year: 'numeric' });
  };
  const typeClass = type => String(type || 'THOUGHT').toLowerCase();
  const typePill = type => `<span class="type-pill ${typeClass(type)}">${esc(TYPE_NAMES[type] || type || 'Thought')}</span>`;
  const statusPill = status => `<span class="status-pill ${String(status).toLowerCase()}">${esc(status === 'GRAVEYARD' ? 'Graveyard' : 'Active')}</span>`;
  const demoPill = isDemo => isDemo ? '<span class="demo-pill">Demo</span>' : '';
  const tagPills = tags => (tags || []).map(tag => `<span class="tag-chip">${esc(tag.name)}</span>`).join('');
  const titleOf = thought => thought?.displayTitle || thought?.title || words(thought?.currentText || thought?.originalText, 70) || 'Untitled thought';

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body !== undefined && !(options.body instanceof Blob) && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, { ...options, headers, body: options.body !== undefined && typeof options.body !== 'string' && !(options.body instanceof Blob) && !(options.body instanceof FormData) ? JSON.stringify(options.body) : options.body });
    const contentType = response.headers.get('content-type') || '';
    const data = contentType.includes('application/json') ? await response.json() : await response.blob();
    if (!response.ok) throw new Error(data?.error || `Request failed (${response.status})`);
    return { data, response };
  }

  function toast(message, kind = 'success') {
    const region = document.getElementById('toast-region');
    const item = document.createElement('div');
    item.className = `toast${kind === 'error' ? ' error' : ''}`;
    item.textContent = message;
    region.appendChild(item);
    window.setTimeout(() => item.remove(), 4200);
  }

  function setTopbar() {
    const label = state.view === 'detail' ? 'THOUGHT DETAIL' : state.view.toUpperCase().replace('DATA', 'DATA & PRIVACY');
    document.getElementById('topbar-label').textContent = label;
    const activeView = state.view === 'detail' ? state.parentView : state.view;
    document.querySelectorAll('.nav-item[data-view]').forEach(button => {
      button.classList.toggle('active', button.dataset.view === activeView);
    });
  }

  async function updateNavStats() {
    try {
      const { data } = await api('/api/stats');
      document.getElementById('nav-active-count').textContent = data.activeThoughts;
      document.getElementById('nav-grave-count').textContent = data.graveyardThoughts;
    } catch (_) { /* The main view reports connection errors. */ }
  }

  async function navigate(view, options = {}) {
    if (view === 'detail') return;
    state.view = view;
    state.thoughtId = null;
    if (view !== 'search' && !options.preserveQuery) state.query = '';
    setTopbar();
    window.scrollTo({ top: 0, behavior: 'smooth' });
    await render();
  }

  async function openThought(id) {
    state.parentView = state.view === 'detail' ? state.parentView : state.view;
    state.view = 'detail';
    state.thoughtId = id;
    setTopbar();
    window.scrollTo({ top: 0, behavior: 'smooth' });
    await render();
  }

  async function render() {
    const token = ++state.renderToken;
    setTopbar();
    app.innerHTML = '<div class="loading-state"><span class="loader"></span><p>Opening your thought vault…</p></div>';
    try {
      let markup = '';
      switch (state.view) {
        case 'capture': markup = await renderCapture(); break;
        case 'vault': markup = await renderVault(); break;
        case 'search': markup = await renderSearch(); break;
        case 'timeline': markup = await renderTimeline(); break;
        case 'graveyard': markup = await renderGraveyard(); break;
        case 'connections': markup = await renderConnections(); break;
        case 'data': markup = await renderData(); break;
        case 'detail': markup = await renderDetail(); break;
        default: state.view = 'capture'; markup = await renderCapture();
      }
      if (token !== state.renderToken) return;
      app.innerHTML = markup;
      setTopbar();
      await updateNavStats();
      if (state.view === 'capture') {
        const textarea = document.getElementById('capture-text');
        if (textarea) window.setTimeout(() => textarea.focus({ preventScroll: true }), 40);
      }
      if (state.view === 'search') {
        const input = document.getElementById('search-input');
        if (input && state.query) {
          input.focus({ preventScroll: true });
          input.setSelectionRange(input.value.length, input.value.length);
        }
      }
    } catch (error) {
      if (token !== state.renderToken) return;
      app.innerHTML = `<div class="empty-state"><div class="empty-icon">!</div><h3>The vault could not load</h3><p>${esc(error.message)}. Check that the local server is running and try again.</p><button class="button" data-action="reload">Try again</button></div>`;
    }
  }

  async function renderCapture() {
    const [{ data: stats }, { data: list }] = await Promise.all([
      api('/api/stats'), api('/api/thoughts?status=ACTIVE')
    ]);
    const recent = list.thoughts.slice(0, 4);
    return `
      <section class="view-heading">
        <div><span class="eyebrow">YOUR PRIVATE IDEA ARCHIVE</span><h1>Save it before it slips away.</h1><p>Capture the thought first. Give it a shape, a title, or a connection later.</p></div>
      </section>
      <div class="capture-grid">
        <section class="capture-card">
          <div class="capture-card-head"><div><span class="eyebrow">QUICK CAPTURE</span><h2>What’s on your mind?</h2><p>A fragment is enough. Your first words are preserved.</p></div><span class="pencil-mark" aria-hidden="true">✳</span></div>
          <form id="capture-form" class="capture-form">
            <textarea id="capture-text" name="originalText" class="capture-compose" maxlength="200000" required placeholder="Could game enemies remember how the player fights and change their strategy later?\n\nWrite freely. You can organize it when you’re ready."></textarea>
            <details class="capture-options"><summary>Optional: add a title, type, or tags</summary><div class="optional-fields">
              <div class="field"><label for="capture-title">Title</label><input id="capture-title" name="title" maxlength="200" placeholder="A short name (optional)"></div>
              <div class="field"><label for="capture-type">Starting type</label><select id="capture-type" name="type"><option value="THOUGHT">Thought</option><option value="EXPERIMENT">Experiment</option><option value="INVENTION">Invention</option><option value="PROJECT">Project</option></select></div>
              <div class="field" style="grid-column:1/-1"><label for="capture-tags">Tags · comma-separated</label><input id="capture-tags" name="tags" maxlength="300" placeholder="gameplay, memory"></div>
            </div></details>
            <div class="capture-footer"><span class="capture-footnote"><span>◉</span> Saved locally · Original capture never overwritten</span><button class="button primary save-thought" type="submit">Save thought <span aria-hidden="true">↗</span></button></div>
          </form>
        </section>
        <aside class="side-stack">
          <section class="quick-recall"><span class="eyebrow">WHEN MEMORY IS A FRAGMENT</span><h3>Find the thought you half-remember.</h3><p>Search by phrase, keyword, title, tag, or a fuzzy recollection.</p><button class="text-link" data-view="search">Open thought recall <span>→</span></button></section>
          <div class="stats-strip" aria-label="Vault overview">
            <div class="stat-tile"><strong>${stats.activeThoughts}</strong><small>Active thoughts</small></div>
            <div class="stat-tile"><strong>${stats.relationships + stats.suggestedRelationships}</strong><small>Connections</small></div>
            <div class="stat-tile"><strong>${stats.revisions}</strong><small>Saved versions</small></div>
            <div class="stat-tile"><strong>${stats.graveyardThoughts}</strong><small>In Graveyard</small></div>
          </div>
        </aside>
      </div>
      <div class="section-head"><h2>Recently tended</h2><button class="button ghost small" data-view="vault">Open the vault →</button></div>
      ${recent.length ? `<div class="recent-list">${recent.map(thoughtCard).join('')}</div>` : emptyState('Your vault is ready', 'Capture your first thought and it will live here, preserved with its own history.', 'Start capturing', 'capture')}
    `;
  }

  function thoughtCard(t) {
    return `<article class="thought-card" data-open-thought="${attr(t.id)}" tabindex="0" role="button" aria-label="Open ${attr(titleOf(t))}">
      <div class="thought-card-top">${typePill(t.type)}${demoPill(t.isDemo)}</div>
      <h3>${esc(titleOf(t))}</h3><p>${esc(words(t.originalText, 175))}</p>
      <div class="thought-card-bottom"><div class="tag-list">${tagPills((t.tags || []).slice(0, 3))}</div><span class="thought-date">${esc(date(t.updatedAt))}</span></div>
    </article>`;
  }

  function emptyState(title, description, buttonText = '', view = '') {
    return `<div class="empty-state"><div class="empty-icon">✳</div><h3>${esc(title)}</h3><p>${esc(description)}</p>${buttonText ? `<button class="button primary" data-view="${attr(view)}">${esc(buttonText)}</button>` : ''}</div>`;
  }

  async function renderVault() {
    const typeParam = state.vaultType === 'ALL' ? '' : `&type=${encodeURIComponent(state.vaultType)}`;
    const { data } = await api(`/api/thoughts?status=ACTIVE${typeParam}`);
    const types = ['ALL', 'THOUGHT', 'EXPERIMENT', 'INVENTION', 'PROJECT'];
    return `<section class="view-heading"><div><span class="eyebrow">THE ACTIVE VAULT</span><h1>Thoughts with room to grow.</h1><p>Browse the original captures, evolving experiments, inventions, and projects.</p></div><div class="heading-actions"><button class="button primary" data-view="capture">＋ Capture</button></div></section>
      <div class="view-filters">${types.map(type => `<button class="filter-chip ${state.vaultType === type ? 'active' : ''}" data-action="vault-type" data-type="${type}">${type === 'ALL' ? 'All types' : esc(TYPE_NAMES[type])}</button>`).join('')}</div>
      ${data.thoughts.length ? `<div class="list-stack">${data.thoughts.map(listThought).join('')}</div>` : emptyState('Nothing in this view yet', state.vaultType === 'ALL' ? 'Capture a thought without deciding what it has to become.' : `No ${TYPE_NAMES[state.vaultType]?.toLowerCase() || 'items'} yet. You can change a thought’s type at any time.`, 'Capture a thought', 'capture')}`;
  }

  function listThought(t, options = {}) {
    const snippet = options.snippet || t.originalText;
    return `<article class="list-thought" data-open-thought="${attr(t.id)}" tabindex="0" role="button" aria-label="Open ${attr(titleOf(t))}">
      <div><div class="list-meta">${typePill(t.type)}${statusPill(t.status)}${demoPill(t.isDemo)}</div><h3>${esc(titleOf(t))}</h3><p>${esc(words(snippet, 235))}</p><div class="tag-list" style="margin-top:9px">${tagPills((t.tags || []).slice(0, 6))}</div></div>
      <div class="list-right"><span class="thought-date">Updated ${esc(date(t.updatedAt))}</span>${options.showResurrect ? `<button class="button small" data-action="resurrect" data-id="${attr(t.id)}">Resurrect ↗</button>` : `<span class="thought-date">${t.relationshipCount ? `${t.relationshipCount} connection${t.relationshipCount === 1 ? '' : 's'}` : 'No connections yet'}</span>`}</div>
    </article>`;
  }

  async function renderGraveyard() {
    const { data } = await api('/api/thoughts?status=GRAVEYARD');
    return `<section class="view-heading"><div><span class="eyebrow">NOT ERASED · STILL PART OF THE STORY</span><h1>The Graveyard.</h1><p>Paused, failed, superseded, or simply not useful right now. Every original thought and its history stays recoverable.</p></div></section>
      ${data.thoughts.length ? `<div class="list-stack">${data.thoughts.map(t => listThought(t, { showResurrect: true })).join('')}</div>` : emptyState('The Graveyard is quiet', 'Ideas you pause will stay here with their history. You can bring them back whenever they become useful.', 'Back to the vault', 'vault')}`;
  }

  async function renderSearch() {
    const results = state.query ? (await api(`/api/search?q=${encodeURIComponent(state.query)}&include_graveyard=${state.includeGraveyard}`)).data.results : [];
    return `<section class="search-hero"><span class="eyebrow">KILLER RECALL · LOCAL SEARCH</span><h1>Search by what you remember.</h1><p>Fragments are welcome. Results always lead back to the original capture.</p>
      <div class="search-box-large"><span class="search-glass" aria-hidden="true">⌕</span><input id="search-input" type="search" value="${attr(state.query)}" placeholder="Try “enemies adapting to the player”…" autocomplete="off"><button class="button ghost small" data-action="clear-search" ${state.query ? '' : 'hidden'} aria-label="Clear search">×</button></div>
      <div class="search-controls"><span class="checkbox-control"><input type="checkbox" id="include-graveyard" ${state.includeGraveyard ? 'checked' : ''}><label for="include-graveyard">Include Graveyard</label></span><span class="search-summary" id="search-summary">${state.query ? `${results.length} result${results.length === 1 ? '' : 's'} · exact, partial, keyword and fuzzy recall` : 'Search runs locally across original text, current text, titles, and tags.'}</span></div>
      </section><div id="search-results">${renderSearchResults(results, state.query)}</div>`;
  }

  function renderSearchResults(results, query) {
    if (!query) return `<div class="empty-state"><div class="empty-icon">⌕</div><h3>Start with a fragment</h3><p>Try a phrase, a person or subject, a title, or a tag. Search also compares word forms and a small transparent local alias list.</p></div>`;
    if (!results.length) return `<div class="empty-state"><div class="empty-icon">∅</div><h3>No matching thought yet</h3><p>Try fewer words, a different fragment, or include Graveyard items. No external AI or cloud search is involved.</p></div>`;
    return `<div class="section-head" style="margin-top:0"><h2>Recall results</h2><span class="section-meta">PRESERVED ORIGINAL TEXT</span></div><div class="list-stack">${results.map(t => `<article class="search-result" data-open-thought="${attr(t.id)}" tabindex="0" role="button" aria-label="Open ${attr(titleOf(t))}"><div><div class="result-meta"><span class="match-pill">${esc(t.matchReason)}</span>${typePill(t.type)}${statusPill(t.status)}${demoPill(t.isDemo)}</div><h3>${esc(titleOf(t))}</h3><p>${esc(t.snippet)}</p><div class="tag-list" style="margin-top:9px">${tagPills((t.tags || []).slice(0, 5))}</div></div><div class="result-date">${esc(date(t.createdAt))}<br><span style="font-size:8px;color:#b0b3ab">originally captured</span></div></article>`).join('')}</div>`;
  }

  async function refreshSearch() {
    const input = document.getElementById('search-input');
    if (!input) return;
    state.query = input.value.trim();
    const include = document.getElementById('include-graveyard');
    state.includeGraveyard = Boolean(include?.checked);
    const resultRegion = document.getElementById('search-results');
    const summary = document.getElementById('search-summary');
    if (!state.query) {
      resultRegion.innerHTML = renderSearchResults([], '');
      summary.textContent = 'Search runs locally across original text, current text, titles, and tags.';
      return;
    }
    summary.textContent = 'Searching the local vault…';
    try {
      const { data } = await api(`/api/search?q=${encodeURIComponent(state.query)}&include_graveyard=${state.includeGraveyard}`);
      resultRegion.innerHTML = renderSearchResults(data.results, state.query);
      summary.textContent = `${data.results.length} result${data.results.length === 1 ? '' : 's'} · exact, partial, keyword and fuzzy recall`;
    } catch (error) {
      summary.textContent = error.message;
    }
  }

  function eventSummary(event) {
    const m = event.metadata || {};
    const labels = {
      THOUGHT_CREATED: 'Thought captured',
      THOUGHT_EDITED: 'A new revision was preserved',
      TYPE_CHANGED: `Type changed · ${m.fromType || ''} → ${m.toType || ''}`,
      PROJECT_PROMOTED: 'Promoted to Project',
      TAG_ADDED: `Tag added · ${m.tagName || 'tag'}`,
      TAG_REMOVED: `Tag removed · ${m.tagName || 'tag'}`,
      MOVED_TO_GRAVEYARD: 'Moved to the Graveyard',
      RESURRECTED: 'Resurrected from the Graveyard',
      RELATIONSHIP_CREATED: `Connection created · ${m.relationshipType || 'RELATED_TO'}`,
      RELATIONSHIP_CONFIRMED: 'Suggested connection confirmed by you',
      RELATIONSHIP_REMOVED: 'Connection removed · history retained',
      AI_SUMMARY_GENERATED: 'AI summary generated · derived data',
    };
    return labels[event.eventType] || String(event.eventType || 'Event').replaceAll('_', ' ').toLowerCase();
  }

  async function renderDetail() {
    const { data } = await api(`/api/thoughts/${encodeURIComponent(state.thoughtId)}`);
    const t = data.thought;
    const { data: ai } = await api('/api/ai/status');
    const liveRelations = (t.relationships || []).filter(r => r.status !== 'REMOVED');
    const historicalRelations = (t.relationships || []).filter(r => r.status === 'REMOVED');
    const connections = [...liveRelations, ...historicalRelations];
    const history = [...(t.events || [])].sort((a, b) => b.timestamp.localeCompare(a.timestamp));
    const revisions = [...(t.revisions || [])].sort((a, b) => b.revisionNumber - a.revisionNumber);
    const otherThoughts = (await api('/api/thoughts?status=ALL')).data.thoughts.filter(item => item.id !== t.id);
    const relationOptions = otherThoughts.map(item => `<option value="${attr(item.id)}">${esc(titleOf(item))} · ${esc(TYPE_NAMES[item.type] || item.type)}</option>`).join('');
    const relationshipRows = connections.length ? connections.map(r => {
      const other = r.sourceId === t.id ? r.target : r.source;
      const isSuggested = r.status === 'SUGGESTED';
      const provenance = isSuggested ? 'AI suggestion · unconfirmed' : r.provenance === 'AI_SUGGESTED' ? 'AI-suggested · confirmed by you' : 'User-confirmed';
      return `<div class="connection-row"><div class="connection-row-main"><div><button class="connection-target" data-open-thought="${attr(other?.id || '')}">${esc(other?.title || 'Missing thought')}</button><div class="connection-sub">${esc(r.type.replaceAll('_', ' '))} · ${esc(provenance)}${r.status === 'REMOVED' ? ' · removed' : ''}</div></div>${r.status === 'REMOVED' ? '<span class="status-pill graveyard">Removed</span>' : `<span class="provenance-pill ${isSuggested ? 'suggested' : 'confirmed'}">${isSuggested ? 'Proposed' : 'Confirmed'}</span>`}</div>${r.metadata?.note ? `<small class="connection-sub">${esc(r.metadata.note)}</small>` : ''}${r.status !== 'REMOVED' ? `<div class="connection-row-actions"><small>${esc(date(r.createdAt))}</small><span>${isSuggested ? `<button class="button small" data-action="confirm-relationship" data-id="${attr(r.id)}">Confirm</button>` : ''} <button class="button ghost small" data-action="remove-relationship" data-id="${attr(r.id)}">Remove</button></span></div>` : ''}</div>`;
    }).join('') : '<div class="empty-state" style="padding:18px"><h3>No connections yet</h3><p>Connect this thought to another when a relationship becomes useful.</p></div>';
    const artifacts = t.derivedArtifacts || [];
    const derivedContent = artifacts.length ? artifacts.map(a => `<div class="revision-card"><span class="derived-mark">AI-generated · ${esc(a.artifactType.replaceAll('_', ' '))}</span><p class="derived-summary">${esc(a.payload?.summary || JSON.stringify(a.payload))}</p><div class="derived-meta">${esc(a.provider)}${a.model ? ` · ${esc(a.model)}` : ''} · ${esc(date(a.generatedAt, true))} · Sources: ${(a.sourceThoughtIds || []).length} thought</div></div>`).join('') : `<p class="derived-empty">${ai.available ? 'No derived summary yet. If you ask, the configured provider receives this thought and its original capture.' : 'No AI is configured. IWTT still works fully; your thought stays local and no AI service is contacted.'}</p>`;
    const graveyardAction = t.status === 'GRAVEYARD'
      ? `<button class="button primary" data-action="resurrect" data-id="${attr(t.id)}">Resurrect</button>`
      : `<button class="button" data-action="graveyard" data-id="${attr(t.id)}">Move to Graveyard</button>`;
    return `<button class="detail-back" data-action="back-to-parent">← Back to ${esc(state.parentView === 'search' ? 'search' : state.parentView)}</button>
      <section class="detail-head"><div><span class="eyebrow">${t.isDemo ? 'FICTIONAL DEMO RECORD' : 'THOUGHT VAULT · ORIGINAL PRESERVED'}</span><h1>${esc(titleOf(t))}</h1><div class="detail-meta">${typePill(t.type)}${statusPill(t.status)}${demoPill(t.isDemo)}<span>Captured ${esc(date(t.createdAt))}</span><span>·</span><span>Updated ${esc(date(t.updatedAt))}</span></div></div><div class="detail-actions">${graveyardAction}</div></section>
      <div class="detail-grid"><div class="detail-main-col">
        <section class="panel"><div class="panel-head"><div><span class="canonical-label">What I actually wrote</span><h2 style="margin-top:8px">Original capture</h2><p>This text is immutable and remains separate from later edits.</p></div></div><blockquote class="original-quote">${esc(t.originalText)}</blockquote><div class="original-caption">Captured ${esc(date(t.createdAt, true))} · ID ${esc(t.id)}</div></section>
        <section class="panel"><div class="panel-head"><div><span class="eyebrow">CANONICAL · USER-EDITABLE</span><h2 style="margin-top:6px">Current version</h2><p>Saving edits creates a new revision; it never changes the original capture.</p></div></div>
          <form id="thought-edit-form" class="edit-form"><div class="field"><label for="detail-title">Title</label><input id="detail-title" name="title" maxlength="200" value="${attr(t.title)}" placeholder="Optional title"></div><div class="field"><label for="detail-text">Current text</label><textarea id="detail-text" name="currentText" required>${esc(t.currentText)}</textarea></div><div class="field"><label for="detail-type">Lifecycle type</label><select id="detail-type" name="type">${Object.entries(TYPE_NAMES).map(([key, label]) => `<option value="${key}" ${t.type === key ? 'selected' : ''}>${label}</option>`).join('')}</select></div><div class="edit-actions"><span class="revision-note">${revisions.length} preserved version${revisions.length === 1 ? '' : 's'}</span><button class="button primary" type="submit">Save version</button></div></form>
        </section>
        <section class="panel"><div class="panel-head"><div><span class="eyebrow">TIME ARCHAEOLOGY</span><h2 style="margin-top:6px">History & revisions</h2><p>How this idea reached its current state.</p></div></div>
          <div class="history-list">${history.map(e => `<div class="history-item"><div class="history-rail"><span class="history-dot"></span></div><div class="history-content"><div class="history-title">${esc(eventSummary(e))}${e.isDemo ? '<span class="demo-ribbon">Demo</span>' : ''}<small>${esc(date(e.timestamp, true))}</small></div>${e.targetType === 'RELATIONSHIP' && e.metadata?.relationshipType ? `<div class="history-detail">${esc(e.metadata.relationshipType.replaceAll('_', ' '))} · ${esc(e.metadata.provenance || '')}${e.metadata.status ? ` · ${esc(e.metadata.status)}` : ''}</div>` : ''}</div></div>`).join('')}</div>
          <div class="section-head" style="margin:20px 0 9px"><h2 style="font-size:16px">Saved snapshots</h2></div><div>${revisions.map(r => `<div class="revision-card"><div class="history-title">Version ${r.revisionNumber} · ${esc(TYPE_NAMES[r.type] || r.type)}<small>${esc(date(r.createdAt, true))} · ${esc(r.changeReason.replaceAll('_', ' ').toLowerCase())}</small></div>${r.title ? `<div class="history-detail">${esc(r.title)}</div>` : ''}<p>${esc(r.text)}</p></div>`).join('')}</div>
        </section>
      </div><div class="detail-side-col">
        <section class="panel"><div class="panel-head"><div><span class="eyebrow">YOUR INDEX</span><h2 style="margin-top:6px">Tags</h2></div><span class="section-meta">${(t.tags || []).length}</span></div><div class="tag-list">${(t.tags || []).map(tag => `<span class="tag-chip removeable">${esc(tag.name)}<button title="Remove tag" data-action="remove-tag" data-thought-id="${attr(t.id)}" data-tag-id="${attr(tag.id)}">×</button></span>`).join('') || '<span class="revision-note">No tags yet.</span>'}</div><form class="tag-form" id="add-tag-form"><input name="name" maxlength="40" placeholder="Add a tag" required><button class="button small" type="submit">Add</button></form></section>
        <section class="panel"><div class="panel-head"><div><span class="eyebrow">CANONICAL LINKS</span><h2 style="margin-top:6px">Connections</h2><p>AI proposals are not confirmed knowledge.</p></div><span class="section-meta">${connections.length}</span></div><div class="connection-list">${relationshipRows}</div><form class="relation-form" id="relationship-form"><select name="targetId" required class="wide"><option value="">Connect to a thought…</option>${relationOptions}</select><select name="type">${RELATION_TYPES.map(type => `<option value="${type}">${esc(type.replaceAll('_', ' '))}</option>`).join('')}</select><select name="provenance"><option value="USER_CONFIRMED">Confirmed by me</option><option value="AI_SUGGESTED">Unconfirmed AI suggestion</option></select><input class="wide" name="note" maxlength="300" placeholder="Optional note about this link"><span class="relation-help">Suggestions stay unconfirmed until you choose Confirm.</span><button class="button small" type="submit">＋ Add connection</button></form></section>
        <section class="panel derived-panel"><div class="panel-head"><div><span class="derived-mark">Derived · rebuildable</span><h2 style="margin-top:7px">AI interpretation</h2><p>Never replaces your original words.</p></div></div>${derivedContent}<button class="button small" data-action="ai-summary" data-id="${attr(t.id)}" ${ai.available ? '' : 'disabled'}>${ai.available ? 'Generate a summary' : 'No AI provider configured'}</button></section>
      </div></div>`;
  }

  async function renderTimeline() {
    const { data } = await api('/api/timeline?limit=200');
    const events = data.events;
    return `<section class="view-heading"><div><span class="eyebrow">TIME ARCHAEOLOGY</span><h1>How the thinking moved.</h1><p>Creation, edits, type changes, Graveyard trips, resurrections, and connections—kept as an event trail.</p></div><div class="timeline-toolbar"><span>Newest first</span><span>·</span><span>${events.length} events shown</span></div></section>
      ${events.length ? `<div class="timeline-list">${events.map(e => `<div class="timeline-event"><div><div class="event-type">${esc(e.eventType.replaceAll('_', ' '))}${e.isDemo ? ' · DEMO' : ''}</div>${e.thoughtId ? `<button class="event-open" data-open-thought="${attr(e.thoughtId)}"><h3>${esc(e.itemTitle || 'Thought')}</h3></button>` : `<h3>${esc(e.itemTitle || 'Vault event')}</h3>`}<p>${esc(e.summary)}${e.contextTitle ? ` · ${esc(e.contextTitle)}` : ''}</p></div><time>${esc(date(e.timestamp, true))}</time></div>`).join('')}</div>` : emptyState('The timeline is waiting', 'Your history begins when you capture a thought. Each meaningful change becomes a dated event.', 'Capture your first thought', 'capture')}`;
  }

  function splitNodeLabel(label) {
    const max = 18;
    const wordsIn = String(label || '').split(/\s+/);
    const rows = ['', ''];
    for (const word of wordsIn) {
      if (!rows[0] || (rows[0] + ' ' + word).trim().length <= max) rows[0] = (rows[0] + ' ' + word).trim();
      else if (!rows[1] || (rows[1] + ' ' + word).trim().length <= max) rows[1] = (rows[1] + ' ' + word).trim();
      else { rows[1] = rows[1].slice(0, max - 1).trimEnd() + '…'; break; }
    }
    if (!rows[1] && rows[0].length > max) { rows[1] = rows[0].slice(max - 1).trim() + '…'; rows[0] = rows[0].slice(0, max - 1); }
    return rows.filter(Boolean);
  }

  function renderGraph(thoughts, relationships) {
    const count = thoughts.length;
    const width = 900, height = 520, cx = width / 2, cy = height / 2;
    const radius = count <= 2 ? 140 : Math.min(202, 80 + count * 8.2);
    const positions = new Map();
    thoughts.forEach((t, i) => {
      const angle = count === 1 ? -Math.PI / 2 : (-Math.PI / 2) + (2 * Math.PI * i / count);
      positions.set(t.id, { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius });
    });
    const visibleIds = new Set(thoughts.map(t => t.id));
    const edges = relationships.filter(r => visibleIds.has(r.sourceId) && visibleIds.has(r.targetId));
    const edgeMarkup = edges.map(r => {
      const a = positions.get(r.sourceId), b = positions.get(r.targetId);
      if (!a || !b) return '';
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const css = r.status === 'SUGGESTED' ? 'graph-edge suggested' : 'graph-edge';
      return `<g><line class="${css}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/><text class="graph-edge-label" x="${mx}" y="${my - 5}">${esc(r.type.replaceAll('_', ' '))}${r.status === 'SUGGESTED' ? ' · ?' : ''}</text></g>`;
    }).join('');
    const nodeMarkup = thoughts.map(t => {
      const p = positions.get(t.id);
      const labels = splitNodeLabel(titleOf(t));
      const labelMarkup = labels.map((line, i) => `<text x="${p.x}" y="${p.y + (i === 0 ? -2 : 9)}">${esc(line)}</text>`).join('');
      return `<g class="graph-node ${typeClass(t.type)} ${t.status === 'GRAVEYARD' ? 'graveyard' : ''}" data-open-thought="${attr(t.id)}" tabindex="0" role="button" aria-label="Open ${attr(titleOf(t))}"><circle cx="${p.x}" cy="${p.y}" r="46"/>${labelMarkup}<text class="node-type" x="${p.x}" y="${p.y + 26}">${esc((TYPE_NAMES[t.type] || t.type).toUpperCase())}${t.status === 'GRAVEYARD' ? ' · GRAVE' : ''}</text></g>`;
    }).join('');
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Small relationship graph of thoughts"><g>${edgeMarkup}</g><g>${nodeMarkup}</g></svg>`;
  }

  async function renderConnections() {
    const [{ data: thoughtData }, { data: relData }] = await Promise.all([
      api('/api/thoughts?status=ALL'), api('/api/relationships?status=LIVE')
    ]);
    const allThoughts = thoughtData.thoughts;
    const allRelationships = relData.relationships;
    const degree = new Map();
    allRelationships.forEach(r => { degree.set(r.sourceId, (degree.get(r.sourceId) || 0) + 1); degree.set(r.targetId, (degree.get(r.targetId) || 0) + 1); });
    const sorted = [...allThoughts].sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0) || b.updatedAt.localeCompare(a.updatedAt));
    const visible = sorted.slice(0, 16);
    const hiddenCount = Math.max(0, allThoughts.length - visible.length);
    return `<section class="view-heading"><div><span class="eyebrow">A SMALL VIEW · CANONICAL LINKS ONLY</span><h1>Connections between thoughts.</h1><p>Every line comes from a saved relationship. Layout is temporary and calculated in your browser; it is not part of your thought data.</p></div><div class="heading-actions"><button class="button" data-view="vault">Browse vault</button></div></section>
      ${allThoughts.length ? `<section class="panel graph-panel"><div class="graph-canvas">${renderGraph(visible, allRelationships)}</div><div class="graph-legend"><span><i class="legend-dot"></i> Thought / Experiment</span><span><i class="legend-dot proj"></i> Invention / Project</span><span><i class="legend-line"></i> Confirmed by you</span><span><i class="legend-line dashed"></i> Unconfirmed suggestion</span><span>Click a node to inspect</span></div>${hiddenCount ? `<p class="revision-note" style="padding:0 5px">Showing the 16 most-connected/recent thoughts of ${allThoughts.length}. The graph is a browsing aid, not a complete Mind World.</p>` : ''}</section>
      <div class="section-head"><h2>Saved connections</h2><span class="section-meta">${allRelationships.length} active · ${allRelationships.filter(r => r.status === 'SUGGESTED').length} unconfirmed</span></div>
      ${allRelationships.length ? `<div class="list-stack">${allRelationships.map(r => `<article class="list-thought" style="cursor:default"><div><div class="list-meta"><span class="match-pill">${esc(r.type.replaceAll('_', ' '))}</span><span class="provenance-pill ${r.status === 'SUGGESTED' ? 'suggested' : 'confirmed'}">${r.status === 'SUGGESTED' ? 'Unconfirmed AI suggestion' : r.provenance === 'AI_SUGGESTED' ? 'Confirmed AI suggestion' : 'User-confirmed'}</span></div><h3><button class="connection-target" data-open-thought="${attr(r.sourceId)}">${esc(r.source?.title || 'Thought')}</button> <span style="color:#9aa197">↔</span> <button class="connection-target" data-open-thought="${attr(r.targetId)}">${esc(r.target?.title || 'Thought')}</button></h3><p>${esc(r.metadata?.note || 'A saved relationship between two canonical thoughts.')}</p></div><div class="list-right">${r.status === 'SUGGESTED' ? `<button class="button small" data-action="confirm-relationship" data-id="${attr(r.id)}">Confirm</button>` : `<span class="thought-date">${esc(date(r.createdAt))}</span>`}</div></article>`).join('')}</div>` : '<div class="empty-state"><div class="empty-icon">⌘</div><h3>No connections saved yet</h3><p>Open a thought to link it to another. AI suggestions, if added, stay proposals until you confirm them.</p></div>'}` : emptyState('Your graph begins with two thoughts', 'Capture a couple of ideas, then connect them from a thought’s detail view.', 'Capture a thought', 'capture')}`;
  }

  async function renderData() {
    const [{ data: health }, { data: stats }, { data: ai }, { data: demo }] = await Promise.all([
      api('/api/health'), api('/api/stats'), api('/api/ai/status'), api('/api/demo/status')
    ]);
    const demoButton = demo.loaded
      ? `<button class="button danger" data-action="remove-demo">Remove demo data</button>`
      : `<button class="button" data-action="seed-demo">Load fictional demo data</button>`;
    return `<section class="view-heading"><div><span class="eyebrow">OWN YOUR MEMORY</span><h1>Data, privacy & portability.</h1><p>Your vault is a local SQLite file. Export it, back it up, or delete rebuildable data without touching canonical thought history.</p></div></section>
      <div class="data-grid"><div class="data-section">
        <section class="data-card"><h2>Take your vault with you</h2><p>JSON is the documented reconstruction format. Markdown is a readable snapshot. A backup is a real SQLite database file saved locally and downloaded to you.</p><div class="data-actions"><button class="button primary" data-action="export-json">↓ Export canonical JSON</button><button class="button" data-action="export-md">↓ Export Markdown</button><button class="button" data-action="backup">↓ Backup SQLite</button><button class="button" data-action="choose-import">↑ Import JSON</button><input id="vault-import-file" class="inline-file" type="file" accept="application/json,.json"></div><span class="path-box">Import is additive and all-or-nothing. Existing IDs or conflicting tags stop the import; existing records are never overwritten.</span></section>
        <section class="data-card"><h2>Rebuildable data</h2><p>Search documents and AI artifacts are derived. Deleting them leaves original/current thought text, revisions, relationships, events, tags, and Graveyard history intact. Search rebuilds its local index automatically when needed.</p><div class="data-actions"><button class="button" data-action="rebuild-index">Rebuild local search index</button><button class="button danger" data-action="delete-derived">Delete all derived data</button></div></section>
        <section class="data-card demo-card"><h2>Fictional demo records</h2><p>Demo thoughts are visibly marked and excluded only when you remove them here. They demonstrate promotions, contradiction, a Graveyard item, resurrection, and an unconfirmed AI relationship.</p><p class="demo-warning">${demo.loaded ? `${demo.thoughts} demo thoughts · ${demo.relationships} demo connections are loaded.` : 'No demo records are currently loaded.'}</p><div class="data-actions">${demoButton}</div></section>
        <section class="data-card"><h2>Optional AI boundary</h2><p>${ai.available ? `Configured: ${esc(ai.provider)}${ai.model ? ` · ${esc(ai.model)}` : ''}. AI is invoked only when you explicitly request a derived action.` : 'No AI provider is configured. IWTT does not send thoughts anywhere and all core features remain available.'}</p><span class="path-box">AI endpoint is opt-in via IWTT_AI_BASE_URL. No default cloud provider, tracking, analytics, or telemetry.</span></section>
      </div><aside class="data-section"><div class="privacy-callout"><strong>Private by design.</strong><p>There is no account, sync, analytics, advertising, or network dependency for basic use. When served on loopback, the vault stays on this computer. Do not bind to a public network unless you add appropriate access controls.</p></div>
        <section class="data-card"><h2>Vault contents</h2><div class="data-stats"><div class="data-stat"><strong>${stats.totalThoughts}</strong><small>Thoughts</small></div><div class="data-stat"><strong>${stats.revisions}</strong><small>Revisions</small></div><div class="data-stat"><strong>${stats.relationships}</strong><small>Confirmed links</small></div><div class="data-stat"><strong>${stats.suggestedRelationships}</strong><small>AI proposals</small></div><div class="data-stat"><strong>${stats.events}</strong><small>History events</small></div><div class="data-stat"><strong>${stats.tags}</strong><small>Tags</small></div></div></section>
        <section class="data-card"><h2>Local storage</h2><p>SQLite · v${esc(health.version)}. The database survives app restarts.</p><span class="path-box">${esc(health.databasePath)}</span><p style="margin-top:10px">Backup files are stored in <code>${esc(health.dataDirectory)}/backups</code> and downloaded via the browser.</p></section>
      </aside></div>`;
  }

  async function downloadEndpoint(path, method = 'GET', filenameFallback = 'iwtt-export') {
    const { data: blob, response } = await api(path, { method, body: method === 'GET' ? undefined : {} });
    const disposition = response.headers.get('content-disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const filename = match?.[1] || filenameFallback;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    return filename;
  }

  async function performAction(action, button) {
    const id = button.dataset.id;
    if (action === 'reload') return render();
    if (action === 'back-to-parent') return navigate(state.parentView, { preserveQuery: state.parentView === 'search' });
    if (action === 'clear-search') { state.query = ''; return render(); }
    if (action === 'vault-type') { state.vaultType = button.dataset.type; return render(); }
    if (action === 'toggle-search-graveyard') { state.includeGraveyard = !state.includeGraveyard; return render(); }
    if (action === 'graveyard') {
      if (!window.confirm('Move this thought to the Graveyard? It will not be deleted. Its original capture, relationships, and history remain.')) return;
      await api(`/api/thoughts/${encodeURIComponent(id)}/graveyard`, { method: 'POST', body: {} });
      toast('Moved to the Graveyard. Nothing was permanently deleted.');
      return render();
    }
    if (action === 'resurrect') {
      await api(`/api/thoughts/${encodeURIComponent(id)}/resurrect`, { method: 'POST', body: {} });
      toast('Thought resurrected. Its Graveyard history is preserved.');
      return render();
    }
    if (action === 'remove-tag') {
      const thoughtId = button.dataset.thoughtId, tagId = button.dataset.tagId;
      await api(`/api/thoughts/${encodeURIComponent(thoughtId)}/tags/${encodeURIComponent(tagId)}`, { method: 'DELETE' });
      toast('Tag removed; the change is recorded in history.');
      return render();
    }
    if (action === 'confirm-relationship') {
      await api(`/api/relationships/${encodeURIComponent(id)}/confirm`, { method: 'POST', body: {} });
      toast('Connection confirmed by you.');
      return render();
    }
    if (action === 'remove-relationship') {
      if (!window.confirm('Remove this connection? The relationship record and its history will be retained.')) return;
      await api(`/api/relationships/${encodeURIComponent(id)}`, { method: 'DELETE' });
      toast('Connection removed; history retained.');
      return render();
    }
    if (action === 'export-json') {
      const filename = await downloadEndpoint('/api/data/export.json', 'GET', 'iwtt-vault-export.json');
      toast(`Canonical JSON exported: ${filename}`);
      return;
    }
    if (action === 'export-md') {
      const filename = await downloadEndpoint('/api/data/export.md', 'GET', 'iwtt-vault-export.md');
      toast(`Markdown export downloaded: ${filename}`);
      return;
    }
    if (action === 'backup') {
      const filename = await downloadEndpoint('/api/data/backup', 'POST', 'iwtt-backup.sqlite3');
      toast(`SQLite backup created and downloaded: ${filename}`);
      return;
    }
    if (action === 'choose-import') return document.getElementById('vault-import-file')?.click();
    if (action === 'delete-derived') {
      if (!window.confirm('Delete every rebuildable search document and AI artifact? Canonical thoughts, revisions, relationships, events, tags, and Graveyard history will remain.')) return;
      const { data } = await api('/api/data/derived', { method: 'DELETE' });
      toast(`Derived data deleted (${data.searchDocumentsDeleted} search docs, ${data.derivedArtifactsDeleted} AI artifacts).`);
      return render();
    }
    if (action === 'rebuild-index') {
      const { data } = await api('/api/data/derived/rebuild', { method: 'POST', body: {} });
      toast(`Local search index rebuilt for ${data.indexedThoughts} thoughts.`);
      return render();
    }
    if (action === 'seed-demo') {
      const { data } = await api('/api/demo/seed', { method: 'POST', body: {} });
      toast(data.seeded ? `Loaded ${data.thoughts} fictional demo thoughts.` : 'Demo records are already loaded.');
      return render();
    }
    if (action === 'remove-demo') {
      if (!window.confirm('Remove fictional demo thoughts, demo relationships, and their demo history? Non-demo records will be kept.')) return;
      const { data } = await api('/api/demo', { method: 'DELETE' });
      toast(`Removed ${data.thoughtsRemoved} fictional demo thoughts.`);
      return render();
    }
    if (action === 'ai-summary') {
      const { data } = await api(`/api/thoughts/${encodeURIComponent(id)}/ai/summary`, { method: 'POST', body: {} });
      toast('AI summary saved as derived data. The original thought was not changed.');
      return render();
    }
  }

  document.addEventListener('click', async event => {
    const actionButton = event.target.closest('[data-action]');
    if (actionButton) {
      event.preventDefault();
      event.stopPropagation();
      try { await performAction(actionButton.dataset.action, actionButton); }
      catch (error) { toast(error.message, 'error'); }
      return;
    }
    const nav = event.target.closest('[data-view]');
    if (nav) {
      event.preventDefault();
      const view = nav.dataset.view;
      if (view === 'search') state.query = '';
      await navigate(view);
      return;
    }
    const open = event.target.closest('[data-open-thought]');
    if (open && open.dataset.openThought) {
      event.preventDefault();
      await openThought(open.dataset.openThought);
    }
  });

  document.addEventListener('keydown', event => {
    const tag = event.target?.tagName?.toLowerCase();
    const typing = ['input', 'textarea', 'select'].includes(tag) || event.target?.isContentEditable;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      document.getElementById('global-search-input').focus();
    } else if (!typing && !event.metaKey && !event.ctrlKey && event.key.toLowerCase() === 'n') {
      event.preventDefault();
      navigate('capture');
    } else if (event.key === 'Enter' && event.target.matches('[data-open-thought]')) {
      event.preventDefault();
      openThought(event.target.dataset.openThought);
    }
  });

  document.addEventListener('submit', async event => {
    const form = event.target;
    if (form.id === 'global-search-form') {
      event.preventDefault();
      state.query = document.getElementById('global-search-input').value.trim();
      state.includeGraveyard = true;
      state.view = 'search';
      state.thoughtId = null;
      setTopbar();
      return render();
    }
    if (form.id === 'capture-form') {
      event.preventDefault();
      const submit = form.querySelector('[type=submit]');
      submit.disabled = true;
      const formData = new FormData(form);
      const tags = String(formData.get('tags') || '').split(',').map(tag => tag.trim()).filter(Boolean);
      try {
        const { data } = await api('/api/thoughts', { method: 'POST', body: {
          originalText: formData.get('originalText'), title: formData.get('title') || '',
          type: formData.get('type') || 'THOUGHT', tags,
        } });
        toast('Thought captured. Your original words are preserved.');
        state.view = 'capture';
        state.thoughtId = null;
        await render();
      } catch (error) { toast(error.message, 'error'); submit.disabled = false; }
      return;
    }
    if (form.id === 'thought-edit-form') {
      event.preventDefault();
      const formData = new FormData(form);
      const submit = form.querySelector('[type=submit]');
      submit.disabled = true;
      try {
        await api(`/api/thoughts/${encodeURIComponent(state.thoughtId)}`, { method: 'PATCH', body: {
          currentText: formData.get('currentText'), title: formData.get('title'), type: formData.get('type'),
        } });
        toast('Saved as a new version. The original capture is unchanged.');
        await render();
      } catch (error) { toast(error.message, 'error'); submit.disabled = false; }
      return;
    }
    if (form.id === 'add-tag-form') {
      event.preventDefault();
      const name = new FormData(form).get('name');
      try {
        await api(`/api/thoughts/${encodeURIComponent(state.thoughtId)}/tags`, { method: 'POST', body: { name } });
        toast('Tag added.');
        await render();
      } catch (error) { toast(error.message, 'error'); }
      return;
    }
    if (form.id === 'relationship-form') {
      event.preventDefault();
      const formData = new FormData(form);
      const note = String(formData.get('note') || '').trim();
      try {
        await api('/api/relationships', { method: 'POST', body: {
          sourceId: state.thoughtId, targetId: formData.get('targetId'), type: formData.get('type'),
          provenance: formData.get('provenance'), metadata: note ? { note } : {},
        } });
        toast(formData.get('provenance') === 'AI_SUGGESTED' ? 'Unconfirmed suggestion saved. It is not user-confirmed knowledge.' : 'Connection saved as user-confirmed.');
        await render();
      } catch (error) { toast(error.message, 'error'); }
      return;
    }
  });

  document.addEventListener('input', event => {
    if (event.target.id === 'search-input') {
      clearTimeout(state.searchTimer);
      state.searchTimer = window.setTimeout(refreshSearch, 160);
    }
  });

  document.addEventListener('change', async event => {
    if (event.target.id === 'include-graveyard') {
      state.includeGraveyard = event.target.checked;
      return refreshSearch();
    }
    if (event.target.id === 'vault-import-file' && event.target.files?.length) {
      const file = event.target.files[0];
      try {
        const text = await file.text();
        const payload = JSON.parse(text);
        if (!window.confirm(`Import “${file.name}” into this vault? This is additive. If any ID conflicts, IWTT stops without importing anything.`)) {
          event.target.value = '';
          return;
        }
        const { data } = await api('/api/data/import', { method: 'POST', body: payload });
        const counts = Object.entries(data.imported).map(([key, value]) => `${value} ${key}`).join(', ');
        toast(`Import complete: ${counts}.`);
        await render();
      } catch (error) { toast(error.message, 'error'); }
      finally { event.target.value = ''; }
    }
  });

  document.getElementById('global-search-form').addEventListener('input', event => {
    if (event.target.id === 'global-search-input') {
      // Enter submits; keep typing from causing a rerender so focus is stable.
    }
  });

  (async function init() {
    await render();
    const params = new URLSearchParams(window.location.search);
    if (params.get('view') === 'search') {
      state.view = 'search';
      state.query = params.get('q') || '';
      await render();
    }
  })();
})();
