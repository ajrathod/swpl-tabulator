"""
PacWest Inline Racing — Race Manager v3
Flask + SQLite/PostgreSQL
"""
from flask import Flask, render_template, request, jsonify
import pandas as pd, math, os
from models import (
    init_db, SessionLocal, POINTS, ALL_EVENT_TEMPLATES, INDIVIDUAL_EVENTS, RELAY_EVENTS_LIST,
    Competition, CompEvent, Skater, Event, Heat, HeatSkater,
    RelayEvent, RelayTeam, RelayMember, RelayHeat, RelayHeatTeam,
)
from seeding import compute_stages, distribute

app = Flask(__name__)

def db():
    return SessionLocal()

# ── Constants ─────────────────────────────────────────────────────────────────

SKIP_SHEETS = {
    "claude cache","saturday relays","sunday relays","elite points race",
    "elims","beginner 50m drag","100m drag race","skills & obstacle course"
}

# ── Excel parsers ─────────────────────────────────────────────────────────────

def _clean(val):
    s = str(val).strip()
    return "" if s in ("nan","NaN","None") else s


def _is_group_header(row):
    """
    Supports three sheet formats:
    A - old Saturday Master: col4=X, col5=X, group in col8
    B - old Sunday Master:   col0 empty, col8 has group keyword
    C - new demo format:     col4=X, col5=X, group in col6
    """
    c0 = _clean(row.get(0,""))
    c4 = _clean(row.get(4,""))
    c5 = _clean(row.get(5,""))
    c6 = _clean(row.get(6,""))
    c8 = _clean(row.get(8,""))
    SKIP = {"Group","Heat / Final","Race Group","Race Group ",""}
    GROUP_KEYWORDS = ("star skate","beginner","elite","juvenile","primary","elementary",
                      "freshman","sophomore","junior","senior","master","classic","veteran","tiny tot")
    if c4 == "X" or c5 == "X":
        grp = c6 if c6 and c6 not in SKIP else c8
        if grp and grp not in SKIP:
            return True, grp
    if c0 == "" and c8 and c8 not in SKIP and any(kw in c8.lower() for kw in GROUP_KEYWORDS):
        return True, c8
    return False, None


def parse_individual(filepath):
    """
    Parse master sheet. Handles old (col8 group) and new (col6 group per skater row) formats.
    Columns: First(0) Last(1) M/F(2) Age(3) Panel(4) Club(5) Race Group(6) [USARS(auto-detected)]
    """
    df = pd.read_excel(filepath, sheet_name=None, header=None, engine='openpyxl')
    skaters = []
    SKIP_GRP = {"Race Group","Race Group ",""}
    for sname, ws in df.items():
        if sname.lower() in SKIP_SHEETS: continue
        hrow = None
        for i, row in ws.iterrows():
            if _clean(row.get(0,"")) == "First":
                hrow = i; break
        if hrow is None: continue
        # Auto-detect USARS column from header row
        header = ws.iloc[hrow]
        usars_col = None
        for col_idx in range(len(header)):
            h = _clean(header.get(col_idx,"")).lower()
            if "usars" in h or "membership" in h:
                usars_col = col_idx; break
        cur_group = None
        seen = set()
        for i in range(hrow + 1, len(ws)):
            row = ws.iloc[i]
            is_hdr, grp = _is_group_header(row)
            if is_hdr:
                cur_group = grp; continue
            first = _clean(row.get(0,""))
            last  = _clean(row.get(1,""))
            if not first: continue
            panel  = _clean(row.get(4,""))
            club   = _clean(row.get(5,""))
            gender = _clean(row.get(2,""))
            # Group: prefer col6 on row (new format), else tracked cur_group (old format)
            row_grp = _clean(row.get(6,""))
            group = row_grp if row_grp and row_grp not in SKIP_GRP else (cur_group or "Unknown")
            usars_no = _clean(row.get(usars_col,"")) if usars_col is not None else ""
            try:   age = int(float(row.get(3, 0)))
            except: age = 0
            if not panel or panel in seen: continue
            seen.add(panel)
            skaters.append({"first":first,"last":last,"gender":gender,"age":age,
                            "panel":panel,"club":club,"group":group,"usars_no":usars_no})
    return skaters


