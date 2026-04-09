/**
 * Shared UI component helpers
 */
import { esc } from './utils.js';
import { svgCheck, svgCross } from './icons.js';

// ==========================================
// Badge
// ==========================================

export function badge(text, type) {
  return `<span class="badge badge-${type}">${text}</span>`;
}

export function statusBadge(status, { live = false } = {}) {
  if (live || status === 'running') {
    return badge(`<span class="live-dot"></span>RUNNING`, 'running');
  }
  switch (status) {
    case 'accepted': return badge('DONE', 'accepted');
    case 'failed':   return badge('FAILED', 'failed');
    default:         return badge('PENDING', 'pending');
  }
}

export function runStatusBadge({ completed = 0, failed = 0, total = 0, inProgress = false }) {
  if (inProgress) return statusBadge('running', { live: true });
  if (failed > 0) return statusBadge('failed');
  if (completed === total && total > 0) return statusBadge('accepted');
  return statusBadge('pending');
}

// ==========================================
// Card
// ==========================================

export function card(content, { title = '', className = '' } = {}) {
  const titleHtml = title ? `<div class="card-title" style="margin-bottom:8px;">${esc(title)}</div>` : '';
  return `<div class="card ${className}">${titleHtml}${content}</div>`;
}

export function emptyCard(message) {
  return card(`<p style="color:var(--text2)">${esc(message)}</p>`);
}

export function errorCard(message) {
  return card(`<p style="color:var(--red)">${esc(message)}</p>`);
}

// ==========================================
// Section title
// ==========================================

export function sectionTitle(text, count = null) {
  const countHtml = count != null
    ? ` <span style="color:var(--text2);font-size:13px;">(${count})</span>`
    : '';
  return `<h3 class="section-title">${esc(text)}${countHtml}</h3>`;
}

export function sectionDesc(text) {
  return `<p class="section-desc">${esc(text)}</p>`;
}

// ==========================================
// Role tag
// ==========================================

export function roleTag(role) {
  return `<span class="step-role">${esc(role)}</span>`;
}

// ==========================================
// Step status (role + check/cross icon with arrow separator)
// ==========================================

export function stepStatusLine(steps) {
  return steps.map(s => {
    const ok = s.success && !(s.parsed && s.parsed.pass === false);
    return `<span class="step-status" title="${esc(s.role)}/${esc(s.action)}">${esc(s.role)}:${ok ? svgCheck() : svgCross()}</span>`;
  }).join('<span class="step-arrow">\u203A</span>');
}

// ==========================================
// Mini bar (phase status dots)
// ==========================================

export function miniBar(summaries) {
  if (!summaries) return '';
  let html = '<span style="display:inline-flex;gap:2px;margin-left:8px;vertical-align:middle;">';
  for (const [, info] of Object.entries(summaries)) {
    const color = info.status === 'accepted' ? 'var(--green)'
      : info.status === 'failed' ? 'var(--red)' : 'var(--text2)';
    html += `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${color};" title="${info.status} (${info.attempts} attempts)"></span>`;
  }
  html += '</span>';
  return html;
}

// ==========================================
// Info table (key-value pairs)
// ==========================================

export function infoTable(rows) {
  let html = '<table class="agent-table"><tbody>';
  for (const [key, value] of rows) {
    html += `<tr><td style="width:160px;color:var(--text2)">${esc(key)}</td><td>${value}</td></tr>`;
  }
  html += '</tbody></table>';
  return html;
}

// ==========================================
// Subsection (グレータイトル + コンテンツ)
// ==========================================

export function subsectionTitle(text) {
  return `<span style="font-size:13px;color:var(--text2);font-weight:600;">${esc(text)}</span>`;
}

// ==========================================
// Config badge (シアン色のタグ)
// ==========================================

export function configBadge(text) {
  return `<span style="padding:2px 8px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;font-size:11px;color:var(--cyan,#80cbc4);">${esc(text)}</span>`;
}

export function fileBadge(text) {
  return `<span style="padding:3px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;font-size:12px;color:var(--cyan,#80cbc4);">${esc(text)}</span>`;
}

// ==========================================
// Issue item
// ==========================================

export function issueItem(issue) {
  const conf = issue.confidence || '?';
  const confColor = conf >= 90 ? 'var(--red)' : conf >= 80 ? 'var(--yellow)' : 'var(--text2)';
  return `<div class="issue-item">
    <div>
      <span class="issue-confidence" style="color:${confColor}">[${conf}]</span>
      ${esc(issue.description || '')}
      ${issue.file ? `<span class="step-time">${esc(issue.file)}</span>` : ''}
    </div>
    ${issue.fix ? `<div class="issue-fix">${esc(issue.fix)}</div>` : ''}
  </div>`;
}
