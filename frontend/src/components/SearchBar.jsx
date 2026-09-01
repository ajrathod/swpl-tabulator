import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import "./SearchBar.css";

export default function SearchBar({ compId, onPick }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    if (q.trim().length < 2) { setResults([]); return; }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const r = await api.searchAthletes(compId, q.trim());
        if (!cancelled) { setResults(r); setOpen(true); }
      } catch { /* ignore */ }
    }, 220);
    return () => { cancelled = true; clearTimeout(t); };
  }, [q, compId]);

  useEffect(() => {
    const onClick = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  return (
    <div className="search-box" ref={boxRef}>
      <svg className="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none">
        <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
        <path d="M21 21l-4.3-4.3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => results.length && setOpen(true)}
        placeholder="Search skater name or panel #"
        aria-label="Search skaters"
      />
      {open && results.length > 0 && (
        <div className="search-results">
          {results.map((s) => (
            <button
              key={s.panel}
              className="search-result-row"
              onClick={() => { onPick(s); setOpen(false); setQ(""); }}
            >
              <span className="entry-name">{s.full_name}</span>
              <span className="entry-meta">
                <span className="mono">#{s.panel}</span>
                <span>{s.club}</span>
                <span>{s.group}</span>
              </span>
            </button>
          ))}
        </div>
      )}
      {open && q.trim().length >= 2 && results.length === 0 && (
        <div className="search-results">
          <div className="search-empty">No skaters found for “{q}”</div>
        </div>
      )}
    </div>
  );
}