def parse_relay(filepath):
    df = pd.read_excel(filepath, sheet_name=None, header=None, engine='openpyxl')
    teams = []
    for sname, ws in df.items():
        if "relay" not in sname.lower(): continue
        cur_event = None; cur_team = None
        for i, row in ws.iterrows():
            col0  = str(row.get(0,"")).strip()
            name  = str(row.get(2,"")).strip()
            club  = str(row.get(1,"")).strip()
            color = str(row.get(5,"")).strip()
            if col0 and "Final" in col0:
                cur_event = col0; cur_team = None; continue
            if club not in ("nan","NaN","") and name not in ("nan","NaN",""):
                cur_team = {"event_name":cur_event,"club":club,"color":color,"members":[name]}
                teams.append(cur_team)
            elif name not in ("nan","NaN","") and cur_team:
                cur_team["members"].append(name)
    return teams


# ── Seeding helpers ───────────────────────────────────────────────────────────

def seed_individual_heats(session, event, panels):
    for h in list(event.heats): session.delete(h)
    session.flush()
    if not panels:
        final = Heat(event_id=event.id, stage="Final", number=1)
        session.add(final); session.flush(); return

    stages = compute_stages(len(panels))
    if event.event_type == "direct_final":
        final = Heat(event_id=event.id, stage="Final", number=1)
        session.add(final); session.flush()
        _fill_heat(session, final, panels, "individual")
        return

    heat_buckets = distribute(panels, stages["heats"]) if stages["heats"] else []
    for i, bucket in enumerate(heat_buckets):
        h = Heat(event_id=event.id, stage="Heat", number=i+1)
        session.add(h); session.flush()
        _fill_heat(session, h, bucket, "individual")
    for i in range(stages["semis"]):
        h = Heat(event_id=event.id, stage="Semi", number=i+1)
        session.add(h); session.flush()
    final = Heat(event_id=event.id, stage="Final", number=1)
    session.add(final); session.flush()
    if not heat_buckets:
        _fill_heat(session, final, panels, "individual")


def _fill_heat(session, heat, ids, kind):
    if kind == "individual":
        for order, panel in enumerate(ids):
            sk = session.query(Skater).filter_by(
                competition_id=heat.event.competition_id, panel=panel).first()
            if sk:
                session.add(HeatSkater(heat_id=heat.id, skater_id=sk.id, seed_order=order))
    else:
        for order, team_id in enumerate(ids):
            session.add(RelayHeatTeam(relay_heat_id=heat.id if kind=="individual" else heat.id,
                                      team_id=team_id, seed_order=order))


def seed_relay_heats(session, relay_event, team_ids):
    for h in list(relay_event.relay_heats): session.delete(h)
    session.flush()
    if not team_ids:
        rh = RelayHeat(relay_event_id=relay_event.id, stage="Final", number=1)
        session.add(rh); session.flush(); return

    stages = compute_stages(len(team_ids))
    heat_buckets = distribute(team_ids, stages["heats"]) if stages["heats"] else []
    for i, bucket in enumerate(heat_buckets):
        rh = RelayHeat(relay_event_id=relay_event.id, stage="Heat", number=i+1)
        session.add(rh); session.flush()
        for order, tid in enumerate(bucket):
            session.add(RelayHeatTeam(relay_heat_id=rh.id, team_id=tid, seed_order=order))
    for i in range(stages["semis"]):
        rh = RelayHeat(relay_event_id=relay_event.id, stage="Semi", number=i+1)
        session.add(rh); session.flush()
    rh_final = RelayHeat(relay_event_id=relay_event.id, stage="Final", number=1)
    session.add(rh_final); session.flush()
    if not heat_buckets:
        for order, tid in enumerate(team_ids):
            session.add(RelayHeatTeam(relay_heat_id=rh_final.id, team_id=tid, seed_order=order))


def reseed_individual(session, event):
    old = {}
    for h in event.heats:
        for hs in h.skaters:
            if hs.placement: old[hs.skater.panel] = hs.placement
    panels = [s.panel for s in session.query(Skater).filter_by(
        competition_id=event.competition_id, group_name=event.group_name).all()]
    seed_individual_heats(session, event, panels)
    session.flush()
    for h in event.heats:
        for hs in h.skaters:
            if hs.skater.panel in old: hs.placement = old[hs.skater.panel]


def reseed_relay(session, relay_event):
    team_ids = [t.id for t in relay_event.teams]
    seed_relay_heats(session, relay_event, team_ids)


# ── Points helpers ────────────────────────────────────────────────────────────

