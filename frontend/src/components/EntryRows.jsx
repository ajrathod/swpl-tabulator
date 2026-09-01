export function SkaterRow({ full_name, club, panel }) {
  return (
    <div className="entry-row">
      <span className="entry-name">{full_name}</span>
      <span className="entry-meta">
        {club && <span className="entry-club">{club}</span>}
        {panel && <span className="entry-panel mono">#{panel}</span>}
      </span>
    </div>
  );
}

export function TeamRow({ club, color, members }) {
  return (
    <div className="entry-row">
      <span className="entry-name">
        {color && <span className="team-dot" style={{ background: teamColor(color) }} />}
        {club}
      </span>
      <span className="entry-meta">
        <span className="entry-members">{(members || []).join(" · ")}</span>
      </span>
    </div>
  );
}

function teamColor(name) {
  const map = {
    red: "#ff4d5e", blue: "#2fd1e8", green: "#37d67a", yellow: "#f4c343",
    white: "#eef2f8", black: "#5c6b8a", orange: "#d98a4f", purple: "#b98af0",
  };
  return map[(name || "").toLowerCase()] || "#5c6b8a";
}
