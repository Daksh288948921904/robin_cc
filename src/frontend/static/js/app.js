/* ═══════════════════════════════════════════════════════════════
   robin cc — News App v3
   ═══════════════════════════════════════════════════════════════ */

// ── State ────────────────────────────────────────────────────
let ALL     = [];        // raw articles from server
let SHOWN   = [];        // filtered + sorted
let CAT     = 'all';
let SRC     = null;
let COUNTRY = null;      // active country filter
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
let _selectedTweets      = {};   // realIdx → [tweet objects]
let LEAD_STORY_IDX   = null;  // server_idx of article currently set as lead story on Global News
let _readerGenToken  = 0;     // incremented on open/close; stale async tasks bail when it changes
let PAGE             = 1;     // current pagination page
const PER_PAGE       = 50;    // articles per page
let _autoGenPollId   = null;  // interval ID for auto-gen status polling

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

// ── Language badge ────────────────────────────────────────────
const LANG_NAMES = {
  'ar':'Arabic','bn':'Bengali','zh':'Chinese','zh-cn':'Chinese','zh-tw':'Chinese',
  'nl':'Dutch','fr':'French','de':'German','gu':'Gujarati','hi':'Hindi',
  'id':'Indonesian','it':'Italian','ja':'Japanese','kn':'Kannada','ko':'Korean',
  'ml':'Malayalam','mr':'Marathi','pa':'Punjabi','pl':'Polish','pt':'Portuguese',
  'ru':'Russian','es':'Spanish','ta':'Tamil','te':'Telugu','th':'Thai',
  'tr':'Turkish','uk':'Ukrainian','ur':'Urdu','vi':'Vietnamese',
  'xx':'Translated',
};

function langBadge(a) {
  const code = (a.content_language || a.language || 'en').toLowerCase().split('-')[0];
  if (code === 'en' || !code) return '';
  const name = LANG_NAMES[code] || code.toUpperCase();
  return `<span class="card-lang-badge">${name}</span>`;
}

function hocalwireBadge(a) {
  if (a.upload_status !== 'uploaded') return '';
  return `<span class="card-hocalwire-badge" title="Published to Hocalwire">&#10003; Hocalwire</span>
<div class="published-hover-overlay">
  <div class="pho-inner">
    <div class="pho-icon">✓</div>
    <div class="pho-text">Already Published</div>
    <div class="pho-sub">Hocalwire</div>
  </div>
</div>`;
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
  buildCountryList(articles);
}

function _extractCountry(a) {
  // Articles use 'region' field (e.g. "USA", "UK", "India")
  // Fall back to location/dateline for legacy/summary objects
  const raw = (a.region || a.location || a.dateline || '').trim();
  if (!raw) return null;
  const parts = raw.split(',').map(p => p.trim()).filter(Boolean);
  return (parts.length > 1 ? parts[parts.length - 1] : parts[0]).toUpperCase();
}

function _extractCity(a) {
  const raw = (a.location || a.dateline || '').trim();
  const parts = raw.split(',').map(p => p.trim()).filter(Boolean);
  return parts.length > 1 ? parts[0].toUpperCase() : null;
}

function buildCountryList(articles) {
  const wrap = $('cbar-inner');
  if (!wrap) return;

  // Count articles per country and city
  const countryCounts = {};
  const cityCounts = {};
  (articles || ALL).forEach(a => {
    const c = _extractCountry(a);
    if (c) countryCounts[c] = (countryCounts[c] || 0) + 1;
    const city = (a.city || '').trim();
    if (city) cityCounts[city] = (cityCounts[city] || 0) + 1;
  });

  const sorted = Object.entries(countryCounts).sort((a, b) => b[1] - a[1]);
  const topCities = Object.entries(cityCounts).sort((a, b) => b[1] - a[1]).slice(0, 2);
  const toTitle = s => s.toLowerCase().replace(/\b\w/g, c => c.toUpperCase());

  // "All" chip
  let html = `<button class="cbar-chip${!COUNTRY ? ' active' : ''}" data-country="">
    <span class="cbar-chip-label">🌍 All</span>
  </button>`;

  // Top-2 city chips (if any) with divider
  if (topCities.length) {
    html += `<div class="cbar-divider"></div>`;
    html += topCities.map(([city]) => `
      <button class="cbar-chip cbar-city${COUNTRY === city ? ' active' : ''}" data-country="${esc(city)}" title="Top city">
        <span class="cbar-chip-label">📍 ${esc(toTitle(city))}</span>
        <span class="cbar-chip-count">${cityCounts[city]}</span>
      </button>`).join('');
    html += `<div class="cbar-divider"></div>`;
  }

  // Country chips sorted by count
  html += sorted.map(([country, count]) => `
    <button class="cbar-chip${COUNTRY === country ? ' active' : ''}" data-country="${esc(country)}">
      <span class="cbar-chip-label">${esc(toTitle(country))}</span>
      <span class="cbar-chip-count">${count}</span>
    </button>`).join('');

  wrap.innerHTML = html;

  wrap.querySelectorAll('.cbar-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const val = btn.dataset.country;
      COUNTRY = (COUNTRY === val || val === '') ? null : val;
      wrap.querySelectorAll('.cbar-chip').forEach(b =>
        b.classList.toggle('active', b.dataset.country === (COUNTRY || '')));
      if (COUNTRY) {
        document.querySelectorAll('#nav-list .nav-btn').forEach(b => b.classList.remove('active'));
        $('nav-list').querySelector('[data-cat="all"]').classList.add('active');
        CAT = 'all';
      }
      applyFilters();
    });
  });
}

// ── Filter & sort ────────────────────────────────────────────
function applyFilters() {
  let list = ALL;

  if (CAT !== 'all') list = list.filter(a=>catOf(a).key===CAT);
  if (COUNTRY)       list = list.filter(a=>_extractCountry(a)===COUNTRY);
  if (Q) {
    const q=Q.toLowerCase();
    list = list.filter(a=>
      (a.heading||'').toLowerCase().includes(q)||
      (a.story||'').toLowerCase().includes(q)||
      (a.source_name||'').toLowerCase().includes(q)
    );
  }

  if      (SORT==='new')  list=[...list].sort((a,b)=>new Date(b.publish_date||0)-new Date(a.publish_date||0));
  else if (SORT==='old')  list=[...list].sort((a,b)=>new Date(a.publish_date||0)-new Date(b.publish_date||0));
  else if (SORT==='long') list=[...list].sort((a,b)=>wc(b)-wc(a));

  // Paginate
  const totalPages = Math.ceil(list.length / PER_PAGE);
  if (PAGE > totalPages) PAGE = totalPages || 1;
  const start = (PAGE - 1) * PER_PAGE;
  SHOWN = list.slice(start, start + PER_PAGE);
  renderFeed();
  renderPagination(list.length, totalPages);
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

  return `<article class="card${isHero?' card-hero':''}${a.upload_status==='uploaded'?' is-published':''}" data-cat="${cat.key}" onclick="openReader(${i})">
    ${media}
    ${hocalwireBadge(a)}
    <div class="card-badges">
      <span class="card-cat-badge ${cat.cls}">${cat.key}</span>
      ${langBadge(a)}
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
      ${langBadge(a)}
      ${hocalwireBadge(a)}
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

  _readerGenToken++;            // cancel any in-flight generation from the previous article

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
  $('r-deck').style.display = '';

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
        <span>Generating image…</span>
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

  // Reset coverage, summary, timeline, news-check, social panels
  $('coverage-section').innerHTML = coverageLoadingHTML();
  $('summary-section').innerHTML  = summaryLoadingHTML();
  $('timeline-section').innerHTML = timelineLoadingHTML();
  const ncSec = $('news-check-section');
  if (ncSec) ncSec.innerHTML = `<div class="nc-loading"><span class="nc-spinner"></span><span>Waiting for briefing…</span></div>`;
  resetArticleChat();
  const tlOuter = $('timeline-outer');
  if (tlOuter) tlOuter.style.display = 'none';
  const twOuter = $('social-twitter-outer');
  const igOuter = $('social-instagram-outer');
  if (twOuter) twOuter.style.display = 'none';
  if (igOuter) igOuter.style.display = 'none';
  $('social-twitter-section').innerHTML = '';
  const pb = $('publish-btn');
  if (pb) { pb.style.display = 'none'; pb.disabled = false; $('publish-label').textContent = 'Publish to Hocalwire'; $('publish-spinner').classList.add('hidden'); }

  const lstTrack = $('lst-track');
  if (lstTrack) {
    lstTrack.classList.remove('on');
    $('lead-story-toggle').classList.remove('disabled');
    $('lead-story-label').textContent = 'Lead Story';
    $('lead-story-spinner').classList.add('hidden');
  }

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
  const anyAction = entry && (entry.published_hocalwire || entry.video_sent || entry.tv_sent || entry.radio_sent);
  if (!anyAction) {
    alertEl.classList.add('hidden');
    return;
  }

  const parts = [];
  if (entry.published_hocalwire) parts.push('Hocalwire');
  if (entry.video_sent)          parts.push('Video Workflow');
  if (entry.tv_sent)             parts.push('TV Script');
  if (entry.radio_sent)          parts.push('Radio Bulletin');
  textEl.textContent = `Already pushed to: ${parts.join(' & ')}`;

  tagsEl.innerHTML = [
    entry.published_hocalwire ? `<span class="pa-tag hocalwire">Hocalwire</span>`      : '',
    entry.video_sent          ? `<span class="pa-tag video">Video</span>`               : '',
    entry.tv_sent             ? `<span class="pa-tag tv">TV Script</span>`              : '',
    entry.radio_sent          ? `<span class="pa-tag radio">Radio</span>`               : '',
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
    <span>Generating briefing…</span>
  </div>`;
}
function timelineLoadingHTML() {
  return `<div class="coverage-loading">
    <span class="cov-spinner"></span>
    <span>Building event timeline…</span>
  </div>`;
}

