/* ═══════════════════════════════════════════════════════════════
   robin cc — News App v3
   ═══════════════════════════════════════════════════════════════ */

// ── State ────────────────────────────────────────────────────
let ALL     = [];        // raw articles from server
let SHOWN   = [];        // filtered + sorted
let CAT     = 'all';
let SRC     = null;      // active source chip filter
let Q       = '';
let SORT    = 'new';
let VIEW    = 'grid';    // 'grid' | 'list'
let READER_OPEN      = false;
let CURRENT_IDX      = -1;   // index in SHOWN of open article
let CURRENT_SUMM     = null; // generated summary object
let CURRENT_REAL_IDX = -1;   // real index in ALL for API calls
let EDIT_MODE        = false; // briefing edit toggle
let TW_EDIT_MODE     = false; // twitter social edit toggle
let IG_EDIT_MODE     = false; // instagram social edit toggle
let ACTIVITY_MAP     = {};    // realIdx → {published_hocalwire, video_sent, headline, source_name, last_at}
let FEED_VISIBLE     = true;  // false when activity view is shown
let SELECTED_IMAGE   = null;  // URL chosen in the image picker (null = use article default)
let _hocalwirePreviewData = null; // stashed between preview fetch and confirm

// ── DOM ──────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const sidebar    = $('sidebar');
const sbBackdrop = $('sb-backdrop');
const gridEl     = $('grid');
const listEl     = $('list-view');
const emptyEl    = $('empty');
const skelEl     = $('skeleton');
const searchEl   = $('search');
const sortEl     = $('sort');
const fetchBtn   = $('btn-fetch');
const loadBtn    = $('btn-load');
const fetchSpinner = $('fetch-spinner');
const fetchLabel = $('fetch-label');
const tickerContent = $('ticker-content');
const toastStack = $('toast-stack');

// ── Category config ──────────────────────────────────────────
const CATS = {
  politics:      { cls:'c-politics',     emoji:'🏛', re:/election|government|parliament|minister|politics|president|prime minister|congress|senate|democrat|republican|modi|trump|biden/i },
  world:         { cls:'c-world',        emoji:'🌐', re:/international|foreign|global|war|peace|diplomacy|treaty|nato|united nations|conflict|military|ceasefire|sanctions/i },
  technology:    { cls:'c-technology',   emoji:'⚡', re:/\btech\b|technology|artificial intelligence|\bai\b|software|hardware|cyber|digital|startup|apple|google|meta|microsoft|openai|robot|machine learning/i },
  business:      { cls:'c-business',     emoji:'📈', re:/business|economy|market|stock|company|corporate|trade|finance|gdp|inflation|revenue|profit|tariff|federal reserve|central bank/i },
  sports:        { cls:'c-sports',       emoji:'🏆', re:/\bsport\b|football|cricket|tennis|olympic|championship|athlete|soccer|nba|nfl|ipl|formula one|f1\b|league|tournament/i },
  entertainment: { cls:'c-entertainment',emoji:'🎬', re:/entertainment|movie|music|celebrity|film|actor|actress|hollywood|bollywood|netflix|oscar|grammy|concert/i },
  health:        { cls:'c-health',       emoji:'🩺', re:/health|medical|disease|hospital|doctor|patient|medicine|covid|virus|cancer|vaccine|fda|who|pandemic|clinical/i },
  science:       { cls:'c-science',      emoji:'🔬', re:/\bscience\b|research study|scientist|discovery|space|climate|nasa|physics|biology|genome|experiment|astronomy/i },
};

function catOf(a) {
  const txt = `${a.heading||''} ${(a.story||'').slice(0,300)}`;
  for (const [k,v] of Object.entries(CATS)) if (v.re.test(txt)) return { key:k,...v };
  return { key:'news', cls:'c-news', emoji:'📰' };
}

// ── Helpers ──────────────────────────────────────────────────
function relTime(s) {
  if (!s) return 'Recent';
  try {
    const d = new Date(s), diff = Date.now() - d;
    if (diff < 60e3)  return 'Just now';
    if (diff < 3600e3)return `${Math.floor(diff/60e3)}m ago`;
    if (diff < 86400e3)return `${Math.floor(diff/3600e3)}h ago`;
    if (diff < 7*86400e3)return `${Math.floor(diff/86400e3)}d ago`;
    return d.toLocaleDateString('en-US',{month:'short',day:'numeric'});
  } catch { return 'Recent'; }
}

function wc(a) { return (a.word_count) || (a.story||'').split(/\s+/).filter(Boolean).length; }
function rt(n) { return `${Math.max(1,Math.ceil(n/200))} min`; }

function esc(s) {
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function stagger(el, i) {
  el.style.animationDelay = `${Math.min(i*35, 350)}ms`;
}

// ── Ticker ───────────────────────────────────────────────────
function buildTicker(articles) {
  if (!articles.length) return;
  const sep = `<span class="ticker-sep">◆</span>`;
  const inner = articles.slice(0,14).map(a=>`<span>${esc(a.heading||'')}</span>`).join(sep);
  tickerContent.innerHTML = inner + sep + inner;
}

// ── Badges & stats ────────────────────────────────────────────
function refreshMeta(articles) {
  const cnts = { all: articles.length };
  articles.forEach(a => { const k=catOf(a).key; cnts[k]=(cnts[k]||0)+1; });
  Object.keys({all:0,...CATS}).forEach(k=>{
    const el=$(`cnt-${k}`);
    if (el) el.textContent = cnts[k]||0;
  });

  // Animated stat bump
  const statsGrid = document.querySelector('.stats-grid');
  if (statsGrid) { statsGrid.classList.add('updating'); setTimeout(()=>statsGrid.classList.remove('updating'),600); }

  function setStatBump(id, val) {
    const el=$(id); if(!el) return;
    el.textContent = val;
    el.classList.remove('bump');
    void el.offsetWidth;
    el.classList.add('bump');
  }
  setStatBump('s-articles', articles.length);
  const srcs = new Set(articles.map(a=>a.source_name).filter(Boolean));
  setStatBump('s-sources', srcs.size||'—');
  setStatBump('s-updated', new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}));
  buildSourceChips([...srcs], articles);
}

function buildSourceChips(sources, articles) {
  const freq = {};
  (articles||ALL).forEach(a=>{ if(a.source_name) freq[a.source_name]=(freq[a.source_name]||0)+1; });
  const wrap = $('sources-chips');
  wrap.innerHTML = sources.slice(0,20).map(s=>`
    <button class="source-chip${SRC===s?' active':''}" data-src="${esc(s)}">
      ${esc(s)}<span class="source-chip-count">${freq[s]||''}</span>
    </button>
  `).join('');
  wrap.querySelectorAll('.source-chip').forEach(btn=>{
    btn.addEventListener('click',()=>{
      SRC = SRC===btn.dataset.src ? null : btn.dataset.src;
      wrap.querySelectorAll('.source-chip').forEach(b=>b.classList.toggle('active',b.dataset.src===SRC));
      applyFilters();
    });
  });
}

// ── Filter & sort ────────────────────────────────────────────
function applyFilters() {
  let list = ALL;

  if (CAT !== 'all') list = list.filter(a=>catOf(a).key===CAT);
  if (SRC)           list = list.filter(a=>a.source_name===SRC);
  if (Q) {
    const q=Q.toLowerCase();
    list = list.filter(a=>
      (a.heading||'').toLowerCase().includes(q)||
      (a.story||'').toLowerCase().includes(q)||
      (a.source_name||'').toLowerCase().includes(q)
    );
  }

  if (SORT==='old')  list=[...list].sort((a,b)=>new Date(a.publish_date||0)-new Date(b.publish_date||0));
  else if(SORT==='long') list=[...list].sort((a,b)=>wc(b)-wc(a));

  SHOWN = list;
  renderFeed();
}

