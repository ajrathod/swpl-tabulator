import "./Header.css";

export default function Header({ comp, liveItem }) {
  return (
    <header className="app-header">
      <div className="header-top">
        <div className="brand">
          <span className="brand-mark">SWPL</span>
          <div className="brand-text">
            <h1>{comp?.name || "Speed Skating Meet"}</h1>
            <div className="brand-meta">
              {comp?.venue && <span>{comp.venue}</span>}
              {comp?.date && <span>{comp.date}</span>}
            </div>
          </div>
        </div>
        <div className="live-pill">
          <span className="pulse-dot" aria-hidden="true" />
          Live results
        </div>
      </div>
      {liveItem && (
        <div className="ticker">
          <span className="ticker-label">Now racing</span>
          <span className="ticker-text">{liveItem}</span>
        </div>
      )}
    </header>
  );
}