async function loadCoverage(realIdx) {
  const myToken = _readerGenToken;
  try {
    const res  = await fetch(`/api/articles/${realIdx}/coverage`);
    if (_readerGenToken !== myToken) return;
    const data = await res.json();
    if (_readerGenToken !== myToken) return;

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
    // Launch RAG generation, timeline, and social reactions simultaneously
    generateSummaryRag(realIdx, myToken);
    generateTimeline(realIdx, myToken);
    loadSocialReactions(realIdx);
  } catch(e) {
    if (_readerGenToken !== myToken) return;
    $('coverage-section').innerHTML = coverageEmptyHTML(`Error: ${e.message}`);
    generateSummaryRag(realIdx, myToken);
    generateTimeline(realIdx, myToken);
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
  const editBtn    = document.querySelector('.edit-briefing-btn');
  const titleEl    = document.querySelector('.sum-title');
  const subtitleEl = document.querySelector('.sum-subtitle');
  const bodyEl     = document.querySelector('.sum-body');

  if (!editBtn || !titleEl || !bodyEl) return;

  if (EDIT_MODE) {
    // Enable editing
    titleEl.contentEditable = 'true';
    titleEl.classList.add('editing');
    if (subtitleEl) { subtitleEl.contentEditable = 'true'; subtitleEl.classList.add('editing'); }
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
    if (subtitleEl) { subtitleEl.contentEditable = 'false'; subtitleEl.classList.remove('editing'); }
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
  const titleEl    = document.querySelector('.sum-title');
  const subtitleEl = document.querySelector('.sum-subtitle');
  const title      = titleEl    ? titleEl.textContent.trim()    : '';
  const subtitle   = subtitleEl ? subtitleEl.textContent.trim() : '';

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

  return { edited_title: title, edited_subtitle: subtitle || undefined, edited_body: body.trim(), category, selected_image: SELECTED_IMAGE || undefined };
}

function renderSummary(s, realIdx) {
  CURRENT_SUMM = s;
  const bodyHtml = bodyToHtml(s.body || '');
  const srcsHtml = (s.sources_list||[]).map((src,n)=>`
    <li class="sum-src-item">
      <span class="sum-src-num">${n+1}</span>
      <div class="sum-src-body">
        <span class="sum-src-name">${esc(src.name||'')}</span>
        ${src.headline?`<span class="sum-src-hl">— ${esc(src.headline.slice(0,90))}</span>`:''}
        ${src.url?`<a class="sum-src-url" href="${esc(src.url)}" target="_blank" rel="noopener">${esc(src.url.slice(0,60))}…</a>`:''}
      </div>
    </li>`).join('');

  const pipelineBadge = s.generation_pipeline === 'aspect_rag_v1'
    ? `<span class="sum-badge sum-badge--rag">RAG Pipeline</span>`
    : s.generation_pipeline === 'direct_llm'
      ? `<span class="sum-badge sum-badge--direct">Standard</span>`
      : `<span class="sum-badge">DNL Briefing</span>`;

  $('summary-section').innerHTML = `
    <div class="summary-result">
      <h2 class="sum-title" contenteditable="false">${esc(s.title||'News Brief')}</h2>
      <p class="sum-subtitle" contenteditable="false">${esc((s.subtitle||s.sub_heading||'').replace(/\*+/g,'').replace(/^sub$/i,'').trim())}</p>
      <button class="sum-category-btn" style="display:none" aria-hidden="true">${esc(s.category||'World')}</button>
      <div class="sum-meta-row">
        ${pipelineBadge}
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

  const pb = $('publish-btn');
  if (pb) pb.style.display = '';
  EDIT_MODE = false;
  runNewsCheck();
}

async function generateSummary(realIdx) {
  // Legacy path — kept for fallback only; production uses generateSummaryRag
  try {
    const res  = await fetch(`/api/articles/${realIdx}/summary`, {method:'POST'});
    const data = await res.json();
    if (data.status !== 'success' || !data.summary) {
      $('summary-section').innerHTML = `<div class="coverage-empty">Error: ${esc(data.message||'Generation failed')}</div>`;
      return;
    }
    renderSummary(data.summary, realIdx);
  } catch(e) {
    $('summary-section').innerHTML = `<div class="coverage-empty">Network error: ${esc(e.message)}</div>`;
  }
}

async function generateSummaryRag(realIdx, genToken) {
  const myToken = genToken ?? _readerGenToken;
  $('summary-section').innerHTML = `<div class="coverage-loading">
    <span class="cov-spinner"></span>
    <span id="rag-status-msg">Running RAG pipeline…</span>
  </div>`;

  try {
    const startRes  = await fetch(`/api/articles/${realIdx}/rag-generate`, {method:'POST'});
    if (_readerGenToken !== myToken) return;
    const startData = await startRes.json();
    if (_readerGenToken !== myToken) return;
    if (startData.status === 'error') {
      $('summary-section').innerHTML = `<div class="coverage-empty">Error: ${esc(startData.message||'Failed to start')}</div>`;
      return;
    }

    // Poll until done
    let elapsed = 0;
    while (true) {
      await new Promise(r => setTimeout(r, 4000));
      if (_readerGenToken !== myToken) return;
      elapsed += 4;
      const msg = $('rag-status-msg');

      const res  = await fetch(`/api/articles/${realIdx}/rag-status`);
      if (_readerGenToken !== myToken) return;
      const data = await res.json();
      if (_readerGenToken !== myToken) return;

      if (data.status === 'error') {
        $('summary-section').innerHTML = `<div class="coverage-empty">Error: ${esc(data.message||'Generation failed')}</div>`;
        return;
      }
      if (data.status === 'done') {
        renderSummary(data.summary, realIdx);
        return;
      }
      // Show server-provided wait message (e.g. after 2min) or default elapsed counter
      if (msg) msg.textContent = data.message || `Running RAG pipeline… ${elapsed}s`;
    }
  } catch(e) {
    if (_readerGenToken !== myToken) return;
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
  // Always reset success/content visibility before showing preview
  $('hocal-success-screen').style.display = 'none';
  $('hocal-preview-content').style.display = '';

  const metaEl = $('hocal-preview-meta');
  const bodyEl = $('hocal-preview-body');

  const imgHtml = `<div class="hocal-prev-img-wrap">
    ${d.image_url ? `<img class="hocal-prev-img" id="hocal-prev-img" src="${esc(d.image_url)}" alt="Article image" onerror="this.style.display='none'">` : ''}
    <button class="hocal-regen-btn" id="hocal-regen-btn" onclick="regenHocalwireImage()">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
      <span id="hocal-regen-label">Regen Image</span>
      <span class="hocal-regen-spinner hidden" id="hocal-regen-spinner"></span>
    </button>
  </div>`;

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

async function regenHocalwireImage() {
  const stored = _hocalwirePreviewData;
  if (!stored) return;

  const btn     = $('hocal-regen-btn');
  const label   = $('hocal-regen-label');
  const spinner = $('hocal-regen-spinner');

  btn.disabled = true;
  label.textContent = 'Generating…';
  spinner.classList.remove('hidden');

  try {
    const res  = await fetch(`/api/articles/${stored.realIdx}/image?force=true`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));

    if (res.ok && data.status === 'success') {
      const imgEl = $('hocal-prev-img');
      if (imgEl) {
        imgEl.src = data.image_url;
        imgEl.style.display = '';
      } else {
        const wrap = document.querySelector('.hocal-prev-img-wrap');
        if (wrap) {
          const newImg = document.createElement('img');
          newImg.className = 'hocal-prev-img';
          newImg.id = 'hocal-prev-img';
          newImg.alt = 'Article image';
          newImg.src = data.image_url;
          newImg.onerror = () => { newImg.style.display = 'none'; };
          wrap.insertBefore(newImg, wrap.firstChild);
        }
      }
      stored.previewResp.image_url = data.image_url;
    } else {
      toast('err', data.message || 'Image generation failed');
    }
  } catch(e) {
    toast('err', `Regen error: ${e.message}`);
  } finally {
    btn.disabled = false;
    label.textContent = 'Regen Image';
    spinner.classList.add('hidden');
  }
}

function closeHocalwirePreview(e) {
  if (e && e.target !== $('hocal-preview-overlay')) return;
  $('hocal-preview-overlay').classList.remove('open');
  $('hocal-success-screen').style.display = 'none';
  $('hocal-preview-content').style.display = '';
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
    const publishPayload = {...edited, selected_tweets: getSelectedTweets()};
    const res  = await fetch(`/api/articles/${realIdx}/publish`, {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify(publishPayload),
    });
    const data = await res.json().catch(() => ({}));

    if (res.ok && data.status === 'success') {
      // Show success screen inside modal, then auto-close
      const feedId = data.feed_id || '';
      $('hocal-success-sub').innerHTML = `Article is now live on the public feed${feedId ? ` · Feed ID: <strong>${esc(feedId)}</strong>` : ''}`;
      $('hocal-success-screen').style.display = 'flex';
      $('hocal-preview-content').style.display = 'none';

      // Update publish button
      const label = $('publish-label');
      if (label) label.textContent = '✓ Published';
      const pb = $('publish-btn');
      if (pb) pb.disabled = true;

      // Mark article in local data so the feed badge appears immediately
      if (ALL[realIdx]) {
        ALL[realIdx].upload_status = 'uploaded';
        if (feedId) ALL[realIdx].hocalwire_feed_id = feedId;
      }
      renderFeed();

      // Update activity tracking
      recordActivity(realIdx, {published_hocalwire: true});
      updatePublishedAlert(realIdx);
      refreshActivityCount();
      renderActivityView();

      // Close modal after 2.5 s
      setTimeout(() => {
        $('hocal-preview-overlay').classList.remove('open');
        $('hocal-success-screen').style.display = 'none';
        $('hocal-preview-content').style.display = '';
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

// ── Lead Story toggle ─────────────────────────────────────────
async function toggleLeadStory() {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) { toast('err', 'No article selected'); return; }

  const toggle  = $('lead-story-toggle');
  const track   = $('lst-track');
  const label   = $('lead-story-label');
  const spinner = $('lead-story-spinner');
  const isActive = LEAD_STORY_IDX === realIdx;

  toggle.classList.add('disabled');
  label.textContent = isActive ? 'Removing…' : 'Setting…';
  spinner.classList.remove('hidden');

  try {
    const res  = await fetch(`/api/articles/${realIdx}/lead-story`, {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify(isActive ? {clear: true} : {}),
    });
    const data = await res.json();
    if (data.status === 'success') {
      LEAD_STORY_IDX = isActive ? null : realIdx;
      track.classList.toggle('on', !isActive);
      label.textContent = 'Lead Story';
      toast('ok', isActive ? 'Lead story removed from Global News' : 'Set as lead story on Global News!');
    } else {
      toast('err', data.message || 'Failed to update lead story');
      label.textContent = 'Lead Story';
    }
  } catch(e) {
    toast('err', `Lead story error: ${e.message}`);
    label.textContent = 'Lead Story';
  } finally {
    toggle.classList.remove('disabled');
    spinner.classList.add('hidden');
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

document.addEventListener('DOMContentLoaded', () => {
  const titleEl = $('r-title');
  const deckEl  = $('r-deck');

  function saveField(el, field) {
    const idx = CURRENT_REAL_IDX;
    if (idx < 0) return;
    const val = el.textContent.trim();
    if (ALL[idx]) ALL[idx][field] = val;
    fetch(`/api/articles/${idx}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [field]: val }),
    });
  }

  titleEl.addEventListener('blur', () => saveField(titleEl, 'heading'));
  deckEl.addEventListener('blur',  () => saveField(deckEl,  'sub_heading'));

  // Prevent newlines in single-line fields
  [titleEl, deckEl].forEach(el => {
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); el.blur(); }
    });
  });
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

