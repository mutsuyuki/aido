/**
 * Project home - settings list + runs list on a single page
 */
import { fetchSettings, fetchRuns, fetchStatus } from '../api.js';

export async function renderProjectHome(container, { onSelectConfig, onSelectRun }) {
  const [settings, runs, status] = await Promise.all([
    fetchSettings(),
    fetchRuns(),
    fetchStatus(),
  ]);

  const activeRunId = status?.run_id || null;

  // Two-column layout wrapper
  const columns = document.createElement('div');
  columns.className = 'home-columns';
  container.appendChild(columns);

  // --- Settings section ---
  const settingsSection = document.createElement('div');
  settingsSection.innerHTML = `
    <h2 class="section-title">Settings <span style="color:var(--text2);font-size:13px;">(${settings.length})</span></h2>
    <p class="section-desc">Pipeline configurations — click to preview phases, agent assignments, and constraints</p>
  `;

  if (settings.length === 0) {
    settingsSection.innerHTML += '<div class="card"><p style="color:var(--text2)">No settings files found in settings/</p></div>';
  } else {
    for (const s of settings) {
      const el = document.createElement('div');
      el.className = 'settings-item';

      const phaseInfo = s.phase_count != null
        ? `${s.phase_count} phases`
        : '';
      const phaseTags = (s.phase_ids || [])
        .map(id => `<span class="phase-tag">${esc(id)}</span>`)
        .join(' ');

      el.innerHTML = `
        <div>
          <div class="settings-item-name">${esc(s.filename)}<span style="color:var(--text2);font-weight:400;margin-left:8px;">${esc(s.project_name || '')}</span></div>
          <div style="margin-top:4px;">${phaseInfo ? `<span style="color:var(--text2);font-size:12px;margin-right:8px;">${phaseInfo}</span>` : ''}${phaseTags}</div>
        </div>
      `;

      el.addEventListener('click', () => onSelectConfig(s.name));
      settingsSection.appendChild(el);
    }
  }
  columns.appendChild(settingsSection);

  // --- Runs section ---
  const runsSection = document.createElement('div');
  runsSection.innerHTML = `
    <h2 class="section-title">Runs <span style="color:var(--text2);font-size:13px;">(${runs.length})</span></h2>
    <p class="section-desc">Pipeline execution history — click to view phase results, attempts, and AI outputs</p>
  `;

  if (runs.length === 0) {
    runsSection.innerHTML += '<div class="card"><p style="color:var(--text2)">No runs yet.</p></div>';
  } else {
    for (const run of runs) {
      const isActive = run.id === activeRunId;
      const el = document.createElement('div');
      el.className = `run-item${isActive ? ' run-item-active' : ''}`;

      const completed = run.completed || 0;
      const failed = run.failed || 0;
      const total = run.total_phases || 0;
      const pending = total - completed - failed;
      const inProgress = run.in_progress || isActive;

      let statusBadge;
      if (isActive || inProgress) {
        statusBadge = '<span class="badge badge-running"><span class="live-dot"></span>RUNNING</span>';
      } else if (failed > 0) {
        statusBadge = '<span class="badge badge-failed">FAILED</span>';
      } else if (completed === total && total > 0) {
        statusBadge = '<span class="badge badge-accepted">DONE</span>';
      } else {
        statusBadge = '<span class="badge badge-pending">PENDING</span>';
      }

      const ts = formatTimestamp(run.id);

      el.innerHTML = `
        <div>
          <div class="run-id">${ts} ${statusBadge}</div>
          <div class="run-project">${esc(run.project || run.id)}${renderMiniBar(run.phase_summaries)}</div>
        </div>
        <div class="run-stats">
          <span class="stat-ok">${completed} ok</span>
          ${failed > 0 ? `<span class="stat-ng">${failed} ng</span>` : ''}
          ${pending > 0 ? `<span style="color:var(--text2)">${pending} left</span>` : ''}
          <span style="color:var(--text2)">${total} phases</span>
        </div>
      `;

      el.addEventListener('click', () => onSelectRun(run.id));
      runsSection.appendChild(el);
    }
  }
  columns.appendChild(runsSection);
}


function formatTimestamp(runId) {
  const m = runId.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/);
  if (!m) return runId;
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`;
}


function renderMiniBar(summaries) {
  if (!summaries) return '';
  let html = '<span style="display:inline-flex;gap:2px;margin-left:8px;vertical-align:middle;">';
  for (const [, info] of Object.entries(summaries)) {
    const color = info.status === 'accepted' ? 'var(--green)' : info.status === 'failed' ? 'var(--red)' : 'var(--text2)';
    html += `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${color};" title="${info.status} (${info.attempts} attempts)"></span>`;
  }
  html += '</span>';
  return html;
}


function esc(str) {
  if (str == null) return '';
  const div = document.createElement('div');
  div.textContent = String(str);
  return div.innerHTML;
}
