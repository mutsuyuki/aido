/**
 * Shared SVG icon helpers
 * Inline SVG so CSS custom properties (var(--green) etc.) work correctly.
 */

export function svgCheck() {
  return `<svg class="status-icon" viewBox="0 0 20 20">
    <circle cx="10" cy="10" r="9" fill="rgba(63,185,80,0.15)" stroke="var(--green)" stroke-width="1.5"/>
    <path d="M6 10.5l2.5 2.5 5.5-5.5" fill="none" stroke="var(--green)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

export function svgCross() {
  return `<svg class="status-icon" viewBox="0 0 20 20">
    <circle cx="10" cy="10" r="9" fill="rgba(248,81,73,0.15)" stroke="var(--red)" stroke-width="1.5"/>
    <path d="M7 7l6 6M13 7l-6 6" fill="none" stroke="var(--red)" stroke-width="2" stroke-linecap="round"/>
  </svg>`;
}