// ── Social Post Preview ───────────────────────────────────────
let _socialPostsCache    = {};      // realIdx → {twitter, instagram, facebook, linkedin}
let _socialImageCache    = {};      // realIdx → image URL
let _socialTvCache       = {};      // realIdx → tv script string
let _socialPodcastCache  = {};      // realIdx → podcast/radio script string
let _activeSocialTab     = 'instagram';

function openSocialPostModal() {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) { toast('err', 'Open an article first'); return; }
  $('smodal-overlay').classList.add('open');
  switchSocialTab(_activeSocialTab);
  if (_socialPostsCache[realIdx]) {
    _renderSocialPosts(_socialPostsCache[realIdx], _socialImageCache[realIdx] || '');
  } else {
    _fetchSocialPosts(realIdx);
  }
}

// ── TV Script Modal ───────────────────────────────
function openTVScriptPreview() {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) { toast('err', 'Open an article first'); return; }
  $('tv-modal-overlay').classList.add('open');
  if (_socialTvCache[realIdx]) {
    _renderTVModal(_socialTvCache[realIdx]);
  } else {
    _fetchAndRenderScripts(realIdx, 'tv');
  }
}

function closeTVModal(e) {
  if (e && e.target !== $('tv-modal-overlay')) return;
  $('tv-modal-overlay').classList.remove('open');
}

function _renderTVModal(script) {
  const ta = $('tv-script-textarea');
  if (!ta || !script) return;
  ta.value = script.replace(/\[WORDS:\s*\d+\]\s*\[TIME:\s*~?\d+s\]\s*$/, '').trim();
  const meta = $('tv-modal-meta');
  if (meta) {
    const wMatch = script.match(/\[WORDS:\s*(\d+)\]/);
    const tMatch = script.match(/\[TIME:\s*~?(\d+)s\]/);
    if (wMatch && tMatch) {
      const mins = Math.floor(parseInt(tMatch[1]) / 60);
      const secs = parseInt(tMatch[1]) % 60;
      meta.textContent = `${mins > 0 ? mins + 'm ' : ''}${secs > 0 ? secs + 's · ' : ''}${wMatch[1]} words · 150 WPM`;
    }
  }
}

