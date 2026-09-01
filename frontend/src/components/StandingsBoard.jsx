import "./StandingsBoard.css";

export default function StandingsBoard({ standings, tiebreakerEvent }) {
  if (!standings || standings.length === 0) {
    return <div className="standings-empty">Standings will appear once results are posted.</div>;
  }
  return (
    <div className="standings-board">
      <table>
        <thead>
          <tr>
            <th className="col-rank">Rank</th>
            <th>Skater</th>
            <th className="col-num">Points</th>
          </tr>
        </thead>
        <tbody>
          {standings.map((row, i) => {
            const rank = row.position ?? row.rank ?? i + 1;
            const medal = rank === 1 ? "gold" : rank === 2 ? "silver" : rank === 3 ? "bronze" : null;
            return (
              <tr key={row.panel || i} className={medal ? `is-${medal}` : ""}>
                <td className="col-rank">
                  <span className={`rank-pill ${medal ? `pill-${medal}` : ""}`}>{rank}</span>
                </td>
                <td>
                  <div className="entry-name">{row.full_name || row.name}</div>
                  <div className="entry-meta">{row.club}</div>
                </td>
                <td className="col-num mono">{row.points}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {tiebreakerEvent && (
        <div className="standings-note">Tiebreaker event: {tiebreakerEvent}</div>
      )}
    </div>
  );
}