// ── Render ───────────────────────────────────────────────────

// Category accent colours for no-image gradient cards
const CAT_COLORS = {
  politics:      ['#ef4444','#dc2626'],
  world:         ['#3b82f6','#2563eb'],
  technology:    ['#8b5cf6','#7c3aed'],
  business:      ['#10b981','#059669'],
  sports:        ['#f97316','#ea580c'],
  entertainment: ['#ec4899','#db2777'],
  health:        ['#06b6d4','#0891b2'],
  science:       ['#a78bfa','#7c3aed'],
  news:          ['#6366f1','#4f46e5'],
};

// Category emoji for no-image placeholder icon
const CAT_ICONS = {
  politics:'🏛', world:'🌐', technology:'⚡', business:'📈',
  sports:'🏆', entertainment:'🎬', health:'🩺', science:'🔬', news:'📰',
};

function cardHTML(a, i) {
  const cat    = catOf(a);
  const words  = wc(a);
  const img    = a.top_image || a.image_url || '';
  const date   = relTime(a.publish_date || a.scraped_at);
  const isHero = i === 0;
  const lede   = (a.story||'').replace(/#{1,3}\s/g,'').replace(/\*\*/g,'').slice(0, 220);
  const [c1] = CAT_COLORS[cat.key] || CAT_COLORS.news;

  // Build media layer
  let media;
  if (img) {
    media = `
      <div class="card-media" data-loaded="false">
        <img class="card-img" src="${esc(img)}" loading="lazy" alt=""
          onload="this.closest('.card-media').dataset.loaded='true'"
          onerror="this.closest('.card-media').classList.add('card-blank-fallback');this.remove()">
        <div class="card-overlay"></div>
      </div>`;
  } else {
    media = `
      <div class="card-media card-blank" style="background:linear-gradient(145deg,${c1}28 0%,rgba(9,9,11,.98) 65%),repeating-linear-gradient(-45deg,transparent,transparent 38px,rgba(255,255,255,.018) 38px,rgba(255,255,255,.018) 39px)">
        <div class="card-blank-icon">${CAT_ICONS[cat.key]||'📰'}</div>
        <div class="card-blank-wordmark">${esc((a.source_name||'').toUpperCase())}</div>
        <div class="card-overlay"></div>
      </div>`;
  }

  return `<article class="card${isHero?' card-hero':''}" data-cat="${cat.key}" onclick="openReader(${i})">
    ${media}
    <div class="card-badges">
      <span class="card-cat-badge ${cat.cls}">${cat.key}</span>
      <span class="card-rt-badge">${rt(words)}</span>
    </div>
    ${isHero ? `<span class="card-featured-label">Lead Story</span>` : ''}
    <div class="card-info">
      <div class="card-source-row">
        <span class="card-source-dot"></span>
        <span class="card-source">${esc(a.source_name||'Unknown')}</span>
        ${a.region ? `<span class="card-region">· ${esc(a.region)}</span>` : ''}
        <span class="card-date">${date}</span>
      </div>
      <h3 class="card-title">${esc(a.heading||'Untitled')}</h3>
      ${isHero && lede ? `<p class="card-lede">${esc(lede)}</p>` : ''}
    </div>
  </article>`;
}

function listCardHTML(a, i) {
  const cat    = catOf(a);
  const img    = a.top_image || a.image_url || '';
  const date   = relTime(a.publish_date || a.scraped_at);
  const abbr   = (a.source_name||'').slice(0,4).toUpperCase()||cat.key.slice(0,4).toUpperCase();
  const thumb  = img
    ? `<div class="list-thumb"><img src="${esc(img)}" loading="lazy"
         onerror="this.parentElement.className='list-thumb no-img-sm';this.parentElement.textContent='${abbr}'"></div>`
    : `<div class="list-thumb no-img-sm" style="font-size:9px;font-weight:800;letter-spacing:.1em;color:var(--ink4)">${abbr}</div>`;

  return `<div class="list-card" onclick="openReader(${i})">
    ${thumb}
    <div class="list-main">
      <div class="list-source">${esc(a.source_name||'Unknown')}${a.region?` <span style="color:var(--ink4);font-weight:400">· ${esc(a.region)}</span>`:''}</div>
      <div class="list-title">${esc(a.heading||'Untitled')}</div>
    </div>
    <div class="list-meta">
      <span class="list-date">${date}</span>
      <span class="list-badge ${cat.cls}">${cat.key}</span>
    </div>
  </div>`;
}

function renderFeed() {
  // Update feed header
  const hdrCount = $('feed-header-count');
  const hdrTitle = $('feed-header-title');
  const hdrDate  = $('feed-header-date');
  if (hdrCount) hdrCount.textContent = SHOWN.length ? `${SHOWN.length} stor${SHOWN.length===1?'y':'ies'}` : '';
  if (hdrTitle) {
    const activeCat = document.querySelector('.nav-btn.active[data-cat]');
    const label = activeCat ? (activeCat.textContent.trim().replace(/\d+/g,'').trim() || 'All Stories') : 'All Stories';
    hdrTitle.textContent = label;
  }
  if (hdrDate) {
    const now = new Date();
    hdrDate.textContent = now.toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric'});
  }

  if (!SHOWN.length) {
    gridEl.innerHTML=''; listEl.innerHTML='';
    gridEl.classList.add('hidden'); listEl.classList.add('hidden');
    emptyEl.style.display='';
    return;
  }
  emptyEl.style.display='none';

  if (VIEW==='grid') {
    listEl.classList.add('hidden');
    gridEl.classList.remove('hidden');
    gridEl.innerHTML = SHOWN.map((a,i)=>cardHTML(a,i)).join('');
    gridEl.querySelectorAll('.card').forEach((el,i)=>stagger(el,i));
  } else {
    gridEl.classList.add('hidden');
    listEl.classList.remove('hidden');
    listEl.innerHTML = SHOWN.map((a,i)=>listCardHTML(a,i)).join('');
    listEl.querySelectorAll('.list-card').forEach((el,i)=>stagger(el,i));
  }
}

// ── Reader ───────────────────────────────────────────────────
const readerOverlay = $('reader-overlay');
const readerScroll  = $('reader-scroll');
const readerProg    = $('reader-prog');

function openReader(i) {
  const a = SHOWN[i]; if (!a) return;
  const cat = catOf(a);
  const words = wc(a);

  CURRENT_IDX      = i;
  CURRENT_SUMM     = null;
  CURRENT_REAL_IDX = -1;
  EDIT_MODE        = false;
  TW_EDIT_MODE     = false;
  IG_EDIT_MODE     = false;

  // Header meta
  $('rh-meta').innerHTML = `
    <span class="rh-cat ${cat.cls}">${cat.key}</span>
    <span class="rh-source">${esc(a.source_name||'Unknown')}</span>
    <span class="rh-date">${relTime(a.publish_date||a.scraped_at)} · ${words} words · ${rt(words)} read</span>
  `;
  const extLink = $('reader-ext');
  if (a.source_url) { extLink.href=a.source_url; extLink.style.display=''; }
  else extLink.style.display='none';

  $('r-cat').textContent   = cat.key;
  $('r-cat').className     = `reader-cat-pill ${cat.cls}`;
  $('r-title').textContent = a.heading||'Untitled';

  const deck = a.sub_heading||a.meta_description||'';
  $('r-deck').textContent  = deck;
  $('r-deck').style.display = deck ? '' : 'none';

  $('r-byline').innerHTML = [
    a.authors?.length ? `<span>✍ ${esc(a.authors.join(', '))}</span>` : '',
    a.location||a.dateline ? `<span>📍 ${esc((a.location||a.dateline||'').replace(/\*+/g,'').trim())}</span>` : '',
    `<span>⏱ ${rt(words)} read</span>`,
    a.source_url ? `<a href="${esc(a.source_url)}" target="_blank" rel="noopener">↗ Original source</a>` : '',
  ].filter(Boolean).join('<span style="color:var(--border-hi)">·</span>');

  SELECTED_IMAGE = null;  // reset picker on new article open

  const img = a.top_image||a.image_url||'';
  $('r-image').innerHTML = `
    <div id="r-img-container" style="position:relative">
      ${img ? `<img id="r-scraped-img" src="${esc(img)}" alt="" loading="lazy" style="width:100%;border-radius:12px;max-height:400px;object-fit:cover">` : ''}
      <div id="r-ai-status" style="margin-top:6px;display:flex;align-items:center;gap:6px;font-size:11px;color:var(--ink4)">
        <span class="cov-spinner" style="width:9px;height:9px;border-width:1.5px;flex-shrink:0"></span>
        <span>Generating AI image with SD Turbo…</span>
      </div>
    </div>`;

  // Markdown → HTML
  const html = (a.story||'')
    .replace(/^#{2}\s+(.+)$/gm,'<h2>$1</h2>')
    .replace(/^#{3}\s+(.+)$/gm,'<h3>$1</h3>')
    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
    .replace(/\*(.+?)\*/g,'<em>$1</em>')
    .replace(/\n\n+/g,'</p><p>')
    .replace(/^/,'<p>').replace(/$/,'</p>');
  $('r-body').innerHTML = html;

  $('reader-raw').textContent = JSON.stringify(a,null,2);
  $('reader-raw').classList.add('hidden');

  // Reset coverage, summary, timeline, social panels
  $('coverage-section').innerHTML = coverageLoadingHTML();
  $('summary-section').innerHTML  = summaryLoadingHTML();
  $('timeline-section').innerHTML = timelineLoadingHTML();
  const tlOuter = $('timeline-outer');
  if (tlOuter) tlOuter.style.display = 'none';
  const twOuter = $('social-twitter-outer');
  const igOuter = $('social-instagram-outer');
  if (twOuter) twOuter.style.display = 'none';
  if (igOuter) igOuter.style.display = 'none';
  $('social-twitter-section').innerHTML  = '';
  $('social-instagram-section').innerHTML = '';
  const pb = $('publish-btn');
  if (pb) { pb.style.display = 'none'; pb.disabled = false; $('publish-label').textContent = 'Publish to Hocalwire'; $('publish-spinner').classList.add('hidden'); }

  readerScroll.scrollTop = 0;
  readerProg.style.width = '0%';

  readerOverlay.classList.add('open');
  document.body.style.overflow='hidden';
  READER_OPEN = true;

  // Use the stable server-side index stamped on load — never search via indexOf.
  const realIdx = (a._serverIdx !== undefined) ? a._serverIdx : ALL.indexOf(a);
  CURRENT_REAL_IDX = realIdx;

  // Show persistent published alert if this article was already pushed
  updatePublishedAlert(realIdx);

  if (realIdx >= 0) {
    loadCoverage(realIdx);
    loadAIImage(realIdx);
  } else {
    $('coverage-section').innerHTML = coverageEmptyHTML('Could not map to source index.');
    const s = $('r-ai-status'); if (s) s.remove();
  }
}

function updatePublishedAlert(realIdx) {
  const alertEl  = $('published-alert');
  const tagsEl   = $('pa-tags');
  const textEl   = $('published-alert-text');
  if (!alertEl) return;

  const entry = realIdx >= 0 ? ACTIVITY_MAP[realIdx] : null;
  if (!entry || (!entry.published_hocalwire && !entry.video_sent)) {
    alertEl.classList.add('hidden');
    return;
  }

  const parts = [];
  if (entry.published_hocalwire) parts.push('Hocalwire');
  if (entry.video_sent)          parts.push('Video Workflow');
  textEl.textContent = `Already pushed to: ${parts.join(' & ')}`;

  tagsEl.innerHTML = [
    entry.published_hocalwire ? `<span class="pa-tag hocalwire">Hocalwire</span>` : '',
    entry.video_sent          ? `<span class="pa-tag video">Video</span>` : '',
  ].join('');

  alertEl.classList.remove('hidden');
}

// ── Coverage panel ───────────────────────────────────────────
function coverageLoadingHTML() {
  return `<div class="coverage-loading">
    <span class="cov-spinner"></span>
    <span>Searching coverage across sources…</span>
  </div>`;
}
function coverageEmptyHTML(msg='No matching coverage found from other outlets.') {
  return `<div class="coverage-empty">${esc(msg)}</div>`;
}
function summaryLoadingHTML() {
  return `<div class="coverage-loading">
    <span class="cov-spinner"></span>
    <span>Generating AI briefing…</span>
  </div>`;
}
function timelineLoadingHTML() {
  return `<div class="coverage-loading">
    <span class="cov-spinner"></span>
    <span>Building event timeline…</span>
  </div>`;
}

async function loadCoverage(realIdx) {
  try {
    const res  = await fetch(`/api/articles/${realIdx}/coverage`);
    const data = await res.json();

    if (data.status !== 'success' || !data.coverage?.length) {
      $('coverage-section').innerHTML = coverageEmptyHTML();
    } else {
      const items = data.coverage;
      $('coverage-section').innerHTML = `
        <div class="coverage-header">
          <span class="coverage-count">${items.length} outlet${items.length!==1?'s':''} covering this story</span>
        </div>
        <ul class="coverage-list">
          ${items.map(art => `
            <li class="coverage-item">
              <div class="cov-source-row">
                <span class="cov-dot"></span>
                <span class="cov-source">${esc(art.source_name||'Unknown')}</span>
                ${art.region ? `<span class="cov-region">${esc(art.region)}</span>` : ''}
                <span class="cov-sim">${Math.round((art.similarity||0)*100)}% match</span>
              </div>
              <a class="cov-headline" href="${esc(art.source_url||'#')}" target="_blank" rel="noopener">
                ${esc(art.heading||'No headline')}
              </a>
            </li>
          `).join('')}
        </ul>`;
    }
    // Launch summary, timeline, and social reactions simultaneously
    generateSummary(realIdx);
    generateTimeline(realIdx);
    loadSocialReactions(realIdx);
  } catch(e) {
    $('coverage-section').innerHTML = coverageEmptyHTML(`Error: ${e.message}`);
    generateSummary(realIdx);
    generateTimeline(realIdx);
    loadSocialReactions(realIdx);
  }
}

// ── Body markdown → HTML (sections) ─────────────────────────
function bodyToHtml(raw) {
  const sections = [];
  let current = { heading: null, paras: [] };
  for (const line of raw.split('\n')) {
    const hMatch = line.match(/^##\s+(.+)/);
    if (hMatch) {
      if (current.paras.length || current.heading) sections.push(current);
      current = { heading: hMatch[1].trim(), paras: [] };
    } else {
      const trimmed = line.trim();
      // drop bare placeholder tokens the LLM sometimes emits literally
      const isPlaceholder = /^\*{0,2}(SUB|SUBTITLE|SUBHEADING|SUBHEAD|BYLINE|LOCATION|DATELINE|N\/A)\*{0,2}$/i.test(trimmed);
      if (trimmed && !isPlaceholder) current.paras.push(trimmed);
    }
  }
  if (current.paras.length || current.heading) sections.push(current);

  return sections.map(sec => {
    const parasHtml = sec.paras
      .join('\n')
      .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
      .replace(/\*(.+?)\*/g,'<em>$1</em>')
      .split(/\n+/).filter(Boolean)
      .map(p=>`<p class="sum-para" contenteditable="false">${p}</p>`).join('');
    if (sec.heading) {
      const headingClean = sec.heading.replace(/\*\*(.+?)\*\*/g,'$1').replace(/\*(.+?)\*/g,'$1');
      return `<div class="sum-section"><h3 class="sum-section-head" contenteditable="false">${esc(headingClean)}</h3>${parasHtml}</div>`;
    }
    return `<div class="sum-section sum-lede">${parasHtml}</div>`;
  }).join('');
}

// ── Edit mode toggle ─────────────────────────────────────────
function toggleEditMode() {
  EDIT_MODE = !EDIT_MODE;
  const editBtn = document.querySelector('.edit-briefing-btn');
  const titleEl = document.querySelector('.sum-title');
  const bodyEl  = document.querySelector('.sum-body');

  if (!editBtn || !titleEl || !bodyEl) return;

  if (EDIT_MODE) {
    // Enable editing
    titleEl.contentEditable = 'true';
    titleEl.classList.add('editing');
    bodyEl.querySelectorAll('.sum-section-head, .sum-para').forEach(el => {
      el.contentEditable = 'true';
      el.classList.add('editing');
    });
    editBtn.classList.add('active');
    editBtn.innerHTML = `
      <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M1 12l3-1 7-7-2-2-7 7-1 3z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9 2l2 2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
      Editing…`;
    titleEl.focus();
    toast('info', 'Briefing unlocked — click any text to edit');
  } else {
    // Lock editing
    titleEl.contentEditable = 'false';
    titleEl.classList.remove('editing');
    bodyEl.querySelectorAll('.sum-section-head, .sum-para').forEach(el => {
      el.contentEditable = 'false';
      el.classList.remove('editing');
    });
    editBtn.classList.remove('active');
    editBtn.innerHTML = `
      <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M9 1l3 3-7 7H2V8l7-7z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>
      Edit Story`;
    toast('ok', 'Edits saved — ready to download');
  }
}

// ── Category picker ──────────────────────────────────────────
const CATEGORIES = ['World','Technology','Politics','Sports','Business','Entertainment','Science','Health','Environment','Crime','Society','Comedy'];

function openCategoryPicker(btn) {
  // Remove any existing picker
  const existing = document.getElementById('cat-picker-popup');
  if (existing) { existing.remove(); return; }

  const popup = document.createElement('div');
  popup.id = 'cat-picker-popup';
  popup.className = 'cat-picker-popup';
  popup.innerHTML = CATEGORIES.map(c =>
    `<button class="cat-picker-item${btn.textContent.trim()===c?' active':''}" onclick="selectCategory(this,'${c}')">${c}</button>`
  ).join('');

  btn.parentNode.insertBefore(popup, btn.nextSibling);

  // Close on outside click
  setTimeout(() => {
    document.addEventListener('click', function handler(e) {
      if (!popup.contains(e.target) && e.target !== btn) {
        popup.remove();
        document.removeEventListener('click', handler);
      }
    });
  }, 0);
}

function selectCategory(_item, category) {
  const btn = document.querySelector('.sum-category-btn');
  if (btn) btn.textContent = category;
  if (CURRENT_SUMM) CURRENT_SUMM.category = category;
  const popup = document.getElementById('cat-picker-popup');
  if (popup) popup.remove();
}

// ── Collect edited content from DOM ─────────────────────────
function collectEditedContent() {
  const titleEl = document.querySelector('.sum-title');
  const title   = titleEl ? titleEl.textContent.trim() : '';

  let body = '';
  const sections = document.querySelectorAll('.sum-body .sum-section');
  sections.forEach(sec => {
    const head = sec.querySelector('.sum-section-head');
    if (head) body += `## ${head.textContent.trim()}\n\n`;
    sec.querySelectorAll('.sum-para').forEach(p => {
      const txt = p.textContent.trim();
      if (txt) body += txt + '\n\n';
    });
  });

  const catBtn  = document.querySelector('.sum-category-btn');
  const category = catBtn ? catBtn.textContent.trim() : (CURRENT_SUMM && CURRENT_SUMM.category) || 'World';

  return { edited_title: title, edited_body: body.trim(), category, selected_image: SELECTED_IMAGE || undefined };
}

async function generateSummary(realIdx) {
  try {
    const res  = await fetch(`/api/articles/${realIdx}/summary`, {method:'POST'});
    const data = await res.json();

    if (data.status !== 'success' || !data.summary) {
      $('summary-section').innerHTML = `<div class="coverage-empty">Error: ${esc(data.message||'Generation failed')}</div>`;
      return;
    }

    CURRENT_SUMM = data.summary;
    const s      = data.summary;

    const bylineRaw   = s.byline || '';
    const bylineParts = bylineRaw.split('|');
    const bylineDate  = (bylineParts[1]||'').trim() || new Date().toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'});

    const bodyHtml    = bodyToHtml(s.body || '');
    const locationStr = (s.location || '').replace(/\*+/g, '').trim().toUpperCase();

    const srcsHtml = (s.sources_list||[]).map((src,n)=>`
      <li class="sum-src-item">
        <span class="sum-src-num">${n+1}</span>
        <div class="sum-src-body">
          <span class="sum-src-name">${esc(src.name||'')}</span>
          ${src.headline?`<span class="sum-src-hl">— ${esc(src.headline.slice(0,90))}</span>`:''}
          ${src.url?`<a class="sum-src-url" href="${esc(src.url)}" target="_blank" rel="noopener">${esc(src.url.slice(0,60))}…</a>`:''}
        </div>
      </li>`).join('');

    $('summary-section').innerHTML = `
      <div class="summary-result">
        <div class="sum-masthead">
          <span class="sum-masthead-brand">robin cc</span>
          <span class="sum-masthead-sep">·</span>
          <span class="sum-masthead-date">${esc(bylineDate)}</span>
        </div>
        ${locationStr ? `<div class="sum-location"><svg width="11" height="11" viewBox="0 0 11 11" fill="none"><circle cx="5.5" cy="4.5" r="2" stroke="currentColor" stroke-width="1.2"/><path d="M5.5 1C3.57 1 2 2.57 2 4.5c0 2.72 3.5 5.5 3.5 5.5s3.5-2.78 3.5-5.5C9 2.57 7.43 1 5.5 1z" stroke="currentColor" stroke-width="1.2"/></svg>${esc(locationStr)}</div>` : ''}
        <h2 class="sum-title" contenteditable="false">${esc(s.title||'News Brief')}</h2>
        ${(()=>{ const st = (s.subtitle||'').replace(/\*+/g,'').replace(/^sub$/i,'').trim(); return st ? `<p class="sum-subtitle">${esc(st)}</p>` : ''; })()}
        <div class="sum-meta-row">
          <span class="sum-badge">AI BRIEFING</span>
          <button class="sum-category-btn" onclick="openCategoryPicker(this)" title="Change category">${esc(s.category||'World')}</button>
          <span class="sum-meta-sources">${(s.sources_list||[]).length} source${(s.sources_list||[]).length!==1?'s':''} synthesised</span>
        </div>
        <div class="sum-divider"></div>
        <div class="sum-body">${bodyHtml}</div>
        ${s.reporter ? `<div class="sum-reporter">Reported by <strong>${esc(s.reporter)}</strong></div>` : ''}
        <div class="sum-sources-block">
          <div class="sum-sources-title">Sources</div>
          <ol class="sum-src-list">${srcsHtml}</ol>
        </div>
        <div class="sum-actions">
          <button class="edit-briefing-btn" onclick="toggleEditMode()">
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M9 1l3 3-7 7H2V8l7-7z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>
            Edit Story
          </button>
          <button class="download-btn" onclick="downloadDocx(${realIdx})">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 1v8M4 6l3 3 3-3M2 11h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
            Download .docx
          </button>
          <span class="sum-note">Channel-ready · ${(s.sources_list||[]).length} source(s)</span>
        </div>
      </div>`;

    // Show the footer publish button now that a summary exists
    const pb = $('publish-btn');
    if (pb) pb.style.display = '';

    EDIT_MODE = false;
  } catch(e) {
    $('summary-section').innerHTML = `<div class="coverage-empty">Network error: ${esc(e.message)}</div>`;
  }
}

async function downloadDocx(realIdx) {
  // If in edit mode, lock first so user sees edits are captured
  if (EDIT_MODE) toggleEditMode();

  toast('info','Preparing your .docx…');
  try {
    const edited = collectEditedContent();
    const res = await fetch(`/api/articles/${realIdx}/download`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(edited),
    });
    if (!res.ok) {
      const err = await res.json().catch(()=>({message:'Download failed'}));
      toast('err', err.message||'Download failed');
      return;
    }
    const blob    = await res.blob();
    const url     = URL.createObjectURL(blob);
    const a       = document.createElement('a');
    const cd      = res.headers.get('Content-Disposition')||'';
    const fnMatch = cd.match(/filename="?([^"]+)"?/);
    a.href        = url;
    a.download    = fnMatch ? fnMatch[1] : 'robincc_summary.docx';
    a.click();
    URL.revokeObjectURL(url);
    toast('ok','Downloaded successfully!');
  } catch(e) {
    toast('err',`Download error: ${e.message}`);
  }
}

// ── Hocalwire publish ────────────────────────────────────────
async function publishToHocalwire() {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) { toast('err', 'No article selected'); return; }

  const pb      = $('publish-btn');
  const label   = $('publish-label');
  const spinner = $('publish-spinner');

  pb.disabled = true;
  label.textContent = 'Loading preview…';
  spinner.classList.remove('hidden');

  try {
    const edited = collectEditedContent();
    const res  = await fetch(`/api/articles/${realIdx}/preview-publish`, {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify(edited),
    });
    const data = await res.json().catch(() => ({}));

    pb.disabled = false;
    label.textContent = 'Publish to Hocalwire';
    spinner.classList.add('hidden');

    if (!res.ok || data.status !== 'success') {
      toast('err', data.message || 'Preview failed');
      return;
    }

    // Stash preview data for confirm step
    _hocalwirePreviewData = { realIdx, edited, previewResp: data };
    _openHocalwirePreview(data);

  } catch(e) {
    pb.disabled = false;
    label.textContent = 'Publish to Hocalwire';
    spinner.classList.add('hidden');
    toast('err', `Preview error: ${e.message}`);
  }
}

function _openHocalwirePreview(d) {
  const metaEl = $('hocal-preview-meta');
  const bodyEl = $('hocal-preview-body');

  const imgHtml = d.image_url
    ? `<img class="hocal-prev-img" src="${esc(d.image_url)}" alt="Article image" onerror="this.style.display='none'">`
    : '';

  metaEl.innerHTML = `
    <div class="hocal-prev-heading">${esc(d.heading)}</div>
    ${d.sub_heading ? `<div class="hocal-prev-subheading">${esc(d.sub_heading)}</div>` : ''}
    ${imgHtml}
    <div class="hocal-prev-tags">
      <span class="hocal-tag hocal-tag-loc">
        <svg width="10" height="10" viewBox="0 0 11 11" fill="none"><circle cx="5.5" cy="4.5" r="2" stroke="currentColor" stroke-width="1.2"/><path d="M5.5 1C3.57 1 2 2.57 2 4.5c0 2.72 3.5 5.5 3.5 5.5s3.5-2.78 3.5-5.5C9 2.57 7.43 1 5.5 1z" stroke="currentColor" stroke-width="1.2"/></svg>
        ${esc(d.location)}
      </span>
      <span class="hocal-tag">${esc(d.category)}</span>
      <span class="hocal-tag">${esc(d.language.toUpperCase())}</span>
      <span class="hocal-tag hocal-tag-type">${esc(d.news_type)}</span>
      ${d.reporter ? `<span class="hocal-tag hocal-tag-reporter">By ${esc(d.reporter)}</span>` : ''}
    </div>`;

  bodyEl.innerHTML = `
    <div class="hocal-prev-label">Story (as sent to Hocalwire)</div>
    <div class="hocal-prev-story">${d.html_story}</div>`;

  const overlay = $('hocal-preview-overlay');
  overlay.classList.add('open');
  $('hocal-confirm-btn').disabled = false;
  $('hocal-confirm-label').textContent = 'Confirm & Publish';
  $('hocal-confirm-spinner').classList.add('hidden');
}

function closeHocalwirePreview(e) {
  if (e && e.target !== $('hocal-preview-overlay')) return;
  $('hocal-preview-overlay').classList.remove('open');
}

async function confirmPublishToHocalwire() {
  const stored = _hocalwirePreviewData;
  if (!stored) return;

  const { realIdx, edited } = stored;
  const confirmBtn    = $('hocal-confirm-btn');
  const confirmLabel  = $('hocal-confirm-label');
  const confirmSpinner = $('hocal-confirm-spinner');

  confirmBtn.disabled = true;
  confirmLabel.textContent = 'Publishing…';
  confirmSpinner.classList.remove('hidden');

  try {
    const res  = await fetch(`/api/articles/${realIdx}/publish`, {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify(edited),
    });
    const data = await res.json().catch(() => ({}));

    if (res.ok && data.status === 'success') {
      // Show success screen inside modal, then auto-close
      const feedId   = data.feed_id || '';
      const modalEl  = $('hocal-preview-overlay').querySelector('.hocal-preview-modal');
      if (modalEl) {
        modalEl.innerHTML = `
          <div class="hocal-success-screen">
            <div class="hocal-success-icon">
              <svg width="40" height="40" viewBox="0 0 40 40" fill="none">
                <circle cx="20" cy="20" r="19" stroke="#22c55e" stroke-width="2"/>
                <path d="M12 20l6 6 10-12" stroke="#22c55e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </div>
            <div class="hocal-success-title">Published to Hocalwire</div>
            <div class="hocal-success-sub">Article is now live on the public feed${feedId ? ` · Feed ID: <strong>${esc(feedId)}</strong>` : ''}</div>
            <div class="hocal-success-closing">Closing in a moment…</div>
          </div>`;
      }

      // Update publish button
      const label = $('publish-label');
      if (label) label.textContent = '✓ Published';
      const pb = $('publish-btn');
      if (pb) pb.disabled = true;

      // Update activity tracking
      recordActivity(realIdx, {published_hocalwire: true});
      updatePublishedAlert(realIdx);
      refreshActivityCount();
      renderActivityView();

      // Close modal after 2.5 s
      setTimeout(() => {
        $('hocal-preview-overlay').classList.remove('open');
      }, 2500);
    } else {
      confirmBtn.disabled = false;
      confirmLabel.textContent = 'Confirm & Publish';
      confirmSpinner.classList.add('hidden');
      toast('err', data.message || 'Publish failed');
    }
  } catch(e) {
    confirmBtn.disabled = false;
    confirmLabel.textContent = 'Confirm & Publish';
    confirmSpinner.classList.add('hidden');
    toast('err', `Publish error: ${e.message}`);
  }
}

// ── Video workflow ────────────────────────────────────────────
function openVideoModal() {
  const overlay = $('video-modal-overlay');
  overlay.classList.add('open');
  // Re-validate the send button in case URL was previously set
  const input = $('video-endpoint');
  validateVideoEndpoint(input.value.trim());
}

function closeVideoModal(e) {
  if (e && e.target !== $('video-modal-overlay')) return;
  $('video-modal-overlay').classList.remove('open');
}

function validateVideoEndpoint(url) {
  const sendBtn = $('vmodal-send-btn');
  const banner  = $('vmodal-coming-banner');
  const valid   = url.startsWith('http://') || url.startsWith('https://');
  sendBtn.disabled = !valid;
  banner.classList.toggle('hidden', valid);
}

// Wire up live validation as user types
document.addEventListener('DOMContentLoaded', () => {
  const input = $('video-endpoint');
  if (input) {
    input.addEventListener('input', () => validateVideoEndpoint(input.value.trim()));
    // Close on Escape
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') $('video-modal-overlay').classList.remove('open');
    });
  }
});

