/**
 * Project home - settings list + runs list on a single page
 */
import { fetchSettings, fetchRuns, fetchStatus } from '../api.js';
import { esc, formatTimestamp } from '../common/utils.js';
import { sectionTitle, sectionDesc, emptyCard, runStatusBadge, miniBar } from '../common/ui.js';

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
    ${sectionTitle('Settings', settings.length)}
    ${sectionDesc('Pipeline configurations — click to preview phases, agent assignments, and constraints')}
  `;

  if (settings.length === 0) {
    settingsSection.innerHTML += emptyCard('No settings files found in settings/');
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
    ${sectionTitle('Runs', runs.length)}
    ${sectionDesc('Pipeline execution history — click to view phase results, attempts, and AI outputs')}
  `;

  if (runs.length === 0) {
    runsSection.innerHTML += emptyCard('No runs yet.');
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
      const aborted = run.aborted || false;

      const badge = runStatusBadge({ completed, failed, total, inProgress, aborted });
      const ts = formatTimestamp(run.id);

      el.innerHTML = `
        <div>
          <div class="run-id">${ts} ${badge}</div>
          <div class="run-project">${esc(run.project || run.id)}${miniBar(run.phase_summaries)}</div>
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
