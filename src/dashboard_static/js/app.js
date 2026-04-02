/**
 * Main application - routing, navigation, active run banner, browser history
 */
import { fetchProject, fetchStatus, connectLive, onLiveUpdate } from './api.js';
import { renderProjectHome } from './components/project-home.js';
import { renderPipelineView } from './components/pipeline-view.js';
import { renderPhaseDetail } from './components/phase-detail.js';
import { renderConfigPreview } from './components/config-preview.js';

const appEl = document.getElementById('app');
const navEl = document.getElementById('nav');
const bannerSlot = document.createElement('div');
bannerSlot.id = 'active-banner';
document.querySelector('header').after(bannerSlot);

let currentView = null;

// ==========================================
// Navigation with browser history
// ==========================================

function navigate(view, params = {}, pushState = true) {
  currentView = { view, params };

  if (pushState) {
    const url = buildUrl(view, params);
    history.pushState({ view, params }, '', url);
  }

  renderNav();

  switch (view) {
    case 'home':    viewHome(); break;
    case 'config':  viewConfig(params); break;
    case 'run':     viewRun(params); break;
    case 'phase':   viewPhaseDetail(params); break;
    default:        viewHome();
  }
}

function buildUrl(view, params) {
  switch (view) {
    case 'config': return `#config/${params.name}`;
    case 'run':    return `#run/${params.runId}`;
    case 'phase':  return `#run/${params.runId}/${params.phaseId}${params.attemptIdx != null ? '/' + params.attemptIdx : ''}`;
    default:       return '#';
  }
}

function parseHash() {
  const hash = location.hash.slice(1);
  if (!hash) return { view: 'home', params: {} };

  const parts = hash.split('/');
  if (parts[0] === 'config' && parts[1]) {
    return { view: 'config', params: { name: parts[1] } };
  }
  if (parts[0] === 'run' && parts[1] && parts[2]) {
    const params = { runId: parts[1], phaseId: parts[2] };
    if (parts[3] != null) params.attemptIdx = parseInt(parts[3]);
    return { view: 'phase', params };
  }
  if (parts[0] === 'run' && parts[1]) {
    return { view: 'run', params: { runId: parts[1] } };
  }
  return { view: 'home', params: {} };
}

window.addEventListener('popstate', (e) => {
  if (e.state) {
    navigate(e.state.view, e.state.params, false);
  } else {
    const { view, params } = parseHash();
    navigate(view, params, false);
  }
});

function renderNav() {
  const isHome = currentView?.view === 'home';
  const crumbs = [`<span class="nav-item${isHome ? ' active' : ''}" data-view="home">Home</span>`];

  if (currentView?.view === 'config') {
    crumbs.push(`<span style="color:var(--text2)">></span>`);
    crumbs.push(`<span class="nav-item active">${currentView.params.name}</span>`);
  } else if (currentView?.view === 'run') {
    crumbs.push(`<span style="color:var(--text2)">></span>`);
    crumbs.push(`<span class="nav-item active">${currentView.params.runId}</span>`);
  } else if (currentView?.view === 'phase') {
    crumbs.push(`<span style="color:var(--text2)">></span>`);
    crumbs.push(`<span class="nav-item" data-view="run" data-run="${currentView.params.runId}">${currentView.params.runId}</span>`);
    crumbs.push(`<span style="color:var(--text2)">></span>`);
    crumbs.push(`<span class="nav-item active">${currentView.params.phaseId}</span>`);
  }

  navEl.innerHTML = crumbs.join(' ');
  navEl.querySelectorAll('.nav-item[data-view]').forEach(el => {
    el.addEventListener('click', () => {
      const view = el.dataset.view;
      if (view === 'run') {
        navigate('run', { runId: el.dataset.run });
      } else {
        navigate(view);
      }
    });
  });
}

// ==========================================
// Views
// ==========================================

function viewHome() {
  appEl.innerHTML = '';
  renderProjectHome(appEl, {
    onSelectConfig: (name) => navigate('config', { name }),
    onSelectRun: (runId) => navigate('run', { runId }),
  });
}

function viewConfig(params) {
  appEl.innerHTML = '';
  renderConfigPreview(appEl, params.name);
}

function viewRun(params) {
  appEl.innerHTML = '';
  renderPipelineView(appEl, params.runId, (phaseId, attemptIdx) => {
    navigate('phase', { runId: params.runId, phaseId, attemptIdx });
  });
}

function viewPhaseDetail(params) {
  appEl.innerHTML = '';
  renderPhaseDetail(appEl, params.runId, params.phaseId, params.attemptIdx, () => {
    navigate('run', { runId: params.runId });
  });
}

// ==========================================
// Active run banner
// ==========================================

async function updateBanner() {
  const status = await fetchStatus();
  if (!status) {
    bannerSlot.innerHTML = '';
    return;
  }

  const { run_id, total_phases, completed, failed, current_phase, current_title, current_attempt, config_name } = status;
  const progress = total_phases ? `${(completed || 0) + (failed || 0)}/${total_phases}` : '...';
  const phaseInfo = current_title || current_phase || '...';
  const attemptInfo = current_attempt ? ` (attempt ${current_attempt})` : '';
  const configInfo = config_name ? `${config_name}` : '';

  bannerSlot.innerHTML = `
    <div class="active-banner" id="banner-click">
      <span class="live-dot"></span>
      <strong>RUNNING</strong>
      ${configInfo ? `<span style="color:var(--text2);margin:0 8px;">|</span>${configInfo}` : ''}
      <span style="color:var(--text2);margin:0 8px;">|</span>
      Phase ${progress}: ${phaseInfo}${attemptInfo}
      <span style="margin-left:auto;color:var(--accent);cursor:pointer;">View details &rarr;</span>
    </div>
  `;
  bannerSlot.querySelector('#banner-click')?.addEventListener('click', () => {
    navigate('run', { runId: run_id });
  });
}

// ==========================================
// Init
// ==========================================

async function init() {
  connectLive();

  onLiveUpdate(() => {
    updateBanner();
    if (currentView) {
      const { view, params } = currentView;
      if (view === 'run') viewRun(params);
      else if (view === 'home') viewHome();
    }
  });

  setInterval(updateBanner, 10000);
  updateBanner();

  // Restore from URL hash or go home
  const { view, params } = parseHash();
  navigate(view, params);
}

init();