async function sendVideoWorkflow() {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) { toast('err', 'No article selected'); return; }

  const webhookUrl = $('video-endpoint').value.trim();
  if (!webhookUrl) { toast('err', 'Enter a webhook URL first'); return; }

  const sendBtn = $('vmodal-send-btn');
  const label   = $('vmodal-send-label');
  const spinner = $('vmodal-spinner');

  sendBtn.disabled = true;
  label.textContent = 'Sending…';
  spinner.classList.remove('hidden');

  try {
    const res  = await fetch(`/api/articles/${realIdx}/video`, {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify({ webhook_url: webhookUrl }),
    });
    const data = await res.json().catch(() => ({}));

    if (res.ok && data.status === 'success') {
      toast('ok', 'Article sent to video workflow');
      $('video-modal-overlay').classList.remove('open');
      // Track locally and refresh alert
      recordActivity(realIdx, {video_sent: true});
      updatePublishedAlert(realIdx);
      refreshActivityCount();
    } else {
      toast('err', data.message || 'Video workflow failed');
    }
  } catch(e) {
    toast('err', `Network error: ${e.message}`);
  } finally {
    sendBtn.disabled = false;
    label.textContent = 'Send to Workflow';
    spinner.classList.add('hidden');
  }
}

// ── Timeline ─────────────────────────────────────────────────
async function generateTimeline(realIdx) {
  const tlOuter = $('timeline-outer');
  if (tlOuter) tlOuter.style.display = '';
  $('timeline-section').innerHTML = timelineLoadingHTML();

  try {
    const res  = await fetch(`/api/articles/${realIdx}/timeline`, {method:'POST'});
    const data = await res.json();

    if (data.status !== 'success' || !data.timeline?.length) {
      if (tlOuter) tlOuter.style.display = 'none';
      return;
    }

    renderTimeline(data.timeline);
  } catch(e) {
    if (tlOuter) tlOuter.style.display = 'none';
  }
}