def compute_group_standings(session, comp_id, group_name):
    """
    Sum points from all points_event finals per skater.
    Points: 1st=30, 2nd=20, 3rd=10, 4th=5.
    Ties broken by placement in the competition's tiebreaker_event final.
    If still tied, flagged as 'TB needed' for operator to resolve.
    """
    comp   = session.query(Competition).get(comp_id)
    tb_ev  = comp.tiebreaker_event if comp else None

    events = session.query(Event).filter_by(
        competition_id=comp_id, group_name=group_name, points_event=1).all()

    skater_points = {}   # panel -> {name, club, points, breakdown, tb_place}
    for ev in events:
        finals = [h for h in ev.heats if h.stage == "Final"]
        for final in finals:
            for hs in final.skaters:
                pl = hs.placement
                if not pl or not pl.isdigit(): continue
                place = int(pl)
                pts   = POINTS.get(place, 0)
                panel = hs.skater.panel
                if panel not in skater_points:
                    skater_points[panel] = {
                        "panel": panel, "name": hs.skater.full_name,
                        "club": hs.skater.club, "points": 0,
                        "breakdown": {}, "tb_place": None
                    }
                skater_points[panel]["points"] += pts
                skater_points[panel]["breakdown"][ev.name] = {"place": place, "pts": pts}
                # Record tiebreaker placement
                if tb_ev and ev.name == tb_ev:
                    skater_points[panel]["tb_place"] = place

    # Sort: primary=points desc, secondary=tb_place asc (lower is better), tertiary=name
    def sort_key(row):
        tb = row["tb_place"] if row["tb_place"] is not None else 999
        return (-row["points"], tb, row["name"])

    standings = sorted(skater_points.values(), key=sort_key)

    # Assign positions — detect ties needing manual resolution
    for i, row in enumerate(standings):
        row["position"] = i + 1
        # Check if tied with next skater and tiebreaker didn't resolve it
        if i < len(standings) - 1:
            nxt = standings[i + 1]
            same_pts   = row["points"] == nxt["points"]
            same_tb    = row["tb_place"] == nxt["tb_place"]   # both None or same place
            row["tb_needed"] = same_pts and same_tb
        else:
            row["tb_needed"] = False

    return standings, tb_ev


# ── Home / competition routes ─────────────────────────────────────────────────

@app.route("/")
def index():
    session = db()
    comps = session.query(Competition).order_by(Competition.created_at.desc()).all()
    session.close()
    return render_template("index.html", competitions=[c.to_dict() for c in comps])


@app.route("/comp/<int:comp_id>")
def comp_view(comp_id):
    session = db()
    comp = session.query(Competition).get(comp_id)
    session.close()
    if not comp: return "Not found", 404
    return render_template("operator.html", comp=comp.to_dict())


@app.route("/public/<int:comp_id>")
def public_view(comp_id):
    session = db()
    comp = session.query(Competition).get(comp_id)
    session.close()
    if not comp: return "Not found", 404
    return render_template("public.html", comp=comp.to_dict())


@app.route("/athletes")
def athletes_page():
    session = db()
    comps = session.query(Competition).order_by(Competition.date.desc()).all()
    session.close()
    return render_template("athletes.html", competitions=[c.to_dict() for c in comps])


@app.route("/api/competitions", methods=["GET"])
def list_competitions():
    session = db()
    comps = session.query(Competition).order_by(Competition.created_at.desc()).all()
    session.close()
    return jsonify([c.to_dict() for c in comps])


@app.route("/api/competitions", methods=["POST"])
def create_competition():
    data = request.json
    session = db()
    comp = Competition(name=data["name"], venue=data.get("venue",""),
                       date=data.get("date",""), comp_type=data.get("type","indoor"))
    session.add(comp); session.commit()
    result = comp.to_dict()
    session.close()
    return jsonify(result)


@app.route("/api/competitions/<int:comp_id>", methods=["DELETE"])
def delete_competition(comp_id):
    session = db()
    comp = session.query(Competition).get(comp_id)
    if comp: session.delete(comp); session.commit()
    session.close()
    return jsonify({"ok": True})


@app.route("/api/event_templates")
def event_templates():
    return jsonify(ALL_EVENT_TEMPLATES)


@app.route("/api/<int:comp_id>/comp_events", methods=["GET"])
def get_comp_events(comp_id):
    session = db()
    ces = session.query(CompEvent).filter_by(competition_id=comp_id).all()
    result = [{"name":ce.event_name,"category":ce.category,
               "event_type":ce.event_type,"points_event":bool(ce.points_event)} for ce in ces]
    session.close()
    return jsonify(result)


