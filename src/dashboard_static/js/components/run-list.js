/**
 * Run list view - shows all available pipeline runs
 */
import { esc, formatTimestamp } from '../common/utils.js';
import { sectionTitle, emptyCard, runStatusBadge, miniBar } from '../common/ui.js';

export function renderRunList(container, runs, onSelect) {
  container.innerHTML = sectionTitle('Pipeline Runs');

  if (runs.length === 0) {
    container.innerHTML += emptyCard('No runs found.');
    return;
  }

  for (const run of runs) {
    const el = document.createElement('div');
    el.className = 'run-item';

    const completed = run.completed || 0;
    const failed = run.failed || 0;
    const total = run.total_phases || 0;
    const pending = total - completed - failed;

    const badge = runStatusBadge({ completed, failed, total, inProgress: completed > 0 && completed < total && failed === 0 });
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

    el.addEventListener('click', () => onSelect(run.id));
    container.appendChild(el);
  }
}