function renderTimeline(events) {
  const tlOuter = $('timeline-outer');
  if (tlOuter) tlOuter.style.display = '';

  // Build node HTML
  const nodesHtml = events.map((ev, i) => `
    <div class="tl-node" data-index="${i}">
      <div class="tl-time" contenteditable="false">${esc(ev.time)}</div>
      <div class="tl-circle">
        <span class="tl-circle-inner"></span>
      </div>
      <div class="tl-event" contenteditable="false">${esc(ev.event)}</div>
    </div>
    ${i < events.length - 1 ? '<div class="tl-connector"></div>' : ''}
  `).join('');

  $('timeline-section').innerHTML = `
    <div class="tl-wrap">
      <div class="tl-rail" id="tl-rail">${nodesHtml}</div>
    </div>
    <div class="tl-actions">
      <button class="edit-briefing-btn tl-edit-btn" id="tl-edit-btn" onclick="toggleTimelineEdit()">
        <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M9 1l3 3-7 7H2V8l7-7z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>
        Edit Timeline
      </button>
    </div>`;
}

let TL_EDIT_MODE = false;
function toggleTimelineEdit() {
  TL_EDIT_MODE = !TL_EDIT_MODE;
  const btn = $('tl-edit-btn');
  const nodes = document.querySelectorAll('.tl-time, .tl-event');

  nodes.forEach(el => {
    el.contentEditable = TL_EDIT_MODE ? 'true' : 'false';
    el.classList.toggle('editing', TL_EDIT_MODE);
  });

  if (btn) {
    btn.classList.toggle('active', TL_EDIT_MODE);
    btn.innerHTML = TL_EDIT_MODE
      ? `<svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M1 12l3-1 7-7-2-2-7 7-1 3z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9 2l2 2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg> Editing…`
      : `<svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M9 1l3 3-7 7H2V8l7-7z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg> Edit Timeline`;
    if (TL_EDIT_MODE) toast('info', 'Timeline unlocked — click any label to edit');
    else toast('ok', 'Timeline locked');
  }
}

