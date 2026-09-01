import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import Header from "../components/Header";
import SearchBar from "../components/SearchBar";
import GroupEventNav from "../components/GroupEventNav";
import LaneCard from "../components/LaneCard";
import StandingsBoard from "../components/StandingsBoard";
import AthleteModal from "../components/AthleteModal";
import { SkaterRow, TeamRow } from "../components/EntryRows";
import "../components/EntryRows.css";
import "./PublicApp.css";

function pickActiveStage(ev) {
  if (ev.finals?.length) return { stage: "Final", heats: ev.finals };
  if (ev.semis?.length) return { stage: "Semi", heats: ev.semis };
  return { stage: "Heat", heats: ev.heats || [] };
}

export default function PublicApp({ compId, comp }) {
  const [activeGroup, setActiveGroup] = useState(null);
  const [activeEvent, setActiveEvent] = useState(null);
  const [activeRelay, setActiveRelay] = useState(null);
  const [selectedPanel, setSelectedPanel] = useState(null);
  const [view, setView] = useState("results"); // results | standings

  const { data: groupsData } = usePolling(() => api.groups(compId), [compId], 20000);
  const { data: relaysData } = usePolling(() => api.relays(compId), [compId], 20000);
  const { data: scheduleData } = usePolling(() => api.schedule(compId), [compId], 15000);

  const groupNames = useMemo(() => Object.keys(groupsData || {}), [groupsData]);
  const relayNames = useMemo(() => Object.keys(relaysData || {}), [relaysData]);

  useEffect(() => {
    if (!activeGroup && groupNames.length) setActiveGroup(groupNames[0]);
  }, [groupNames, activeGroup]);

  const eventNames = useMemo(
    () => (activeGroup && groupsData?.[activeGroup]?.events) || [],
    [groupsData, activeGroup]
  );

  useEffect(() => {
    if (eventNames.length && !eventNames.includes(activeEvent)) setActiveEvent(eventNames[0]);
  }, [eventNames, activeEvent]);

  const { data: groupDetail } = usePolling(
    () => (activeGroup ? api.group(compId, activeGroup) : Promise.resolve(null)),
    [compId, activeGroup],
    10000
  );

  const { data: standingsData } = usePolling(
    () => (activeGroup ? api.standings(compId, activeGroup) : Promise.resolve(null)),
    [compId, activeGroup],
    15000
  );

  const liveItem = useMemo(() => {
    const item = (scheduleData || []).find((s) => s.status === "in_progress");
    if (!item) return null;
    return `${item.label} — ${item.stage}${item.race_number ? ` (Race ${item.race_number})` : ""}`;
  }, [scheduleData]);

  const currentEvent = activeEvent && groupDetail?.[activeEvent];
  const activeStage = currentEvent ? pickActiveStage(currentEvent) : null;

  const currentRelay = activeRelay && relaysData?.[activeRelay];
  const relayStage = currentRelay ? pickActiveStage(currentRelay) : null;

  return (
    <div className="page">
      <Header comp={comp} liveItem={liveItem} />

      <main className="page-body">
        <div className="toolbar">
          <SearchBar compId={compId} onPick={(s) => setSelectedPanel(s.panel)} />
          <div className="view-toggle">
            <button className={view === "results" ? "active" : ""} onClick={() => setView("results")}>Results</button>
            <button className={view === "standings" ? "active" : ""} onClick={() => setView("standings")}>Standings</button>
          </div>
        </div>

        <GroupEventNav
          groups={groupNames}
          activeGroup={activeGroup}
          onGroup={(g) => { setActiveGroup(g); setActiveEvent(null); }}
          events={eventNames}
          activeEvent={activeEvent}
          onEvent={setActiveEvent}
          relayNames={relayNames}
          activeRelay={activeRelay}
          onRelay={setActiveRelay}
        />

        {view === "standings" && (
          <section className="section">
            <h2 className="section-title">Group Standings — {activeGroup}</h2>
            <StandingsBoard
              standings={standingsData?.standings}
              tiebreakerEvent={standingsData?.tiebreaker_event}
            />
          </section>
        )}

        {view === "results" && activeStage && (
          <section className="section">
            <h2 className="section-title">{activeEvent}</h2>
            <div className="lane-grid">
              {activeStage.heats.length === 0 && (
                <div className="standings-empty">Heats haven’t been posted for this event yet.</div>
              )}
              {activeStage.heats.map((h) => (
                <LaneCard
                  key={h.id}
                  stage={activeStage.stage}
                  number={activeStage.heats.length > 1 ? h.number : null}
                  entries={h.skaters}
                  renderRow={(s) => <SkaterRow {...s} />}
                />
              ))}
            </div>
          </section>
        )}

        {view === "results" && relayStage && (
          <section className="section">
            <h2 className="section-title">{activeRelay}</h2>
            <div className="lane-grid">
              {relayStage.heats.length === 0 && (
                <div className="standings-empty">Heats haven’t been posted for this relay yet.</div>
              )}
              {relayStage.heats.map((h) => (
                <LaneCard
                  key={h.id}
                  stage={relayStage.stage}
                  number={relayStage.heats.length > 1 ? h.number : null}
                  entries={h.teams}
                  renderRow={(t) => <TeamRow {...t} />}
                />
              ))}
            </div>
          </section>
        )}
      </main>

      {selectedPanel && (
        <AthleteModal panel={selectedPanel} onClose={() => setSelectedPanel(null)} />
      )}
    </div>
  );
}