@app.route("/api/<int:comp_id>/comp_events", methods=["POST"])
def save_comp_events(comp_id):
    """Save the event checklist for this competition."""
    data = request.json   # list of event names selected
    selected = set(data.get("events", []))
    session = db()
    try:
        # Delete existing
        session.query(CompEvent).filter_by(competition_id=comp_id).delete()
        session.flush()
        # Re-insert selected
        tpl_map = {t["name"]: t for t in ALL_EVENT_TEMPLATES}
        for name in selected:
            tpl = tpl_map.get(name)
            if not tpl: continue
            session.add(CompEvent(
                competition_id=comp_id, event_name=name,
                category=tpl["category"], event_type=tpl["event_type"],
                points_event=int(tpl["points_event"])))
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


# ── Upload ────────────────────────────────────────────────────────────────────

@app.route("/api/upload/individual/<int:comp_id>", methods=["POST"])
def upload_individual(comp_id):
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}), 400
    path = f"/tmp/ind_{f.filename}"; f.save(path)
    session = db()
    try:
        comp = session.query(Competition).get(comp_id)
        if not comp: return jsonify({"error":"Competition not found"}), 404

        # Get selected individual events for this comp
        ind_events = session.query(CompEvent).filter_by(
            competition_id=comp_id, category="individual").all()
        if not ind_events:
            return jsonify({"error": "No individual events selected. Please configure events first."}), 400

        # Clear existing individual data
        for s in list(comp.skaters): session.delete(s)
        for e in list(comp.events):  session.delete(e)
        session.flush()

        raw = parse_individual(path)
        if not raw: return jsonify({"error":"No skaters found"}), 400

        # Deduplicate by panel
        seen = set(); panels_by_group = {}
        for r in raw:
            if r["panel"] in seen: continue
            seen.add(r["panel"])
            sk = Skater(competition_id=comp_id, panel=r["panel"],
                        usars_no=r.get("usars_no",""),
                        first=r["first"], last=r["last"], gender=r["gender"],
                        age=r["age"], club=r["club"], group_name=r["group"])
            session.add(sk)
            panels_by_group.setdefault(r["group"], []).append(r["panel"])
        session.flush()

        # Create one Event row per (group, enabled_individual_event)
        for group, panels in panels_by_group.items():
            for ce in ind_events:
                ev = Event(competition_id=comp_id, group_name=group,
                           name=ce.event_name, event_type=ce.event_type,
                           points_event=ce.points_event)
                session.add(ev); session.flush()
                seed_individual_heats(session, ev, panels)

        session.commit()
        return jsonify({"ok":True, "skater_count":len(seen), "groups":sorted(panels_by_group.keys())})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


@app.route("/api/upload/relay/<int:comp_id>", methods=["POST"])
def upload_relay(comp_id):
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}), 400
    path = f"/tmp/rel_{f.filename}"; f.save(path)
    session = db()
    try:
        # Clear existing relays
        for re in session.query(RelayEvent).filter_by(competition_id=comp_id).all():
            session.delete(re)
        session.flush()

        raw = parse_relay(path)
        relay_map = {}  # event_name -> RelayEvent

        for t in raw:
            ev_name = (t["event_name"] or "Unknown Relay").strip()
            if ev_name not in relay_map:
                re = RelayEvent(competition_id=comp_id, event_name=ev_name, event_type="heat_based")
                session.add(re); session.flush()
                relay_map[ev_name] = re
            team = RelayTeam(relay_event_id=relay_map[ev_name].id,
                             club=t["club"], color=t["color"])
            session.add(team); session.flush()
            for name in t["members"]:
                if name and name not in ("nan",""):
                    session.add(RelayMember(team_id=team.id, name=name))

        # Seed heats for each relay event
        session.flush()
        for re in relay_map.values():
            team_ids = [t.id for t in re.teams]
            seed_relay_heats(session, re, team_ids)

        session.commit()
        return jsonify({"ok":True, "relay_events": list(relay_map.keys())})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


# ── Group / event data ────────────────────────────────────────────────────────