// ── Social Reactions ─────────────────────────────────────────
async function loadSocialReactions(realIdx) {
  const twOuter = $('social-twitter-outer');
  const igOuter = $('social-instagram-outer');
  const twSec   = $('social-twitter-section');
  const igSec   = $('social-instagram-section');

  if (twOuter) twOuter.style.display = '';
  if (igOuter) igOuter.style.display = '';

  const loadHTML = `<div class="coverage-loading"><span class="cov-spinner"></span><span>Fetching social reactions… (may take ~2 min)</span></div>`;
  twSec.innerHTML = loadHTML;
  igSec.innerHTML = loadHTML;

  try {
    const res  = await fetch(`/api/articles/${realIdx}/social`, {method:'POST'});
    const data = await res.json();

    if (data.status !== 'success' || !data.social) {
      const msg = esc(data.message || 'No social data available');
      twSec.innerHTML = `<div class="coverage-empty">${msg}</div>`;
      igSec.innerHTML = `<div class="coverage-empty">${msg}</div>`;
      return;
    }

    renderSocialSection('twitter',   data.social.twitter,   twSec,  twOuter,  realIdx);
    renderSocialSection('instagram', data.social.instagram, igSec,  igOuter,  realIdx);
  } catch(e) {
    twSec.innerHTML = `<div class="coverage-empty">Error: ${esc(e.message)}</div>`;
    igSec.innerHTML = `<div class="coverage-empty">Error: ${esc(e.message)}</div>`;
  }
}