function copyTVScript() {
  const ta = $('tv-script-textarea');
  const text = ta ? ta.value : (_socialTvCache[CURRENT_REAL_IDX] || '');
  if (!text) { toast('err', 'No script to copy'); return; }
  navigator.clipboard.writeText(text).then(
    () => toast('ok', 'TV script copied'),
    () => toast('err', 'Clipboard write failed')
  );
}

async function regenTVScript() {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) return;
  delete _socialPostsCache[realIdx];
  delete _socialTvCache[realIdx];
  delete _socialPodcastCache[realIdx];
  delete _socialImageCache[realIdx];
  await _fetchAndRenderScripts(realIdx, 'tv');
}

// ── Radio Modal ───────────────────────────────────
function openPodcastPreview() {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) { toast('err', 'Open an article first'); return; }
  $('radio-modal-overlay').classList.add('open');
  if (_socialPodcastCache[realIdx]) {
    _renderRadioModal(_socialPodcastCache[realIdx]);
  } else {
    _fetchAndRenderScripts(realIdx, 'radio');
  }
}

function closeRadioModal(e) {
  if (e && e.target !== $('radio-modal-overlay')) return;
  $('radio-modal-overlay').classList.remove('open');
}

function _renderRadioModal(script) {
  const ta = $('radio-script-textarea');
  if (!ta || !script) return;
  ta.value = script.replace(/\[WORDS:\s*\d+\]\s*\[TIME:\s*~?\d+s\]\s*$/, '').trim();
  const meta = $('radio-modal-meta');
  if (meta) {
    const wMatch = script.match(/\[WORDS:\s*(\d+)\]/);
    const tMatch = script.match(/\[TIME:\s*~?(\d+)s\]/);
    if (wMatch && tMatch) meta.textContent = `~${tMatch[1]}s · ${wMatch[1]} words`;
  }
}

function copyRadioScript() {
  const ta = $('radio-script-textarea');
  const text = ta ? ta.value : (_socialPodcastCache[CURRENT_REAL_IDX] || '');
  if (!text) { toast('err', 'No script to copy'); return; }
  navigator.clipboard.writeText(text).then(
    () => toast('ok', 'Radio script copied'),
    () => toast('err', 'Clipboard write failed')
  );
}

async function regenRadioScript() {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) return;
  delete _socialPostsCache[realIdx];
  delete _socialTvCache[realIdx];
  delete _socialPodcastCache[realIdx];
  delete _socialImageCache[realIdx];
  await _fetchAndRenderScripts(realIdx, 'radio');
}

// ── Audio generation (Rig TTS server) ─────────────
async function _sendToInbox(scriptType) {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) { toast('err', 'No article selected'); return; }

  const isTv    = scriptType === 'tv';
  const btnId   = isTv ? 'tv-gen-audio-btn'   : 'radio-gen-audio-btn';
  const labelId = isTv ? 'tv-gen-audio-label' : 'radio-gen-audio-label';
  const btn     = $(btnId);
  const label   = $(labelId);

  btn.disabled = true;
  label.textContent = 'Sending…';

  try {
    const res  = await fetch(`/api/articles/${realIdx}/send-to-inbox`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ script_type: scriptType }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      toast('ok', `Script sent to Rig Studio inbox — open localhost:3000/inbox to review`);
      recordActivity(realIdx, { [`${scriptType === 'tv' ? 'tv' : 'radio'}_sent`]: true });
      updatePublishedAlert(realIdx);
      refreshActivityCount();
    } else {
      toast('err', data.error || `Failed to send (${res.status})`);
    }
  } catch(e) {
    toast('err', `Send error: ${e.message}`);
  } finally {
    btn.disabled = false;
    label.textContent = 'Send to Inbox';
  }
}

function generateTVAudio()    { _sendToInbox('tv'); }
function generateRadioAudio() { _sendToInbox('podcast'); }

// ── Shared fetch for TV + Radio modals ───────────
async function _fetchAndRenderScripts(realIdx, target) {
  const loadingId = target === 'tv' ? 'tv-modal-loading' : 'radio-modal-loading';
  const loadingEl = $(loadingId);
  if (loadingEl) loadingEl.classList.remove('hidden');

  try {
    const res  = await fetch(`/api/articles/${realIdx}/social-posts`, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'success') {
      if (data.posts) _socialPostsCache[realIdx] = data.posts;
      const imageToUse = SELECTED_IMAGE || data.image_url || '';
      _socialImageCache[realIdx]   = imageToUse;
      _socialTvCache[realIdx]      = data.tv_script      || '';
      _socialPodcastCache[realIdx] = data.podcast_script || '';
      if (target === 'tv')    _renderTVModal(_socialTvCache[realIdx]);
      if (target === 'radio') _renderRadioModal(_socialPodcastCache[realIdx]);
    } else {
      toast('err', data.message || 'Generation failed');
    }
  } catch(e) {
    toast('err', `Network error: ${e.message}`);
  } finally {
    if (loadingEl) loadingEl.classList.add('hidden');
  }
}

function closeSocialPostModal(e) {
  if (e && e.target !== $('smodal-overlay')) return;
  $('smodal-overlay').classList.remove('open');
}

function switchSocialTab(platform) {
  _activeSocialTab = platform;
  ['instagram','twitter','facebook','linkedin'].forEach(p => {
    $('stab-' + p).classList.toggle('active', p === platform);
    $('spane-' + p).classList.toggle('active', p === platform);
  });
}

async function _fetchSocialPosts(realIdx) {
  const loading = $('smodal-loading');
  loading.classList.remove('hidden');
  ['sp-ig-text','sp-tw-text','sp-fb-text','sp-li-text'].forEach(id => {
    const el = $(id);
    if (el) { if (id === 'sp-ig-text') { const body = el.querySelector('.sp-ig-caption-body'); if (body) body.textContent = '…'; } else el.textContent = '…'; }
  });

  try {
    const res  = await fetch(`/api/articles/${realIdx}/social-posts`, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'success' && data.posts) {
      _socialPostsCache[realIdx]   = data.posts;
      const imageToUse = SELECTED_IMAGE || data.image_url || '';
      _socialImageCache[realIdx]   = imageToUse;
      _socialTvCache[realIdx]      = data.tv_script      || '';
      _socialPodcastCache[realIdx] = data.podcast_script || '';
      _renderSocialPosts(data.posts, imageToUse);
    } else {
      toast('err', data.message || 'Could not generate social posts');
    }
  } catch(e) {
    toast('err', `Network error: ${e.message}`);
  } finally {
    loading.classList.add('hidden');
  }
}

function _renderSocialPosts(posts, imageUrl) {
  // Instagram caption
  const igBody = $('sp-ig-text');
  if (igBody) {
    const captionEl = igBody.querySelector('.sp-ig-caption-body');
    if (captionEl) captionEl.innerHTML = _formatPostText(posts.instagram || '');
  }
  // Instagram image — replace placeholder with actual image
  const igImg = $('sp-ig-image');
  if (igImg && imageUrl) {
    igImg.innerHTML = `<img src="${esc(imageUrl)}" alt="" style="width:100%;aspect-ratio:1/1;object-fit:cover;display:block" onerror="this.parentElement.innerHTML='<div class=sp-img-err>Image unavailable</div>'">`;
  }
  // Twitter
  const twEl = $('sp-tw-text');
  if (twEl) twEl.innerHTML = _formatPostText(posts.twitter || '');
  _setCardImage('sp-tw-image', imageUrl, 'sp-tw-img-wrap');
  // Facebook
  const fbEl = $('sp-fb-text');
  if (fbEl) fbEl.innerHTML = _formatPostText(posts.facebook || '');
  _setCardImage('sp-fb-image', imageUrl, 'sp-fb-img-wrap');
  // LinkedIn
  const liEl = $('sp-li-text');
  if (liEl) liEl.innerHTML = _formatPostText(posts.linkedin || '');
  _setCardImage('sp-li-image', imageUrl, 'sp-li-img-wrap');
}