@app.route("/api/<int:comp_id>/groups")
def api_groups(comp_id):
    session = db()
    events = session.query(Event).filter_by(competition_id=comp_id).all()
    groups = {}
    for ev in events:
        g = ev.group_name
        if g not in groups:
            count = session.query(Skater).filter_by(competition_id=comp_id, group_name=g).count()
            groups[g] = {"skater_count": count, "events": []}
        if ev.name not in groups[g]["events"]:
            groups[g]["events"].append(ev.name)
    session.close()
    return jsonify(groups)


@app.route("/api/<int:comp_id>/group/<path:group>")
def api_group(comp_id, group):
    session = db()
    events = session.query(Event).filter_by(competition_id=comp_id, group_name=group).all()
    result = {}
    for ev in events:
        heats  = sorted([h for h in ev.heats if h.stage=="Heat"],  key=lambda h: h.number)
        semis  = sorted([h for h in ev.heats if h.stage=="Semi"],  key=lambda h: h.number)
        finals = sorted([h for h in ev.heats if h.stage=="Final"], key=lambda h: h.number)
        result[ev.name] = {
            "id": ev.id, "name": ev.name, "type": ev.event_type,
            "points_event": bool(ev.points_event),
            "heats":  [h.to_dict() for h in heats],
            "semis":  [h.to_dict() for h in semis],
            "finals": [h.to_dict() for h in finals],
        }
    session.close()
    return jsonify(result)


@app.route("/api/<int:comp_id>/standings/<path:group>")
def api_standings(comp_id, group):
    session = db()
    standings, tb_ev = compute_group_standings(session, comp_id, group)
    session.close()
    return jsonify({"standings": standings, "tiebreaker_event": tb_ev or ""})


@app.route("/api/<int:comp_id>/tiebreaker", methods=["POST"])
def set_tiebreaker(comp_id):
    data = request.json
    session = db()
    try:
        comp = session.query(Competition).get(comp_id)
        if not comp: return jsonify({"error": "Not found"}), 404
        comp.tiebreaker_event = data.get("event_name") or None
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/<int:comp_id>/relays")
def api_relays(comp_id):
    session = db()
    relay_events = session.query(RelayEvent).filter_by(competition_id=comp_id).all()
    result = {}
    for re in relay_events:
        heats  = sorted([h for h in re.relay_heats if h.stage=="Heat"],  key=lambda h: h.number)
        semis  = sorted([h for h in re.relay_heats if h.stage=="Semi"],  key=lambda h: h.number)
        finals = sorted([h for h in re.relay_heats if h.stage=="Final"], key=lambda h: h.number)
        result[re.event_name] = {
            "id": re.id, "event_type": re.event_type,
            "teams": [t.to_dict() for t in re.teams],
            "heats":  [h.to_dict() for h in heats],
            "semis":  [h.to_dict() for h in semis],
            "finals": [h.to_dict() for h in finals],
        }
    session.close()
    return jsonify(result)


# ── Save results (individual) ─────────────────────────────────────────────────

@app.route("/api/save_results", methods=["POST"])
def save_results():
    data = request.json
    heat_id = data["heat_id"]; results = data["results"]
    session = db()
    try:
        heat = session.query(Heat).get(heat_id)
        if not heat: return jsonify({"error":"Heat not found"}), 404
        for hs in heat.skaters:
            val = str(results.get(hs.skater.panel,"")).strip().upper()
            hs.placement = "DQ" if val=="DQ" else (val if val.isdigit() else None)
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


# ── Save relay results ────────────────────────────────────────────────────────

@app.route("/api/save_relay_results", methods=["POST"])
def save_relay_results():
    data = request.json
    relay_heat_id = data["relay_heat_id"]; results = data["results"]  # {team_id: placement}
    session = db()
    try:
        rh = session.query(RelayHeat).get(relay_heat_id)
        if not rh: return jsonify({"error":"Relay heat not found"}), 404
        for rht in rh.teams:
            val = str(results.get(str(rht.team_id),"")).strip().upper()
            rht.placement = "DQ" if val=="DQ" else (val if val.isdigit() else None)
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


# ── Advance (individual) ──────────────────────────────────────────────────────