function renderSocialSection(platform, data, secEl, outerEl, _realIdx) {
  if (!data || !data.posts?.length) {
    if (outerEl) outerEl.style.display = 'none';
    return;
  }

  const posts = data.posts;
  const isTw  = platform === 'twitter';
  const editId = `${platform}-edit-btn`;

  const postsHTML = posts.map(p => {
    const url    = esc(p.post_url || '#');
    const author = esc(isTw ? `@${p.username||p.author}` : `@${p.author}`);
    const text   = esc(isTw ? (p.text||'') : (p.caption||''));
    const stats  = isTw
      ? `<span class="soc-stat">❤ ${p.likes||0}</span><span class="soc-stat">🔁 ${p.reposts||0}</span><span class="soc-stat">💬 ${p.replies||0}</span>`
      : `<span class="soc-stat">❤ ${p.likes||0}</span><span class="soc-stat">💬 ${p.comments_count||0}</span>`;

    const commentsHTML = !isTw && p.comments?.length
      ? `<ul class="soc-comments">${p.comments.map(c=>`<li class="soc-comment"><span class="soc-comment-author">@${esc(c.author)}</span> ${esc(c.text)}</li>`).join('')}</ul>`
      : '';

    const hasUrl = url && url !== '#' && url !== '';
    return `
      <div class="soc-post">
        <div class="soc-post-header">
          ${hasUrl
            ? `<a class="soc-author soc-author-link" href="${url}" target="_blank" rel="noopener">${author}</a>`
            : `<span class="soc-author">${author}</span>`}
          <div class="soc-stats">${stats}</div>
          ${hasUrl ? `<a class="soc-link" href="${url}" target="_blank" rel="noopener">↗ Open</a>` : ''}
        </div>
        ${hasUrl
          ? `<a class="soc-text-link" href="${url}" target="_blank" rel="noopener"><p class="soc-text" contenteditable="false">${text}</p></a>`
          : `<p class="soc-text" contenteditable="false">${text}</p>`}
        ${commentsHTML}
      </div>`;
  }).join('');

  const summaryHTML = `<div class="soc-summary" id="${platform}-summary-body">${
    (data.summary||'')
      .replace(/^#{2}\s+(.+)$/gm,'<h3 class="soc-sum-head" contenteditable="false">$1</h3>')
      .replace(/^#{3}\s+(.+)$/gm,'<h4 class="soc-sum-head" contenteditable="false">$1</h4>')
      .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
      .replace(/\*(.+?)\*/g,'<em>$1</em>')
      .split(/\n+/).filter(Boolean)
      .map(l => l.startsWith('<h') ? l : `<p class="soc-sum-para" contenteditable="false">${l}</p>`)
      .join('')
  }</div>`;

  secEl.innerHTML = `
    <div class="social-result">
      <div class="soc-ai-block">
        <div class="soc-ai-label">AI Analysis</div>
        ${summaryHTML}
      </div>
      <div class="soc-posts-block">
        <div class="soc-posts-label">${posts.length} post${posts.length!==1?'s':''} found</div>
        <div class="soc-posts-list">${postsHTML}</div>
      </div>
      <div class="soc-actions">
        <button class="edit-briefing-btn" id="${editId}" onclick="toggleSocialEdit('${platform}')">
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M9 1l3 3-7 7H2V8l7-7z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>
          Edit
        </button>
      </div>
    </div>`;
}