function _setCardImage(containerId, imageUrl, wrapClass) {
  const el = $(containerId);
  if (!el) return;
  if (imageUrl) {
    el.innerHTML = `<img src="${esc(imageUrl)}" alt="" class="${wrapClass}" onerror="this.parentElement.classList.add('hidden')">`;
    el.classList.remove('hidden');
  } else {
    el.classList.add('hidden');
  }
}

function _formatPostText(text) {
  return esc(text)
    .replace(/(#\w+)/g, '<span class="sp-hashtag">$1</span>')
    .replace(/\n/g, '<br>');
}

function copySocialPost() {
  const realIdx = CURRENT_REAL_IDX;
  const posts = _socialPostsCache[realIdx];
  if (!posts) { toast('err', 'No posts generated yet'); return; }
  const text = posts[_activeSocialTab] || '';
  if (!text) { toast('err', 'Nothing to copy'); return; }
  navigator.clipboard.writeText(text).then(
    ()  => toast('ok', `${_activeSocialTab} post copied`),
    ()  => toast('err', 'Clipboard write failed')
  );
}

async function pushSocialToAll() {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) { toast('err', 'No article selected'); return; }

  const btn     = $('smodal-push-btn');
  const label   = $('smodal-push-label');
  const spinner = $('smodal-push-spinner');
  const statusRow = $('smodal-push-status');
  const chips   = { instagram: $('spush-ig'), twitter: $('spush-tw'), facebook: $('spush-fb'), linkedin: $('spush-li') };

  btn.disabled = true;
  label.textContent = 'Pushing…';
  spinner.classList.remove('hidden');
  statusRow.classList.remove('hidden');
  Object.values(chips).forEach(c => { c.className = 'spush-chip pending'; });

  try {
    const res  = await fetch(`/api/articles/${realIdx}/push-social`, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'success') {
      // All platforms routed via Slack — mark all ok if Slack succeeded
      Object.keys(chips).forEach(p => {
        chips[p].className = 'spush-chip ok';
        chips[p].textContent = chips[p].textContent.split(' ')[0] + ' ' +
          ({ instagram:'Instagram', twitter:'X', facebook:'Facebook', linkedin:'LinkedIn' }[p]);
      });
      label.textContent = 'Pushed ✓';
      toast('ok', 'Social posts pushed to all platforms via Slack!');
    } else {
      Object.values(chips).forEach(c => { c.className = 'spush-chip err'; });
      label.textContent = 'Push to All';
      toast('err', data.message || 'Push failed');
    }
  } catch(e) {
    Object.values(chips).forEach(c => { c.className = 'spush-chip err'; });
    label.textContent = 'Push to All';
    toast('err', `Push error: ${e.message}`);
  } finally {
    btn.disabled = false;
    spinner.classList.add('hidden');
  }
}

async function regenSocialPosts() {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) return;
  delete _socialPostsCache[realIdx];
  delete _socialImageCache[realIdx];
  delete _socialTvCache[realIdx];
  delete _socialPodcastCache[realIdx];
  await _fetchSocialPosts(realIdx);
}

// Close script modals on Escape
document.addEventListener('DOMContentLoaded', () => {
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      $('tv-modal-overlay')?.classList.remove('open');
      $('radio-modal-overlay')?.classList.remove('open');
    }
  });
});

// Close social modal on Escape
document.addEventListener('DOMContentLoaded', () => {
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') $('smodal-overlay').classList.remove('open');
  });
});

// ── News Verification ────────────────────────────────────────
async function runNewsCheck() {
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx < 0) return;
  const sec = $('news-check-section');
  if (!sec) return;

  sec.innerHTML = `<div class="nc-loading"><span class="nc-spinner"></span><span>Analysing article…</span></div>`;

  try {
    const res  = await fetch(`/api/articles/${realIdx}/news-check`, {method:'POST'});
    const data = await res.json();
    if (data.status !== 'success') { sec.innerHTML = `<div class="nc-error">Verification failed: ${esc(data.message||'')}</div>`; return; }

    const c = data.check;

    const credColor  = {concrete:'nc-green', speculative:'nc-yellow', misleading:'nc-orange', false:'nc-red'}[c.credibility] || 'nc-gray';
    const fakeColor  = {credible:'nc-green', unverified:'nc-gray', potentially_misleading:'nc-orange', likely_false:'nc-red'}[c.fake_check] || 'nc-gray';
    const trendColor = c.trending === 'trending' ? 'nc-blue' : 'nc-gray';
    const toneColor  = {positive:'nc-green', negative:'nc-red', neutral:'nc-gray'}[c.tone] || 'nc-gray';
    const overallColor = {VERIFIED:'nc-green','LIKELY FALSE':'nc-red','USE CAUTION':'nc-orange',UNVERIFIED:'nc-gray'}[c.overall] || 'nc-gray';

    const credLabel  = c.credibility.replace(/_/g,' ').toUpperCase();
    const fakeLabel  = c.fake_check.replace(/_/g,' ').toUpperCase();
    const trendLabel = c.trending === 'trending' ? 'TRENDING' : 'NOT TRENDING';
    const toneLabel  = (c.tone || 'neutral').toUpperCase();

    // Alert banner: shown when score < 65, negative tone, or not VERIFIED
    const needsAlert = c.overall !== 'VERIFIED' || c.credibility_score < 65 || c.tone === 'negative';
    const alertHtml = needsAlert && c.alert_reason
      ? `<div class="nc-alert-box ${overallColor === 'nc-green' ? 'nc-orange' : overallColor}">
           <div class="nc-alert-icon"><svg width="13" height="13" viewBox="0 0 12 12" fill="none"><path d="M6 1L11 10H1L6 1z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M6 5v2.5M6 9h.01" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg></div>
           <div class="nc-alert-text">${esc(c.alert_reason)}</div>
         </div>`
      : '';

    const redFlagsHtml = c.red_flags && c.red_flags.length
      ? `<div class="nc-flags"><span class="nc-flags-label">Red Flags</span>${c.red_flags.map(f=>`<span class="nc-flag">${esc(f)}</span>`).join('')}</div>`
      : '';

    const speculHtml = c.speculation_indicators && c.speculation_indicators.length
      ? `<div class="nc-flags nc-specul"><span class="nc-flags-label nc-specul-label">Speculation Indicators</span>${c.speculation_indicators.map(f=>`<span class="nc-flag nc-specul-tag">"${esc(f)}"</span>`).join('')}</div>`
      : '';

    const negFramingHtml = c.tone === 'negative' && c.negative_framing && c.negative_framing.length
      ? `<div class="nc-flags nc-negframing"><span class="nc-flags-label nc-negframing-label">Negative Framing</span>${c.negative_framing.map(f=>`<span class="nc-flag nc-negframing-tag">"${esc(f)}"</span>`).join('')}</div>`
      : '';

    const claimsHtml = c.key_claims && c.key_claims.length
      ? `<div class="nc-claims"><span class="nc-claims-label">Key Claims</span><ul>${c.key_claims.map(cl=>`<li>${esc(cl)}</li>`).join('')}</ul></div>`
      : '';

    // Reason text is brighter (inherits card color) when card is flagged
    const credReasonClass = credColor !== 'nc-green' ? 'nc-card-reason nc-card-reason--flagged' : 'nc-card-reason';
    const fakeReasonClass  = fakeColor  !== 'nc-green' ? 'nc-card-reason nc-card-reason--flagged' : 'nc-card-reason';
    const toneReasonClass  = toneColor  !== 'nc-green' ? 'nc-card-reason nc-card-reason--flagged' : 'nc-card-reason';

    sec.innerHTML = `
      ${alertHtml}

      <div class="nc-overall ${overallColor}">
        <div class="nc-overall-left">
          <span class="nc-overall-label">Overall Verdict</span>
          <span class="nc-overall-value">${esc(c.overall)}</span>
        </div>
        <div class="nc-overall-score">${c.credibility_score}<span>/100</span></div>
      </div>

      <div class="nc-grid">
        <div class="nc-card ${credColor}">
          <div class="nc-card-header">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M6 1l1.3 2.6 2.9.4-2.1 2 .5 2.9L6 7.5 3.4 8.9l.5-2.9L1.8 4l2.9-.4L6 1z" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/></svg>
            <span>Credibility</span>
          </div>
          <div class="nc-card-badge">${credLabel}</div>
          <div class="${credReasonClass}">${esc(c.credibility_reason)}</div>
        </div>

        <div class="nc-card ${fakeColor}">
          <div class="nc-card-header">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><circle cx="6" cy="6" r="5" stroke="currentColor" stroke-width="1.1"/><path d="M6 3.5v3M6 8h.01" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
            <span>Authenticity</span>
          </div>
          <div class="nc-card-badge">${fakeLabel}</div>
          <div class="${fakeReasonClass}">${esc(c.fake_reason)}</div>
        </div>

        <div class="nc-card ${toneColor}">
          <div class="nc-card-header">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 9c1-2 2-3 4-3s3 1 4 3" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><circle cx="4" cy="4.5" r="1" fill="currentColor"/><circle cx="8" cy="4.5" r="1" fill="currentColor"/></svg>
            <span>Tone</span>
          </div>
          <div class="nc-card-badge">${toneLabel}</div>
          <div class="${toneReasonClass}">${esc(c.tone_reason)}</div>
        </div>

        <div class="nc-card ${trendColor}">
          <div class="nc-card-header">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M1 9l3-3 2.5 2L10 3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            <span>Trending</span>
          </div>
          <div class="nc-card-badge">${trendLabel}</div>
          <div class="nc-card-reason">${esc(c.trending_reason)}</div>
        </div>
      </div>

      ${redFlagsHtml}${speculHtml}${negFramingHtml}${claimsHtml}
      <button class="nc-rerun-btn" onclick="runNewsCheck()">Re-run</button>`;
  } catch(e) {
    sec.innerHTML = `<div class="nc-error">Network error: ${esc(String(e))}</div>`;
  }
}

