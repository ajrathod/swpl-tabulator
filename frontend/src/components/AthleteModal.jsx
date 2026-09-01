import { useEffect, useState } from "react";
import { api } from "../api/client";
import "./AthleteModal.css";

export default function AthleteModal({ panel, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api.athleteHistory(panel).then((d) => !cancelled && setData(d)).catch((e) => !cancelled && setError(e));
    return () => { cancelled = true; };
  }, [panel]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        {!data && !error && <div className="modal-loading">Loading…</div>}
        {error && <div className="modal-loading">Couldn’t load this skater’s history.</div>}
        {data && (
          <>
            <div className="modal-head">
              <h2>{data.name}</h2>
              <div className="brand-meta">
                <span className="mono">#{data.panel}</span>
                {data.club && <span>{data.club}</span>}
                {data.age ? <span>Age {data.age}</span> : null}
              </div>
            </div>

            <div className="stat-row">
              <Stat label="Finals" value={data.stats.total_finals} />
              <Stat label="Podiums" value={data.stats.podiums} />
              <Stat label="Gold" value={data.stats.gold} accent="gold" />
              <Stat label="Silver" value={data.stats.silver} accent="silver" />
              <Stat label="Bronze" value={data.stats.bronze} accent="bronze" />
            </div>

            <div className="history-list">
              {data.history.length === 0 && (
                <div className="modal-loading">No final results yet.</div>
              )}
              {data.history.map((h, i) => (
                <div className="history-row" key={i}>
                  <div className="history-main">
                    <span className="entry-name">{h.event}</span>
                    <span className="entry-meta">{h.competition} · {h.date}</span>
                  </div>
                  <span className={`lane-badge ${badgeClass(h.placement)}`}>{h.placement || "—"}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function badgeClass(p) {
  if (p === "1") return "badge-gold";
  if (p === "2") return "badge-silver";
  if (p === "3") return "badge-bronze";
  if (p === "DQ") return "badge-dq";
  return "badge-plain";
}

function Stat({ label, value, accent }) {
  return (
    <div className="stat-cell">
      <div className={`stat-value ${accent ? `stat-${accent}` : ""}`}>{value ?? 0}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