@app.route("/api/advance", methods=["POST"])
def advance():
    data = request.json
    event_id = data["event_id"]; from_stage = data["from"]
    session = db()
    try:
        ev = session.query(Event).get(event_id)
        if not ev: return jsonify({"error":"Event not found"}), 404
        src = [h for h in ev.heats if h.stage==("Heat" if from_stage=="heats" else "Semi")]
        dst = [h for h in ev.heats if h.stage==("Semi" if from_stage=="heats" else "Final")]
        stages = compute_stages(session.query(Skater).filter_by(
            competition_id=ev.competition_id, group_name=ev.group_name).count())
        adv = stages["adv_heat"] if from_stage=="heats" else stages["adv_semi"]

        qualifiers = []
        for heat in sorted(src, key=lambda h: h.number):
            ranked = sorted([hs for hs in heat.skaters if hs.placement and hs.placement.isdigit()],
                            key=lambda hs: int(hs.placement))
            qualifiers.extend([hs.skater for hs in ranked[:adv]])

        for h in dst:
            for hs in list(h.skaters): session.delete(hs)
        session.flush()

        buckets = [qualifiers[:7]] if len(dst)==1 else \
                  [qualifiers[:math.ceil(len(qualifiers)/2)], qualifiers[math.ceil(len(qualifiers)/2):]]
        for heat, bucket in zip(dst, buckets):
            for order, sk in enumerate(bucket):
                session.add(HeatSkater(heat_id=heat.id, skater_id=sk.id, seed_order=order))

        session.commit()
        dest = dst[0].stage if dst else "Final"
        return jsonify({"ok":True, "message":f"{len(qualifiers)} skaters → {dest}"})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


# ── Advance relay ─────────────────────────────────────────────────────────────

@app.route("/api/advance_relay", methods=["POST"])
def advance_relay():
    data = request.json
    relay_event_id = data["relay_event_id"]; from_stage = data["from"]
    session = db()
    try:
        re = session.query(RelayEvent).get(relay_event_id)
        if not re: return jsonify({"error":"Not found"}), 404
        src = [h for h in re.relay_heats if h.stage==("Heat" if from_stage=="heats" else "Semi")]
        dst = [h for h in re.relay_heats if h.stage==("Semi" if from_stage=="heats" else "Final")]
        stages = compute_stages(len(re.teams))
        adv = stages["adv_heat"] if from_stage=="heats" else stages["adv_semi"]

        qualifiers = []
        for heat in sorted(src, key=lambda h: h.number):
            ranked = sorted([t for t in heat.teams if t.placement and t.placement.isdigit()],
                            key=lambda t: int(t.placement))
            qualifiers.extend([t.team for t in ranked[:adv]])

        for h in dst:
            for rht in list(h.teams): session.delete(rht)
        session.flush()

        buckets = [qualifiers[:7]] if len(dst)==1 else \
                  [qualifiers[:math.ceil(len(qualifiers)/2)], qualifiers[math.ceil(len(qualifiers)/2):]]
        for heat, bucket in zip(dst, buckets):
            for order, team in enumerate(bucket):
                session.add(RelayHeatTeam(relay_heat_id=heat.id, team_id=team.id, seed_order=order))

        session.commit()
        return jsonify({"ok":True, "message":f"{len(qualifiers)} teams advanced"})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


# ── Reseed ────────────────────────────────────────────────────────────────────

@app.route("/api/reseed", methods=["POST"])
def reseed():
    data = request.json; event_id = data["event_id"]
    session = db()
    try:
        ev = session.query(Event).get(event_id)
        if not ev: return jsonify({"error":"Not found"}), 404
        reseed_individual(session, ev)
        session.commit()
        return jsonify({"ok":True, "message":"Heats re-seeded"})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


@app.route("/api/reseed_relay", methods=["POST"])
def reseed_relay_route():
    data = request.json; relay_event_id = data["relay_event_id"]
    session = db()
    try:
        re = session.query(RelayEvent).get(relay_event_id)
        if not re: return jsonify({"error":"Not found"}), 404
        reseed_relay(session, re)
        session.commit()
        return jsonify({"ok":True, "message":"Relay heats re-seeded"})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


# ── Transfer ──────────────────────────────────────────────────────────────────

@app.route("/api/transfer", methods=["POST"])
def transfer():
    data = request.json
    comp_id = data["comp_id"]; panels = data["panels"]; to_group = data["to_group"]
    session = db()
    try:
        for panel in panels:
            sk = session.query(Skater).filter_by(competition_id=comp_id, panel=panel).first()
            if not sk: continue
            from_group = sk.group_name; sk.group_name = to_group
            for ev in session.query(Event).filter_by(competition_id=comp_id, group_name=from_group).all():
                for heat in ev.heats:
                    for hs in list(heat.skaters):
                        if hs.skater_id == sk.id: session.delete(hs)
            session.flush()
            for ev in session.query(Event).filter_by(competition_id=comp_id, group_name=from_group).all():
                reseed_individual(session, ev)
        session.flush()
        for ev in session.query(Event).filter_by(competition_id=comp_id, group_name=to_group).all():
            reseed_individual(session, ev)
        session.commit()
        return jsonify({"ok":True})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


