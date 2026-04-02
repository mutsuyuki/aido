/**
 * API client + WebSocket connection
 */

const BASE = '';

export async function fetchProject() {
  const res = await fetch(`${BASE}/api/project`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchSettings() {
  const res = await fetch(`${BASE}/api/settings`);
  if (!res.ok) return [];
  return res.json();
}

export async function fetchSetting(name) {
  const res = await fetch(`${BASE}/api/settings/${name}`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchRuns() {
  const res = await fetch(`${BASE}/api/runs`);
  if (!res.ok) return [];
  return res.json();
}

export async function fetchRun(runId) {
  const res = await fetch(`${BASE}/api/runs/${runId}`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchPhase(runId, phaseId) {
  const res = await fetch(`${BASE}/api/runs/${runId}/phases/${phaseId}`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchStatus() {
  const res = await fetch(`${BASE}/api/status`);
  if (!res.ok) return null;
  return res.json();
}

// ==========================================
// WebSocket
// ==========================================

let _ws = null;
let _listeners = [];
let _reconnectTimer = null;

export function onLiveUpdate(callback) {
  _listeners.push(callback);
  return () => { _listeners = _listeners.filter(l => l !== callback); };
}

export function connectLive() {
  if (_ws && _ws.readyState <= 1) return;

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  _ws = new WebSocket(`${proto}//${location.host}/ws/live`);

  _ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      _listeners.forEach(fn => fn(data));
    } catch (e) { /* ignore */ }
  };

  _ws.onclose = () => {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = setTimeout(connectLive, 3000);
  };

  _ws.onerror = () => { _ws.close(); };
}
