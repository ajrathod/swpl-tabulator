import "./GroupEventNav.css";

export default function GroupEventNav({
  groups, activeGroup, onGroup,
  events, activeEvent, onEvent,
  relayNames, activeRelay, onRelay,
}) {
  return (
    <div className="nav-row">
      <div className="nav-field">
        <label>Group</label>
        <select value={activeGroup || ""} onChange={(e) => onGroup(e.target.value)}>
          <option value="" disabled>Select group…</option>
          {groups.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
      </div>

      {activeGroup && (
        <div className="nav-field">
          <label>Event</label>
          <select value={activeEvent || ""} onChange={(e) => onEvent(e.target.value)}>
            <option value="" disabled>Select event…</option>
            {events.map((ev) => <option key={ev} value={ev}>{ev}</option>)}
          </select>
        </div>
      )}

      {relayNames && relayNames.length > 0 && (
        <div className="nav-field">
          <label>Relay</label>
          <select value={activeRelay || ""} onChange={(e) => onRelay(e.target.value)}>
            <option value="">— None —</option>
            {relayNames.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
      )}
    </div>
  );
}
