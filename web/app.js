/* IWTT v0.2 — local-first Thought Vault UI (Recall & Graph Intelligence) */
(() => {
  const app = document.getElementById('app-view');
  const state = {
    view: 'connections',
    parentView: 'vault',
    thoughtId: null,
    query: '',
    includeGraveyard: true,
    vaultType: 'ALL',
    searchTimer: null,
    renderToken: 0,
    // v0.2 — Find My Thought.  Kept separate from the v0.1 `query`/`search`
    // state so classic search behaves exactly as it did before.
    recall: {
      query: '',
      outcome: null,
      time: '',
      includeGraveyard: true,
      type: '',
      loading: false,
      timer: null,
      breakdownFor: null,
      breakdown: null,
      semanticIndex: null,
    },
    // v0.2 — Connection Explorer V2
    graph: { rootId: null, sourceId: '', targetId: '', path: null, neighbourhood: null, depth: 1, selectedId: null },
    aiConnections: { loadingFor: null, result: null },
    // AI link review beyond a single thought: a pairwise comparison and a
    // bounded vault sweep.  Both are explicit, both are proposals only.
    aiLinkReview: { pair: null, pairLoading: false, sweep: null, sweepLoading: false },
    // v0.3 — Thinking Partner.  Findings are derived proposals; nothing here
    // is written to the vault unless the person presses an explicit save.
    partner: {
      tool: 'ask',
      query: '',
      selectedIds: [],
      result: null,
      loading: false,
      error: '',
      status: null,
      thoughts: [],
      useProvider: true,
      includeGraveyard: false,
    },
    importedFile: { name: '', text: '', error: '' },
    captureDraft: null,
    vaultSourceType: 'ALL',
    graphCommunity: 'ALL',
  };

  const TYPE_NAMES = { THOUGHT: 'Thought', EXPERIMENT: 'Experiment', INVENTION: 'Invention', PROJECT: 'Project' };
  const SOURCE_NAMES = { THOUGHT: 'My thought', IDEA: 'Idea', MESSAGE: 'Message', PROMPT: 'Prompt', IMPORTED: 'Imported', AI_INTERPRETATION: 'AI interpretation' };
  const COMMUNITY_COLORS = ['#a78bfa', '#55d6be', '#f0abfc', '#d4af37', '#fb7185', '#7dd3fc', '#c4b5fd', '#67e8f9'];
  const RELATION_TYPES = ['INSPIRED_BY', 'LED_TO', 'RELATED_TO', 'CONTRADICTS', 'SUPPORTS', 'DEPENDS_ON', 'PART_OF', 'SUPERSEDES', 'REVIVES', 'TESTS', 'IMPLEMENTED_BY'];

  // The seven Thinking Partner entry points are different *questions*, not
  // seven different ranks.  Each maps onto one of the six closed reasoning
  // modes; compare and ask both use grounded explanation with different inputs.
  const PARTNER_TOOLS = [
    { key: 'ask', mode: 'explain', icon: '?', label: 'Ask My Vault', hint: 'A grounded answer from selected memories.', prompts: ['What have I been thinking about lately?', 'Which of my notes disagree with each other?'] },
    { key: 'explore', mode: 'similar', icon: '✧', label: 'Explore Ideas', hint: 'Neighbours of one selected thought.', prompts: ['Find thoughts close to this one', 'What else did I write about this subject?'] },
    { key: 'connect', mode: 'connections', icon: '⌁', label: 'Find Connections', hint: 'Unsaved links worth your review.', prompts: ['Which unlinked thought might this connect to?', 'Show me candidate links from this thought'] },
    { key: 'compare', mode: 'explain', icon: '⇄', label: 'Compare Thoughts', hint: 'Read two or more captures together.', prompts: ['How do these two thoughts relate?', 'What is different between these captures?'] },
    { key: 'patterns', mode: 'themes', icon: '⁂', label: 'Identify Patterns', hint: 'Recurring terms across separate captures.', prompts: ['Which ideas keep returning in my notes?', 'What subjects appear again and again?'] },
    { key: 'evolution', mode: 'evolution', icon: '◷', label: 'Track Thinking Evolution', hint: 'Originals, revisions and events in order.', prompts: ['How did my thinking on this change over time?', 'Show the history of this idea'] },
    { key: 'reflect', mode: 'contradictions', icon: '⚖', label: 'Review & Reflect', hint: 'Possible tensions to reread, not verdicts.', prompts: ['Where do my captures pull in different directions?', 'Which thoughts might contradict each other?'] },
  ];
  const partnerTool = key => PARTNER_TOOLS.find(item => item.key === key) || PARTNER_TOOLS[0];

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
  const sourcePill = source => `<span class="source-pill source-${String(source || 'THOUGHT').toLowerCase()}">${esc(SOURCE_NAMES[source] || SOURCE_NAMES.THOUGHT)}</span>`;
  const statusPill = status => `<span class="status-pill ${String(status).toLowerCase()}">${esc(status === 'GRAVEYARD' ? 'Graveyard' : 'Active')}</span>`;
  const demoPill = isDemo => isDemo ? '<span class="demo-pill">Demo</span>' : '';
  const tagPills = tags => (tags || []).map(tag => `<span class="tag-chip">${esc(tag.name)}</span>`).join('');
  const titleOf = thought => thought?.displayTitle || thought?.title || words(thought?.currentText || thought?.originalText, 70) || 'Untitled thought';
  const communityColor = id => {
    let hash = 2166136261;
    for (const char of String(id || 'unlinked')) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
    return COMMUNITY_COLORS[(hash >>> 0) % COMMUNITY_COLORS.length];
  };

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
    const LABELS = { data: 'DATA & PRIVACY', recall: 'FIND MY THOUGHT', partner: 'THINKING PARTNER', clusters: 'DERIVED CLUSTERS', connections: 'CONNECTION EXPLORER' };
    const label = state.view === 'detail' ? 'THOUGHT DETAIL' : (LABELS[state.view] || state.view.toUpperCase());
    document.getElementById('topbar-label').textContent = label;
    document.body.dataset.view = state.view;
    const globalSearch = document.getElementById('global-search-input');
    if (globalSearch) globalSearch.placeholder = state.view === 'connections' ? 'Jump to concept...' : 'Find my thought from a fragment…';
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
    if (view !== 'search' && view !== 'recall' && !options.preserveQuery) state.query = '';
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
        case 'recall': markup = await renderRecall(); break;
        case 'partner': markup = await renderPartner(); break;
        case 'clusters': markup = await renderClusters(); break;
        case 'import': markup = renderFileImport(); break;
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
      if (state.view === 'recall') {
        const input = document.getElementById('recall-input');
        if (input) {
          input.focus({ preventScroll: true });
          if (state.recall.query) input.setSelectionRange(input.value.length, input.value.length);
        }
      }
      if (state.view === 'partner') {
        const input = document.getElementById('partner-query');
        if (input && !state.partner.result) input.focus({ preventScroll: true });
      }
    } catch (error) {
      if (token !== state.renderToken) return;
      app.innerHTML = `<div class="empty-state"><div class="empty-icon">!</div><h3>The vault could not load</h3><p>${esc(error.message)}. Check that the local server is running and try again.</p><button class="button" data-action="reload">Try again</button></div>`;
    }
  }

  async function renderCapture() {
    const draft = state.captureDraft || {};
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
            ${draft.sourceType === 'AI_INTERPRETATION' ? `<div class="interpretation-review"><strong>AI-generated draft</strong><span>Review and edit this before saving. Your choice will be recorded as an AI interpretation source.</span></div>` : ''}
            <label class="capture-origin-control" for="capture-source-type"><span>Capture source</span><select id="capture-source-type" name="sourceType">${Object.entries(SOURCE_NAMES).filter(([kind]) => kind !== 'IMPORTED').map(([kind, label]) => `<option value="${kind}" ${draft.sourceType === kind ? 'selected' : kind === 'THOUGHT' && !draft.sourceType ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select></label>
            <textarea id="capture-text" name="originalText" class="capture-compose" maxlength="200000" required placeholder="Could game enemies remember how the player fights and change their strategy later?\n\nWrite freely. You can organize it when you’re ready.">${esc(draft.text || '')}</textarea>
            <details class="capture-options"><summary>Optional: add a title, lifecycle, or tags</summary><div class="optional-fields">
              <div class="field"><label for="capture-title">Title</label><input id="capture-title" name="title" maxlength="200" value="${attr(draft.title || '')}" placeholder="A short name (optional)"></div>
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
      <div class="thought-card-top">${typePill(t.type)}${sourcePill(t.sourceType)}${demoPill(t.isDemo)}</div>
      <h3>${esc(titleOf(t))}</h3><p>${esc(words(t.originalText, 175))}</p>
      <div class="thought-card-bottom"><div class="tag-list">${tagPills((t.tags || []).slice(0, 3))}</div><span class="thought-date">${esc(date(t.updatedAt))}</span></div>
    </article>`;
  }

  function emptyState(title, description, buttonText = '', view = '') {
    return `<div class="empty-state"><div class="empty-icon">✳</div><h3>${esc(title)}</h3><p>${esc(description)}</p>${buttonText ? `<button class="button primary" data-view="${attr(view)}">${esc(buttonText)}</button>` : ''}</div>`;
  }

  async function renderVault() {
    const typeParam = state.vaultType === 'ALL' ? '' : `&type=${encodeURIComponent(state.vaultType)}`;
    const sourceParam = state.vaultSourceType === 'ALL' ? '' : `&source_type=${encodeURIComponent(state.vaultSourceType)}`;
    const { data } = await api(`/api/thoughts?status=ACTIVE${typeParam}${sourceParam}`);
    const types = ['ALL', 'THOUGHT', 'EXPERIMENT', 'INVENTION', 'PROJECT'];
    return `<section class="view-heading"><div><span class="eyebrow">THE ACTIVE VAULT</span><h1>Thoughts with room to grow.</h1><p>Browse the original captures, evolving experiments, inventions, and projects.</p></div><div class="heading-actions"><button class="button primary" data-view="capture">＋ Capture</button></div></section>
      <div class="view-filters">${types.map(type => `<button class="filter-chip ${state.vaultType === type ? 'active' : ''}" data-action="vault-type" data-type="${type}">${type === 'ALL' ? 'All types' : esc(TYPE_NAMES[type])}</button>`).join('')}<select class="filter-select" aria-label="Filter by source" data-action="vault-source-type">${[['ALL','All sources'], ...Object.entries(SOURCE_NAMES)].map(([kind,label]) => `<option value="${kind}" ${state.vaultSourceType === kind ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select></div>
      ${data.thoughts.length ? `<div class="list-stack">${data.thoughts.map(listThought).join('')}</div>` : emptyState('Nothing in this view yet', state.vaultType === 'ALL' ? 'Capture a thought without deciding what it has to become.' : `No ${TYPE_NAMES[state.vaultType]?.toLowerCase() || 'items'} yet. You can change a thought’s type at any time.`, 'Capture a thought', 'capture')}`;
  }

  function listThought(t, options = {}) {
    const snippet = options.snippet || t.originalText;
    return `<article class="list-thought" data-open-thought="${attr(t.id)}" tabindex="0" role="button" aria-label="Open ${attr(titleOf(t))}">
      <div><div class="list-meta">${typePill(t.type)}${sourcePill(t.sourceType)}${statusPill(t.status)}${demoPill(t.isDemo)}</div><h3>${esc(titleOf(t))}</h3><p>${esc(words(snippet, 235))}</p><div class="tag-list" style="margin-top:9px">${tagPills((t.tags || []).slice(0, 6))}</div></div>
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

  /* ======================= v0.2 · FIND MY THOUGHT (Recall V2) =======================
     Product surface for deterministic recall.  Two rules shape every pixel here:
       1. what the user WROTE is rendered in a distinct canonical block, verbatim;
       2. everything IWTT computed (score, reasons, connections, clusters) is
          rendered in a visually separate derived block and labelled as derived.
     Diagnostics live behind a <details> disclosure — recall is a feature, not a
     developer dashboard.
     ========================================================================== */

  const RECALL_STATES = {
    STRONG_MATCH: { label: 'Strong match', className: 'strong' },
    POSSIBLE_MATCH: { label: 'Possible match', className: 'possible' },
    AMBIGUOUS: { label: 'Several likely memories', className: 'ambiguous' },
    NO_MATCH: { label: 'Nothing conclusive', className: 'none' },
  };
  const RESULT_STATES = {
    STRONG: 'strong', POSSIBLE: 'possible', AMBIGUOUS: 'ambiguous',
  };
  const TIME_WINDOWS = ['', 'today', 'yesterday', 'this week', 'last week', 'this month', 'last month', 'this year', 'last year', 'last 7 days', 'last 30 days'];

  function recallQuery(params = {}) {
    const query = new URLSearchParams();
    query.set('q', state.recall.query);
    query.set('include_graveyard', state.recall.includeGraveyard ? 'true' : 'false');
    if (state.recall.time) query.set('time', state.recall.time);
    if (state.recall.type) query.set('types', state.recall.type);
    if (params.limit) query.set('limit', String(params.limit));
    return `/api/recall/find?${query.toString()}`;
  }

  async function runRecall() {
    const region = document.getElementById('recall-results');
    const summary = document.getElementById('recall-summary');
    if (!state.recall.query) {
      state.recall.outcome = null;
      if (region) region.innerHTML = recallEmpty();
      if (summary) summary.textContent = 'Recall runs entirely on this device.';
      return;
    }
    state.recall.loading = true;
    if (summary) summary.textContent = 'Recalling…';
    try {
      const { data } = await api(recallQuery());
      state.recall.outcome = data;
      state.recall.loading = false;
      if (region) region.innerHTML = recallResults(data, state.recall.semanticIndex);
      if (summary) summary.textContent = recallSummaryLine(data);
    } catch (error) {
      state.recall.loading = false;
      if (summary) summary.textContent = error.message;
      if (region) region.innerHTML = `<div class="empty-state"><div class="empty-icon">!</div><h3>Recall failed</h3><p>${esc(error.message)}</p></div>`;
    }
  }

  async function buildSemanticIndex(button) {
    const original = button?.textContent || 'Build / update index';
    if (button) { button.disabled = true; button.textContent = 'Building…'; }
    let total = 0;
    let model = null;
    try {
      // Bounded loop: each call embeds at most one batch, so the UI stays
      // responsive and a huge vault cannot lock the tab. The server decides
      // what remains; we stop as soon as it reports the index is current.
      for (let pass = 0; pass < 40; pass += 1) {
        const { data } = await api('/api/recall/embeddings/build', { method: 'POST', body: { limit: 200 } });
        total += data.built || 0;
        model = data.model || model;
        if (button) button.textContent = `Building… ${total} embedded`;
        if (data.upToDate || !data.remaining) break;
      }
      toast(total
        ? `${total} thought(s) embedded${model ? ` with ${model}` : ''}. Semantic results now appear as a separate section.`
        : 'The semantic index was already up to date.');
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      if (button) { button.disabled = false; button.textContent = original; }
    }
    return render();
  }

  async function eraseSemanticIndex() {
    if (!window.confirm('Erase the semantic index? It is derived data: your thoughts and connections are untouched, and you can rebuild it at any time.')) return;
    try {
      const { data } = await api('/api/recall/embeddings', { method: 'DELETE' });
      toast(`${data.erased} vector(s) erased. Nothing canonical changed.`);
    } catch (error) { toast(error.message, 'error'); }
    return render();
  }

  function recallSummaryLine(data) {
    const ms = data.timingsMs?.total;
    const parts = [
      `${data.resultCount} candidate${data.resultCount === 1 ? '' : 's'}`,
      `${data.candidatesExamined} examined`,
      data.stateLabel || data.state,
    ];
    if (typeof ms === 'number') parts.push(`${ms.toFixed(1)} ms`);
    parts.push(data.semantic?.enabled
      ? 'scored locally · separate semantic section'
      : 'local · no AI · no network');
    return parts.join(' · ');
  }

  function recallEmpty() {
    return `<div class="empty-state"><div class="empty-icon">✦</div><h3>Describe the thought you half-remember</h3>
      <p>A fragment is enough: a phrase you think you used, a rough idea, something it was connected to, when you wrote it, or whether it is in the Graveyard. IWTT searches word forms, typos, character patterns, titles, tags, time and saved connections — then shows you what you actually wrote.</p>
      <p class="revision-note">No embeddings, no cloud, no AI. Identical input always produces identical ordering.</p></div>`;
  }

  function explanationChips(explanations) {
    return (explanations || []).map(item => `<span class="explain-chip" title="${attr(item.detail || '')}">${esc(item.label)}</span>`).join('');
  }

  function recallCard(result, outcome) {
    const stateMeta = RECALL_STATES[result.state] || { label: result.state, className: 'possible' };
    const written = result.whatIActuallyWrote || {};
    const context = result.relationshipContext;
    const cluster = result.cluster;
    const edited = written.currentText && written.currentText !== written.originalText;
    const ambiguous = outcome?.state === 'AMBIGUOUS';
    return `<article class="recall-card ${RESULT_STATES[result.state] || 'possible'}" data-thought="${attr(result.thoughtId)}">
      <header class="recall-head">
        <span class="recall-rank">${result.rank}</span>
        <div class="recall-head-main">
          <div class="result-meta">
            <span class="state-pill ${stateMeta.className}">${esc(stateMeta.label)}</span>
            ${typePill(result.type)}${statusPill(result.status)}${demoPill(result.isDemo)}
            <span class="score-pill" title="Weighted score out of ${(outcome?.analysis?.theoreticalMax || 21.25)}">${result.score.toFixed(2)}</span>
          </div>
          <h3><button class="connection-target" data-open-thought="${attr(result.thoughtId)}">${esc(result.displayTitle)}</button></h3>
        </div>
        <div class="result-date">${esc(date(result.createdAt, true))}<br><span class="micro">originally captured</span></div>
      </header>

      <div class="canonical-block">
        <div class="block-label"><span class="dot canonical"></span>WHAT YOU WROTE <em>canonical · immutable</em></div>
        <p class="original-text">${esc(written.originalText || result.snippet)}</p>
        ${edited ? `<p class="revision-note">Current text has since been edited to: “${esc(words(written.currentText, 220))}”. The original capture above is unchanged and can never be overwritten.</p>` : ''}
        <div class="tag-list">${(result.tags || []).map(tag => `<span class="tag-chip">${esc(tag)}</span>`).join('')}</div>
      </div>

      <div class="derived-block">
        <div class="block-label"><span class="dot derived"></span>WHY IWTT MATCHED IT <em>derived · rebuildable</em></div>
        <p class="why-line">${esc(result.why)}</p>
        <div class="explain-row">${explanationChips(result.explanations)}</div>
        ${result.matchReason ? `<p class="micro">v0.1-compatible match reason: <strong>${esc(result.matchReason)}</strong></p>` : ''}
        ${context ? `<p class="micro">Reached through a connection: <strong>${esc(context.displayTitle)}</strong> ${esc(context.label)} this thought · ${esc(context.display)}${context.depth > 1 ? ` · ${context.depth} hops` : ''}</p>` : ''}
        ${cluster && cluster.clusterId ? `<p class="micro">Sits in ${esc(cluster.label || 'an automatically discovered cluster')} · ${esc(cluster.size || 0)} member${cluster.size === 1 ? '' : 's'} · derived, never canonical</p>` : ''}
        ${result.status === 'GRAVEYARD' ? `<p class="micro">In the Graveyard since ${esc(date(result.graveyardAt))}${result.resurrectedAt ? ` · resurrected ${esc(date(result.resurrectedAt))}` : ''}. Shown because you asked for it; nothing was silently excluded.</p>` : ''}
        ${ambiguous && (result.rivals || []).length ? `<p class="micro">What separates it from the others: ${result.rivals.map(esc).join('; ')}</p>` : ''}
      </div>

      <footer class="recall-foot">
        <button class="button primary small" data-open-thought="${attr(result.thoughtId)}">Open what I wrote</button>
        <button class="button small" data-action="recall-feedback" data-feedback="USEFUL" data-id="${attr(result.thoughtId)}" data-rank="${result.rank}">This is it</button>
        <button class="button ghost small" data-action="recall-feedback" data-feedback="NOT_USEFUL" data-id="${attr(result.thoughtId)}" data-rank="${result.rank}">Not this</button>
        <button class="button ghost small" data-action="recall-breakdown" data-id="${attr(result.thoughtId)}">Scoring breakdown</button>
      </footer>
    </article>`;
  }

  function recallBanner(outcome) {
    if (outcome.state === 'AMBIGUOUS') {
      return `<div class="recall-banner ambiguous" role="status">
        <strong>${esc(outcome.headline || 'I found several likely memories')}</strong>
        <p>${esc(outcome.message)}</p>
        ${outcome.ambiguity?.orderingIsTieBreakNotJudgement ? '<p class="micro">The order below is a deterministic tie-break, not a judgement that one is better.</p>' : ''}
      </div>`;
    }
    if (outcome.state === 'NO_MATCH') {
      const excluded = outcome.honesty?.filteredOut;
      return `<div class="recall-banner none" role="status">
        <strong>${esc(outcome.headline || 'I could not find that thought')}</strong>
        <p>${esc(outcome.message)}</p>
        ${excluded && excluded.totalExcluded ? `<p class="micro">Hidden by your filters: ${Object.entries(excluded.byReason).map(([reason, count]) => `${count} × ${esc(excluded.labels[reason] || reason)}`).join(', ')}. ${(excluded.examples?.status || excluded.examples?.time || []).map(esc).join(' · ')}</p>` : ''}
      </div>`;
    }
    if (outcome.state === 'POSSIBLE_MATCH') {
      return `<div class="recall-banner possible" role="status"><strong>${esc(outcome.headline || 'This may be the thought you meant')}</strong><p>${esc(outcome.message)}</p></div>`;
    }
    return `<div class="recall-banner strong" role="status"><strong>${esc(outcome.headline || 'This looks like the thought you meant')}</strong><p>${esc(outcome.message)}</p></div>`;
  }

  function recallBreakdownPanel() {
    const data = state.recall.breakdown;
    if (!data) return '';
    const result = data.result;
    if (!result) return `<section class="panel breakdown-panel"><p class="micro">${esc(data.result?.reason || 'That thought was not in the returned set for this query.')}</p></section>`;
    const rows = Object.entries(result.contributions || {}).map(([name, item]) => `
      <tr><td>${esc(name)}</td><td class="num">${item.weight}</td><td class="num">${(item.signal ?? 0).toFixed(4)}</td><td class="num">${item.contribution.toFixed(4)}</td></tr>`).join('');
    const terms = (result.perTerm || []).map(term => `
      <tr><td>${esc(term.term)}</td><td>${esc(term.tier)}</td><td>${esc(term.matchedForm || '—')}</td><td class="num">${term.idf}</td><td class="num">${(term.tierValue ?? 0).toFixed(4)}</td><td class="num">${term.contributionToScore.toFixed(4)}</td></tr>`).join('');
    return `<section class="panel breakdown-panel">
      <div class="section-head" style="margin-top:0"><h2>Scoring breakdown</h2><span class="section-meta">DERIVED · DEBUG VIEW</span></div>
      <p class="micro">${esc(result.displayTitle)} · score <strong>${result.score}</strong> · ${esc(result.matchReason)}</p>
      <h4>Weighted signals</h4>
      <table class="breakdown-table"><thead><tr><th>signal</th><th>weight</th><th>signal value</th><th>contribution</th></tr></thead><tbody>${rows}</tbody></table>
      <h4>Per-term lexical tiers</h4>
      <table class="breakdown-table"><thead><tr><th>query term</th><th>tier</th><th>matched form</th><th>idf</th><th>tier value</th><th>contribution</th></tr></thead><tbody>${terms}</tbody></table>
      <p class="micro">idf signal = Σ(idf × tierValue) / Σ(idf) = <strong>${(result.idfSignal?.value ?? 0).toFixed(4)}</strong> · coverage ${JSON.stringify(result.coverage?.matched || [])} of ${JSON.stringify([...(result.coverage?.matched || []), ...(result.coverage?.missing || [])])}</p>
      <h4>Candidate generation</h4>
      <p class="micro">${esc(data.candidateGeneration?.guardExplanation || '')}</p>
      <p class="micro">${esc(data.candidateGeneration?.fuzzyPassExplanation || '')} Candidates in the fuzzy pass: ${data.candidateGeneration?.fuzzyPassCandidates ?? 0} of a cap of ${data.candidateGeneration?.fuzzyPassCap ?? 0}.</p>
      <h4>Tie-break (total order)</h4>
      <p class="micro">${esc((data.tieBreak?.order || []).join(' → '))} — ${esc(data.tieBreak?.explanation || '')}</p>
      <h4>Honesty</h4>
      <ul class="honesty-list">${Object.entries(data.honesty || {}).filter(([key]) => typeof data.honesty[key] === 'string').map(([key, value]) => `<li><strong>${esc(key)}</strong>: ${esc(value)}</li>`).join('')}</ul>
      <details class="sub-details"><summary>Full formula and weights</summary><pre class="formula">${esc(data.scoringFormula || '')}</pre><pre class="formula">${esc(JSON.stringify(data.weights, null, 2))}</pre></details>
    </section>`;
  }

  function renderSemanticSection(outcome) {
    const semantic = outcome?.semantic;
    if (!semantic || (!semantic.enabled && !semantic.indexedCount)) return '';
    if (!semantic.enabled) {
      return `<section class="panel semantic-panel semantic-off"><div class="section-head" style="margin-top:0"><h2>Semantic stage</h2><span class="section-meta">OFF</span></div><p class="micro">${esc(semantic.reason || 'The semantic stage is off.')}</p></section>`;
    }
    const entries = (semantic.results || []).map(entry => `
      <li class="semantic-hit">
        <span class="semantic-score">${Math.round((entry.similarity || 0) * 100)}%</span>
        <span class="semantic-body"><button class="connection-target" data-open-thought="${attr(entry.thoughtId)}">${esc(entry.displayTitle || entry.thoughtId.slice(0, 8))}</button>
        ${entry.preview ? `<small>${esc(entry.preview)}</small>` : ''}</span>
      </li>`).join('');
    return `<section class="panel semantic-panel">
        <div class="section-head" style="margin-top:0"><h2>Same meaning, different words</h2><span class="section-meta">SEMANTIC · LABELLED SEPARATELY · NOT SCORED MATCHES</span></div>
        <p class="micro">Neighbours by embedding similarity (${esc(semantic.model || 'unknown model')}). These are ranked hints from an explicitly built vector index — they never entered the scored ranking above, and a high percentage is not proof of relevance. ${semantic.omittedAlreadyReturned ? `${semantic.omittedAlreadyReturned} neighbour(s) already appear in the ranked list.` : ''}</p>
        <ul class="semantic-list">${entries || '<li class="micro">Nothing cleared the similarity floor for this query.</li>'}</ul>
      </section>`;
  }

  function semanticIndexCard(semanticIndex) {
    const info = semanticIndex || {};
    const status = info.available
      ? `<strong>${info.count}</strong> vector(s) · model <code>${esc(info.model || 'unknown')}</code> · ${info.dimensions} dimensions · built ${esc(date(info.builtAt, true))}`
      : 'No semantic index has been built. Recall works fully without it.';
    return `<section class="panel semantic-panel">
        <div class="section-head" style="margin-top:0"><h2>Semantic index (optional)</h2><span class="section-meta">DERIVED · OPT-IN · ERASABLE</span></div>
        <p class="micro">${status}</p>
        <p class="micro">Building sends each thought's indexed text to the configured AI provider (preferably a local Ollama embedding model such as <code>nomic-embed-text</code>) and stores only the resulting vectors. Similarity then appears as a separate “same meaning, different words” section under Find My Thought. It never changes the scored ranking, and deleting it loses nothing.</p>
        <div class="data-actions">
          <button class="button" data-action="build-semantic-index" ${info.available ? '' : ''}>Build / update index</button>
          ${info.available ? '<button class="button danger" data-action="erase-semantic-index">Erase semantic index</button>' : ''}
        </div>
      </section>`;
  }

  function recallResults(outcome, semanticIndex) {
    if (!outcome) {
      return `${recallEmpty()}${semanticIndexCard(semanticIndex)}`;
    }
    const results = outcome.results || [];
    const cards = results.map(result => recallCard(result, outcome)).join('');
    const semanticOn = outcome.semantic?.enabled;
    return `${recallBanner(outcome)}
      ${results.length ? `<div class="section-head" style="margin-top:0"><h2>Ranked candidates</h2><span class="section-meta">ORIGINAL TEXT ALWAYS SHOWN</span></div><div class="recall-stack">${cards}</div>` : ''}
      ${renderSemanticSection(outcome)}
      <div id="recall-breakdown">${recallBreakdownPanel()}</div>
      <section class="panel honesty-panel">
        <div class="section-head" style="margin-top:0"><h2>How this answer was produced</h2><span class="section-meta">${semanticOn ? 'SEMANTIC STAGE ON · SCORED RANKING STAYED DETERMINISTIC' : 'NO AI · NO NETWORK · NO EMBEDDINGS'}</span></div>
        <ul class="honesty-list">
          <li><strong>Retrieval</strong>: lexical tiers (exact › word form › prefix › substring), character trigrams, bounded fuzzy similarity, IDF weighting, coverage, titles, tags, time, lifecycle and saved connections.</li>
          <li><strong>Ranking</strong>: a deterministic weighted sum with a total-order tie-break. The same vault, query and configuration always produce the same order.</li>
          <li><strong>Embeddings</strong>: ${esc(outcome.honesty?.embeddings || 'off — no embedding index has been built.')}</li>
          <li><strong>Network</strong>: ${esc(outcome.honesty?.network || 'none. No request left this process.')}</li>
          <li><strong>Graph expansion</strong>: enriches candidates only; it never replaces text retrieval, and it never invents a connection.</li>
        </ul>
        <details class="sub-details"><summary>Stage timings &amp; index state</summary><pre class="formula">${esc(JSON.stringify({ timingsMs: outcome.timingsMs, indexState: outcome.indexState, graph: outcome.graph?.expansion }, null, 2))}</pre></details>
      </section>
      ${semanticIndexCard(semanticIndex)}`;
  }

  async function renderRecall() {
    let outcome = null;
    let semanticIndex = null;
    if (state.recall.query) {
      try { outcome = (await api(recallQuery())).data; } catch (error) { outcome = null; toast(error.message, 'error'); }
    }
    try { semanticIndex = (await api('/api/recall/embeddings')).data; } catch (error) { semanticIndex = null; }
    state.recall.outcome = outcome;
    state.recall.semanticIndex = semanticIndex;
    const types = ['', 'THOUGHT', 'EXPERIMENT', 'INVENTION', 'PROJECT'];
    return `<section class="search-hero recall-hero">
        <span class="eyebrow">FIND MY THOUGHT · RECALL V2</span>
        <h1>Remember a fragment. Get the thought back.</h1>
        <p>A phrase, a rough idea, a connection, a time, or a lifecycle state. IWTT returns ranked candidates, what you actually wrote, why each one matched, and what it is connected to — and it says so when it is not sure.</p>
        <form id="recall-form" class="recall-form" role="search">
          <div class="search-box-large"><span class="search-glass" aria-hidden="true">✦</span>
            <input id="recall-input" type="search" value="${attr(state.recall.query)}" placeholder="“the idea about enemies learning from the player…”" autocomplete="off">
            <button class="button ghost small" data-action="clear-recall" ${state.recall.query ? '' : 'hidden'} aria-label="Clear">×</button>
          </div>
          <div class="recall-filters">
            <label>When <select id="recall-time">${TIME_WINDOWS.map(value => `<option value="${attr(value)}" ${state.recall.time === value ? 'selected' : ''}>${esc(value || 'any time')}</option>`).join('')}</select></label>
            <label>Type <select id="recall-type">${types.map(value => `<option value="${attr(value)}" ${state.recall.type === value ? 'selected' : ''}>${esc(value ? (TYPE_NAMES[value] || value) : 'any type')}</option>`).join('')}</select></label>
            <span class="checkbox-control"><input type="checkbox" id="recall-graveyard" ${state.recall.includeGraveyard ? 'checked' : ''}><label for="recall-graveyard">Include Graveyard</label></span>
            <button class="button primary small" type="submit">Find my thought</button>
            <span class="search-summary" id="recall-summary">${outcome ? esc(recallSummaryLine(outcome)) : 'Recall runs entirely on this device.'}</span>
          </div>
        </form>
      </section>
      <div id="recall-results">${recallResults(outcome, semanticIndex)}</div>`;
  }

  // ---------------------------------------------------------------- partner --
  // A dedicated view for questions *across* memories.  It never shares the
  // scored recall ranking, and no finding reaches the vault without a person.
  function partnerRefs(item) {
    const refs = new Map();
    const add = ref => {
      if (ref?.thoughtId && !refs.has(ref.thoughtId)) refs.set(ref.thoughtId, ref);
    };
    add(item.citation); add(item.source); add(item.target); add(item.left); add(item.right);
    if (Array.isArray(item.evidence)) item.evidence.slice(0, 4).forEach(add);
    if (!refs.size && Array.isArray(item.thoughtIds)) {
      item.thoughtIds.slice(0, 4).forEach(id => add({ thoughtId: id }));
    }
    return [...refs.values()].slice(0, 4).map(ref => {
      const thought = state.partner.thoughts.find(entry => entry.id === ref.thoughtId);
      const title = ref.title || titleOf(thought || {}) || 'Original thought';
      const excerpt = ref.excerpt || (thought ? words(thought.currentText || thought.originalText, 90) : '');
      return '<button class="reasoning-ref" type="button" data-open-thought="' + attr(ref.thoughtId) + '">' +
        '<strong>' + esc(title) + '</strong>' +
        (excerpt ? '<small>' + esc(String(excerpt).slice(0, 120)) + '</small>' : '') + '</button>';
    }).join('');
  }

  function partnerFinding(item, index) {
    const tool = partnerTool(state.partner.tool);
    const title = item.title || item.label || item.observation ||
      (item.kind ? String(item.kind).replaceAll('_', ' ') : 'Finding');
    const detail = item.reason || item.observation || item.caveat || item.note || item.excerpt ||
      (Array.isArray(item.sharedTerms) ? 'Shared terms: ' + item.sharedTerms.join(' · ') : '');
    const score = item.score !== undefined ? ' · ' + Math.round(item.score * 100) + '% ranked match'
      : item.similarity !== undefined ? ' · ' + Math.round(item.similarity * 100) + '% semantic'
      : item.strength !== undefined ? ' · strength ' + Math.round(item.strength * 100) + '%'
      : item.tension !== undefined ? ' · tension clue ' + Math.round(item.tension * 100) + '%' : '';
    const uncertainty = item.uncertainty || item.caveat ||
      (tool.key === 'reflect' ? 'A prompt to reread both originals, not a verdict about which is right.'
        : 'A derived clue from your captures, not a claim about your intent.');
    const proposal = item.requiresApproval
      ? '<div class="partner-actions"><span class="status-pill suggested">Proposal only · nothing saved</span>' +
        '<button class="button small" type="button" data-action="partner-save-connection" data-index="' + index + '">Save proposal for review</button></div>'
      : '';
    return '<article class="partner-finding"><div class="partner-finding-head"><strong>' + esc(title) + '</strong>' +
      (score ? '<span class="reasoning-score">' + esc(score) + '</span>' : '') + '</div>' +
      (detail ? '<p>' + esc(detail) + '</p>' : '') +
      '<p class="partner-uncertainty">' + esc(uncertainty) + '</p>' +
      (partnerRefs(item) ? '<div class="reasoning-refs">' + partnerRefs(item) + '</div>' : '') +
      proposal + '</article>';
  }

  function partnerFollowUps() {
    const result = state.partner.result;
    if (!result) return '';
    const selected = state.partner.selectedIds.length;
    const options = [
      { tool: 'ask', prompt: 'What can you say about these memories without guessing?', label: 'Ask for a grounded summary' },
      { tool: 'reflect', prompt: 'Do any of these captures disagree with each other?', label: 'Look for tensions' },
      { tool: 'evolution', prompt: 'Show how this thinking changed over time.', label: 'Trace the history' },
    ];
    if (selected >= 2) options.unshift({ tool: 'compare', prompt: 'Compare the memories I selected.', label: 'Compare selected memories' });
    if (selected === 1) options.unshift({ tool: 'connect', prompt: 'Find possible links from the first selected memory.', label: 'Look for links' });
    return '<section class="panel partner-followups"><div class="section-head" style="margin-top:0"><h2>Follow the thread</h2><span class="section-meta">NOT A NEW TASK · SAME VAULT</span></div>' +
      '<p class="revision-note">Each follow-up keeps your selected memories and asks a narrower question. You can change the selection at any time.</p>' +
      '<div class="partner-prompts">' + options.slice(0, 4).map(item =>
        '<button class="chip" type="button" data-action="partner-followup" data-tool="' + attr(item.tool) + '" data-prompt="' + attr(item.prompt) + '">' + esc(item.label) + '</button>'
      ).join('') + '</div></section>';
  }

  function partnerResultMarkup() {
    const partner = state.partner;
    if (partner.loading) {
      return '<section class="panel partner-result"><div class="loading-state"><span class="loader"></span><p>Reading your captures locally…</p></div></section>';
    }
    if (partner.error) {
      return '<section class="panel partner-result"><div class="empty-state"><div class="empty-icon">!</div><h3>No answer was produced</h3><p>' + esc(partner.error) + '</p><p class="revision-note">Nothing in the vault was changed.</p></div></section>';
    }
    const result = partner.result;
    if (!result) {
      return '<section class="panel partner-result"><div class="empty-state"><div class="empty-icon">◇</div><h3>Ask about your own thinking</h3>' +
        '<p>Choose a question above, add a focus phrase, and optionally select memories. IWTT will show only what it can ground in your captures, with the uncertainty stated.</p>' +
        '<p class="revision-note">Nothing is saved, edited or connected until you explicitly ask for it.</p></div></section>';
    }
    const findings = Array.isArray(result.findings) ? result.findings : [];
    const view = result.providerView;
    return '<section class="panel partner-result"><div class="partner-result-head">' +
      '<span class="derived-mark">DERIVED READING · ' + esc(String(result.mode || '').replaceAll('_', ' ')) + '</span>' +
      '<h2>' + esc(partnerTool(partner.tool).label) + '</h2>' +
      (result.prompt ? '<p class="partner-context">Asked: “' + esc(result.prompt) + '”' +
        (partner.selectedIds.length ? ' · ' + partner.selectedIds.length + ' selected memor' + (partner.selectedIds.length === 1 ? 'y' : 'ies') : '') + '</p>' : '') +
      '<p class="partner-note">' + esc(result.summary || result.note || '') + '</p>' +
      '<p class="partner-provider">' + esc(result.provider || '') + (result.model ? ' · ' + esc(result.model) : '') + '</p>' +
      (result.warnings || []).map(warning => '<p class="partner-warning">' + esc(warning) + '</p>').join('') +
      '</div>' +
      (findings.length ? '<div class="partner-findings">' + findings.map(partnerFinding).join('') + '</div>'
        : '<div class="empty-state"><div class="empty-icon">—</div><h3>No grounded finding</h3><p>' + esc(result.note || 'IWTT found nothing it can honestly support from these memories.') + '</p></div>') +
      (view ? '<div class="reasoning-provider-view"><span class="derived-mark">PROVIDER VIEW · PROPOSAL ONLY</span><p>' + esc(view.summary || '') + '</p>' +
        (partnerRefs(view) ? '<div class="reasoning-refs">' + partnerRefs(view) + '</div>' : '') +
        '<p class="partner-uncertainty">' + esc(view.caveat || 'One provider reading; never the source of truth.') + '</p></div>' : '') +
      '<p class="partner-boundary">' + esc(result.boundary || 'Derived, erasable, and never a change to your original words.') + '</p>' +
      (findings.length ? '<div class="data-actions"><button class="button small" type="button" data-action="partner-save">Save this finding as derived data</button></div>' : '') +
      '</section>' + partnerFollowUps();
  }

  async function renderPartner() {
    let status = null;
    let thoughts = [];
    try { status = (await api('/api/reasoning')).data; } catch (_) { status = null; }
    try { thoughts = (await api('/api/thoughts?status=ALL')).data.thoughts || []; } catch (_) { thoughts = []; }
    state.partner.status = status;
    state.partner.thoughts = thoughts;
    const partner = state.partner;
    const tool = partnerTool(partner.tool);
    const selected = new Set(partner.selectedIds);
    const providerLine = status?.providerAvailable
      ? 'Provider: ' + esc(status.provider) + (status.model ? ' · ' + esc(status.model) : '') + '. Its view is optional and labelled.'
      : 'No provider configured. The deterministic local baseline is active — no key, no network, no paid service.';
    return '<section class="view-heading partner-heading"><div><span class="eyebrow">THINKING PARTNER · DERIVED REASONING</span>' +
      '<h1>Think with what you actually wrote.</h1>' +
      '<p>Ask across memories, compare them, follow a theme or inspect a change. Every answer cites the original captures and says what it cannot know.</p></div>' +
      '<div class="heading-actions"><button class="button ghost" data-view="recall">Find a specific thought</button></div></section>' +
      '<section class="partner-tools"><div class="section-head" style="margin-top:0"><h2>Choose a way to think</h2><span class="section-meta">SEVEN QUESTIONS · SIX CLOSED MODES</span></div>' +
      '<div class="partner-tool-grid">' + PARTNER_TOOLS.map(item =>
        '<button class="partner-tool ' + (item.key === tool.key ? 'selected' : '') + '" type="button" data-action="partner-tool" data-tool="' + item.key + '">' +
        '<span class="partner-tool-icon">' + esc(item.icon) + '</span><strong>' + esc(item.label) + '</strong><small>' + esc(item.hint) + '</small></button>'
      ).join('') + '</div></section>' +
      '<div class="partner-workspace"><div class="partner-left">' +
      '<section class="panel partner-ask"><div class="section-head" style="margin-top:0"><h2>' + esc(tool.label) + '</h2><span class="section-meta">' + esc(tool.hint) + '</span></div>' +
      '<form id="partner-form" class="partner-form form" role="search">' +
      '<label class="field">Question or focus<textarea id="partner-query" name="query" rows="3" maxlength="2000" placeholder="' + attr(tool.prompts[0]) + '">' + esc(partner.query) + '</textarea></label>' +
      '<div class="partner-prompts">' + tool.prompts.map(prompt =>
        '<button class="chip" type="button" data-action="partner-prompt" data-prompt="' + attr(prompt) + '">' + esc(prompt) + '</button>'
      ).join('') + '</div>' +
      '<div class="partner-options"><span class="checkbox-control"><input type="checkbox" id="partner-use-provider" ' + (partner.useProvider ? 'checked' : '') + '><label for="partner-use-provider">Allow configured provider view</label></span>' +
      '<span class="checkbox-control"><input type="checkbox" id="partner-include-graveyard" ' + (partner.includeGraveyard ? 'checked' : '') + '><label for="partner-include-graveyard">Include Graveyard</label></span></div>' +
      '<p class="revision-note">' + esc(providerLine) + '</p>' +
      '<div class="data-actions"><button class="button primary" type="submit">' + (partner.loading ? 'Reading…' : 'Ask the partner') + '</button>' +
      '<button class="button ghost" type="button" data-action="partner-clear">Clear answer</button></div></form></section>' +
      '<section class="panel partner-memory-panel"><div class="section-head" style="margin-top:0"><h2>Choose memories</h2><span class="section-meta">' + partner.selectedIds.length + ' SELECTED · MAX 20</span></div>' +
      '<p class="revision-note">Selection grounds Ask, Compare, Explore, Connections and Evolution. Patterns and Reflect read the active vault. Choosing a memory never edits it.</p>' +
      (thoughts.length ? '<div class="partner-memory-list">' + thoughts.slice(0, 80).map(item =>
        '<label class="partner-memory ' + (selected.has(item.id) ? 'selected' : '') + '" data-action="partner-select" data-id="' + attr(item.id) + '">' +
        '<input type="checkbox" ' + (selected.has(item.id) ? 'checked' : '') + ' aria-label="Select ' + attr(titleOf(item)) + '">' +
        '<span><strong>' + esc(titleOf(item)) + '</strong><small>' + esc(words(item.currentText || item.originalText, 90)) + '</small></span></label>'
      ).join('') + '</div>' : '<div class="empty-state"><div class="empty-icon">✳</div><h3>No memories to select yet</h3><p>Capture a thought first; the partner only reasons over what you actually wrote.</p></div>') +
      (partner.selectedIds.length ? '<div class="data-actions"><button class="button small ghost" type="button" data-action="partner-clear-selection">Clear selection</button></div>' : '') +
      '</section></div><div class="partner-right">' + partnerResultMarkup() + '</div></div>';
  }

  function syncPartnerQuery() {
    const input = document.getElementById('partner-query');
    if (input) state.partner.query = String(input.value || '').slice(0, 2000);
  }

  async function runPartner() {
    const partner = state.partner;
    const tool = partnerTool(partner.tool);
    const query = String(partner.query || '').trim();
    const selected = partner.selectedIds.slice(0, 20);
    if (tool.key === 'explore' || tool.key === 'connect') {
      if (!selected.length) { partner.error = 'Select one thought to ' + tool.label.toLowerCase() + '.'; partner.result = null; return render(); }
    } else if (tool.key === 'compare' && selected.length < 2) {
      partner.error = 'Select at least two thoughts to compare them.'; partner.result = null; return render();
    } else if (tool.key === 'ask' && !query && !selected.length) {
      partner.error = 'Add a question or select at least one memory.'; partner.result = null; return render();
    } else if (tool.key === 'evolution' && !query && !selected.length) {
      partner.error = 'Add a topic or select a thought to trace its evolution.'; partner.result = null; return render();
    }
    const payload = {
      mode: tool.mode,
      query,
      thoughtIds: selected,
      limit: 8,
      useProvider: partner.useProvider,
      includeGraveyard: partner.includeGraveyard,
    };
    if ((tool.key === 'explore' || tool.key === 'connect' || tool.key === 'evolution') && selected.length) {
      payload.thoughtId = selected[0];
    }
    if (tool.key === 'patterns' || tool.key === 'reflect') {
      delete payload.thoughtIds;
      if (selected.length === 1 && tool.key === 'reflect') payload.thoughtId = selected[0];
    }
    partner.loading = true;
    partner.error = '';
    partner.result = null;
    await render();
    try {
      const { data } = await api('/api/reasoning', { method: 'POST', body: payload });
      data.prompt = query || (selected.length ? partner.selectedIds.length + ' selected memories' : tool.label);
      data.partnerTool = tool.key;
      partner.result = data;
    } catch (error) {
      partner.error = error.message;
    } finally {
      partner.loading = false;
    }
    return render();
  }

  async function savePartnerFinding() {
    const result = state.partner.result;
    if (!result) throw new Error('Run a question before saving a finding.');
    await api('/api/reasoning/save', { method: 'POST', body: { mode: result.mode, finding: result } });
    toast('Finding saved as derived data. Your original captures are unchanged.');
  }

  async function savePartnerConnection(button) {
    const result = state.partner.result;
    const finding = result?.findings?.[Number(button.dataset.index)];
    if (!finding?.sourceId || !finding?.targetId) throw new Error('That proposal is no longer available.');
    await api('/api/relationships', { method: 'POST', body: {
      sourceId: finding.sourceId, targetId: finding.targetId,
      type: finding.type || 'RELATED_TO', provenance: 'AI_SUGGESTED',
    } });
    toast('Proposal saved as unconfirmed. Open Connections to review it before confirming.');
  }

  /* ======================= v0.2 · DERIVED CLUSTERS ======================= */

  async function renderClusters() {
    const [{ data }, { data: learning }] = await Promise.all([api('/api/clusters'), api('/api/recall/learning')]);
    const clusters = data.clusters || [];
    return `<section class="view-heading"><div><span class="eyebrow">DERIVED ORGANISATION · NEVER CANONICAL</span><h1>Automatically discovered clusters.</h1><p>Groupings IWTT computes from your saved connections and labels. They are a reading of the graph, not a fact about your thoughts: delete all derived data and they disappear without losing anything.</p></div>
        <div class="heading-actions"><button class="button" data-action="rebuild-clusters">Recompute clusters</button><button class="button ghost" data-view="connections">Connection Explorer</button></div></section>
      ${clusters.length ? `<div class="cluster-grid">${clusters.map(cluster => `
        <section class="panel cluster-card">
          <div class="block-label"><span class="dot derived"></span>${esc(cluster.authority || 'AUTOMATICALLY_DISCOVERED_CLUSTER')} <em>${esc(cluster.algorithm || '')}</em></div>
          <h3>${esc(cluster.label || 'Unnamed cluster')}</h3>
          <p class="micro">${cluster.size} member${cluster.size === 1 ? '' : 's'} · cohesion ${(cluster.cohesion ?? 0).toFixed(3)} · id <code>${esc(cluster.clusterId)}</code></p>
          <ul class="cluster-members">${(cluster.members || []).slice(0, 8).map(id => `<li><button class="connection-target" data-open-thought="${attr(id)}">${esc((cluster.memberTitles || {})[id] || id.slice(0, 8))}</button></li>`).join('')}</ul>
          ${cluster.size > 8 ? `<p class="micro">+ ${cluster.size - 8} more</p>` : ''}
        </section>`).join('')}</div>` : emptyState('No clusters yet', 'Clusters appear once thoughts are connected to each other. They are computed locally with a deterministic algorithm (connected components plus label propagation).', 'Open the Connection Explorer', 'connections')}
      <section class="panel">
        <div class="section-head" style="margin-top:0"><h2>Query-outcome memory</h2><span class="section-meta">DERIVED · DECAYING · RESETTABLE</span></div>
        <p class="micro">When you mark a result useful or not, IWTT remembers — for that phrasing only, with a 180-day half-life, gated until an outcome is corroborated twice. It never edits a thought, never confirms a relationship, and never survives a reset.</p>
        <p class="micro">Events stored: <strong>${learning.stats?.events ?? 0}</strong> · distinct phrasings: <strong>${learning.stats?.queries ?? 0}</strong> · last reset: <strong>${esc(learning.stats?.learningResetAt || 'never')}</strong></p>
        ${(learning.learnedQueries || []).length ? `<table class="breakdown-table"><thead><tr><th>phrasing</th><th>events</th><th>thoughts</th><th>last signal</th></tr></thead><tbody>${learning.learnedQueries.map(row => `<tr><td><code>${esc(row.queryNorm)}</code></td><td class="num">${row.events}</td><td class="num">${row.thoughts}</td><td class="num">${esc(date(row.lastAt, true))}</td></tr>`).join('')}</tbody></table>` : '<p class="micro">Nothing learned yet.</p>'}
        <div class="data-actions"><button class="button danger" data-action="reset-learning">Reset all query memory</button></div>
      </section>`;
  }

  /* ==================== v0.2 · CONNECTION EXPLORER V2 ====================
     Additive panels below the v0.1 graph.  The v0.1 SVG view and the saved
     connection list are unchanged; these add path discovery, a direction-aware
     neighbourhood inspector, and the derived/canonical boundary in plain words.
     ====================================================================== */

  function thoughtOptions(thoughts, selected) {
    return ['<option value="">— choose a thought —</option>']
      .concat(thoughts.map(t => `<option value="${attr(t.id)}" ${selected === t.id ? 'selected' : ''}>${esc(titleOf(t))}</option>`))
      .join('');
  }

  function connectionExplorerPanels(thoughts) {
    const graph = state.graph;
    return `<section class="panel explorer-panel">
        <div class="section-head" style="margin-top:0"><h2>Trace a path</h2><span class="section-meta">STORED RELATIONSHIPS ONLY</span></div>
        <p class="micro">IWTT walks relationships you actually saved. It will not invent an edge to close a gap, and it will tell you when no path exists.</p>
        <form id="path-form" class="path-form">
          <label>From <select id="path-source">${thoughtOptions(thoughts, graph.sourceId)}</select></label>
          <label>To <select id="path-target">${thoughtOptions(thoughts, graph.targetId)}</select></label>
          <span class="checkbox-control"><input type="checkbox" id="path-undirected"><label for="path-undirected">Ignore stored direction</label></span>
          <button class="button small" type="submit">Find path</button>
        </form>
        <div id="path-result">${graph.path ? renderPathResult(graph.path) : ''}</div>
      </section>
      <section class="panel explorer-panel">
        <div class="section-head" style="margin-top:0"><h2>Neighbourhood</h2><span class="section-meta">BOUNDED · DIRECTION-AWARE</span></div>
        <p class="micro">One thought, its immediate connections, the direction and wording of each edge, and whether a neighbour is a hub. Nothing here loads the whole graph.</p>
        <div class="path-form">
          <label>Thought <select id="neighbourhood-root">${thoughtOptions(thoughts, graph.rootId)}</select></label>
          <label>Depth <select id="neighbourhood-depth">${[1, 2].map(value => `<option value="${value}" ${graph.depth === value ? 'selected' : ''}>${value}</option>`).join('')}</select></label>
          <button class="button small" data-action="load-neighbourhood">Inspect</button>
        </div>
        <div id="neighbourhood-result">${graph.neighbourhood ? renderNeighbourhood(graph.neighbourhood) : ''}</div>
      </section>
      <section class="panel explorer-panel">
        <div class="section-head" style="margin-top:0"><h2>What is canonical here</h2><span class="section-meta">THE BOUNDARY</span></div>
        <ul class="honesty-list">
          <li><strong>Canonical</strong>: the thoughts, their original text, and every relationship you saved — including whether it was user-confirmed or AI-suggested, and when you confirmed it.</li>
          <li><strong>Derived</strong>: degrees, hub factors, clusters, path search order and every score. Recomputed from canonical data on demand; deleting them loses nothing.</li>
          <li><strong>Never</strong>: an AI-suggested connection does not become confirmed because you searched with it, opened it, or marked it useful. Only the Confirm button does that, and confirming keeps the AI_SUGGESTED origin on record.</li>
          <li><strong>Layout</strong>: node positions are computed in your browser and thrown away. No coordinate is stored, and none is used to infer meaning.</li>
        </ul>
      </section>`;
  }

  function renderPathResult(path) {
    if (!path.found) {
      return `<div class="recall-banner none"><strong>No path found</strong><p>${esc(path.reason || 'IWTT could not connect those two thoughts through saved relationships.')}</p>
        <p class="micro">Searched ${path.visited ?? 0} nodes within ${path.bounds?.maxDepth ?? 6} hops, following stored directions: ${path.directed ? 'yes' : 'no'}.</p></div>`;
    }
    const steps = (path.edges || []).map((edge, index) => {
      const from = (path.nodes || [])[index] || {};
      const to = (path.nodes || [])[index + 1] || {};
      return `<li class="path-step">
          <button class="connection-target" data-open-thought="${attr(from.id)}">${esc(from.displayTitle || from.id || '')}</button>
          <span class="path-edge ${edge.provenance === 'USER_CONFIRMED' ? 'confirmed' : 'suggested'}" title="${attr(edge.display || '')}">
            ${edge.direction === 'OUT' ? '—' : '←'} ${esc(edge.label || edge.type)} ${edge.direction === 'OUT' ? '→' : '—'}
          </span>
          <button class="connection-target" data-open-thought="${attr(to.id)}">${esc(to.displayTitle || to.id || '')}</button>
          <span class="micro">${esc(edge.display || '')}${edge.symmetric ? ' · symmetric' : ' · directional'}</span>
          ${(edge.evidenceNotes || []).map(note => `<small class="path-evidence-note">User note: ${esc(note.text || '')}</small>`).join('')}
        </li>`;
    }).join('');
    return `<div class="recall-banner strong"><strong>${esc(path.narrative || 'Path found')}</strong>
        <p class="micro">${path.length} hop${path.length === 1 ? '' : 's'} · ${path.visited ?? 0} nodes examined · direction respected: ${path.directed ? 'yes' : 'no'}</p></div>
      <ol class="path-list">${steps}</ol>`;
  }

  function renderNeighbourhood(data) {
    const levels = (data.levels || []).map((level, index) => `
      <div class="neighbourhood-level"><h4>Level ${index + 1}</h4><ul class="cluster-members">${level.map(item => `
        <li><button class="connection-target" data-open-thought="${attr(item.thoughtId)}">${esc(item.displayTitle)}</button>
        <span class="micro">${esc(item.direction === 'OUT' ? '→' : '←')} ${esc(item.label)} · ${esc(item.display)} · degree ${item.degree}${item.hubFactor < 1 ? ` · hub factor ${item.hubFactor}` : ''}</span></li>`).join('')}</ul></div>`).join('');
    return `<div class="recall-banner possible"><strong>${esc(data.root?.displayTitle || '')}</strong>
        <p class="micro">Degree ${data.degree} · hub factor ${data.hubFactor} · bounds ${esc(JSON.stringify(data.bounds || {}))}</p></div>${levels || '<p class="micro">No saved connections.</p>'}`;
  }

  /* ==================== v0.2 · actions & live input ==================== */

  async function recallFeedback(button) {
    const feedback = button.dataset.feedback;
    const thoughtId = button.dataset.id;
    const rank = Number(button.dataset.rank || 0) || null;
    await api('/api/recall/feedback', {
      method: 'POST',
      body: { query: state.recall.query, thoughtId, feedback, rank },
    });
    toast(feedback === 'USEFUL'
      ? 'Recorded as useful. This is derived, decaying memory — it never edits the thought.'
      : 'Recorded as not useful. Reset it any time from Clusters → query memory.');
  }

  async function loadBreakdown(button) {
    const thoughtId = button.dataset.id;
    const region = document.getElementById('recall-breakdown');
    if (state.recall.breakdownFor === thoughtId && state.recall.breakdown) {
      state.recall.breakdownFor = null;
      state.recall.breakdown = null;
      if (region) region.innerHTML = '';
      return;
    }
    state.recall.breakdownFor = thoughtId;
    state.recall.breakdown = null;
    if (region) region.innerHTML = '<div class="loading-state"><span class="loader"></span><p>Computing the breakdown…</p></div>';
    try {
      const { data } = await api(`/api/recall/diagnostics?q=${encodeURIComponent(state.recall.query)}&thought_id=${encodeURIComponent(thoughtId)}`);
      state.recall.breakdown = data;
      if (region) region.innerHTML = recallBreakdownPanel();
    } catch (error) {
      if (region) region.innerHTML = `<div class="empty-state"><div class="empty-icon">!</div><h3>Breakdown unavailable</h3><p>${esc(error.message)}</p></div>`;
    }
  }

  async function findGraphPath() {
    const source = document.getElementById('path-source')?.value || '';
    const target = document.getElementById('path-target')?.value || '';
    if (!source || !target) return toast('Choose both thoughts first.', 'error');
    if (source === target) return toast('A thought is trivially connected to itself.', 'error');
    const undirected = document.getElementById('path-undirected')?.checked ? 'true' : 'false';
    state.graph.sourceId = source;
    state.graph.targetId = target;
    const region = document.getElementById('path-result');
    if (region) region.innerHTML = '<div class="loading-state"><span class="loader"></span><p>Walking saved relationships…</p></div>';
    try {
      const { data } = await api(`/api/graph/path?source=${encodeURIComponent(source)}&target=${encodeURIComponent(target)}&undirected=${undirected}`);
      state.graph.path = data;
      if (region) region.innerHTML = renderPathResult(data);
    } catch (error) {
      if (region) region.innerHTML = `<p class="micro">${esc(error.message)}</p>`;
    }
  }

  async function loadNeighbourhood() {
    const root = document.getElementById('neighbourhood-root')?.value || '';
    const depth = Number(document.getElementById('neighbourhood-depth')?.value || 1);
    if (!root) return toast('Choose a thought first.', 'error');
    state.graph.rootId = root;
    state.graph.depth = depth;
    const region = document.getElementById('neighbourhood-result');
    if (region) region.innerHTML = '<div class="loading-state"><span class="loader"></span><p>Loading neighbourhood…</p></div>';
    try {
      const { data } = await api(`/api/graph/neighbourhood/${encodeURIComponent(root)}?depth=${depth}`);
      state.graph.neighbourhood = data;
      if (region) region.innerHTML = renderNeighbourhood(data);
    } catch (error) {
      if (region) region.innerHTML = `<p class="micro">${esc(error.message)}</p>`;
    }
  }

  function scheduleRecall() {
    window.clearTimeout(state.recall.timer);
    state.recall.timer = window.setTimeout(() => {
      state.recall.query = (document.getElementById('recall-input')?.value || '').trim();
      runRecall();
    }, 240);
  }

  function eventSummary(event) {
    const m = event.metadata || {};
    if (event.eventType === 'THOUGHT_CREATED' && m.captureSource?.kind === 'FILE_IMPORT') {
      return `Imported file captured Â· ${m.captureSource.fileName || 'source file'}`;
    }
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
      RELATIONSHIP_EVIDENCE_NOTE_ADDED: 'User-authored evidence note added to a connection',
      AI_SUMMARY_GENERATED: 'AI summary generated · derived data',
      AI_INTERPRETATION_GENERATED: 'AI interpretation generated · derived data',
      AI_LINK_ANALYSIS_GENERATED: 'AI link analysis generated · derived data',
      AI_SIMILARITY_GENERATED: 'Similarity analysis saved · derived data',
      AI_CONNECTION_DISCOVERY_GENERATED: 'Connection discovery saved · derived data',
      AI_THEME_ANALYSIS_GENERATED: 'Theme analysis saved · derived data',
      AI_CONTRADICTION_REVIEW_GENERATED: 'Contradiction review saved · derived data',
      AI_EVOLUTION_REVIEW_GENERATED: 'Evolution review saved · derived data',
      AI_CONTEXT_EXPLANATION_GENERATED: 'Contextual explanation saved · derived data',
    };
    return labels[event.eventType] || String(event.eventType || 'Event').replaceAll('_', ' ').toLowerCase();
  }

  const CANDIDATE_SOURCE_NAMES = {
    LEXICAL: 'shared words',
    GRAPH: 'a stored connection',
    COMMUNITY: 'the same community',
    TAG: 'a shared tag',
    LIFECYCLE: 'the same lifecycle',
    VAULT_CONTEXT: 'vault context',
  };

  function renderDerivedArtifact(artifact, thought) {
    const type = artifact.artifactType || '';
    const payload = artifact.payload || {};
    const head = '<div class="revision-card"><span class="derived-mark">AI-generated &middot; ' + esc(type.replaceAll('_', ' ')) + '</span>';
    const meta = '<div class="derived-meta">' + esc(artifact.provider) + (artifact.model ? ' &middot; ' + esc(artifact.model) : '') + ' &middot; ' + esc(date(artifact.generatedAt, true)) + ' &middot; Sources: ' + (artifact.sourceThoughtIds || []).length + ' thought</div></div>';

    if (type === 'AI_LINK_ANALYSIS') {
      const suggestions = payload.suggestions || [];
      const sources = payload.candidateSources || {};
      const sourceBreakdown = Object.keys(sources).length
        ? '<p class="revision-note">Candidates came from ' + Object.entries(sources).map(([key, count]) => esc((CANDIDATE_SOURCE_NAMES[key] || key) + ' (' + count + ')')).join(', ') + '.</p>'
        : '';
      const rows = suggestions.length
        ? '<div class="ai-connection-results">' + suggestions.map(item => '<article class="ai-connection-result"><div><strong>' + esc(String(item.type || '').replaceAll('_', ' ')) + ' <span>&rarr;</span> ' + esc(item.targetTitle || 'Thought') + '</strong><small>AI suggested</small><p>' + esc(item.reason || '') + '</p></div></article>').join('') + '</div>'
        : '<p class="revision-note">No link was proposed among ' + (payload.examinedCount || 0) + ' candidates read.</p>';
      return head + '<p class="derived-summary">' + (payload.linkedCount || 0) + ' possible link' + (payload.linkedCount === 1 ? '' : 's') + ' among ' + (payload.examinedCount || 0) + ' candidates read</p>' + sourceBreakdown + rows + '<p class="revision-note">' + esc(payload.boundary || 'A screening result, not proof.') + '</p>' + meta;
    }

    if (Object.keys(REASONING_ARTIFACT_LABELS).includes(type)) {
      const providerView = payload.providerView;
      return head + '<p class="derived-summary">' + esc((payload.summary || payload.note || '')) + '</p>' +
        renderReasoningFindings(payload) +
        (providerView ? '<div class="reasoning-provider-view"><span class="derived-mark">Provider interpretation · proposal only</span><p>' + esc(providerView.summary || '') + '</p>' + reasoningReferences(providerView) + '</div>' : '') +
        (payload.warnings?.length ? '<p class="revision-note">' + esc(payload.warnings.join(' ')) + '</p>' : '') +
        '<p class="revision-note">' + esc(REASONING_ARTIFACT_LABELS[type]) + ' · derived, erasable, and never a change to your original words.</p>' + meta;
    }

    if (type === 'AI_INTERPRETATION') {
      const columns = [['Concepts', payload.concepts], ['Open questions', payload.openQuestions], ['Possible next steps', payload.possibleNextSteps]]
        .map(([label, items]) => items?.length ? '<section><strong>' + esc(label) + '</strong><ul>' + items.map(item => '<li>' + esc(item) + '</li>').join('') + '</ul></section>' : '')
        .join('');
      return head + '<p class="derived-summary">' + esc(payload.summary || '') + '</p>' + (columns ? '<div class="interpretation-columns">' + columns + '</div>' : '') + '<button class="button small" data-action="capture-ai-interpretation" data-id="' + attr(thought.id) + '">Use as an editable idea draft</button>' + (payload.sourceTruncated ? '<small class="revision-note">Source exceeded the AI input limit; interpretation used a bounded excerpt.</small>' : '') + meta;
    }

    if (type === 'AI_SUMMARY') {
      return head + '<p class="derived-summary">' + esc(payload.summary || '') + '</p>' + meta;
    }

    // An unknown future artifact type. Render it as a readable key/value list
    // rather than a raw JSON blob, so the person who wrote the thought never
    // has to read machine output.
    const fallback = Object.keys(payload).length
      ? Object.entries(payload).map(([key, value]) => '<div class="revision-note"><strong>' + esc(key) + '</strong>: ' + esc(readableValue(value)) + '</div>').join('')
      : '<p class="revision-note">This derived artifact has no readable content.</p>';
    return head + fallback + meta;
  }

  // Kept inside the renderer block so the artifact renderer stays self-contained
  // for the static UI tests and for any future derived view that reuses it.
  const REASONING_ARTIFACT_LABELS = {
    'AI_SIMILARITY_ANALYSIS': 'Similar thoughts',
    'AI_CONNECTION_DISCOVERY': 'Possible connections',
    'AI_THEME_ANALYSIS': 'Recurring themes',
    'AI_CONTRADICTION_REVIEW': 'Possible tensions',
    'AI_EVOLUTION_REVIEW': 'Thinking evolution',
    'AI_CONTEXT_EXPLANATION': 'Contextual explanation',
  };

  function reasoningReferences(item) {
    const refs = [item.citation, item.source, item.target, item.left, item.right].filter(Boolean);
    if (!refs.length && Array.isArray(item.evidence)) refs.push(...item.evidence.slice(0, 4));
    if (!refs.length && Array.isArray(item.thoughtIds)) {
      refs.push(...item.thoughtIds.map(id => ({ thoughtId: id, title: id, excerpt: '' })));
    }
    if (!refs.length) return '';
    const unique = new Map();
    refs.forEach(ref => { if (ref?.thoughtId && !unique.has(ref.thoughtId)) unique.set(ref.thoughtId, ref); });
    return '<div class="reasoning-refs">' + [...unique.values()].slice(0, 4).map(ref =>
      '<button class="reasoning-ref" type="button" data-open-thought="' + attr(ref.thoughtId) + '">' +
      '<strong>' + esc(ref.title || 'Original thought') + '</strong>' +
      (ref.excerpt ? '<small>' + esc(String(ref.excerpt).slice(0, 120)) + '</small>' : '') +
      '</button>').join('') + '</div>';
  }

  function renderReasoningFindings(payload, limit = 8) {
    const findings = Array.isArray(payload.findings) ? payload.findings : [];
    if (!findings.length) return '<p class="revision-note">' + esc(payload.note || 'No findings were recorded.') + '</p>';
    return '<div class="reasoning-findings">' + findings.slice(0, limit).map(item => {
      const title = item.title || item.label || item.observation ||
        (item.kind ? String(item.kind).replaceAll('_', ' ') : 'Finding');
      const detail = item.reason || item.observation || item.caveat || item.note ||
        (Array.isArray(item.sharedTerms) ? item.sharedTerms.join(' · ') : '');
      const score = item.score !== undefined ? ' · ' + Math.round(item.score * 100) + '% match'
        : item.similarity !== undefined ? ' · ' + Math.round(item.similarity * 100) + '% semantic'
        : item.strength !== undefined ? ' · strength ' + Math.round(item.strength * 100) + '%' : '';
      return '<article class="reasoning-finding"><strong>' + esc(title) + '</strong>' +
        (score ? '<span class="reasoning-score">' + esc(score) + '</span>' : '') +
        (detail ? '<p>' + esc(detail) + '</p>' : '') +
        reasoningReferences(item) + '</article>';
    }).join('') + '</div>';
  }

  function readableValue(value) {
    if (value === null || value === undefined) return '';
    if (Array.isArray(value)) return value.map(readableValue).join(', ');
    if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}: ${readableValue(item)}`).join('; ');
    return String(value);
  }

  function renderAIConnectionsPanel(thoughtId, aiAvailable, compact = false) {
    const loading = state.aiConnections.loadingFor === thoughtId;
    const result = state.aiConnections.result?.thoughtId === thoughtId ? state.aiConnections.result : null;
    const results = result?.suggestions?.length ? `<div class="ai-connection-results">${result.suggestions.map((item, index) => `<article class="ai-connection-result"><div><strong>${esc(item.type.replaceAll('_', ' '))} <span>→</span> ${esc(item.targetTitle)}</strong><small>${esc(SOURCE_NAMES[item.targetSourceType] || 'Thought')} · ${item.targetStatus === 'GRAVEYARD' ? 'Graveyard' : 'Active'} · AI suggested${item.candidateReason ? ` · ${esc(item.candidateReason)}` : ''}</small><p>${esc(item.reason)}</p></div><button class="button small ${item.saved ? '' : 'primary'}" type="button" data-action="save-ai-relationship" data-id="${attr(thoughtId)}" data-index="${index}" ${item.saved ? 'disabled' : ''}>${item.saved ? 'Proposal saved' : 'Save proposal'}</button></article>`).join('')}</div>` : '';
    const sourceBreakdown = result && result.sources && Object.keys(result.sources).length
      ? `<p class="ai-connection-sources">Candidates came from ${Object.entries(result.sources).map(([key, count]) => `${esc(CANDIDATE_SOURCE_NAMES[key] || key)} (${count})`).join(', ')}.</p>`
      : '';
    const stateMessage = loading
      ? '<p class="ai-connection-status">Reading this thought and comparing it with local candidates…</p>'
      : result?.error
        ? `<p class="ai-connection-status error">${esc(result.error)}</p>`
        : result && !result.suggestions?.length
          ? `<p class="ai-connection-status">No likely link found among ${result.examinedCount} candidate thoughts${result.notLinkedCount ? ` (${result.notLinkedCount} read and judged unrelated)` : ''}. This is a screening result, not proof that no connection exists.</p>`
          : result
            ? `<p class="ai-connection-status">${result.suggestions.length} possible link${result.suggestions.length === 1 ? '' : 's'} found among ${result.examinedCount} candidates · ${esc(result.provider)}${result.model ? ` · ${esc(result.model)}` : ''}. Nothing is saved until you choose Save proposal.</p>`
            : '';
    return `<section class="ai-connection-panel ${compact ? 'compact' : ''}"><div class="ai-connection-heading"><div><span class="eyebrow">AI · REVIEWABLE SUGGESTIONS</span><strong>Find possible links</strong></div>${aiAvailable ? `<button class="button ${compact ? 'small' : ''}" type="button" data-action="ai-find-connections" data-id="${attr(thoughtId)}" ${loading ? 'disabled' : ''}>${loading ? 'Checking…' : result ? 'Check again' : 'Check connections'}</button>` : '<button class="button small" type="button" data-view="data">Configure provider</button>'}</div><p class="ai-connection-privacy">When requested, IWTT sends this thought and up to 16 locally selected candidates to your configured provider. Candidates are drawn from several structural sources, not only shared words, so a link that uses different vocabulary can still be found. The original captures remain unchanged.</p>${stateMessage}${sourceBreakdown}${results}</section>`;
  }

  async function findAIConnections(thoughtId) {
    state.aiConnections.loadingFor = thoughtId;
    state.aiConnections.result = null;
    await render();
    try {
      const { data } = await api(`/api/thoughts/${encodeURIComponent(thoughtId)}/ai/relationships`, { method: 'POST', body: {} });
      state.aiConnections.result = { thoughtId, ...data };
    } catch (error) {
      state.aiConnections.result = { thoughtId, error: error.message, suggestions: [], examinedCount: 0 };
    } finally {
      state.aiConnections.loadingFor = null;
      await render();
    }
  }

  async function compareAIThoughts(sourceId, targetId) {
    state.aiLinkReview.pairLoading = true;
    state.aiLinkReview.pair = null;
    await render();
    try {
      const { data } = await api('/api/ai/link-check/pair', { method: 'POST', body: { sourceId, targetId } });
      state.aiLinkReview.pair = data.verdict;
    } catch (error) {
      state.aiLinkReview.pair = { error: error.message, linked: false };
    } finally {
      state.aiLinkReview.pairLoading = false;
      await render();
    }
  }

  async function sweepAILinks(limit, perThought) {
    state.aiLinkReview.sweepLoading = true;
    state.aiLinkReview.sweep = null;
    await render();
    try {
      const { data } = await api('/api/ai/link-check/sweep', { method: 'POST', body: { limit, perThought } });
      state.aiLinkReview.sweep = data;
    } catch (error) {
      state.aiLinkReview.sweep = { error: error.message, results: [] };
    } finally {
      state.aiLinkReview.sweepLoading = false;
      await render();
    }
  }

  async function saveSweepRelationship(button) {
    const index = Number(button.dataset.index);
    const sweep = state.aiLinkReview.sweep;
    const proposal = sweep?.results?.[index];
    if (!proposal) return;
    button.disabled = true;
    try {
      await api('/api/relationships', { method: 'POST', body: {
        sourceId: proposal.sourceId, targetId: proposal.targetId,
        type: proposal.type, provenance: 'AI_SUGGESTED',
        metadata: { note: proposal.reason, aiProvider: sweep.provider, aiModel: sweep.model || null },
      } });
      proposal.saved = true;
      toast('Saved as an unconfirmed AI proposal. Review it before confirming.');
      await render();
    } catch (error) {
      button.disabled = false;
      toast(error.message, 'error');
    }
  }

  async function saveAIConnectionSuggestion(button) {
    const index = Number(button.dataset.index);
    const result = state.aiConnections.result;
    const suggestion = result?.thoughtId === button.dataset.id ? result.suggestions?.[index] : null;
    if (!suggestion || suggestion.saved) return;
    button.disabled = true;
    try {
      await api('/api/relationships', { method: 'POST', body: {
        sourceId: result.thoughtId, targetId: suggestion.targetId,
        type: suggestion.type, provenance: 'AI_SUGGESTED',
        metadata: { note: suggestion.reason, aiProvider: result.provider, aiModel: result.model || null },
      } });
      suggestion.saved = true;
      toast('Saved as an unconfirmed AI proposal. Review it before confirming.');
      await render();
    } catch (error) {
      button.disabled = false;
      toast(error.message, 'error');
    }
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
    const relationshipEvidenceMarkup = relationship => {
      const notes = Array.isArray(relationship.metadata?.evidenceNotes) ? relationship.metadata.evidenceNotes : [];
      const existing = relationship.metadata?.note
        ? `<div class="connection-evidence-note"><small>Connection note</small><p>${esc(relationship.metadata.note)}</p></div>` : '';
      const noteList = notes.map(note => `<div class="connection-evidence-note"><small>User-authored note · ${esc(date(note.createdAt, true))}</small><p>${esc(note.text || '')}</p></div>`).join('');
      const addForm = relationship.status !== 'REMOVED'
        ? `<form class="relationship-evidence-form" data-relationship-id="${attr(relationship.id)}"><textarea name="text" maxlength="2000" rows="2" placeholder="Add why you made this connection…" required></textarea><button class="button ghost small" type="submit">＋ Add evidence note</button></form>` : '';
      return `<div class="connection-evidence"><div class="connection-evidence-heading">Connection context · user-authored</div>${existing}${noteList || '<small class="connection-sub">No evidence notes yet. Add your reason or supporting context.</small>'}${addForm}</div>`;
    };
    const relationshipRows = connections.length ? connections.map(r => {
      const other = r.sourceId === t.id ? r.target : r.source;
      const isSuggested = r.status === 'SUGGESTED';
      const provenance = isSuggested ? 'AI suggestion · unconfirmed' : r.provenance === 'AI_SUGGESTED' ? 'AI-suggested · confirmed by you' : 'User-confirmed';
      return `<div class="connection-row"><div class="connection-row-main"><div><button class="connection-target" data-open-thought="${attr(other?.id || '')}">${esc(other?.title || 'Missing thought')}</button><div class="connection-sub">${esc(r.type.replaceAll('_', ' '))} · ${esc(provenance)}${r.status === 'REMOVED' ? ' · removed' : ''}</div></div>${r.status === 'REMOVED' ? '<span class="status-pill graveyard">Removed</span>' : `<span class="provenance-pill ${isSuggested ? 'suggested' : 'confirmed'}">${isSuggested ? 'Proposed' : 'Confirmed'}</span>`}</div>${relationshipEvidenceMarkup(r)}${r.status !== 'REMOVED' ? `<div class="connection-row-actions"><small>${esc(date(r.createdAt))}</small><span>${isSuggested ? `<button class="button small" data-action="confirm-relationship" data-id="${attr(r.id)}">Confirm</button>` : ''} <button class="button ghost small" data-action="remove-relationship" data-id="${attr(r.id)}">Remove</button></span></div>` : ''}</div>`;
    }).join('') : '<div class="empty-state" style="padding:18px"><h3>No connections yet</h3><p>Connect this thought to another when a relationship becomes useful.</p></div>';
    const artifacts = t.derivedArtifacts || [];
    const derivedContent = artifacts.length ? artifacts.map(a => renderDerivedArtifact(a, t)).join('') : '<p class="derived-empty">' + (ai.available ? 'No interpretation yet. The provider receives this thought only when you request interpretation.' : 'No AI is configured. IWTT still works fully; your thought stays local and no AI service is contacted.') + '</p>';
    const graveyardAction = t.status === 'GRAVEYARD'
      ? `<button class="button primary" data-action="resurrect" data-id="${attr(t.id)}">Resurrect</button>`
      : `<button class="button" data-action="graveyard" data-id="${attr(t.id)}">Move to Graveyard</button>`;
    return `<button class="detail-back" data-action="back-to-parent">← Back to ${esc(state.parentView === 'search' ? 'search' : state.parentView)}</button>
      <section class="detail-head"><div><span class="eyebrow">${t.isDemo ? 'FICTIONAL DEMO RECORD' : 'THOUGHT VAULT · ORIGINAL PRESERVED'}</span><h1>${esc(titleOf(t))}</h1><div class="detail-meta">${typePill(t.type)}${sourcePill(t.sourceType)}${statusPill(t.status)}${demoPill(t.isDemo)}<span>Captured ${esc(date(t.createdAt))}</span><span>·</span><span>Updated ${esc(date(t.updatedAt))}</span></div></div><div class="detail-actions">${graveyardAction}</div></section>
      <div class="detail-grid"><div class="detail-main-col">
        <section class="panel"><div class="panel-head"><div><span class="canonical-label">What I actually wrote</span><h2 style="margin-top:8px">Original capture</h2><p>This text is immutable and remains separate from later edits.</p></div></div><blockquote class="original-quote">${esc(t.originalText)}</blockquote><div class="original-caption">Captured ${esc(date(t.createdAt, true))} · ID ${esc(t.id)}</div></section>
        <section class="panel"><div class="panel-head"><div><span class="eyebrow">CANONICAL · USER-EDITABLE</span><h2 style="margin-top:6px">Current version</h2><p>Saving edits creates a new revision; it never changes the original capture.</p></div></div>
          <form id="thought-edit-form" class="edit-form"><div class="field"><label for="detail-title">Title</label><input id="detail-title" name="title" maxlength="200" value="${attr(t.title)}" placeholder="Optional title"></div><div class="field"><label for="detail-text">Current text</label><textarea id="detail-text" name="currentText" required>${esc(t.currentText)}</textarea></div><div class="field"><label for="detail-type">Lifecycle type</label><select id="detail-type" name="type">${Object.entries(TYPE_NAMES).map(([key, label]) => `<option value="${key}" ${t.type === key ? 'selected' : ''}>${label}</option>`).join('')}</select></div><div class="edit-actions"><span class="revision-note">${revisions.length} preserved version${revisions.length === 1 ? '' : 's'}</span><button class="button primary" type="submit">Save version</button></div></form>
        </section>
        <section class="panel"><div class="panel-head"><div><span class="eyebrow">TIME ARCHAEOLOGY</span><h2 style="margin-top:6px">History & revisions</h2><p>How this idea reached its current state.</p></div></div>
          <div class="history-list">${history.map(e => `<div class="history-item"><div class="history-rail"><span class="history-dot"></span></div><div class="history-content"><div class="history-title">${esc(eventSummary(e))}${e.isDemo ? '<span class="demo-ribbon">Demo</span>' : ''}<small>${esc(date(e.timestamp, true))}</small></div>${e.targetType === 'RELATIONSHIP' && e.metadata?.relationshipType ? `<div class="history-detail">${esc(e.metadata.relationshipType.replaceAll('_', ' '))} · ${esc(e.metadata.provenance || '')}${e.metadata.status ? ` · ${esc(e.metadata.status)}` : ''}</div>` : ''}${e.eventType === 'RELATIONSHIP_EVIDENCE_NOTE_ADDED' && e.metadata?.text ? `<div class="history-detail">User note: ${esc(e.metadata.text)}</div>` : ''}</div></div>`).join('')}</div>
          <div class="section-head" style="margin:20px 0 9px"><h2 style="font-size:16px">Saved snapshots</h2></div><div>${revisions.map(r => `<div class="revision-card"><div class="history-title">Version ${r.revisionNumber} · ${esc(TYPE_NAMES[r.type] || r.type)}<small>${esc(date(r.createdAt, true))} · ${esc(r.changeReason.replaceAll('_', ' ').toLowerCase())}</small></div>${r.title ? `<div class="history-detail">${esc(r.title)}</div>` : ''}<p>${esc(r.text)}</p></div>`).join('')}</div>
        </section>
      </div><div class="detail-side-col">
        <section class="panel"><div class="panel-head"><div><span class="eyebrow">YOUR INDEX</span><h2 style="margin-top:6px">Tags</h2></div><span class="section-meta">${(t.tags || []).length}</span></div><div class="tag-list">${(t.tags || []).map(tag => `<span class="tag-chip removeable">${esc(tag.name)}<button title="Remove tag" data-action="remove-tag" data-thought-id="${attr(t.id)}" data-tag-id="${attr(tag.id)}">×</button></span>`).join('') || '<span class="revision-note">No tags yet.</span>'}</div><form class="tag-form" id="add-tag-form"><input name="name" maxlength="40" placeholder="Add a tag" required><button class="button small" type="submit">Add</button></form></section>
        <section class="panel"><div class="panel-head"><div><span class="eyebrow">CANONICAL LINKS</span><h2 style="margin-top:6px">Connections</h2><p>AI proposals are not confirmed knowledge.</p></div><span class="section-meta">${connections.length}</span></div><div class="connection-list">${relationshipRows}</div><form class="relation-form" id="relationship-form"><select name="targetId" required class="wide"><option value="">Connect to a thought…</option>${relationOptions}</select><select name="type">${RELATION_TYPES.map(type => `<option value="${type}">${esc(type.replaceAll('_', ' '))}</option>`).join('')}</select><select name="provenance"><option value="USER_CONFIRMED">Confirmed by me</option><option value="AI_SUGGESTED">Unconfirmed AI suggestion</option></select><input class="wide" name="note" maxlength="300" placeholder="Optional note about this link"><span class="relation-help">Suggestions stay unconfirmed until you choose Confirm.</span><button class="button small" type="submit">＋ Add connection</button></form></section>
        ${renderAIConnectionsPanel(t.id, ai.available)}
        <section class="panel derived-panel"><div class="panel-head"><div><span class="derived-mark">Derived · rebuildable</span><h2 style="margin-top:7px">AI interpretation</h2><p>Never replaces your original words.</p></div></div>${derivedContent}<div class="data-actions"><button class="button primary small" data-action="ai-interpret" data-id="${attr(t.id)}" ${ai.available ? "" : "disabled"}>${ai.available ? "Interpret this thought" : "Configure AI in Data & privacy"}</button><button class="button small" data-action="ai-summary" data-id="${attr(t.id)}" ${ai.available ? '' : 'disabled'}>${ai.available ? 'Generate a summary' : 'No AI provider configured'}</button></div></section>
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

  function renderGraph(nodes, relationships, selectedId = '') {
    const width = 1200, height = 760, center = { x: width / 2, y: height / 2 };
    const byId = new Map(nodes.map(node => [node.id, node]));
    const parent = new Map(nodes.map(node => [node.id, node.id]));
    const find = id => { let root = id; while (parent.get(root) !== root) root = parent.get(root); while (parent.get(id) !== id) { const next = parent.get(id); parent.set(id, root); id = next; } return root; };
    const join = (a, b) => { let ra = find(a), rb = find(b); if (ra === rb) return; if (ra > rb) [ra, rb] = [rb, ra]; parent.set(rb, ra); };
    const edgeKey = (a, b) => a < b ? `${a}|${b}` : `${b}|${a}`;
    const canonicalPairs = new Set();
    relationships.forEach(edge => { if (byId.has(edge.source) && byId.has(edge.target)) { canonicalPairs.add(edgeKey(edge.source, edge.target)); join(edge.source, edge.target); } });
    const degree = new Map();
    const affinities = [];
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j], shared = (a.concepts || []).filter(term => (b.concepts || []).includes(term));
      if (shared.length < 2 || canonicalPairs.has(edgeKey(a.id, b.id))) continue;
      const unionSize = new Set([...(a.concepts || []), ...(b.concepts || [])]).size;
      const score = shared.length / Math.max(1, unionSize);
      if (score >= 0.12) affinities.push({ source: a.id, target: b.id, score });
    }
    affinities.sort((a, b) => b.score - a.score || a.source.localeCompare(b.source) || a.target.localeCompare(b.target));
    const derivedEdges = [];
    for (const edge of affinities) {
      const a = degree.get(edge.source) || 0, b = degree.get(edge.target) || 0;
      if (a >= 3 || b >= 3) continue;
      degree.set(edge.source, a + 1); degree.set(edge.target, b + 1);
      derivedEdges.push(edge); join(edge.source, edge.target);
    }
    const grouped = new Map();
    nodes.forEach(node => { const root = node.clusterId || find(node.id); if (!grouped.has(root)) grouped.set(root, []); grouped.get(root).push(node); });
    const communities = [...grouped.entries()].map(([id, members]) => {
      const ordered = members.sort((a, b) => a.id.localeCompare(b.id));
      const terms = new Map();
      ordered.forEach(node => { (node.concepts || []).forEach(term => terms.set(term, (terms.get(term) || 0) + 1)); });
      const label = [...terms.entries()].filter(([, count]) => count > 1).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 2).map(([term]) => term).join(' · ');
      return { id, members: ordered, label: label || ordered[0]?.clusterLabel || 'Unlinked concepts' };
    })
      .sort((a, b) => b.members.length - a.members.length || a.id.localeCompare(b.id));
    const fillById = new Map();
    const positions = new Map();
    const regionMarkup = [];
    communities.forEach((community, index) => {
      const color = community.members.length > 1 ? communityColor(community.id) : '#8d8797';
      const angle = -Math.PI / 2 + (Math.PI * 2 * index / Math.max(1, communities.length));
      const orbit = communities.length === 1 ? 0 : Math.min(250, 90 + communities.length * 7);
      const c = { x: center.x + Math.cos(angle) * orbit, y: center.y + Math.sin(angle) * orbit * 0.72 };
      const ring = community.members.length <= 1 ? 0 : Math.min(185, 62 + community.members.length * 17);
      community.members.forEach((node, i) => {
        const localAngle = -Math.PI / 2 + Math.PI * 2 * i / community.members.length;
        positions.set(node.id, { x: c.x + Math.cos(localAngle) * ring, y: c.y + Math.sin(localAngle) * ring * 0.78 });
        fillById.set(node.id, color);
      });
      if (community.members.length > 1) {
        const radius = Math.max(70, ring + 58);
        regionMarkup.push(`<ellipse cx="${c.x}" cy="${c.y}" rx="${radius * 1.24}" ry="${radius}" fill="${color}" fill-opacity=".02" stroke="${color}" stroke-opacity=".08" stroke-dasharray="3 7"/><text class="community-label" x="${c.x}" y="${c.y - radius - 8}">${esc(community.label.slice(0, 30))} · ${community.members.length}</text>`);
      }
    });
    const edgeMarkup = relationships.map(edge => {
      const a = positions.get(edge.source), b = positions.get(edge.target); if (!a || !b) return '';
      const css = edge.status === 'SUGGESTED' ? 'graph-edge suggested' : 'graph-edge';
      return `<g><line class="${css}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" marker-end="url(#graph-arrow)"/><title>${esc(edge.type)} · ${esc(edge.label || 'source to target')} · ${esc(edge.display || '')}</title></g>`;
    }).join('');
    const affinityMarkup = derivedEdges.map(edge => { const a = positions.get(edge.source), b = positions.get(edge.target); return `<line class="graph-edge affinity" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`; }).join('');
    const radius = nodes.length > 100 ? 19 : nodes.length > 50 ? 29 : 43;
    const nodeMarkup = nodes.map(node => {
      const p = positions.get(node.id), color = fillById.get(node.id), title = node.displayLabel || node.id;
      const lines = splitNodeLabel(title).slice(0, 2);
      const label = lines.map((line, index) => `<text x="${p.x}" y="${p.y + (index === 0 ? -2 : 10)}">${esc(line)}</text>`).join('');
      return `<g class="graph-node community-node ${node.status === 'GRAVEYARD' ? 'graveyard' : ''} ${node.id === selectedId ? 'selected' : ''}" data-action="select-graph-node" data-id="${attr(node.id)}" data-open-thought="${attr(node.id)}" tabindex="0" role="button" aria-pressed="${node.id === selectedId}" aria-label="Select ${attr(title)}; press Enter to open"><circle cx="${p.x}" cy="${p.y}" r="${radius}" style="--community-color:${color}"/><title>${esc(title)} · ${esc(SOURCE_NAMES[node.sourceType] || 'My thought')} · ${esc(node.lifecycleState || 'THOUGHT')} · ${esc(node.clusterLabel || 'Unlinked concept')}</title>${label}</g>`;
    }).join('');
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Clickable graph of thought communities"><defs><marker id="graph-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 z" fill="#9ba99c"/></marker></defs><g>${regionMarkup.join('')}${affinityMarkup}${edgeMarkup}${nodeMarkup}</g></svg>`;
  }

  function renderGraphInspector(thought, node, links, aiAvailable) {
    if (!thought || !node) return '<aside class="concept-inspector"><span class="eyebrow">CONCEPT INSPECTOR</span><h2>Select a concept</h2><p>Choose any node to inspect its original thought, source, lifecycle, and saved connections.</p></aside>';
    const linkRows = links.length ? links.map(({ edge, other, direction }) => `<a class="inspector-link" data-open-thought="${attr(other.id)}"><span class="inspector-link-arrow">${direction === 'out' ? '→' : '←'}</span><span><strong>${esc(edge.type.replaceAll('_', ' '))}</strong><small>${esc(direction === 'out' ? 'To' : 'From')} · ${esc(titleOf(other))}</small></span><em class="provenance-pill ${edge.status === 'SUGGESTED' ? 'suggested' : 'confirmed'}">${edge.status === 'SUGGESTED' ? 'Suggested' : edge.provenance === 'AI_SUGGESTED' ? 'AI confirmed' : 'Confirmed'}</em></a>`).join('') : '<p class="inspector-empty">No saved relationships. Dashed graph hints are derived and do not create links.</p>';
    return `<aside class="concept-inspector"><div class="inspector-heading"><span class="eyebrow">CONCEPT INSPECTOR</span><span class="inspector-community" style="--community-color:${communityColor(node.clusterId || node.clusterLabel || 'unlinked')}">${esc(node.clusterLabel || 'Unlinked concept')}</span></div><div class="inspector-badges">${sourcePill(node.sourceType)}${typePill(node.lifecycleState)}${statusPill(thought.status)}</div><h2>${esc(titleOf(thought))}</h2><time>${esc(date(thought.createdAt, true))}</time><p class="inspector-original">${esc(words(thought.originalText, 260))}</p><div class="tag-list">${tagPills((thought.tags || []).slice(0, 5))}</div><div class="inspector-section-head"><strong>Saved connections</strong><span>${links.length}</span></div><div class="inspector-links">${linkRows}</div>${renderAIConnectionsPanel(thought.id, aiAvailable, true)}<div class="inspector-actions"><button class="button small" data-action="ai-interpret" data-id="${attr(thought.id)}" ${aiAvailable ? '' : 'disabled'}>Interpret</button><button class="button primary small" data-open-thought="${attr(thought.id)}">Open original</button></div></aside>`;
  }

  function renderFileImport() {
    const file = state.importedFile;
    const extensions = '.txt,.text,.md,.markdown,.rst,.csv,.tsv,.json,.html,.htm,.xml,.yaml,.yml,.toml,.ini,.log,.py,.js,.jsx,.ts,.tsx,.css,.sql,.c,.h,.cpp,.java,.go,.rs,.sh,.ps1';
    return `<section class="view-heading"><div><span class="eyebrow">LOCAL SOURCE READING</span><h1>Bring a document into thought.</h1><p>Choose a text or source file. IWTT reads it in this browser, then saves its extracted text as an imported thought so you can search, revise, connect, and revisit it.</p></div></section>
      <section class="panel file-import-panel"><div class="file-drop"><span class="file-drop-icon">â†‘</span><strong>Bring a file into thought</strong><small>Text Â· Markdown Â· HTML Â· CSV Â· JSON Â· source code<br>2 MB file limit Â· up to 200,000 characters</small><small class="drop-hint">Drop a supported file here, or choose one below.</small><button class="button primary" type="button" data-action="choose-thought-file">Browse files</button><label class="file-input-label" for="thought-file-input">Or choose a file here</label><input id="thought-file-input" type="file" accept="${extensions}" aria-label="Choose a text or source-code file"></div>
        <p class="file-import-privacy">Read locally; no AI is used during import. PDF and Word extraction are not supported in this build.</p>
        ${file.error ? `<div class="recall-banner none"><strong>Could not read that file</strong><p>${esc(file.error)}</p></div>` : ''}
        ${file.text ? `<form id="file-import-form" class="file-import-preview"><div class="file-preview-head"><div><span class="eyebrow">READY TO PRESERVE</span><h2>${esc(file.name)}</h2><p>${file.text.length.toLocaleString()} characters Â· will be stored as the immutable original capture</p></div><label class="field">Thought title<input name="title" maxlength="200" value="${attr(file.name)}"></label></div><pre class="file-preview-text">${esc(file.text.slice(0, 6000))}${file.text.length > 6000 ? '\n\nâ€¦ preview shortened; the full text will be preserved.' : ''}</pre><div class="edit-actions"><button class="button ghost" type="button" data-action="clear-file-preview">Clear preview</button><button class="button primary" type="button" data-action="build-imported-thought">Build imported thought</button></div></form>` : ''}
      </section>`;
  }

  function renderLinkReviewPanel(allThoughts, aiAvailable) {
    if (!aiAvailable) {
      return `<section class="panel link-review-panel"><div class="panel-head"><div><span class="eyebrow">AI · LINK REVIEW</span><h2 style="margin-top:7px">Compare two thoughts, or sweep the vault</h2><p>Needs an AI provider. Configure one in Data & privacy; IWTT works fully without it.</p></div><button class="button small" type="button" data-view="data">Configure provider</button></div></section>`;
    }
    const options = allThoughts.map(thought => `<option value="${attr(thought.id)}">${esc(titleOf(thought))}</option>`).join('');
    const review = state.aiLinkReview;
    const pair = review.pair;
    const pairMarkup = pair
      ? (pair.error
        ? `<p class="ai-connection-status error">${esc(pair.error)}</p>`
        : pair.linked
          ? `<div class="recall-banner possible"><strong>Possible link · ${esc(String(pair.type || 'RELATED_TO').replaceAll('_', ' '))}</strong><p>${esc(pair.reason || '')}</p><p class="revision-note">A proposal only. Save it as a connection to keep it; it stays unconfirmed until you review it.</p></div>`
          : `<div class="recall-banner none"><strong>No link found</strong><p>${esc(pair.reason || '')}</p><p class="revision-note">The provider read both thoughts and did not find a defensible connection. That is a screening result, not proof that none exists.</p></div>`)
      : '';
    const sweep = review.sweep;
    const sweepMarkup = sweep
      ? (sweep.error
        ? `<p class="ai-connection-status error">${esc(sweep.error)}</p>`
        : `<div class="recall-banner ${sweep.results?.length ? 'possible' : 'none'}"><strong>${sweep.results?.length || 0} possible link${sweep.results?.length === 1 ? '' : 's'} across ${sweep.sourceCount} thought${sweep.sourceCount === 1 ? '' : 's'}</strong><p>${esc(sweep.note || '')}</p></div>${sweep.results?.length ? `<div class="ai-connection-results">${sweep.results.map((item, index) => `<article class="ai-connection-result"><div><strong>${esc(item.sourceTitle)} <span>→</span> ${esc(item.targetTitle)}</strong><small>${esc(String(item.type || '').replaceAll('_', ' '))} · AI suggested${item.candidateSource ? ` · ${esc(CANDIDATE_SOURCE_NAMES[item.candidateSource] || item.candidateSource)}` : ''}</small><p>${esc(item.reason)}</p></div><button class="button small ${item.saved ? '' : 'primary'}" type="button" data-action="save-sweep-relationship" data-index="${index}" ${item.saved ? 'disabled' : ''}>${item.saved ? 'Proposal saved' : 'Save proposal'}</button></article>`).join('')}</div>` : ''}`)
      : '';
    return `<section class="panel link-review-panel"><div class="panel-head"><div><span class="eyebrow">AI · LINK REVIEW</span><h2 style="margin-top:7px">Compare two thoughts, or sweep the vault</h2><p>Both actions read thoughts and report what the provider finds. Nothing is saved until you choose Save proposal.</p></div></div>
      <div class="link-review-columns">
        <div class="link-review-column"><strong>Compare two thoughts</strong><p class="revision-note">The first thought is the source; the direction of any proposal respects that order.</p><form id="ai-pair-form" class="link-review-form"><label class="field">Source thought<select name="sourceId" required>${options}</select></label><label class="field">Compare with<select name="targetId" required>${options}</select></label><div class="data-actions"><button class="button primary small" type="submit" ${review.pairLoading ? 'disabled' : ''}>${review.pairLoading ? 'Comparing…' : 'Compare'}</button></div></form>${pairMarkup}</div>
        <div class="link-review-column"><strong>Sweep the vault</strong><p class="revision-note">Checks the least-connected thoughts against a bounded candidate pool. This makes one provider request per thought, so keep the count low.</p><form id="ai-sweep-form" class="link-review-form"><label class="field">Thoughts to check<select name="limit"><option value="3">3</option><option value="6" selected>6</option><option value="10">10</option></select></label><label class="field">Candidates each<select name="perThought"><option value="4">4</option><option value="6" selected>6</option><option value="10">10</option></select></label><div class="data-actions"><button class="button primary small" type="submit" ${review.sweepLoading ? 'disabled' : ''}>${review.sweepLoading ? 'Sweeping…' : 'Sweep'}</button></div></form>${sweepMarkup}</div>
      </div></section>`;
  }

  async function renderConnections() {
    const [{ data: thoughtData }, { data: relData }, { data: graphData }] = await Promise.all([
      api('/api/thoughts?status=ALL'), api('/api/relationships?status=LIVE'), api('/api/graph/view?max_nodes=200&include_graveyard=true&include_suggested=true')
    ]);
    const allThoughts = thoughtData.thoughts;
    const allRelationships = relData.relationships;
    const allVisible = graphData.nodes || [];
    const communityMap = new Map();
    allVisible.forEach(node => { const key = node.clusterId || node.clusterLabel || 'unlinked'; const entry = communityMap.get(key) || { id: key, label: node.clusterLabel || 'Unlinked concepts', count: 0 }; entry.count++; communityMap.set(key, entry); });
    const communityChoices = [...communityMap.values()].sort((a,b) => b.count-a.count || a.label.localeCompare(b.label));
    const visible = state.graphCommunity === 'ALL' ? allVisible : allVisible.filter(node => (node.clusterId || node.clusterLabel || 'unlinked') === state.graphCommunity);
    const visibleIds = new Set(visible.map(node => node.id));
    const visibleEdges = (graphData.edges || []).filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target));
    const selectedId = visibleIds.has(state.graph.selectedId) ? state.graph.selectedId : (visible[0]?.id || '');
    state.graph.selectedId = selectedId || null;
    const selectedNode = visible.find(node => node.id === selectedId);
    const selectedThought = allThoughts.find(thought => thought.id === selectedId);
    const thoughtById = new Map(allThoughts.map(thought => [thought.id, thought]));
    const selectedLinks = visibleEdges.filter(edge => edge.source === selectedId || edge.target === selectedId).map(edge => ({
      edge, direction: edge.source === selectedId ? 'out' : 'in', other: thoughtById.get(edge.source === selectedId ? edge.target : edge.source),
    })).filter(link => link.other);
    const { data: aiStatus } = await api('/api/ai/status');
    const hiddenCount = Math.max(0, allThoughts.length - visible.length);
    return `<section class="view-heading"><div><span class="eyebrow">CONNECTION EXPLORER V2 · CANONICAL LINKS ONLY</span><h1>Connections between thoughts.</h1><p>Solid arrows show saved directional relationships; dashed links are local shared-concept hints. Click a concept to open its source thought. Positions and inferred links are never saved.</p></div><div class="heading-actions"><button class="button" data-view="clusters">Derived clusters</button><button class="button" data-view="vault">Browse vault</button></div></section>
      ${allThoughts.length ? `<div class="graph-workspace"><aside class="community-rail"><div class="community-rail-head"><div><strong>Communities</strong><small>DERIVED GROUPS</small></div><span>${communityChoices.length} total</span></div><button class="community-filter ${state.graphCommunity === 'ALL' ? 'selected' : ''}" data-action="graph-community" data-community="ALL"><i class="community-swatch all"></i><span>All concepts</span><small>${allVisible.length}</small></button>${communityChoices.map((community,index) => `<button class="community-filter ${state.graphCommunity === community.id ? 'selected' : ''}" data-action="graph-community" data-community="${attr(community.id)}"><i class="community-swatch" style="--community-color:${communityColor(community.id)}"></i><span>${esc(community.label)}</span><small>${community.count}</small></button>`).join('')}<p>Automatically discovered groups are derived; they do not change your saved thoughts.</p></aside><section class="panel graph-panel"><div class="graph-toolbar"><span><strong>${visible.length}</strong> concepts · <strong>${visibleEdges.length}</strong> stored links</span><small>Click any concept to inspect the original thought.</small></div><div class="graph-canvas">${renderGraph(visible, visibleEdges, selectedId)}</div><div class="graph-legend"><span><i class="legend-line"></i> Saved, directional relationship</span><span><i class="legend-line affinity"></i> Shared concepts · derived, not saved</span><span>Color regions = automatically detected communities</span></div>${hiddenCount ? `<p class="revision-note" style="padding:0 5px">Showing ${visible.length} of ${allThoughts.length} concepts (graph view cap). Use search or click a visible node to navigate.</p>` : ''}</section>${renderGraphInspector(selectedThought, selectedNode, selectedLinks, aiStatus.available)}</div>${renderLinkReviewPanel(allThoughts, aiStatus.available)}${connectionExplorerPanels(allThoughts)}
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
        <section class="data-card ai-config-card"><h2>AI provider — same app, optional</h2><p>Choose a provider for this session. <strong>Ollama</strong> runs locally and costs nothing; an <strong>OpenAI-compatible endpoint</strong> can be local or hosted; the <strong>mock</strong> is deterministic and offline, useful for trying AI features safely. IWTT sends selected thought text only after you explicitly invoke an AI action. Keys and configuration stay in memory until IWTT closes and are never placed in the vault export.</p><form id="ai-config-form" class="ai-config-form"><label class="field">Provider<select name="providerKind" id="ai-provider-kind"><option value="ollama" selected>Ollama — local, free (recommended)</option><option value="endpoint">OpenAI-compatible endpoint</option><option value="mock">Mock — deterministic, no network</option></select></label><div id="ai-endpoint-fields"><label class="field">API base URL<input name="baseUrl" type="text" maxlength="500" placeholder="http://127.0.0.1:11434/v1"></label><div class="ai-config-row"><label class="field">Model<input name="model" maxlength="120" list="ai-model-list" placeholder="e.g. llama3.1"></label><label class="field">API key<input name="apiKey" type="password" maxlength="1000" autocomplete="off" placeholder="Stored in memory only"></label></div><datalist id="ai-model-list"></datalist></div><div class="data-actions"><button class="button" type="button" data-action="ai-list-models">List models</button><button class="button primary" type="submit">Enable AI for this session</button><button class="button" type="button" data-action="disable-ai">Disconnect AI</button></div></form><span class="path-box">AI is optional. A provider receives thought text only when you request an AI action. Nothing is saved to the vault about the key.</span></section>
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

  async function saveImportedThought(trigger) {
    const form = trigger.closest('form');
    const submit = form?.querySelector('[type=submit]') || trigger;
    const title = String((form ? new FormData(form).get('title') : '') || state.importedFile.name).trim();
    if (!state.importedFile.name || !state.importedFile.text) {
      toast('Choose a readable file before building an imported thought.', 'error');
      return;
    }
    submit.disabled = true;
    const oldLabel = submit.textContent;
    submit.textContent = 'Building…';
    try {
      const { data } = await api('/api/files/import', { method: 'POST', body: { filename: state.importedFile.name, text: state.importedFile.text, title } });
      toast('File preserved as an imported thought. The source text is unchanged.');
      state.importedFile = { name: '', text: '', error: '' };
      await openThought(data.thought.id);
    } catch (error) {
      toast(`Could not build imported thought: ${error.message}`, 'error');
      submit.disabled = false;
      submit.textContent = oldLabel;
    }
  }

  async function performAction(action, button) {
    const id = button.dataset.id;
    if (action === 'reload') return render();
    if (action === 'clear-file-preview') { state.importedFile = { name: '', text: '', error: '' }; return render(); }
    if (action === 'back-to-parent') return navigate(state.parentView, { preserveQuery: state.parentView === 'search' });
    if (action === 'clear-search') { state.query = ''; return render(); }
    // ---- v0.2 actions ----
    if (action === 'clear-recall') {
      state.recall.query = ''; state.recall.outcome = null;
      state.recall.breakdown = null; state.recall.breakdownFor = null;
      return render();
    }
    if (action === 'recall-feedback') return recallFeedback(button);
    if (action === 'recall-breakdown') return loadBreakdown(button);
    // ---- v0.3 Thinking Partner: every action is explicit and derived ----
    if (action === 'partner-tool') {
      syncPartnerQuery();
      state.partner.tool = button.dataset.tool || 'ask';
      state.partner.result = null; state.partner.error = '';
      return render();
    }
    if (action === 'partner-prompt') {
      state.partner.query = button.dataset.prompt || '';
      return runPartner();
    }
    if (action === 'partner-followup') {
      state.partner.tool = button.dataset.tool || state.partner.tool;
      state.partner.query = button.dataset.prompt || '';
      return runPartner();
    }
    if (action === 'partner-select') {
      syncPartnerQuery();
      const selected = state.partner.selectedIds;
      const index = selected.indexOf(id);
      if (index >= 0) selected.splice(index, 1);
      else if (selected.length >= 20) { toast('Select at most 20 memories for one question.', 'error'); return; }
      else selected.push(id);
      state.partner.result = null; state.partner.error = '';
      return render();
    }
    if (action === 'partner-clear-selection') {
      syncPartnerQuery();
      state.partner.selectedIds = [];
      state.partner.result = null; state.partner.error = '';
      return render();
    }
    if (action === 'partner-clear') {
      state.partner.result = null; state.partner.error = ''; state.partner.query = '';
      return render();
    }
    if (action === 'partner-save') return savePartnerFinding();
    if (action === 'partner-save-connection') return savePartnerConnection(button);
    if (action === 'load-neighbourhood') return loadNeighbourhood();
    if (action === 'rebuild-clusters') {
      await api('/api/recall/index/rebuild', { method: 'POST', body: { full: true, rebuildClusters: true } });
      toast('Derived indexes and clusters recomputed from canonical data.');
      return render();
    }
    if (action === 'build-semantic-index') return buildSemanticIndex(button);
    if (action === 'erase-semantic-index') return eraseSemanticIndex();
    if (action === 'reset-learning') {
      if (!window.confirm('Delete every derived query-outcome signal? Your thoughts, connections and history are untouched.')) return;
      await api('/api/recall/learning', { method: 'DELETE' });
      toast('Query memory reset. Recall keeps working; it simply forgets past feedback.');
      return render();
    }
    if (action === 'vault-type') { state.vaultType = button.dataset.type; return render(); }
    if (action === 'graph-community') { state.graphCommunity = button.dataset.community || 'ALL'; return render(); }
    if (action === 'select-graph-node') { state.graph.selectedId = id; return render(); }
    if (action === 'ai-interpret') {
      await api(`/api/thoughts/${encodeURIComponent(id)}/ai/interpret`, { method: 'POST', body: {} });
      toast('Interpretation saved as derived information. Your original capture is unchanged.');
      return render();
    }
    if (action === 'ai-find-connections') return findAIConnections(id);
    if (action === 'save-ai-relationship') return saveAIConnectionSuggestion(button);
    if (action === 'save-sweep-relationship') return saveSweepRelationship(button);
    if (action === 'capture-ai-interpretation') {
      const { data } = await api(`/api/thoughts/${encodeURIComponent(id)}`);
      const thought = data.thought;
      const artifact = (thought.derivedArtifacts || []).find(item => item.artifactType === 'AI_INTERPRETATION');
      if (!artifact) throw new Error('Interpret this thought first.');
      const payload = artifact.payload || {};
      state.captureDraft = {
        sourceType: 'AI_INTERPRETATION', title: `Idea from ${titleOf(thought)}`,
        text: [payload.summary, payload.concepts?.length ? `Concepts: ${payload.concepts.join(', ')}` : '', payload.openQuestions?.length ? `Questions to explore:\n${payload.openQuestions.map(item => `• ${item}`).join('\n')}` : ''].filter(Boolean).join('\n\n'),
      };
      return navigate('capture');
    }
    if (action === 'disable-ai') {
      await api('/api/ai/configure', { method: 'POST', body: { enabled: false } });
      toast('AI disconnected for this session.');
      return render();
    }
    if (action === 'ai-list-models') {
      const form = button.closest('form');
      const values = new FormData(form);
      const kind = String(values.get('providerKind') || 'ollama');
      try {
        const { data } = await api('/api/ai/models', { method: 'POST', body: {
          provider: kind,
          baseUrl: values.get('baseUrl') || '',
          apiKey: values.get('apiKey') || '',
        } });
        const list = document.getElementById('ai-model-list');
        list.innerHTML = (data.models || []).map(model => `<option value="${attr(model)}"></option>`).join('');
        toast(`${data.count} models available at the endpoint.`);
      } catch (error) { toast(error.message, 'error'); }
      return;
    }
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
    if (action === 'choose-thought-file') return document.getElementById('thought-file-input')?.click();
    if (action === 'build-imported-thought') return saveImportedThought(button);
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
      if (actionButton.dataset.action === 'choose-thought-file') {
        const picker = document.getElementById('thought-file-input');
        if (picker) {
          try { if (typeof picker.showPicker === 'function') picker.showPicker(); else picker.click(); }
          catch (_) { picker.click(); }
        }
        return;
      }
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
      if (view === 'recall') state.recall.breakdown = null;
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
      // v0.2: ⌘K opens FIND MY THOUGHT (Recall V2).  The v0.1 lexical search view
      // is unchanged and still reachable from the sidebar as "Classic search".
      state.recall.query = document.getElementById('global-search-input').value.trim();
      state.view = 'recall';
      state.thoughtId = null;
      setTopbar();
      return render();
    }
    if (form.id === 'recall-form') {
      event.preventDefault();
      state.recall.query = (document.getElementById('recall-input')?.value || '').trim();
      return runRecall();
    }
    if (form.id === 'partner-form') {
      event.preventDefault();
      state.partner.query = (document.getElementById('partner-query')?.value || '').trim();
      return runPartner();
    }
    if (form.id === 'path-form') {
      event.preventDefault();
      return findGraphPath();
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
          type: formData.get('type') || 'THOUGHT', sourceType: formData.get('sourceType') || 'THOUGHT', tags,
        } });
        toast('Thought captured. Your original words are preserved.');
        state.view = 'capture';
        state.thoughtId = null;
        state.captureDraft = null;
        await render();
      } catch (error) { toast(error.message, 'error'); submit.disabled = false; }
      return;
    }
    if (form.id === 'ai-config-form') {
      event.preventDefault();
      const values = new FormData(form);
      const kind = String(values.get('providerKind') || 'ollama');
      const submit = form.querySelector('[type=submit]');
      submit.disabled = true;
      try {
        if (kind === 'mock') {
          await api('/api/ai/configure', { method: 'POST', body: { provider: 'mock' } });
          toast('Mock AI enabled for this session: deterministic and offline.');
        } else if (kind === 'ollama') {
          await api('/api/ai/configure', { method: 'POST', body: {
            provider: 'ollama',
            baseUrl: values.get('baseUrl') || '',
            model: values.get('model'),
          } });
          toast('Ollama enabled for this session. Models run locally and cost nothing.');
        } else {
          await api('/api/ai/configure', { method: 'POST', body: {
            provider: 'openai-compatible',
            baseUrl: values.get('baseUrl'), model: values.get('model'), apiKey: values.get('apiKey') || '',
          } });
          toast('AI enabled for this session. The key is not saved in the vault.');
        }
        await render();
      } catch (error) { toast(error.message, 'error'); submit.disabled = false; }
      return;
    }
    if (form.id === 'ai-pair-form') {
      event.preventDefault();
      const values = new FormData(form);
      const sourceId = String(values.get('sourceId') || '');
      const targetId = String(values.get('targetId') || '');
      if (!sourceId || !targetId) return;
      if (sourceId === targetId) {
        toast('Choose two different thoughts to compare.', 'error');
        return;
      }
      await compareAIThoughts(sourceId, targetId);
      return;
    }
    if (form.id === 'ai-sweep-form') {
      event.preventDefault();
      const values = new FormData(form);
      await sweepAILinks(Number(values.get('limit') || 6), Number(values.get('perThought') || 6));
      return;
    }
    if (form.id === 'file-import-form') {
      event.preventDefault();
      await saveImportedThought(form);
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
    if (form.classList.contains('relationship-evidence-form')) {
      event.preventDefault();
      const text = String(new FormData(form).get('text') || '').trim();
      try {
        await api(`/api/relationships/${encodeURIComponent(form.dataset.relationshipId)}/evidence-notes`, {
          method: 'POST', body: { text },
        });
        toast('Evidence note saved in connection history.');
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
    if (event.target.id === 'recall-input') scheduleRecall();
  });

  async function readThoughtFile(file) {
    if (!file) return;
    const supported = /\.(txt|text|md|markdown|rst|csv|tsv|json|html?|xml|ya?ml|toml|ini|log|py|jsx?|tsx?|css|sql|c|h|cpp|java|go|rs|sh|ps1)$/i.test(file.name);
    if (!supported || file.size > 2_000_000) {
      state.importedFile = { name: '', text: '', error: !supported ? 'This format is not supported. Choose a text, Markdown, data, HTML, note, or source-code file.' : 'This file is larger than the 2 MB limit.' };
      return render();
    }
    try {
      let text = await file.text();
      if (/\.html?$/i.test(file.name)) {
        const doc = new DOMParser().parseFromString(text, 'text/html');
        doc.querySelectorAll('script,style,noscript,template').forEach(node => node.remove());
        text = (doc.body?.innerText || doc.body?.textContent || '').trim();
      }
      if (!text.trim()) throw new Error('No readable text was found in this file.');
      if (text.length > 200_000) throw new Error('This file contains more than 200,000 characters.');
      state.importedFile = { name: file.name, text, error: '' };
    } catch (error) { state.importedFile = { name: '', text: '', error: error.message }; }
    return render();
  }

  app.addEventListener('dragenter', event => {
    const area = event.target.closest('.file-drop');
    if (!area) return;
    event.preventDefault();
    area.classList.add('is-dragover');
  });
  app.addEventListener('dragover', event => {
    if (!event.target.closest('.file-drop')) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  });
  app.addEventListener('dragleave', event => {
    const area = event.target.closest('.file-drop');
    if (area && !area.contains(event.relatedTarget)) area.classList.remove('is-dragover');
  });
  app.addEventListener('drop', event => {
    const area = event.target.closest('.file-drop');
    if (!area) return;
    event.preventDefault();
    area.classList.remove('is-dragover');
    const file = event.dataTransfer?.files?.[0];
    if (file) void readThoughtFile(file);
  });

  document.addEventListener('change', async event => {
    if (event.target.id === 'ai-provider-kind') {
      const fields = document.getElementById('ai-endpoint-fields');
      if (fields) fields.style.display = event.target.value === 'mock' ? 'none' : '';
      return;
    }
    if (event.target.id === 'thought-file-input' && event.target.files?.length) {
      return readThoughtFile(event.target.files[0]);
    }
    if (event.target.id === 'include-graveyard') {
      state.includeGraveyard = event.target.checked;
      return refreshSearch();
    }
    // ---- v0.2 Find My Thought filters ----
    if (event.target.id === 'recall-time') { state.recall.time = event.target.value; return runRecall(); }
    if (event.target.id === 'recall-type') { state.recall.type = event.target.value; return runRecall(); }
    if (event.target.id === 'recall-graveyard') {
      state.recall.includeGraveyard = event.target.checked;
      return runRecall();
    }
    if (event.target.id === 'partner-use-provider') {
      syncPartnerQuery();
      state.partner.useProvider = event.target.checked;
      state.partner.result = null;
      return render();
    }
    if (event.target.id === 'partner-include-graveyard') {
      syncPartnerQuery();
      state.partner.includeGraveyard = event.target.checked;
      state.partner.result = null;
      return render();
    }
    if (event.target.matches('[data-action="vault-source-type"]')) {
      state.vaultSourceType = event.target.value;
      return render();
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
    const params = new URLSearchParams(window.location.search);
    if (params.get('view') === 'search') {
      state.view = 'search';
      state.query = params.get('q') || '';
    } else if (['connections', 'import'].includes(params.get('view'))) {
      state.view = params.get('view');
    }
    await render();
  })();
})();
