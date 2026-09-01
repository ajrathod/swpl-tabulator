// Thin fetch wrapper around the existing Flask JSON API.
// In dev, Vite proxies /api to the Flask server (see vite.config.js).
// In production, this is served from the same origin as Flask, so relative paths just work.

async function get(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${url} → ${res.status}`);
  return res.json();
}

export const api = {
  groups: (compId) => get(`/api/${compId}/groups`),
  group: (compId, group) => get(`/api/${compId}/group/${encodeURIComponent(group)}`),
  standings: (compId, group) => get(`/api/${compId}/standings/${encodeURIComponent(group)}`),
  relays: (compId) => get(`/api/${compId}/relays`),
  schedule: (compId) => get(`/api/comp/${compId}/schedule`),
  checkinStatus: (compId) => get(`/api/comp/${compId}/checkin_status`),
  scratches: (compId, group) => get(`/api/${compId}/scratches/${encodeURIComponent(group)}`),
  standbys: (compId, group) => get(`/api/${compId}/standbys/${encodeURIComponent(group)}`),
  searchAthletes: (compId, q) => get(`/api/athletes/search?comp_id=${compId}&q=${encodeURIComponent(q)}`),
  athleteHistory: (panel) => get(`/api/athlete/${encodeURIComponent(panel)}`),
};