// ── Timeline ─────────────────────────────────────────────────
async function generateTimeline(realIdx, genToken) {
  const myToken = genToken ?? _readerGenToken;
  const tlOuter = $('timeline-outer');
  if (tlOuter) tlOuter.style.display = '';
  $('timeline-section').innerHTML = timelineLoadingHTML();

  try {
    const res  = await fetch(`/api/articles/${realIdx}/timeline`, {method:'POST'});
    if (_readerGenToken !== myToken) return;
    const data = await res.json();
    if (_readerGenToken !== myToken) return;

    if (data.status !== 'success' || !data.timeline?.length) {
      if (tlOuter) tlOuter.style.display = 'none';
      return;
    }

    renderTimeline(data.timeline);
  } catch(e) {
    if (_readerGenToken !== myToken) return;
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

  if (twOuter) twOuter.style.display = '';
  if (igOuter) igOuter.style.display = 'none';

  const loadHTML = `<div class="coverage-loading"><span class="cov-spinner"></span><span>Searching for social reactions…</span></div>`;
  twSec.innerHTML = loadHTML;

  try {
    const res  = await fetch(`/api/articles/${realIdx}/social`, {method:'POST'});
    const data = await res.json();

    if (data.status !== 'success' || !data.social) {
      const msg = esc(data.message || 'No social data available');
      twSec.innerHTML = `<div class="coverage-empty">${msg}</div>`;
      return;
    }

    renderSocialSection('twitter', data.social.twitter, twSec, twOuter, realIdx);
  } catch(e) {
    twSec.innerHTML = `<div class="coverage-empty">Error: ${esc(e.message)}</div>`;
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

  const postsHTML = posts.map((p, pi) => {
    const url    = esc(p.post_url || '#');
    const author = esc(isTw ? `@${p.username||p.author}` : `@${p.author}`);
    const text   = esc(isTw ? (p.text||'') : (p.caption||''));
    const hasStats = (p.likes||0) + (p.reposts||0) + (p.replies||0) + (p.comments_count||0) > 0;
    const stats  = !hasStats ? '' : isTw
      ? `<span class="soc-stat">❤ ${p.likes||0}</span><span class="soc-stat">🔁 ${p.reposts||0}</span><span class="soc-stat">💬 ${p.replies||0}</span>`
      : `<span class="soc-stat">❤ ${p.likes||0}</span><span class="soc-stat">💬 ${p.comments_count||0}</span>`;

    const commentsHTML = !isTw && p.comments?.length
      ? `<ul class="soc-comments">${p.comments.map(c=>`<li class="soc-comment"><span class="soc-comment-author">@${esc(c.author)}</span> ${esc(c.text)}</li>`).join('')}</ul>`
      : '';

    const hasUrl = url && url !== '#' && url !== '';
    const tweetData = isTw ? esc(JSON.stringify(p)) : '';
    const checkboxHTML = isTw ? `
      <label class="soc-select-label" title="Select for publishing">
        <input type="checkbox" class="soc-select-cb" data-pidx="${pi}" data-tweet="${tweetData}" onchange="toggleTweetSelect(this, ${_realIdx})"/>
        <span class="soc-select-tick"></span>
      </label>` : '';

    return `
      <div class="soc-post${isTw ? ' soc-post-selectable' : ''}" id="soc-post-${platform}-${pi}">
        <div class="soc-post-header">
          ${checkboxHTML}
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

  const rawSummary = (data.summary||'');

  // Extract sentiment from first line (e.g. "~60% supportive, ~30% critical")
  const sentimentMatch = rawSummary.match(/(?:sentiment|mood|tone|reaction)[^.]*?(angry|furious|outraged|disappointed|critical|negative|concerned|anxious|worried|divided|mixed|split|neutral|cautious|skeptic|supportive|positive|hopeful|excited|celebratory|proud|happy|enthusiastic|amused|sarcastic|mocking|humorous|indifferent)/i);
  const sentimentWord = sentimentMatch ? sentimentMatch[1].toLowerCase() : null;

  const sentimentConfig = {
    angry:'neg', furious:'neg', outraged:'neg', disappointed:'neg', critical:'neg', negative:'neg', concerned:'neg', anxious:'neg', worried:'neg',
    divided:'mix', mixed:'mix', split:'mix', neutral:'mix', cautious:'mix', skeptic:'mix',
    supportive:'pos', positive:'pos', hopeful:'pos', excited:'pos', celebratory:'pos', proud:'pos', happy:'pos', enthusiastic:'pos',
    amused:'fun', sarcastic:'fun', mocking:'fun', humorous:'fun',
    indifferent:'neu',
  };
  const sentimentType = sentimentWord ? (sentimentConfig[sentimentWord] || 'mix') : 'mix';
  const sentimentLabels = { neg:'Negative', mix:'Mixed', pos:'Positive', fun:'Playful', neu:'Neutral' };
  const sentimentIcons  = { neg:'😠', mix:'⚖️', pos:'😊', fun:'😏', neu:'😐' };

  const formattedSummary = rawSummary
    .replace(/^#{1,2}\s+\*?\*?Sentiment Overview\*?\*?\s*$/gim, '')
    .replace(/^#{1,2}\s+\*?\*?Public Mood\*?\*?\s*$/gim, '')
    .replace(/^#{2}\s+(.+)$/gm,'<h3 class="soc-sum-head" contenteditable="false">$1</h3>')
    .replace(/^#{3}\s+(.+)$/gm,'<h4 class="soc-sum-head" contenteditable="false">$1</h4>')
    .replace(/^[-•]\s+(.+)$/gm,'<li class="soc-sum-bullet">$1</li>')
    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
    .replace(/\*(.+?)\*/g,'<em>$1</em>')
    .replace(/@(\w+)/g,'<span class="soc-mention">@$1</span>')
    .split(/\n+/).filter(Boolean)
    .map(l => {
      if (l.startsWith('<h') || l.startsWith('<li')) return l;
      return `<p class="soc-sum-para" contenteditable="false">${l}</p>`;
    })
    .join('')
    .replace(/(<li[^>]*>.*?<\/li>\s*)+/g, m => `<ul class="soc-sum-list">${m}</ul>`);

  const summaryHTML = `<div class="soc-summary" id="${platform}-summary-body">${formattedSummary}</div>`;

  const sentimentBadge = `<div class="soc-sentiment-badge soc-sentiment-${sentimentType}">
    <span class="soc-sentiment-icon">${sentimentIcons[sentimentType]}</span>
    <span class="soc-sentiment-text">${sentimentLabels[sentimentType]}</span>
  </div>`;

  secEl.innerHTML = `
    <div class="social-result">
      <div class="soc-analysis-block">
        <div class="soc-analysis-header">
          <div class="soc-analysis-title">Sentiment Analysis</div>
          ${sentimentBadge}
        </div>
        ${summaryHTML}
      </div>
      <div class="soc-posts-block">
        <div class="soc-posts-header">
          <div class="soc-posts-label">${posts.length} post${posts.length!==1?'s':''} found</div>
          ${isTw ? `<div class="soc-select-counter" id="soc-select-counter">
            <span class="soc-select-count" id="soc-select-count">0</span> selected for publish
          </div>` : ''}
        </div>
        <div class="soc-posts-list">${postsHTML}</div>
      </div>
      <div class="soc-actions">
        <button class="edit-briefing-btn" id="${editId}" onclick="toggleSocialEdit('${platform}')">
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M9 1l3 3-7 7H2V8l7-7z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>
          Edit
        </button>
      </div>
    </div>`;

  // Highlight top 3 tweets with golden styling
  if (isTw && posts.length > 0) {
    const topCount = Math.min(3, posts.length);
    for (let i = 0; i < topCount; i++) {
      const el = document.getElementById(`soc-post-${platform}-${i}`);
      if (el) el.classList.add('soc-post-top');
    }
  }
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

function toggleTweetSelect(cb, realIdx) {
  const key = String(realIdx);
  if (!_selectedTweets[key]) _selectedTweets[key] = [];

  const tweet = JSON.parse(cb.dataset.tweet);
  const card  = cb.closest('.soc-post');

  if (cb.checked) {
    _selectedTweets[key].push(tweet);
    if (card) card.classList.add('soc-post-selected');
  } else {
    _selectedTweets[key] = _selectedTweets[key].filter(t => t.post_url !== tweet.post_url);
    if (card) card.classList.remove('soc-post-selected');
  }

  const countEl = $('soc-select-count');
  const counterEl = $('soc-select-counter');
  if (countEl) countEl.textContent = _selectedTweets[key].length;
  if (counterEl) counterEl.classList.toggle('has-selected', _selectedTweets[key].length > 0);
}

function getSelectedTweets() {
  const key = String(CURRENT_REAL_IDX);
  return _selectedTweets[key] || [];
}

// ── Image picker helper ──────────────────────────────────────
function selectImage(url, type) {
  SELECTED_IMAGE = url;
  document.querySelectorAll('.img-pick-option').forEach(el => {
    const isThis = el.dataset.type === type;
    el.classList.toggle('img-pick-selected', isThis);
    el.querySelector('.img-pick-radio').classList.toggle('img-pick-radio-on', isThis);
  });
  // Live-update social preview images if the modal already has posts loaded
  const realIdx = CURRENT_REAL_IDX;
  if (realIdx >= 0 && _socialPostsCache[realIdx]) {
    _socialImageCache[realIdx] = url;
    const igImg = $('sp-ig-image');
    if (igImg) igImg.innerHTML = `<img src="${esc(url)}" alt="" style="width:100%;aspect-ratio:1/1;object-fit:cover;display:block" onerror="this.parentElement.innerHTML='<div class=sp-img-err>Image unavailable</div>'">`;
    _setCardImage('sp-tw-image', url, 'sp-tw-img-wrap');
    _setCardImage('sp-fb-image', url, 'sp-fb-img-wrap');
    _setCardImage('sp-li-image', url, 'sp-li-img-wrap');
  }
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

      const aiOpt = _makePickOption('ai', 'Generated', data.model_label || 'FLUX', data.image_url, true);
      picker.appendChild(aiOpt);

      if (scrapedUrl) {
        const scrapedOpt = _makePickOption('original', 'Original Photo', 'Scraped', scrapedUrl, false);
        picker.appendChild(scrapedOpt);
      }

      // Replace old image area with picker
      if (scrapedImg) scrapedImg.remove();
      container.insertBefore(picker, container.firstChild);

      // Regen button below picker
      const regenBtn = document.createElement('button');
      regenBtn.id = 'reader-regen-btn';
      regenBtn.className = 'reader-regen-btn';
      regenBtn.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> Regenerate Image`;
      regenBtn.onclick = () => regenReaderImage(realIdx);
      container.insertBefore(regenBtn, picker.nextSibling);
    }
  } catch(_e) {
    const statusEl = $('r-ai-status');
    if (statusEl) statusEl.remove();
  }
}

async function regenReaderImage(realIdx) {
  const btn = $('reader-regen-btn');
  if (!btn) return;

  btn.disabled = true;
  btn.innerHTML = `<span class="reader-regen-spinner"></span> Generating…`;

  try {
    const res  = await fetch(`/api/articles/${realIdx}/image?force=true`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));

    if (res.ok && data.status === 'success') {
      // Update AI image thumbnail and selection
      const aiOpt = document.querySelector('.img-pick-option[data-type="ai"]');
      if (aiOpt) {
        const thumb = aiOpt.querySelector('.img-pick-thumb');
        if (thumb) thumb.src = data.image_url;
        aiOpt.onclick = () => selectImage(data.image_url, 'ai');
      }
      // Re-select AI image with new URL
      SELECTED_IMAGE = data.image_url;
      selectImage(data.image_url, 'ai');
    } else {
      toast('err', data.message || 'Image generation failed');
    }
  } catch(e) {
    toast('err', `Regen error: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> Regenerate Image`;
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
  _readerGenToken++;            // abort any in-flight generation
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
      ALL = (data.articles||[]).map((a, i) => { a._serverIdx = i; return a; });
      refreshMeta(ALL);
      buildTicker(ALL);
      PAGE = 1;
      applyFilters();
      triggerAutoGenForPage();
    }
  } catch(e) { console.error(e); }
}

// ── Pagination ──────────────────────────────────────────────
function renderPagination(total, totalPages) {
  let el = document.getElementById('pagination');
  if (!el) {
    el = document.createElement('div');
    el.id = 'pagination';
    el.className = 'pagination';
    const feedArea = document.getElementById('feed-area');
    if (feedArea) feedArea.appendChild(el);
  }
  if (totalPages <= 1) { el.innerHTML = ''; return; }

  let html = '';
  if (PAGE > 1) html += `<button class="page-btn" onclick="goToPage(${PAGE-1})">← Prev</button>`;
  for (let p = 1; p <= totalPages; p++) {
    if (totalPages > 7 && Math.abs(p - PAGE) > 2 && p !== 1 && p !== totalPages) {
      if (p === 2 || p === totalPages - 1) html += `<span class="page-dots">…</span>`;
      continue;
    }
    html += `<button class="page-btn${p===PAGE?' active':''}" onclick="goToPage(${p})">${p}</button>`;
  }
  if (PAGE < totalPages) html += `<button class="page-btn" onclick="goToPage(${PAGE+1})">Next →</button>`;
  html += `<span class="page-info">${total} articles</span>`;
  el.innerHTML = html;
}

function goToPage(p) {
  PAGE = p;
  applyFilters();
  triggerAutoGenForPage();
  window.scrollTo({top: 0, behavior: 'smooth'});
}

// ── Auto-generation ─────────────────────────────────────────
function triggerAutoGenForPage() {
  const indices = SHOWN.map(a => a._serverIdx).filter(i => i !== undefined);
  if (!indices.length) return;

  fetch('/api/auto-generate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({indices}),
  }).then(r => r.json()).then(data => {
    if (data.count > 0) {
      toast('info', `Generating content for ${data.count} articles…`);
      startAutoGenPoll();
    }
  }).catch(e => console.error('Auto-gen trigger failed:', e));
}

