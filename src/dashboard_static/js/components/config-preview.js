/**
 * Config preview - shows YAML configuration, agent assignments, phase structure
 */
import { fetchSetting } from '../api.js';
import { esc } from '../common/utils.js';
import { card, errorCard, sectionTitle, roleTag, infoTable, subsectionTitle, configBadge, fileBadge } from '../common/ui.js';

export async function renderConfigPreview(container, name) {
  const configData = await fetchSetting(name);
  if (!configData || configData.error) {
    container.innerHTML = errorCard(`Failed to load: ${name}`);
    return;
  }

  // Project info
  const infoCard = document.createElement('div');
  infoCard.className = 'card';
  infoCard.innerHTML = `
    <div class="card-title" style="margin-bottom:8px;">${esc(configData.project_name || name)}</div>
    ${infoTable([
      ['Config File', esc(configData.config_file || name)],
      ['Work Dir', esc(configData.work_dir)],
      ['Default Backend', `${esc(configData.generation?.default_backend)} / ${esc(configData.generation?.default_model)}`],
      ['Max Retries', configData.generation?.max_retries],
      ['Stop on Failure', configData.generation?.stop_on_failure ? 'Yes' : 'No'],
      ['Leader', configData.generation?.use_leader ? 'ON' : 'OFF'],
      ['Confidence', `${configData.generation?.confidence_threshold}% (+${configData.generation?.confidence_step}% per retry)`],
      ...(configData.generation?.failure_taxonomy && Object.keys(configData.generation.failure_taxonomy).length > 0
        ? [['Failure Taxonomy', Object.entries(configData.generation.failure_taxonomy).map(([k,v]) => `${k}: ${v}`).join(', ')]]
        : []),
    ])}
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
  container.innerHTML += sectionTitle('Phases', configData.phases?.length || 0);

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
  const el = document.createElement('div');
  el.className = 'card';

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
        <td>${roleTag(step.role)}</td>
        <td>${backendModel}</td>
        <td>${isShell ? '-' : esc(step.session)}</td>
        <td>${fb}</td>
      </tr>`;
    }
  }

  html += '</tbody></table>';
  el.innerHTML = html;
  return el;
}


function renderPhaseConfig(phase) {
  const el = document.createElement('div');
  el.className = 'card';

  let html = `<div class="card-header"><div class="card-title">${esc(phase.id)} - ${esc(phase.title)}</div></div>`;

  if (phase.description) {
    html += `<p style="color:var(--text);margin-bottom:14px;font-size:14px;">${esc(phase.description)}</p>`;
  }

  if (phase.dependencies?.length > 0) {
    html += `<div style="margin-bottom:10px;">${subsectionTitle('Dependencies:')} <span style="font-size:13px;">${phase.dependencies.map(d => esc(d)).join(', ')}</span></div>`;
  }

  if (phase.tasks?.length > 0) {
    html += `<div style="margin-bottom:10px;">${subsectionTitle('Tasks:')}`;
    html += '<ul class="task-list" style="margin-left:16px;">';
    for (const task of phase.tasks) html += `<li>${esc(task)}</li>`;
    html += '</ul></div>';
  }

  if (phase.constraints?.length > 0) {
    html += `<div style="margin-bottom:10px;">${subsectionTitle('Constraints:')}`;
    html += '<ul class="task-list" style="margin-left:16px;">';
    for (const c of phase.constraints) html += `<li style="color:var(--orange);">${esc(c)}</li>`;
    html += '</ul></div>';
  }

  if (phase.review_checklist?.length > 0) {
    html += `<div style="margin-bottom:10px;">${subsectionTitle('Review Checklist:')}`;
    html += '<ul class="task-list" style="margin-left:16px;">';
    for (const c of phase.review_checklist) html += `<li style="color:var(--purple,#b388ff);">${esc(c)}</li>`;
    html += '</ul></div>';
  }

  // Outputs（宣言された成果物。phase 完了時に存在チェックされる）
  if (phase.outputs?.length > 0) {
    html += `<div style="margin-bottom:10px;">${subsectionTitle('Outputs:')}`;
    html += '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-left:16px;margin-top:4px;">';
    for (const f of phase.outputs) html += fileBadge(f);
    html += '</div></div>';
  }

  // Steps with agent info
  html += `<div style="margin-bottom:6px;">${subsectionTitle('Steps:')}</div>`;
  html += '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-left:16px;">';
  for (let i = 0; i < (phase.steps || []).length; i++) {
    const step = phase.steps[i];
    if (i > 0) html += '<span style="color:var(--text2);">&rarr;</span>';
    html += `<span style="padding:5px 10px;background:var(--surface2);border-radius:4px;font-size:13px;">
      ${roleTag(step.role)}/${esc(step.action)}
      <span style="color:var(--text2);font-size:12px;">(${esc(step.backend)}/${esc(step.model?.split('/').pop())})</span>
    </span>`;
  }
  html += '</div>';

  // Settings: フラグ・設定値をバッジで表示（常に表示）
  html += `<div style="margin-top:10px;">${subsectionTitle('Settings:')}`;
  html += '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-left:16px;margin-top:4px;">';

  // pass_on_max_retries（常に表示）
  const pomr = phase.pass_on_max_retries ? 'true' : 'false';
  html += configBadge(`pass_on_max_retries: ${pomr}`);

  // contract 系
  if (phase.contract?.checker_must_pass) {
    html += configBadge('checker_must_pass');
  }
  if (phase.contract?.reviewer_confidence_min) {
    html += configBadge(`confidence \u2265 ${phase.contract.reviewer_confidence_min}`);
  }
  if (phase.contract?.forbidden_patterns?.length) {
    html += configBadge(`forbidden: ${phase.contract.forbidden_patterns.join(', ')}`);
  }

  // phase-level overrides
  if (phase.max_retries != null) {
    html += configBadge(`max_retries: ${phase.max_retries}`);
  }
  if (phase.confidence_step != null) {
    html += configBadge(`confidence_step: ${phase.confidence_step}`);
  }

  html += '</div></div>';

  el.innerHTML = html;
  return el;
}


function renderDependencyGraph(phases) {
  const hasDeps = phases.some(p => p.dependencies?.length > 0);
  if (!hasDeps) return null;

  const el = document.createElement('div');
  el.className = 'card';
  el.innerHTML = '<div class="card-title" style="margin-bottom:8px;">Dependency Graph</div>';

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

  el.appendChild(graph);
  return el;
}
