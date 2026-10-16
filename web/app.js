/* IWTT v0.2 — local-first Thought Vault UI (Recall & Graph Intelligence) */
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
    },
    // v0.2 — Connection Explorer V2
    graph: { rootId: null, sourceId: '', targetId: '', path: null, neighbourhood: null, depth: 1 },
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
    const LABELS = { data: 'DATA & PRIVACY', recall: 'FIND MY THOUGHT', clusters: 'DERIVED CLUSTERS', connections: 'CONNECTION EXPLORER' };
    const label = state.view === 'detail' ? 'THOUGHT DETAIL' : (LABELS[state.view] || state.view.toUpperCase());
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
        case 'clusters': markup = await renderClusters(); break;
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
      if (region) region.innerHTML = recallResults(data);
      if (summary) summary.textContent = recallSummaryLine(data);
    } catch (error) {
      state.recall.loading = false;
      if (summary) summary.textContent = error.message;
      if (region) region.innerHTML = `<div class="empty-state"><div class="empty-icon">!</div><h3>Recall failed</h3><p>${esc(error.message)}</p></div>`;
    }
  }

  function recallSummaryLine(data) {
    const ms = data.timingsMs?.total;
    const parts = [
      `${data.resultCount} candidate${data.resultCount === 1 ? '' : 's'}`,
      `${data.candidatesExamined} examined`,
      data.stateLabel || data.state,
    ];
    if (typeof ms === 'number') parts.push(`${ms.toFixed(1)} ms`);
    parts.push('local · no AI · no network');
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

  function recallResults(outcome) {
    if (!outcome) return recallEmpty();
    const results = outcome.results || [];
    const cards = results.map(result => recallCard(result, outcome)).join('');
    return `${recallBanner(outcome)}
      ${results.length ? `<div class="section-head" style="margin-top:0"><h2>Ranked candidates</h2><span class="section-meta">ORIGINAL TEXT ALWAYS SHOWN</span></div><div class="recall-stack">${cards}</div>` : ''}
      <div id="recall-breakdown">${recallBreakdownPanel()}</div>
      <section class="panel honesty-panel">
        <div class="section-head" style="margin-top:0"><h2>How this answer was produced</h2><span class="section-meta">NO AI · NO NETWORK · NO EMBEDDINGS</span></div>
        <ul class="honesty-list">
          <li><strong>Retrieval</strong>: lexical tiers (exact › word form › prefix › substring), character trigrams, bounded fuzzy similarity, IDF weighting, coverage, titles, tags, time, lifecycle and saved connections.</li>
          <li><strong>Ranking</strong>: a deterministic weighted sum with a total-order tie-break. The same vault, query and configuration always produce the same order.</li>
          <li><strong>Not implemented</strong>: ${esc(outcome.honesty?.embeddings || 'no embeddings, no vector index, no semantic stage.')}</li>
          <li><strong>Graph expansion</strong>: enriches candidates only; it never replaces text retrieval, and it never invents a connection.</li>
        </ul>
        <details class="sub-details"><summary>Stage timings &amp; index state</summary><pre class="formula">${esc(JSON.stringify({ timingsMs: outcome.timingsMs, indexState: outcome.indexState, graph: outcome.graph?.expansion }, null, 2))}</pre></details>
      </section>`;
  }

  async function renderRecall() {
    let outcome = null;
    if (state.recall.query) {
      try { outcome = (await api(recallQuery())).data; } catch (error) { outcome = null; toast(error.message, 'error'); }
    }
    state.recall.outcome = outcome;
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
      <div id="recall-results">${recallResults(outcome)}</div>`;
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
    return `<section class="view-heading"><div><span class="eyebrow">CONNECTION EXPLORER V2 · CANONICAL LINKS ONLY</span><h1>Connections between thoughts.</h1><p>Every line comes from a relationship you saved. Trace a path, inspect one thought's neighbourhood, and see which parts are canonical and which are derived. Layout is temporary and calculated in your browser; it is not part of your thought data.</p></div><div class="heading-actions"><button class="button" data-view="clusters">Derived clusters</button><button class="button" data-view="vault">Browse vault</button></div></section>
      ${allThoughts.length ? connectionExplorerPanels(allThoughts) + `<section class="panel graph-panel"><div class="graph-canvas">${renderGraph(visible, allRelationships)}</div><div class="graph-legend"><span><i class="legend-dot"></i> Thought / Experiment</span><span><i class="legend-dot proj"></i> Invention / Project</span><span><i class="legend-line"></i> Confirmed by you</span><span><i class="legend-line dashed"></i> Unconfirmed suggestion</span><span>Click a node to inspect</span></div>${hiddenCount ? `<p class="revision-note" style="padding:0 5px">Showing the 16 most-connected/recent thoughts of ${allThoughts.length}. The graph is a browsing aid, not a complete Mind World.</p>` : ''}</section>
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
    // ---- v0.2 actions ----
    if (action === 'clear-recall') {
      state.recall.query = ''; state.recall.outcome = null;
      state.recall.breakdown = null; state.recall.breakdownFor = null;
      return render();
    }
    if (action === 'recall-feedback') return recallFeedback(button);
    if (action === 'recall-breakdown') return loadBreakdown(button);
    if (action === 'load-neighbourhood') return loadNeighbourhood();
    if (action === 'rebuild-clusters') {
      await api('/api/recall/index/rebuild', { method: 'POST', body: { full: true, rebuildClusters: true } });
      toast('Derived indexes and clusters recomputed from canonical data.');
      return render();
    }
    if (action === 'reset-learning') {
      if (!window.confirm('Delete every derived query-outcome signal? Your thoughts, connections and history are untouched.')) return;
      await api('/api/recall/learning', { method: 'DELETE' });
      toast('Query memory reset. Recall keeps working; it simply forgets past feedback.');
      return render();
    }
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
    if (event.target.id === 'recall-input') scheduleRecall();
  });

  document.addEventListener('change', async event => {
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
