import "./LaneCard.css";

function placementBadge(placement) {
  if (!placement) return { label: "—", cls: "pending" };
  if (placement === "DQ") return { label: "DQ", cls: "dq" };
  const n = parseInt(placement, 10);
  if (n === 1) return { label: "1st", cls: "gold" };
  if (n === 2) return { label: "2nd", cls: "silver" };
  if (n === 3) return { label: "3rd", cls: "bronze" };
  if (!isNaN(n)) return { label: `${n}th`, cls: "plain" };
  return { label: placement, cls: "plain" };
}

function isComplete(entries, key) {
  return entries.length > 0 && entries.every((e) => e.placement && e.placement !== "");
}

/**
 * A single heat/final, shown as a stack of "lanes" — one row per skater or relay team.
 * entries: array of { seed_order, placement, ...display fields }
 * renderRow: (entry, index) => JSX for the row's content (name/club or team/members)
 */
export default function LaneCard({ stage, number, entries, renderRow, emptyLabel = "No entries yet" }) {
  const complete = isComplete(entries);
  const started = entries.some((e) => e.placement);

  return (
    <div className={`lane-card ${complete ? "is-done" : started ? "is-live" : "is-pending"}`}>
      <div className="lane-card-head">
        <span className="lane-card-stage">{stage}{number ? ` ${number}` : ""}</span>
        <span className={`lane-card-status status-${complete ? "done" : started ? "live" : "pending"}`}>
          {complete ? "Final" : started ? "In progress" : "Not started"}
          {started && !complete && <span className="pulse-dot" aria-hidden="true" />}
        </span>
      </div>
      <div className="lane-list">
        {entries.length === 0 && <div className="lane-empty">{emptyLabel}</div>}
        {entries.map((entry, i) => {
          const badge = placementBadge(entry.placement);
          return (
            <div className="lane-row" key={i}>
              <span className="lane-number mono">{(entry.seed_order ?? i) + 1}</span>
              <div className="lane-content">{renderRow(entry, i)}</div>
              <span className={`lane-badge badge-${badge.cls}`}>{badge.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
