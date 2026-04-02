/**
 * Pipeline overview - vertical phase list with attempts
 */
import { fetchRun, fetchSettings, fetchSetting } from '../api.js';
import { esc, formatDuration } from '../common/utils.js';
import { statusBadge, sectionTitle, roleTag, stepStatusLine } from '../common/ui.js';

export async function renderPipelineView(container, runId, onPhaseClick) {
  const summary = await fetchRun(runId);
  if (!summary) {
    container.innerHTML = '<div class="card">Run not found.</div>';
    return;
  }

  // Try to find matching config for agent info
  let configData = null;
  const projectName = summary.project || '';
  if (projectName) {
    const settings = await fetchSettings();
    for (const s of settings) {
      if (s.project_name === projectName) {
        configData = await fetchSetting(s.name);
        break;
      }
    }
  }

  // Header
  const header = document.createElement('div');
  header.className = 'card-header';
  header.innerHTML = `
    <div>
      <div class="card-title">${esc(summary.project || runId)}</div>
      <div style="color:var(--text2);font-size:13px;margin-top:4px;">
        Run: ${runId}
        &nbsp;|&nbsp; ${summary.completed?.length || 0}/${summary.total_phases} phases completed
      </div>
    </div>
  `;
  container.appendChild(header);

  // Agent config summary
  if (configData) {
    container.appendChild(renderAgentSummary(configData));
  }

  // Phase results title
  const resultCount = (summary.results || []).length;
  container.innerHTML += sectionTitle('Results', resultCount);

  // Phase list (vertical)
  const list = document.createElement('div');
  list.className = 'pipeline-list';

  for (const phase of summary.results || []) {
    list.appendChild(renderPhaseCard(phase, configData, onPhaseClick));
  }
  container.appendChild(list);

  // Issues
  if (summary.issues?.length > 0) {
    const issuesSection = document.createElement('div');
    issuesSection.innerHTML = sectionTitle('Tracked Issues');
    for (const issue of summary.issues) {
      const el = document.createElement('div');
      el.className = 'issue-item';
      el.textContent = issue;
      issuesSection.appendChild(el);
    }
    container.appendChild(issuesSection);
  }
}


function renderPhaseCard(phase, configData, onPhaseClick) {
  const card = document.createElement('div');
  card.className = 'phase-card';

  // Header
  const badge = statusBadge(phase.status);
  const header = document.createElement('div');
  header.className = 'phase-header';
  header.innerHTML = `
    <span>${esc(phase.title || phase.phase_id)}</span>
    ${badge}
  `;
  card.appendChild(header);

  // Attempts
  const body = document.createElement('div');
  body.className = 'phase-body';

  for (let i = 0; i < (phase.attempts || []).length; i++) {
    const attempt = phase.attempts[i];
    const decision = attempt.decision || 'unknown';
    const row = document.createElement('div');
    row.className = `attempt-row attempt-${decision}`;

    const totalSec = (attempt.steps || []).reduce((sum, s) => sum + (s.elapsed_sec || 0), 0);
    const stepIcons = stepStatusLine(attempt.steps || []);

    row.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <span>try ${attempt.attempt} <span style="color:var(--text2);font-size:12px">${decision}</span></span>
        <span class="step-time">${formatDuration(totalSec)}</span>
      </div>
      <div style="margin-top:2px;font-family:monospace;">${stepIcons}</div>
    `;

    row.addEventListener('click', () => onPhaseClick(phase.phase_id, i));
    body.appendChild(row);
  }
  card.appendChild(body);

  // Agent info footer
  if (configData) {
    const phaseConfig = configData.phases?.find(p => p.id === phase.phase_id);
    if (phaseConfig) {
      const footer = document.createElement('div');
      footer.className = 'phase-agents';
      footer.textContent = (phaseConfig.steps || [])
        .map(s => `${s.role}: ${s.backend}/${s.model?.split('/').pop()}`)
        .join(', ');
      card.appendChild(footer);
    }
  }

  return card;
}


function renderAgentSummary(configData) {
  const card = document.createElement('div');
  card.className = 'card';
  card.style.marginBottom = '16px';

  const roles = new Map();
  for (const phase of configData.phases || []) {
    for (const step of phase.steps || []) {
      if (!roles.has(step.role)) roles.set(step.role, step);
    }
  }

  let html = '<div class="card-title" style="margin-bottom:8px;">Agent Configuration</div>';
  html += '<table class="agent-table"><thead><tr>';
  html += '<th>Role</th><th>Backend</th><th>Model</th><th>Session</th><th>Fallback</th>';
  html += '</tr></thead><tbody>';

  for (const [role, info] of roles) {
    const isShell = role === 'checker' || (info.backend === '?' && info.model === '?');
    const fb = info.fallbacks?.length > 0
      ? info.fallbacks.map(f => `${f.fallback_backend}/${f.fallback_model}`).join(', ')
      : '-';
    html += `<tr>
      <td>${roleTag(role)}</td>
      <td>${isShell ? 'shell' : esc(info.backend)}</td>
      <td>${isShell ? '(commands)' : esc(info.model)}</td>
      <td>${isShell ? '-' : esc(info.session)}</td>
      <td>${fb !== '-' ? `<span class="fallback-info">${esc(fb)}</span>` : '-'}</td>
    </tr>`;
  }

  html += '</tbody></table>';
  card.innerHTML = html;
  return card;
}
