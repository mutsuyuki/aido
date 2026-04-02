/**
 * Config preview - shows YAML configuration, agent assignments, phase structure
 */
import { fetchSetting } from '../api.js';

export async function renderConfigPreview(container, name) {
  const configData = await fetchSetting(name);
  if (!configData || configData.error) {
    container.innerHTML = `<div class="card"><p style="color:var(--red)">Failed to load: ${name}</p></div>`;
    return;
  }

  // Project info
  const infoCard = document.createElement('div');
  infoCard.className = 'card';
  infoCard.innerHTML = `
    <div class="card-title" style="margin-bottom:8px;">${esc(configData.project_name || name)}</div>
    <table class="agent-table">
      <tbody>
        <tr><td style="width:160px;color:var(--text2)">Config File</td><td>${esc(configData.config_file || name)}</td></tr>
        <tr><td style="color:var(--text2)">Work Dir</td><td>${esc(configData.work_dir)}</td></tr>
        <tr><td style="color:var(--text2)">Default Backend</td><td>${esc(configData.generation?.default_backend)} / ${esc(configData.generation?.default_model)}</td></tr>
        <tr><td style="color:var(--text2)">Max Retries</td><td>${configData.generation?.max_retries}</td></tr>
        <tr><td style="color:var(--text2)">Stop on Failure</td><td>${configData.generation?.stop_on_failure ? 'Yes' : 'No'}</td></tr>
        <tr><td style="color:var(--text2)">Leader</td><td>${configData.generation?.use_leader ? 'ON' : 'OFF'}</td></tr>
        <tr><td style="color:var(--text2)">Confidence</td><td>${configData.generation?.confidence_threshold}% (+${configData.generation?.confidence_step}% per retry)</td></tr>
      </tbody>
    </table>
  `;
  container.appendChild(infoCard);

  // Checks
  if (configData.checks?.commands?.length > 0) {
    const checksCard = document.createElement('div');
    checksCard.className = 'card';
    checksCard.innerHTML = `
      <div class="card-title" style="margin-bottom:8px;">Check Commands</div>
      <div class="file-content" style="margin-top:0;">${configData.checks.commands.map(c => esc(c)).join('\n')}</div>
    `;
    container.appendChild(checksCard);
  }

  // Agent matrix
  container.appendChild(renderAgentMatrix(configData));

  // Phases
  const phasesTitle = document.createElement('h3');
  phasesTitle.className = 'section-title';
  phasesTitle.textContent = `Phases (${configData.phases?.length || 0})`;
  container.appendChild(phasesTitle);

  const phasesWrap = document.createElement('div');
  phasesWrap.style.cssText = 'max-width:960px;margin:0 auto;';

  const depGraph = renderDependencyGraph(configData.phases || []);
  if (depGraph) phasesWrap.appendChild(depGraph);

  for (const phase of configData.phases || []) {
    phasesWrap.appendChild(renderPhaseConfig(phase));
  }
  container.appendChild(phasesWrap);
}


function renderAgentMatrix(configData) {
  const card = document.createElement('div');
  card.className = 'card';

  let html = '<div class="card-title" style="margin-bottom:8px;">Agent Assignment Matrix</div>';
  html += '<table class="agent-table"><thead><tr>';
  html += '<th>Phase</th><th>Step</th><th>Role</th><th>Backend / Model</th><th>Session</th><th>Fallback</th>';
  html += '</tr></thead><tbody>';

  for (const phase of configData.phases || []) {
    const steps = phase.steps || [];
    for (let i = 0; i < steps.length; i++) {
      const step = steps[i];
      const fb = step.fallbacks?.length > 0
        ? step.fallbacks.map(f => {
            const patterns = f.error_patterns?.join(', ') || '';
            return `<div class="fallback-info">${esc(f.fallback_backend)}/${esc(f.fallback_model)}<br><span style="font-size:9px;color:var(--text2)">on: ${esc(patterns)}</span></div>`;
          }).join('')
        : '<span style="color:var(--text2)">-</span>';

      const isShell = step.role === 'checker' || (step.backend === '?' && step.model === '?');
      const backendModel = isShell
        ? 'shell / (commands)'
        : `${esc(step.backend)} / ${esc(step.model)}${step.prompt_override ? `<br><span style="font-size:10px;color:var(--text2)">prompt: ${esc(step.prompt_override)}</span>` : ''}`;

      html += `<tr>
        ${i === 0 ? `<td rowspan="${steps.length}" style="vertical-align:top;font-weight:600;">${esc(phase.title || phase.id)}</td>` : ''}
        <td>${esc(step.action)}</td>
        <td><span class="step-role">${esc(step.role)}</span></td>
        <td>${backendModel}</td>
        <td>${isShell ? '-' : esc(step.session)}</td>
        <td>${fb}</td>
      </tr>`;
    }
  }

  html += '</tbody></table>';
  card.innerHTML = html;
  return card;
}