function startAutoGenPoll() {
  if (_autoGenPollId) clearInterval(_autoGenPollId);
  _autoGenPollId = setInterval(pollAutoGenStatus, 3000);
}

async function pollAutoGenStatus() {
  try {
    const data = await fetch('/api/auto-generate/status').then(r => r.json());
    if (data.status !== 'success') return;
    const states = data.states;

    let running = 0, done = 0, total = 0;
    for (const idx of SHOWN.map(a => a._serverIdx)) {
      const st = states[String(idx)];
      if (!st) continue;
      total++;
      if (st === 'done') done++;
      else if (st === 'running' || st === 'pending') running++;
    }

    // Update cards with generation status
    SHOWN.forEach((a, i) => {
      const st = states[String(a._serverIdx)];
      const card = document.querySelector(`.card[onclick="openReader(${i})"]`);
      if (!card) return;
      let badge = card.querySelector('.autogen-badge');
      if (st === 'done') {
        if (badge) badge.remove();
        if (!card.classList.contains('autogen-done')) card.classList.add('autogen-done');
      } else if (st === 'running') {
        if (!badge) {
          badge = document.createElement('div');
          badge.className = 'autogen-badge';
          card.appendChild(badge);
        }
        badge.innerHTML = '<span class="autogen-spinner"></span> Generating…';
      }
    });

    if (running === 0 && total > 0) {
      clearInterval(_autoGenPollId);
      _autoGenPollId = null;
      if (done > 0) toast('ok', `${done} articles ready`);
    }
  } catch(e) { console.error('Auto-gen poll error:', e); }
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
  document.querySelectorAll('#nav-list .nav-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  CAT=btn.dataset.cat; SRC=null; COUNTRY=null;
  $('cbar-inner')?.querySelectorAll('.cbar-chip').forEach(b=>b.classList.toggle('active',b.dataset.country===''));
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
    tv_sent:              false,
    radio_sent:           false,
    last_at:              null,
  };
  ACTIVITY_MAP[realIdx] = Object.assign(existing, patch, { last_at: new Date().toISOString() });
}