function toggleSocialEdit(platform) {
  const isTw   = platform === 'twitter';
  const isOn   = isTw ? !TW_EDIT_MODE : !IG_EDIT_MODE;
  if (isTw) TW_EDIT_MODE = isOn; else IG_EDIT_MODE = isOn;

  const btn    = $(`${platform}-edit-btn`);
  const body   = $(`${platform}-summary-body`);
  if (!btn || !body) return;

  body.querySelectorAll('.soc-sum-head, .soc-sum-para, .soc-text').forEach(el => {
    el.contentEditable = isOn ? 'true' : 'false';
    el.classList.toggle('editing', isOn);
  });

  btn.classList.toggle('active', isOn);
  btn.innerHTML = isOn
    ? `<svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M1 12l3-1 7-7-2-2-7 7-1 3z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9 2l2 2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg> Editing…`
    : `<svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M9 1l3 3-7 7H2V8l7-7z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg> Edit`;

  if (isOn) toast('info', `${isTw ? 'Twitter' : 'Instagram'} reactions unlocked — click any text to edit`);
  else toast('ok', 'Edits saved');
}

// ── Image picker helper ──────────────────────────────────────
function selectImage(url, type) {
  SELECTED_IMAGE = url;
  document.querySelectorAll('.img-pick-option').forEach(el => {
    const isThis = el.dataset.type === type;
    el.classList.toggle('img-pick-selected', isThis);
    el.querySelector('.img-pick-radio').classList.toggle('img-pick-radio-on', isThis);
  });
}

// ── AI Image loader ──────────────────────────────────────────
async function loadAIImage(realIdx) {
  const myIdx = realIdx;
  try {
    const res  = await fetch(`/api/articles/${realIdx}/image`, {method: 'POST'});
    const data = await res.json();

    if (CURRENT_REAL_IDX !== myIdx) return;

    const statusEl = $('r-ai-status');
    if (statusEl) statusEl.remove();

    if (data.status === 'success' && data.image_url) {
      const container = $('r-img-container');
      if (!container) return;

      const scrapedImg = $('r-scraped-img');
      const scrapedUrl = scrapedImg ? scrapedImg.src : '';

      // Default selection: AI image
      SELECTED_IMAGE = data.image_url;

      // Build picker
      const picker = document.createElement('div');
      picker.id = 'img-picker';
      picker.className = 'img-picker';

      const aiOpt = _makePickOption('ai', 'AI Generated', 'SD Turbo', data.image_url, true);
      picker.appendChild(aiOpt);

      if (scrapedUrl) {
        const scrapedOpt = _makePickOption('original', 'Original Photo', 'Scraped', scrapedUrl, false);
        picker.appendChild(scrapedOpt);
      }

      // Replace old image area with picker
      if (scrapedImg) scrapedImg.remove();
      container.insertBefore(picker, container.firstChild);
    }
  } catch(_e) {
    const statusEl = $('r-ai-status');
    if (statusEl) statusEl.remove();
  }
}

function _makePickOption(type, label, sublabel, imgUrl, selected) {
  const opt = document.createElement('div');
  opt.className = 'img-pick-option' + (selected ? ' img-pick-selected' : '');
  opt.dataset.type = type;
  opt.onclick = () => selectImage(imgUrl, type);
  opt.innerHTML = `
    <div class="img-pick-thumb-wrap">
      <img class="img-pick-thumb" src="${esc(imgUrl)}" alt="" loading="lazy">
      <span class="img-pick-badge">${esc(sublabel)}</span>
    </div>
    <div class="img-pick-footer">
      <span class="img-pick-radio${selected ? ' img-pick-radio-on' : ''}"></span>
      <span class="img-pick-label">${esc(label)}</span>
      ${selected ? '<span class="img-pick-check">✓ Selected for publish</span>' : ''}
    </div>`;
  return opt;
}

function closeReader() {
  readerOverlay.classList.remove('open');
  document.body.style.overflow='';
  READER_OPEN = false;
}

function toggleRawJson() { $('reader-raw').classList.toggle('hidden'); }

readerScroll.addEventListener('scroll',()=>{
  const {scrollTop,scrollHeight,clientHeight} = readerScroll;
  const pct = scrollHeight-clientHeight>0 ? scrollTop/(scrollHeight-clientHeight)*100 : 0;
  readerProg.style.width = Math.min(100,pct)+'%';
},{passive:true});

// ── Scraping ─────────────────────────────────────────────────
function setBusy(busy) {
  fetchBtn.disabled = busy;
  fetchSpinner.classList.toggle('hidden',!busy);
  fetchLabel.textContent = busy ? 'Fetching…' : 'Fetch Latest';
}

async function startFetch() {
  setBusy(true);
  showSkeleton(true);
  toast('info','Connecting to news sources…');

  try {
    const res  = await fetch('/api/scrape',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({max_articles:15}),
    });
    const data = await res.json();
    if (data.status==='started'||data.status==='running') await pollFetch();
    else { toast('err',data.message||'Scrape failed'); showSkeleton(false); }
  } catch(e) {
    toast('err',`Network error: ${e.message}`);
    showSkeleton(false);
  } finally { setBusy(false); }
}

async function pollFetch() {
  let elapsed=0;
  while(true) {
    await sleep(3000); elapsed+=3;
    let s;
    try { s=await fetch('/api/scrape/status').then(r=>r.json()); } catch{continue;}
    if (s.status==='running') { continue; }
    if (s.status==='done') {
      await loadArticles();
      toast('ok',`${s.count} stories loaded`);
      showSkeleton(false);
      return;
    }
    if (s.status==='error') {
      toast('err',s.message||'Scrape error');
      showSkeleton(false);
      return;
    }
    break;
  }
  showSkeleton(false);
}