function renderPhaseConfig(phase) {
  const card = document.createElement('div');
  card.className = 'card';

  let html = `<div class="card-header"><div class="card-title">${esc(phase.id)} - ${esc(phase.title)}</div></div>`;

  if (phase.description) {
    html += `<p style="color:var(--text);margin-bottom:14px;font-size:14px;">${esc(phase.description)}</p>`;
  }

  if (phase.dependencies?.length > 0) {
    html += `<div style="margin-bottom:10px;font-size:13px;color:var(--yellow);">Dependencies: ${phase.dependencies.map(d => esc(d)).join(', ')}</div>`;
  }

  if (phase.tasks?.length > 0) {
    html += '<div style="margin-bottom:10px;"><span style="font-size:13px;color:var(--text2);font-weight:600;">Tasks:</span>';
    html += '<ul class="task-list" style="margin-left:16px;">';
    for (const task of phase.tasks) html += `<li>${esc(task)}</li>`;
    html += '</ul></div>';
  }

  if (phase.constraints?.length > 0) {
    html += '<div style="margin-bottom:10px;"><span style="font-size:13px;color:var(--orange);font-weight:600;">Constraints:</span>';
    html += '<ul class="task-list" style="margin-left:16px;">';
    for (const c of phase.constraints) html += `<li style="color:var(--orange);">${esc(c)}</li>`;
    html += '</ul></div>';
  }

  // Steps with agent info
  html += '<div style="font-size:13px;color:var(--text2);font-weight:600;margin-bottom:6px;">Steps:</div>';
  html += '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-left:16px;">';
  for (let i = 0; i < (phase.steps || []).length; i++) {
    const step = phase.steps[i];
    if (i > 0) html += '<span style="color:var(--text2);">&rarr;</span>';
    html += `<span style="padding:5px 10px;background:var(--surface2);border-radius:4px;font-size:13px;">
      <span class="step-role">${esc(step.role)}</span>/${esc(step.action)}
      <span style="color:var(--text2);font-size:12px;">(${esc(step.backend)}/${esc(step.model?.split('/').pop())})</span>
    </span>`;
  }
  html += '</div>';

  card.innerHTML = html;
  return card;
}


function renderDependencyGraph(phases) {
  const hasDeps = phases.some(p => p.dependencies?.length > 0);
  if (!hasDeps) return null;

  const card = document.createElement('div');
  card.className = 'card';
  card.innerHTML = '<div class="card-title" style="margin-bottom:8px;">Dependency Graph</div>';

  const graph = document.createElement('div');
  graph.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;';

  for (let i = 0; i < phases.length; i++) {
    if (i > 0) {
      const arrow = document.createElement('span');
      arrow.className = 'dep-arrow';
      arrow.textContent = '\u2192';
      graph.appendChild(arrow);
    }
    const node = document.createElement('span');
    node.style.cssText = 'padding:6px 12px;background:var(--surface2);border:1px solid var(--border);border-radius:6px;font-size:11px;';
    node.textContent = phases[i].title || phases[i].id;
    graph.appendChild(node);
  }

  card.appendChild(graph);
  return card;
}


function esc(str) {
  if (str == null) return '';
  const div = document.createElement('div');
  div.textContent = String(str);
  return div.innerHTML;
}
