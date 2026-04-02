/**
 * Phase detail view - shows attempts, steps, AI output, review issues
 */
import { fetchPhase } from '../api.js';
import { esc, formatDuration } from '../utils.js';
import { badge, issueItem } from '../ui.js';

export async function renderPhaseDetail(container, runId, phaseId, attemptIdx, onBack) {
  const detail = await fetchPhase(runId, phaseId);
  if (!detail) {
    container.innerHTML = '<div class="card">Phase not found.</div>';
    return;
  }

  // Header with back button
  const header = document.createElement('div');
  header.style.cssText = 'margin-bottom:16px;';
  header.innerHTML = `
    <span class="nav-item" id="back-btn" style="cursor:pointer;">< Back</span>
    <span class="section-title" style="display:inline;margin-left:12px;border:none;">
      ${esc(phaseId)}
    </span>
  `;
  container.appendChild(header);
  header.querySelector('#back-btn').addEventListener('click', onBack);

  // Attempt tabs
  const attempts = detail.attempts || [];
  if (attempts.length === 0) {
    container.innerHTML += '<div class="card">No attempts found.</div>';
    return;
  }

  const selectedIdx = attemptIdx != null ? attemptIdx : attempts.length - 1;

  // Tab bar
  const tabBar = document.createElement('div');
  tabBar.style.cssText = 'margin-bottom:12px;display:flex;gap:4px;';
  attempts.forEach((att, i) => {
    const tab = document.createElement('span');
    tab.className = `file-tab${i === selectedIdx ? ' active' : ''}`;
    const decision = att.log?.decision || '?';
    const decColor = decision === 'accepted' ? 'var(--green)' : 'var(--red)';
    tab.innerHTML = `${esc(att.name)} <span style="color:${decColor};font-size:10px;">${decision}</span>`;
    tab.addEventListener('click', () => {
      container.innerHTML = '';
      renderPhaseDetail(container, runId, phaseId, i, onBack);
    });
    tabBar.appendChild(tab);
  });
  container.appendChild(tabBar);

  // Selected attempt
  const attempt = attempts[selectedIdx];
  renderAttemptDetail(container, attempt);
}


function renderAttemptDetail(container, attempt) {
  const log = attempt.log || {};
  const steps = log.steps || [];
  const files = attempt.files || {};

  // Step timeline
  const timeline = document.createElement('div');
  timeline.className = 'card';
  timeline.innerHTML = '<div class="card-title" style="margin-bottom:12px;">Steps</div>';

  const list = document.createElement('ul');
  list.className = 'step-list';

  for (const step of steps) {
    const isOk = step.success && !(step.parsed && step.parsed.pass === false);
    const li = document.createElement('li');
    li.className = `step-item ${isOk ? 'success' : 'failure'}`;

    const duration = formatDuration(step.elapsed_sec || 0);
    const sessionInfo = step.session_id ? ` session:${step.session_id.substring(0, 8)}` : '';

    li.innerHTML = `
      <div class="step-header">
        <span class="step-role">${esc(step.role)}</span>
        <span>/${esc(step.action)}</span>
        ${badge(isOk ? 'OK' : 'NG', isOk ? 'accepted' : 'failed')}
        <span class="step-time">${duration}${sessionInfo}</span>
      </div>
    `;

    // Show review issues inline
    if (step.parsed && step.parsed.issues && step.parsed.issues.length > 0) {
      const issuesDiv = document.createElement('div');
      issuesDiv.style.cssText = 'margin-top:8px;';
      for (const issue of step.parsed.issues) {
        issuesDiv.innerHTML += issueItem(issue);
      }
      li.appendChild(issuesDiv);
    }

    // Show repair instructions
    if (step.parsed && step.parsed.repair_instructions) {
      const repairEl = document.createElement('div');
      repairEl.style.cssText = 'margin-top:8px;padding:8px;background:rgba(210,153,34,0.1);border-radius:4px;font-size:11px;color:var(--yellow);';
      repairEl.textContent = step.parsed.repair_instructions;
      li.appendChild(repairEl);
    }

    list.appendChild(li);
  }

  timeline.appendChild(list);
  container.appendChild(timeline);

  // File artifacts
  const fileNames = Object.keys(files);
  if (fileNames.length > 0) {
    const fileCard = document.createElement('div');
    fileCard.className = 'card';
    fileCard.innerHTML = '<div class="card-title" style="margin-bottom:12px;">Artifacts</div>';

    // File tabs
    const fileTabBar = document.createElement('div');
    fileTabBar.style.cssText = 'margin-bottom:0;';

    const fileContent = document.createElement('div');
    fileContent.className = 'file-content';

    // Show first file by default
    let activeFile = fileNames[0];

    function showFile(name) {
      activeFile = name;
      const content = files[name] || '';
      // Try to pretty-format JSON
      if (name.endsWith('.json')) {
        try {
          const parsed = JSON.parse(content);
          fileContent.textContent = JSON.stringify(parsed, null, 2);
        } catch {
          fileContent.textContent = content;
        }
      } else {
        fileContent.textContent = content;
      }
      // Update tab active state
      fileTabBar.querySelectorAll('.file-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.file === name);
      });
    }

    for (const name of fileNames) {
      const tab = document.createElement('span');
      tab.className = `file-tab${name === activeFile ? ' active' : ''}`;
      tab.dataset.file = name;
      tab.textContent = name;
      tab.addEventListener('click', () => showFile(name));
      fileTabBar.appendChild(tab);
    }

    fileCard.appendChild(fileTabBar);
    showFile(activeFile);
    fileCard.appendChild(fileContent);
    container.appendChild(fileCard);
  }
}