# ── Add event ─────────────────────────────────────────────────────────────────

@app.route("/api/add_event", methods=["POST"])
def add_event():
    data = request.json
    comp_id=data["comp_id"]; group=data["group"]; name=data["name"]; ev_type=data["type"]
    is_points = int(data.get("points_event", 0))
    session = db()
    try:
        panels = [s.panel for s in session.query(Skater).filter_by(
            competition_id=comp_id, group_name=group).all()]
        ev = Event(competition_id=comp_id, group_name=group, name=name,
                   event_type=ev_type, points_event=is_points)
        session.add(ev); session.flush()
        seed_individual_heats(session, ev, panels)
        session.commit()
        return jsonify({"ok":True, "event_id":ev.id})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


# ── Athlete history ───────────────────────────────────────────────────────────

@app.route("/api/athletes/search")
def athletes_search():
    q = request.args.get("q","").strip()
    comp_id = request.args.get("comp_id", type=int)
    session = db()
    query = session.query(Skater)
    if comp_id: query = query.filter_by(competition_id=comp_id)
    if q:
        from sqlalchemy import or_
        query = query.filter(or_(
            Skater.first.ilike(f"%{q}%"), Skater.last.ilike(f"%{q}%"),
            Skater.panel.ilike(f"%{q}%"), Skater.club.ilike(f"%{q}%"),
            Skater.usars_no.ilike(f"%{q}%")))
    skaters = query.order_by(Skater.last, Skater.first).limit(50).all()
    seen = {}
    for s in skaters:
        if s.panel not in seen: seen[s.panel] = s.to_dict()
    session.close()
    return jsonify(list(seen.values()))


@app.route("/api/athlete/<panel>")
def athlete_history(panel):
    session = db()
    skaters = session.query(Skater).filter_by(panel=panel)\
                     .order_by(Skater.competition_id.desc()).all()
    # Only show Final results in athlete profile
    history = []; info = None; comps_seen = set()
    for sk in skaters:
        comp = sk.competition
        if not info:
            info = {"panel": panel, "usars_no": sk.usars_no or "",
                    "name": sk.full_name, "club": sk.club,
                    "gender": sk.gender, "age": sk.age}
        for hs in sk.heat_skaters:
            h = hs.heat; ev = h.event
            # Skip heats and semis — only show Finals
            if h.stage != "Final": continue
            history.append({
                "competition": comp.name, "competition_id": comp.id,
                "date": comp.date or "", "venue": comp.venue or "",
                "group": sk.group_name, "event": ev.name,
                "event_type": ev.event_type, "points_event": bool(ev.points_event),
                "stage": h.stage, "heat_number": h.number,
                "placement": hs.placement or "",
            })
        comps_seen.add(comp.id)

    # Podium stats: only Finals from points_events
    final_pts = [r for r in history if r["points_event"] and r["placement"].isdigit()]
    placements = [int(r["placement"]) for r in final_pts]
    history.sort(key=lambda x: (x["date"], x["competition"]), reverse=True)

    stats = {
        "total_finals": len(history),
        "competitions": len(comps_seen),
        "podiums": sum(1 for p in placements if p <= 3),
        "gold":   placements.count(1),
        "silver": placements.count(2),
        "bronze": placements.count(3),
        "best":   min(placements) if placements else None,
    }
    if not info: info = {"panel":panel,"usars_no":"","name":panel,"club":"","gender":"","age":0}
    session.close()
    return jsonify({**info, "history": history, "stats": stats})


if __name__ == "__main__":
    init_db()
    import socket
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(("8.8.8.8",80))
        ip=s.getsockname()[0]; s.close()
    except: ip="localhost"
    print(f"\n{'='*55}\n  PacWest Race Manager v3\n{'='*55}")
    print(f"  Operator : http://localhost:5001")
    print(f"  Public   : http://{ip}:5001/public/<comp_id>")
    print(f"  Athletes : http://localhost:5001/athletes")
    print(f"  Database : skating.db\n{'='*55}\n")
    app.run(host="0.0.0.0", port=5001, debug=False)
