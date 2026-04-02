/**
 * Run list view - shows all available pipeline runs
 */

export function renderRunList(container, runs, onSelect) {
  const title = document.createElement('h2');
  title.className = 'section-title';
  title.textContent = 'Pipeline Runs';
  container.appendChild(title);

  if (runs.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'card';
    empty.innerHTML = '<p style="color:var(--text2)">No runs found.</p>';
    container.appendChild(empty);
    return;
  }

  for (const run of runs) {
    const el = document.createElement('div');
    el.className = 'run-item';

    const completed = run.completed || 0;
    const failed = run.failed || 0;
    const total = run.total_phases || 0;
    const pending = total - completed - failed;

    // Determine overall status
    let statusBadge = '';
    if (failed > 0) {
      statusBadge = '<span class="badge badge-failed">FAILED</span>';
    } else if (completed === total && total > 0) {
      statusBadge = '<span class="badge badge-accepted">DONE</span>';
    } else if (completed > 0) {
      statusBadge = '<span class="badge badge-running">RUNNING</span>';
    } else {
      statusBadge = '<span class="badge badge-pending">PENDING</span>';
    }

    // Format timestamp from run ID
    const ts = formatTimestamp(run.id);

    el.innerHTML = `
      <div>
        <div class="run-id">${ts} ${statusBadge}</div>
        <div class="run-project">${run.project || run.id}</div>
      </div>
      <div class="run-stats">
        <span class="stat-ok">${completed} ok</span>
        ${failed > 0 ? `<span class="stat-ng">${failed} ng</span>` : ''}
        ${pending > 0 ? `<span style="color:var(--text2)">${pending} left</span>` : ''}
        <span style="color:var(--text2)">${total} phases</span>
      </div>
    `;

    // Phase summary mini-bar
    if (run.phase_summaries) {
      const bar = createMiniBar(run.phase_summaries);
      el.querySelector('.run-project').appendChild(bar);
    }

    el.addEventListener('click', () => onSelect(run.id));
    container.appendChild(el);
  }
}


function formatTimestamp(runId) {
  // 20260402_005739 -> 2026-04-02 00:57:39
  const m = runId.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/);
  if (!m) return runId;
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`;
}


function createMiniBar(summaries) {
  const bar = document.createElement('span');
  bar.style.cssText = 'display:inline-flex;gap:2px;margin-left:8px;vertical-align:middle;';

  for (const [, info] of Object.entries(summaries)) {
    const dot = document.createElement('span');
    dot.style.cssText = `display:inline-block;width:8px;height:8px;border-radius:2px;`;
    if (info.status === 'accepted') {
      dot.style.background = 'var(--green)';
    } else if (info.status === 'failed') {
      dot.style.background = 'var(--red)';
    } else {
      dot.style.background = 'var(--text2)';
    }
    dot.title = `${info.status} (${info.attempts} attempts)`;
    bar.appendChild(dot);
  }
  return bar;
}