async function refreshActivityCount() {
  const el = $('cnt-activity');
  if (!el) return;
  try {
    const res  = await fetch('/api/published-feed');
    const data = await res.json();
    const count = (data.articles || []).length;
    el.textContent = count;
    el.style.display = count > 0 ? '' : 'none';
  } catch {
    // keep whatever value is shown
  }
}

async function renderActivityView() {
  const emptyEl = $('activity-empty');
  const feedEl  = $('pub-feed');
  if (!feedEl) return;

  feedEl.innerHTML = `<div class="pub-feed-loading"><span class="cov-spinner"></span> Loading…</div>`;
  emptyEl.style.display = 'none';

  try {
    const res  = await fetch('/api/published-feed');
    const data = await res.json();
    const articles = data.articles || [];
    const cntEl = $('cnt-activity');
    if (cntEl) { cntEl.textContent = articles.length; cntEl.style.display = articles.length > 0 ? '' : 'none'; }

    if (articles.length === 0) {
      feedEl.innerHTML = '';
      emptyEl.style.display = '';
      return;
    }

    feedEl.innerHTML = articles.map((a, i) => {
      const ago = a.published_at ? relTime(a.published_at) : '—';
      const expiry = a.published_at ? (() => {
        const ms = new Date(a.published_at).getTime() + 2*24*60*60*1000 - Date.now();
        if (ms <= 0) return 'Expiring soon';
        const h = Math.floor(ms/3600000);
        return h < 24 ? `Expires in ${h}h` : `Expires in ${Math.floor(h/24)}d`;
      })() : '';
      return `
        <div class="pub-card" onclick="openPubDetail(${i})" data-idx="${i}">
          <div class="pub-card-top">
            <span class="pub-card-cat">${esc(a.category||'News')}</span>
            <span class="pub-card-expiry">${esc(expiry)}</span>
          </div>
          <h3 class="pub-card-heading">${esc(a.heading||'Untitled')}</h3>
          ${a.sub_heading ? `<p class="pub-card-sub">${esc(a.sub_heading)}</p>` : ''}
          <div class="pub-card-meta">
            ${a.reporter ? `<span>✍ ${esc(a.reporter)}</span>` : ''}
            ${a.hocalwire_id ? `<span class="pub-card-id">ID: ${esc(a.hocalwire_id)}</span>` : ''}
            <span class="pub-card-time">${ago}</span>
          </div>
        </div>`;
    }).join('');

    // Store for detail view
    window._PUB_FEED_DATA = articles;

  } catch(e) {
    feedEl.innerHTML = `<div class="pub-feed-loading" style="color:var(--err)">Failed to load feed</div>`;
  }
}

function openPubDetail(i) {
  const a = (window._PUB_FEED_DATA || [])[i];
  if (!a) return;
  const content = $('pub-detail-content');
  const ago = a.published_at ? relTime(a.published_at) : '—';
  content.innerHTML = `
    <div class="pub-detail-cat">${esc(a.category||'News')}</div>
    <h2 class="pub-detail-heading">${esc(a.heading||'Untitled')}</h2>
    ${a.sub_heading ? `<p class="pub-detail-sub">${esc(a.sub_heading)}</p>` : ''}
    <div class="pub-detail-row">
      ${a.reporter  ? `<span>✍ ${esc(a.reporter)}</span>` : ''}
      <span>🕐 Published ${ago}</span>
      ${a.hocalwire_id ? `<span>🔗 Hocalwire ID: <strong>${esc(a.hocalwire_id)}</strong></span>` : ''}
    </div>
    ${a.image_url ? `<img class="pub-detail-img" src="${esc(a.image_url)}" alt="" onerror="this.style.display='none'">` : ''}
    ${a.body_html  ? `<div class="pub-detail-body">${a.body_html}</div>` : ''}
  `;
  $('pub-detail-overlay').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

function closePubDetail(e) {
  if (e && e.target !== $('pub-detail-overlay')) return;
  $('pub-detail-overlay').classList.add('hidden');
  document.body.style.overflow = '';
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

// ── Article Chatbot ───────────────────────────────────────────
let CHAT_HISTORY = [];

function resetArticleChat() {
  CHAT_HISTORY = [];
  const msgs = $('chat-messages');
  if (msgs) msgs.innerHTML = '';
  const inp = $('chat-input');
  if (inp) inp.value = '';
}

function appendChatBubble(role, text) {
  const msgs = $('chat-messages');
  if (!msgs) return;
  const div = document.createElement('div');
  div.className = `chat-bubble chat-${role}`;
  div.textContent = text;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

async function sendChatMessage(e) {
  e.preventDefault();
  const inp  = $('chat-input');
  const btn  = $('chat-send-btn');
  const text = inp.value.trim();
  if (!text || CURRENT_REAL_IDX == null) return;

  appendChatBubble('user', text);
  CHAT_HISTORY.push({role: 'user', content: text});
  inp.value = '';
  btn.disabled = true;

  const thinking = document.createElement('div');
  thinking.className = 'chat-bubble chat-assistant chat-thinking';
  thinking.textContent = '…';
  $('chat-messages').appendChild(thinking);
  $('chat-messages').scrollTop = $('chat-messages').scrollHeight;

  try {
    const res  = await fetch(`/api/articles/${CURRENT_REAL_IDX}/chat`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, history: CHAT_HISTORY.slice(0, -1)}),
    });
    const data = await res.json();
    thinking.remove();
    if (data.status === 'success') {
      appendChatBubble('assistant', data.answer);
      CHAT_HISTORY.push({role: 'assistant', content: data.answer});
    } else {
      appendChatBubble('assistant', 'Error: ' + (data.message || 'Unknown error'));
    }
  } catch (err) {
    thinking.remove();
    appendChatBubble('assistant', 'Network error. Please try again.');
  } finally {
    btn.disabled = false;
    inp.focus();
  }
}

// ── Init ──────────────────────────────────────────────────────
loadArticles();
refreshActivityCount();