async function loadArticles() {
  try {
    const data = await fetch('/api/articles').then(r=>r.json());
    if (data.status==='success') {
      // Stamp each article with its stable server-side index so openReader
      // never has to search for it via indexOf (which can mis-map after a
      // re-sort or concurrent load).
      ALL = (data.articles||[]).map((a, i) => { a._serverIdx = i; return a; });
      refreshMeta(ALL);
      buildTicker(ALL);
      applyFilters();
    }
  } catch(e) { console.error(e); }
}

async function loadSaved() {
  loadBtn.disabled=true;
  try {
    const data=await fetch('/api/load',{method:'POST'}).then(r=>r.json());
    if(data.status==='success'){await loadArticles();toast('ok','Saved articles loaded');}
    else toast('err',data.message||'Nothing saved');
  } catch(e){toast('err',e.message);}
  finally{loadBtn.disabled=false;}
}

function showSkeleton(show) {
  skelEl.style.display   = show ? '' : 'none';
  gridEl.classList.toggle('hidden',show);
  listEl.classList.toggle('hidden',true);
  if(show) emptyEl.style.display='none';
}

// ── Toast system ─────────────────────────────────────────────
function toast(type,msg,duration=4000) {
  const icons = {ok:'✓',err:'✕',info:'•'};
  const el=document.createElement('div');
  el.className='toast';
  el.innerHTML=`
    <span class="toast-icon ${type}">${icons[type]||'•'}</span>
    <span class="toast-msg">${esc(msg)}</span>
    <div class="toast-bar-wrap"><div class="toast-bar-fill"></div></div>
  `;
  toastStack.appendChild(el);
  setTimeout(()=>el.querySelector('.toast-bar-fill').style.height='100%',50);
  setTimeout(()=>{
    el.style.opacity='0';el.style.transform='translateY(4px)';
    el.style.transition='opacity .3s,transform .3s';
    setTimeout(()=>el.remove(),300);
  },duration);
}

// ── View toggle ───────────────────────────────────────────────
$('vb-grid').addEventListener('click',()=>{
  VIEW='grid';
  $('vb-grid').classList.add('active');$('vb-list').classList.remove('active');
  renderFeed();
});
$('vb-list').addEventListener('click',()=>{
  VIEW='list';
  $('vb-list').classList.add('active');$('vb-grid').classList.remove('active');
  renderFeed();
});

// ── Events ───────────────────────────────────────────────────
fetchBtn.addEventListener('click',startFetch);
loadBtn.addEventListener('click',loadSaved);

searchEl.addEventListener('input',e=>{Q=e.target.value.trim();applyFilters();});

sortEl.addEventListener('change',e=>{SORT=e.target.value;applyFilters();});

// Category nav
$('nav-list').addEventListener('click',e=>{
  const btn=e.target.closest('.nav-btn');if(!btn)return;
  document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  CAT=btn.dataset.cat;SRC=null;
  applyFilters();
  if(window.innerWidth<=900) closeSidebar();
});

// Sidebar mobile
function openSidebar(){sidebar.classList.add('open');sbBackdrop.classList.add('open');document.body.style.overflow='hidden';}
function closeSidebar(){sidebar.classList.remove('open');sbBackdrop.classList.remove('open');document.body.style.overflow='';}
$('hamburger').addEventListener('click',openSidebar);
$('sidebar-close').addEventListener('click',closeSidebar);
sbBackdrop.addEventListener('click',closeSidebar);

// Keyboard
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key==='k'){e.preventDefault();searchEl.focus();}
  if(e.key==='Escape'){
    if(READER_OPEN)closeReader();
    else searchEl.blur();
  }
});

// Util
function sleep(ms){return new Promise(r=>setTimeout(r,ms));}

// ── Live clock ────────────────────────────────────────────────
(function startClock() {
  const el = $('topbar-clock');
  if (!el) return;
  function tick() {
    const now = new Date();
    el.textContent = now.toLocaleDateString('en-GB',{day:'2-digit',month:'short'})
      + '  ' + now.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  }
  tick();
  setInterval(tick, 1000);
})();

// ── Activity ──────────────────────────────────────────────────
function recordActivity(realIdx, patch) {
  if (realIdx < 0) return;
  const a = ALL[realIdx];
  const existing = ACTIVITY_MAP[realIdx] || {
    headline:             a ? (a.heading || 'Untitled') : 'Unknown',
    source_name:          a ? (a.source_name || '—')    : '—',
    published_hocalwire:  false,
    video_sent:           false,
    last_at:              null,
  };
  ACTIVITY_MAP[realIdx] = Object.assign(existing, patch, { last_at: new Date().toISOString() });
}

function refreshActivityCount() {
  const count = Object.keys(ACTIVITY_MAP).length;
  const el = $('cnt-activity');
  if (el) el.textContent = count;
}

function renderActivityView() {
  const entries = Object.entries(ACTIVITY_MAP); // [[realIdx, data], ...]
  const emptyEl = $('activity-empty');
  const tableEl = $('activity-table');
  const tbody   = $('activity-tbody');
  if (!tbody) return;

  if (entries.length === 0) {
    emptyEl.style.display = '';
    tableEl.classList.add('hidden');
    return;
  }

  emptyEl.style.display = 'none';
  tableEl.classList.remove('hidden');

  // Sort by last_at descending
  entries.sort((a, b) => (b[1].last_at || '').localeCompare(a[1].last_at || ''));

  tbody.innerHTML = entries.map(([, d]) => {
    const hocBadge   = d.published_hocalwire
      ? `<span class="at-badge yes-hocalwire">Yes</span>`
      : `<span class="at-badge no">—</span>`;
    const vidBadge   = d.video_sent
      ? `<span class="at-badge yes-video">Yes</span>`
      : `<span class="at-badge no">—</span>`;
    const timeStr    = d.last_at ? relTime(d.last_at) : '—';
    return `<tr>
      <td><div class="at-headline">${esc(d.headline)}</div></td>
      <td><div class="at-source">${esc(d.source_name)}</div></td>
      <td style="text-align:center">${hocBadge}</td>
      <td style="text-align:center">${vidBadge}</td>
      <td class="at-time">${timeStr}</td>
    </tr>`;
  }).join('');
}

// Activity nav button
(function wireActivityNav() {
  const actBtn  = $('nav-activity-btn');
  const feedArea = $('feed-area');
  const actView  = $('activity-view');
  if (!actBtn || !feedArea || !actView) return;

  actBtn.addEventListener('click', () => {
    const isActive = actBtn.classList.contains('active');
    if (isActive) return; // already showing

    // Deactivate all category nav buttons and activate activity btn
    document.querySelectorAll('#nav-list .nav-btn').forEach(b => b.classList.remove('active'));
    actBtn.classList.add('active');
    FEED_VISIBLE = false;

    // Hide feed panels, show activity
    $('grid').classList.add('hidden');
    $('list-view').classList.add('hidden');
    $('skeleton').style.display = 'none';
    $('empty').style.display    = 'none';
    actView.classList.remove('hidden');
    renderActivityView();

    if (window.innerWidth <= 900) closeSidebar();
  });

  // When a Feed category nav button is clicked, switch back to feed
  $('nav-list').addEventListener('click', () => {
    if (!FEED_VISIBLE) {
      FEED_VISIBLE = true;
      actView.classList.add('hidden');
      actBtn.classList.remove('active');
      applyFilters();
    }
  });
})();

// ── Init ──────────────────────────────────────────────────────
loadArticles();
