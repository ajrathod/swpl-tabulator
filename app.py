"""
PacWest Inline Racing — Race Manager v3
Flask + SQLite/PostgreSQL
"""
from flask import Flask, render_template, request, jsonify, make_response, redirect, url_for
import pandas as pd, math, os, re, uuid
from models import (
    init_db, SessionLocal, POINTS, ALL_EVENT_TEMPLATES, INDIVIDUAL_EVENTS, RELAY_EVENTS_LIST,
    Competition, CompEvent, Skater, Event, Heat, HeatSkater,
    RelayEvent, RelayTeam, RelayMember, RelayHeat, RelayHeatTeam,
    AthleteRegistry, ScratchRecord, RelayPoolAthlete,
    RegistrationLink, Registration,
)
from seeding import compute_stages, distribute

app = Flask(__name__)

# Initialise DB on startup — works with both gunicorn and direct python run
print(f"[startup] DATABASE_URL = {os.environ.get('DATABASE_URL','sqlite:///skating.db')[:40]}...")
init_db()
print("[startup] Database tables initialised OK")

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
    """
    Parse relay master sheet. Three supported formats:

    Format A (old): col0 contains 'Final', event name is col0 itself
    Format B (Roseville current): col0='FINAL', event name in col1
    Format C (new simplified):    col0='EVENT' or just a division name in col0,
                                  col1 empty — e.g. "Elite Elementary 2 Boy (2000M) Final"
                                  No FINAL keyword needed; any col0 value with no col1
                                  that looks like an event header triggers a new event.

    Team rows in all formats:
      col0=club, col1=first member  → new team
      col0 empty, col1=member       → additional member
      col0='HEAT' or 'Heat N'       → skip (heat divider rows ignored, teams still parsed)
      col0='XX'                     → skip (section header)
    """
    # Keywords that are never club names
    SKIP_KEYWORDS = {"XX", "FINAL", "HEAT"}
    HEAT_RE = re.compile(r'^heat\s*\d*$', re.I)

    df = pd.read_excel(filepath, sheet_name=None, header=None, engine='openpyxl')
    teams = []
    for sname, ws in df.items():
        cur_event = None; cur_team = None
        for i, row in ws.iterrows():
            col0 = _clean(row.get(0, ""))
            col1 = _clean(row.get(1, ""))
            col2 = _clean(row.get(2, ""))
            col5 = _clean(row.get(5, ""))

            # ── Skip blank rows ─────────────────────────────────────────────
            if not col0 and not col1:
                continue

            # ── Skip section dividers (XX, 'N PERSON RELAY', etc.) ─────────
            if col0 == "XX":
                continue

            # ── Skip heat divider rows (HEAT, Heat 1, Heat 2, …) ───────────
            # But if col0=='HEAT' and col1 has content, it's an event header
            if col0.upper() == "HEAT":
                if col1:
                    cur_event = col1  # e.g. "HEAT | Elite Juvenile Any 3 (900M) Final"
                cur_team = None
                continue
            if HEAT_RE.match(col0):
                cur_team = None  # Heat 1 / Heat 2 divider — just reset cur_team
                continue

            # ── Event header detection ──────────────────────────────────────
            # Format B: col0 == 'FINAL', event name in col1
            if col0.upper() == "FINAL" and col1:
                cur_event = col1; cur_team = None; continue

            # Format A: col0 contains 'Final' keyword and no col1
            if "final" in col0.lower() and not col1:
                cur_event = col0; cur_team = None; continue

            # Format C (new): col0 has event name, col1 empty, col0 is not a club keyword
            # Heuristic: col0 looks like an event name (contains letters + digits/parens,
            # no col1) and we're not in the middle of a team list
            if col1 == "" and col0 and col0.upper() not in SKIP_KEYWORDS and not HEAT_RE.match(col0):
                # Distinguish event header from a team with a blank member field:
                # event headers tend to be longer / contain parentheses / contain digits
                looks_like_event = (
                    "(" in col0 or           # e.g. "(900M)"
                    len(col0.split()) >= 3 or # multi-word name
                    col0.upper() == col0       # all-caps section marker
                )
                if looks_like_event:
                    cur_event = col0; cur_team = None; continue

            # ── Team / member rows ──────────────────────────────────────────
            if col0 and col0.upper() not in SKIP_KEYWORDS and not HEAT_RE.match(col0) and col1:
                # New team
                cur_team = {"event_name": cur_event, "club": col0,
                            "color": col5 if col5 else "", "members": [col1]}
                teams.append(cur_team)
                if col2: cur_team["members"].append(col2)

            elif not col0 and col1 and cur_team:
                # Continuation — additional member
                cur_team["members"].append(col1)
                if col2: cur_team["members"].append(col2)

    # Deduplicate members per team
    for t in teams:
        seen = set(); deduped = []
        for m in t["members"]:
            if m and m not in seen:
                seen.add(m); deduped.append(m)
        t["members"] = deduped

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
    """
    Relay seeding rules:
    - Max 7 teams per heat
    - Semis only if more than 6 teams qualify from heats
    - Otherwise go straight from heats to final
    """
    for h in list(relay_event.relay_heats): session.delete(h)
    session.flush()
    if not team_ids:
        rh = RelayHeat(relay_event_id=relay_event.id, stage="Final", number=1)
        session.add(rh); session.flush(); return

    # Relay uses max 7 per heat, semi only if >6 qualifiers
    stages = compute_stages(len(team_ids), max_per=7, semi_threshold=6)
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

    # If no heats (≤7 teams) go straight to final
    if not heat_buckets:
        for order, tid in enumerate(team_ids):
            session.add(RelayHeatTeam(relay_heat_id=rh_final.id, team_id=tid, seed_order=order))


def reseed_individual(session, event):
    old = {}
    for h in event.heats:
        for hs in h.skaters:
            if hs.placement: old[hs.skater.panel] = hs.placement
    # Filter in Python to handle missing status column on older DBs
    all_skaters = session.query(Skater).filter_by(
        competition_id=event.competition_id, group_name=event.group_name).all()
    panels = [s.panel for s in all_skaters
              if getattr(s, 'status', 'active') in (None, 'active')]
    seed_individual_heats(session, event, panels)
    session.flush()
    for h in event.heats:
        for hs in h.skaters:
            if hs.skater.panel in old: hs.placement = old[hs.skater.panel]


def reseed_relay(session, relay_event):
    # Only reseed active teams — exclude scratched
    team_ids = [t.id for t in relay_event.teams if t.status == 'active']
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
    if not is_authed(comp_id):
        return render_template("auth.html", comp=comp.to_dict())
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


@app.route("/registry")
def registry_page():
    return render_template("registry.html")


# ── Registry API ──────────────────────────────────────────────────────────────

@app.route("/api/registry", methods=["GET"])
def registry_list():
    q = request.args.get("q","").strip()
    session = db()
    query = session.query(AthleteRegistry)
    if q:
        from sqlalchemy import or_
        query = query.filter(or_(
            AthleteRegistry.first.ilike(f"%{q}%"),
            AthleteRegistry.last.ilike(f"%{q}%"),
            AthleteRegistry.usars_no.ilike(f"%{q}%"),
            AthleteRegistry.club.ilike(f"%{q}%"),
        ))
    athletes = query.order_by(AthleteRegistry.last, AthleteRegistry.first).limit(200).all()
    total = session.query(AthleteRegistry).count()
    session.close()
    return jsonify({"athletes": [a.to_dict() for a in athletes], "total": total})


@app.route("/api/registry", methods=["POST"])
def registry_add():
    """Add a single athlete to the registry."""
    data = request.json
    usars = (data.get("usars_no") or "").strip()
    first = (data.get("first") or "").strip()
    last  = (data.get("last")  or "").strip()
    if not usars or not first or not last:
        return jsonify({"error": "USARS No., First Name and Last Name are required"}), 400
    session = db()
    try:
        existing = session.query(AthleteRegistry).filter_by(usars_no=usars).first()
        if existing:
            return jsonify({"error": f"USARS No. {usars} already exists ({existing.full_name})"}), 409
        a = AthleteRegistry(
            usars_no=usars, first=first, last=last,
            gender=(data.get("gender") or "").strip(),
            dob=(data.get("dob") or "").strip(),
            club=(data.get("club") or "").strip(),
        )
        session.add(a); session.commit()
        result = a.to_dict()
        return jsonify({"ok": True, "athlete": result})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/registry/<int:athlete_id>", methods=["PUT"])
def registry_update(athlete_id):
    data = request.json
    session = db()
    try:
        a = session.query(AthleteRegistry).get(athlete_id)
        if not a: return jsonify({"error": "Not found"}), 404
        for field in ("first","last","gender","dob","club"):
            if field in data:
                setattr(a, field, (data[field] or "").strip())
        session.commit()
        return jsonify({"ok": True, "athlete": a.to_dict()})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/registry/<int:athlete_id>", methods=["DELETE"])
def registry_delete(athlete_id):
    session = db()
    try:
        a = session.query(AthleteRegistry).get(athlete_id)
        if a: session.delete(a); session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/registry/import", methods=["POST"])
def registry_import():
    """Bulk import athletes from Excel. Columns: USARS No, First, Last, Gender, DOB, Club."""
    f = request.files.get("file")
    if not f: return jsonify({"error": "No file"}), 400
    path = f"/tmp/reg_{f.filename}"; f.save(path)
    session = db()
    try:
        df = pd.read_excel(path, header=0, engine="openpyxl")
        df.columns = [str(c).strip().lower() for c in df.columns]

        # Flexible column detection
        def find_col(candidates):
            for c in candidates:
                for col in df.columns:
                    if c in col: return col
            return None

        usars_col  = find_col(["usars","membership"])
        first_col  = find_col(["first"])
        last_col   = find_col(["last"])
        gender_col = find_col(["gender","m/f","sex"])
        dob_col    = find_col(["dob","birth","born"])
        club_col   = find_col(["club","team"])

        if not usars_col or not first_col or not last_col:
            return jsonify({"error":
                "Could not find required columns. Need: USARS No., First, Last"}), 400

        added = 0; updated = 0; errors = []
        for _, row in df.iterrows():
            usars = str(row.get(usars_col,"")).strip()
            first = str(row.get(first_col,"")).strip()
            last  = str(row.get(last_col, "")).strip()
            if not usars or usars == "nan" or not first or first == "nan": continue
            gender = str(row.get(gender_col,"")).strip() if gender_col else ""
            dob    = str(row.get(dob_col,   "")).strip() if dob_col    else ""
            club   = str(row.get(club_col,  "")).strip() if club_col   else ""
            if dob == "nan": dob = ""
            if gender == "nan": gender = ""
            if club == "nan": club = ""

            existing = session.query(AthleteRegistry).filter_by(usars_no=usars).first()
            if existing:
                existing.first=first; existing.last=last
                if gender: existing.gender=gender
                if dob:    existing.dob=dob
                if club:   existing.club=club
                updated += 1
            else:
                session.add(AthleteRegistry(
                    usars_no=usars, first=first, last=last,
                    gender=gender, dob=dob, club=club))
                added += 1

        session.commit()
        return jsonify({"ok":True, "added":added, "updated":updated})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


@app.route("/api/registry/validate", methods=["POST"])
def registry_validate():
    """
    Check a list of panel numbers against the registry using USARS No. as key.
    Since event sheets may not have USARS, we match on panel + name combo,
    or accept if operator has pre-mapped panels → USARS in the registry.
    Returns missing athletes (in sheet but not in registry).
    """
    data = request.json
    # Expect list of {panel, first, last, usars_no (optional)}
    athletes = data.get("athletes", [])
    session = db()
    missing = []; found = []
    for a in athletes:
        usars = (a.get("usars_no") or "").strip()
        first = (a.get("first") or "").strip().lower()
        last  = (a.get("last")  or "").strip().lower()
        match = None
        if usars:
            match = session.query(AthleteRegistry).filter_by(usars_no=usars).first()
        if not match and first and last:
            match = session.query(AthleteRegistry).filter(
                AthleteRegistry.first.ilike(first),
                AthleteRegistry.last.ilike(last)).first()
        if match:
            found.append({**a, "registry_id": match.id,
                          "usars_no": match.usars_no,
                          "gender": match.gender})
        else:
            missing.append(a)
    session.close()
    return jsonify({"found": found, "missing": missing,
                    "can_proceed": len(missing) == 0})


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
                       date=data.get("date",""), comp_type=data.get("type","indoor"),
                       passcode=data.get("passcode","").strip() or None)
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

        # ── Registry validation — block if any athlete not found ──────────────
        session_check = db()
        missing = []
        for r in raw:
            usars = r.get("usars_no","").strip()
            first = r.get("first","").strip().lower()
            last  = r.get("last","").strip().lower()
            match = None
            if usars:
                match = session_check.query(AthleteRegistry).filter_by(usars_no=usars).first()
            if not match:
                match = session_check.query(AthleteRegistry).filter(
                    AthleteRegistry.first.ilike(first),
                    AthleteRegistry.last.ilike(last)).first()
            if not match:
                missing.append({"panel": r["panel"], "first": r["first"],
                                 "last": r["last"], "club": r["club"]})
        session_check.close()

        if missing:
            return jsonify({
                "error": "Some athletes are not in the registry. "
                         "Please add them first before importing.",
                "missing": missing,
                "missing_count": len(missing),
            }), 422

        # Deduplicate by panel, enrich gender/usars from registry
        seen = set(); panels_by_group = {}
        reg_session = db()
        for r in raw:
            if r["panel"] in seen: continue
            seen.add(r["panel"])
            usars = r.get("usars_no","").strip()
            reg = None
            if usars:
                reg = reg_session.query(AthleteRegistry).filter_by(usars_no=usars).first()
            if not reg:
                reg = reg_session.query(AthleteRegistry).filter(
                    AthleteRegistry.first.ilike(r["first"].strip()),
                    AthleteRegistry.last.ilike(r["last"].strip())).first()
            gender  = (reg.gender  if reg else r.get("gender","")) or ""
            usars_n = (reg.usars_no if reg else usars) or ""
            sk = Skater(competition_id=comp_id, panel=r["panel"],
                        usars_no=usars_n,
                        first=r["first"], last=r["last"], gender=gender,
                        age=r["age"], club=r["club"], group_name=r["group"])
            session.add(sk)
            panels_by_group.setdefault(r["group"], []).append(r["panel"])
        reg_session.close()
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


@app.route("/api/arrange_heats", methods=["POST"])
def arrange_heats():
    """
    Save manually arranged heat rosters.
    Payload: {event_id, stage, heats: [{panels: [panel,...]}], kind: 'individual'|'relay'}
    """
    data     = request.json
    event_id = data["event_id"]
    stage    = data["stage"]
    heats_in = data["heats"]
    kind     = data.get("kind", "individual")

    session = db()
    try:
        if kind == "relay":
            re = session.query(RelayEvent).get(event_id)
            if not re: return jsonify({"error": "Relay event not found"}), 404

            target_heats = [h for h in re.relay_heats if h.stage == stage]
            existing = sorted(target_heats, key=lambda h: h.number)

            # Build team_id → RelayTeam lookup
            tm_map = {str(t.id): t for t in re.teams}

            # Create / remove heats to match incoming count
            while len(existing) < len(heats_in):
                new_h = RelayHeat(relay_event_id=event_id, stage=stage, number=len(existing)+1)
                session.add(new_h); session.flush()
                existing.append(new_h)
            while len(existing) > len(heats_in):
                extra = existing.pop(); session.delete(extra)
            session.flush()

            for heat, heat_data in zip(existing, heats_in):
                for rht in list(heat.teams):
                    session.delete(rht)
                session.flush()
                for order, panel in enumerate(heat_data.get("panels", [])):
                    team = tm_map.get(str(panel))
                    if team:
                        session.add(RelayHeatTeam(
                            relay_heat_id=heat.id, team_id=team.id, seed_order=order))

            session.commit()
            return jsonify({"ok": True})

        # ── Individual ──────────────────────────────────────────────────────
        ev = session.query(Event).get(event_id)
        if not ev: return jsonify({"error": "Event not found"}), 404

        target_heats = [h for h in ev.heats if h.stage == stage]
        sk_map = {s.panel: s for s in session.query(Skater).filter_by(
            competition_id=ev.competition_id).all()}

        n_incoming = len(heats_in)
        existing   = sorted(target_heats, key=lambda h: h.number)

        while len(existing) < n_incoming:
            new_h = Heat(event_id=event_id, stage=stage, number=len(existing)+1)
            session.add(new_h); session.flush()
            existing.append(new_h)
        while len(existing) > n_incoming:
            extra = existing.pop(); session.delete(extra)
        session.flush()

        for heat, heat_data in zip(existing, heats_in):
            for hs in list(heat.skaters):
                session.delete(hs)
            session.flush()
            for order, panel in enumerate(heat_data.get("panels", [])):
                sk = sk_map.get(panel)
                if sk:
                    session.add(HeatSkater(
                        heat_id=heat.id, skater_id=sk.id, seed_order=order))

        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()




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
    session.expire_all()  # force fresh load from DB, no stale cache
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
    from flask import make_response
    resp = make_response(jsonify(result))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp


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


# ── Relay Assignment Page ─────────────────────────────────────────────────────

@app.route("/relay_assign/<int:comp_id>")
def relay_assign_page(comp_id):
    if not is_authed(comp_id):
        return redirect(url_for('comp_view', comp_id=comp_id))
    session = db()
    comp = session.query(Competition).get(comp_id)
    session.close()
    if not comp: return "Not found", 404
    return render_template("relay_assign.html", comp=comp.to_dict())


@app.route("/api/comp/<int:comp_id>/prev_relay_events")
def get_prev_relay_events(comp_id):
    """Return relay event names from the most recent other competition."""
    session = db()
    prev = session.query(Competition)\
        .filter(Competition.id != comp_id)\
        .order_by(Competition.created_at.desc()).first()
    events = []
    if prev:
        rows = session.query(RelayEvent.event_name)\
            .filter_by(competition_id=prev.id).all()
        events = sorted([r[0] for r in rows if r[0]])
    session.close()
    return jsonify({"events": events, "from_comp": prev.name if prev else None})


@app.route("/api/comp/<int:comp_id>/create_relay_event", methods=["POST"])
def create_relay_event_for_comp(comp_id):
    """Create a new relay event for this competition."""
    data       = request.json
    event_name = data.get("event_name", "").strip()
    if not event_name:
        return jsonify({"error": "Event name required"}), 400
    session = db()
    try:
        # Check if already exists
        existing = session.query(RelayEvent).filter_by(
            competition_id=comp_id, event_name=event_name).first()
        if existing:
            return jsonify({"ok": True, "event": existing.to_dict(), "existed": True})
        ev = RelayEvent(competition_id=comp_id, event_name=event_name, event_type="relay")
        session.add(ev)
        session.commit()
        return jsonify({"ok": True, "event": ev.to_dict(), "existed": False})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()



def relay_assign_data(comp_id):
    session = db()
    relay_events = session.query(RelayEvent).filter_by(competition_id=comp_id)\
        .order_by(RelayEvent.event_name).all()
    events_data = []
    for re in relay_events:
        active_teams = [t for t in re.teams if getattr(t,'status','active') != 'scratched']
        events_data.append({
            "id": re.id, "event_name": re.event_name,
            "teams": [{"id":t.id,"club":t.club,"color":t.color or "",
                       "members":[m.name for m in t.members]} for t in active_teams]
        })
    # Attending athletes
    links    = session.query(RegistrationLink).filter_by(competition_id=comp_id).all()
    athletes = []
    seen_ids = set()
    for link in links:
        for reg in link.registrations:
            if reg.status == "attending" and reg.athlete and reg.athlete_id not in seen_ids:
                seen_ids.add(reg.athlete_id)
                a = reg.athlete
                athletes.append({"id":a.id,"full_name":a.full_name,"club":a.club or "",
                                  "usars_no":a.usars_no or "","gender":a.gender or ""})
    # Relay pool extras not already in attending
    pool_names = {a["full_name"] for a in athletes}
    for p in session.query(RelayPoolAthlete).filter_by(competition_id=comp_id).all():
        if p.full_name not in pool_names:
            athletes.append({"id":f"pool_{p.id}","full_name":p.full_name,
                              "club":p.club or "","usars_no":p.usars_no or "",
                              "gender":p.gender or "","from_pool":True})
    athletes.sort(key=lambda a:(a["club"],a["full_name"]))
    session.close()
    return jsonify({"events":events_data,"athletes":athletes})


@app.route("/api/relay_team/<int:team_id>/set_members", methods=["POST"])
def set_relay_team_members(team_id):
    data    = request.json
    members = data.get("members", [])
    color   = data.get("color","").strip()
    session = db()
    try:
        team = session.query(RelayTeam).get(team_id)
        if not team: return jsonify({"error":"Team not found"}), 404
        if color: team.color = color
        for m in list(team.members): session.delete(m)
        session.flush()
        for name in members:
            if name.strip(): session.add(RelayMember(team_id=team_id, name=name.strip()))
        session.commit()
        return jsonify({"ok":True})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
    finally:
        session.close()


@app.route("/api/comp/<int:comp_id>/relay_event/<int:event_id>/add_team", methods=["POST"])
def quick_add_relay_team(comp_id, event_id):
    data    = request.json
    club    = data.get("club","").strip()
    color   = data.get("color","").strip()
    members = data.get("members",[])
    session = db()
    try:
        re = session.query(RelayEvent).get(event_id)
        if not re: return jsonify({"error":"Relay event not found"}), 404
        team = RelayTeam(relay_event_id=event_id, club=club, color=color, status="active")
        session.add(team); session.flush()
        for name in members:
            if name.strip(): session.add(RelayMember(team_id=team.id, name=name.strip()))
        session.commit()
        return jsonify({"ok":True,"team":{"id":team.id,"club":club,"color":color,"members":members}})
    except Exception as e:
        session.rollback(); return jsonify({"error":str(e)}), 500
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
        # Include all teams (active + scratched) so operator can unscratch
        all_teams = [t.to_dict() for t in re.teams]
        result[re.event_name] = {
            "id": re.id, "event_type": re.event_type,
            "competition_id": comp_id,
            "teams": all_teams,
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
        # Determine destination: if advancing from heats and no semis exist, go straight to final
        if from_stage == "heats":
            semis = [h for h in ev.heats if h.stage=="Semi"]
            dst = semis if semis else [h for h in ev.heats if h.stage=="Final"]
        else:
            dst = [h for h in ev.heats if h.stage=="Final"]
        # Count only active skaters — use getattr for safety if column missing
        all_skaters = session.query(Skater).filter_by(
            competition_id=ev.competition_id, group_name=ev.group_name).all()
        active_count = sum(1 for s in all_skaters
                           if getattr(s, 'status', 'active') in (None, 'active'))
        stages = compute_stages(active_count)
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
        import traceback
        session.rollback()
        return jsonify({"error": str(e), "detail": traceback.format_exc()[-500:]}), 500

@app.route("/api/advance_relay", methods=["POST"])
def advance_relay():
    data = request.json
    relay_event_id = data["relay_event_id"]; from_stage = data["from"]
    session = db()
    try:
        re = session.query(RelayEvent).get(relay_event_id)
        if not re: return jsonify({"error":"Not found"}), 404
        src = [h for h in re.relay_heats if h.stage==("Heat" if from_stage=="heats" else "Semi")]
        # If advancing from heats and no semis exist, go straight to Final
        if from_stage == "heats":
            semis = [h for h in re.relay_heats if h.stage=="Semi"]
            dst = semis if semis else [h for h in re.relay_heats if h.stage=="Final"]
        else:
            dst = [h for h in re.relay_heats if h.stage=="Final"]
        stages = compute_stages(len([t for t in re.teams if t.status == 'active']), max_per=7, semi_threshold=6)
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



# ── Operator Auth ─────────────────────────────────────────────────────────────

def is_authed(comp_id):
    """Check if request has valid operator cookie for this competition."""
    session = db()
    comp = session.query(Competition).get(comp_id)
    session.close()
    if not comp or not comp.passcode:
        return True  # no passcode set = open access
    cookie = request.cookies.get(f"op_auth_{comp_id}")
    return cookie == comp.passcode


@app.route("/api/auth/<int:comp_id>", methods=["POST"])
def auth_operator(comp_id):
    """Validate operator passcode and set 7-day cookie."""
    data = request.json or {}
    code = data.get("code", "").strip()
    session = db()
    comp = session.query(Competition).get(comp_id)
    session.close()
    if not comp:
        return jsonify({"error": "Competition not found"}), 404
    if not comp.passcode:
        return jsonify({"ok": True, "message": "No passcode required"})
    if code != comp.passcode:
        return jsonify({"error": "Incorrect passcode"}), 401
    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie(
        f"op_auth_{comp_id}", code,
        max_age=7 * 24 * 3600,  # 7 days
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
    )
    return resp


@app.route("/api/auth/<int:comp_id>/logout", methods=["POST"])
def logout_operator(comp_id):
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(f"op_auth_{comp_id}")
    return resp


@app.route("/api/competitions/<int:comp_id>/passcode", methods=["POST"])
def set_passcode(comp_id):
    """Set or update the operator passcode for a competition."""
    data = request.json or {}
    code = data.get("passcode", "").strip()
    session = db()
    try:
        comp = session.query(Competition).get(comp_id)
        if not comp: return jsonify({"error": "Not found"}), 404
        comp.passcode = code or None
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()




@app.route("/api/scratch", methods=["POST"])
def scratch_skater():
    """
    Scratch a skater from one event or all events.
    scope: "event" (event_id required) | "all" (all events in competition)
    """
    data = request.json
    comp_id  = data["comp_id"]
    panel    = data["panel"]
    scope    = data.get("scope", "event")   # "event" | "all"
    event_id = data.get("event_id")
    reason   = data.get("reason", "")
    session  = db()
    try:
        sk = session.query(Skater).filter_by(competition_id=comp_id, panel=panel).first()
        if not sk: return jsonify({"error": "Skater not found"}), 404

        if scope == "all":
            # Remove from all heats across all events
            for hs in list(sk.heat_skaters):
                session.delete(hs)
            sk.status = "scratched"
        else:
            # Remove from specific event's heats only
            if not event_id: return jsonify({"error": "event_id required for scope=event"}), 400
            for hs in list(sk.heat_skaters):
                if hs.heat.event_id == int(event_id):
                    session.delete(hs)

        # Log it
        session.add(ScratchRecord(
            competition_id=comp_id, panel=panel,
            event_id=event_id if scope=="event" else None,
            scope=scope, reason=reason, entity_type="skater", action="scratch"))
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/unscratch", methods=["POST"])
def unscratch_skater():
    """Restore a scratched skater — re-add to heats in specified events."""
    data = request.json
    comp_id  = data["comp_id"]
    panel    = data["panel"]
    scope    = data.get("scope", "event")
    event_id = data.get("event_id")
    session  = db()
    try:
        sk = session.query(Skater).filter_by(competition_id=comp_id, panel=panel).first()
        if not sk: return jsonify({"error": "Skater not found"}), 404

        if scope == "all":
            sk.status = "active"
            # Re-seed all events for this group
            events = session.query(Event).filter_by(
                competition_id=comp_id, group_name=sk.group_name).all()
            for ev in events:
                reseed_individual(session, ev)
        else:
            if not event_id: return jsonify({"error": "event_id required"}), 400
            ev = session.query(Event).get(int(event_id))
            if ev: reseed_individual(session, ev)

        session.add(ScratchRecord(
            competition_id=comp_id, panel=panel,
            event_id=event_id if scope=="event" else None,
            scope=scope, entity_type="skater", action="unscratch"))
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/scratch_relay", methods=["POST"])
def scratch_relay_team():
    """Scratch a relay team from their relay event."""
    data = request.json
    team_id  = data["team_id"]
    reason   = data.get("reason", "")
    session  = db()
    try:
        team = session.query(RelayTeam).get(team_id)
        if not team: return jsonify({"error": "Team not found"}), 404
        # Remove from all heats
        for rht in list(team.relay_heats):
            session.delete(rht)
        team.status = "scratched"
        re = team.relay_event
        session.add(ScratchRecord(
            competition_id=re.competition_id, team_id=team_id,
            scope="event", entity_type="relay_team", reason=reason, action="scratch"))
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/unscratch_relay", methods=["POST"])
def unscratch_relay_team():
    """Restore a scratched relay team — re-seed the relay event."""
    data = request.json
    team_id = data["team_id"]
    session = db()
    try:
        team = session.query(RelayTeam).get(team_id)
        if not team: return jsonify({"error": "Team not found"}), 404
        team.status = "active"
        re = team.relay_event
        reseed_relay(session, re)
        session.add(ScratchRecord(
            competition_id=re.competition_id, team_id=team_id,
            scope="event", entity_type="relay_team", action="unscratch"))
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


# ── Standby athletes ──────────────────────────────────────────────────────────

@app.route("/api/add_standby", methods=["POST"])
def add_standby():
    """
    Add a standby athlete to the competition roster.
    Can come from registry (by usars_no) or ad-hoc (first, last, panel).
    """
    data    = request.json
    comp_id = data["comp_id"]
    group   = data["group"]
    panel   = data.get("panel", "").strip()
    usars   = data.get("usars_no", "").strip()
    first   = data.get("first", "").strip()
    last    = data.get("last", "").strip()
    session = db()
    try:
        if not panel: return jsonify({"error": "Panel number required"}), 400

        # Check for duplicate panel in this competition
        existing = session.query(Skater).filter_by(
            competition_id=comp_id, panel=panel).first()
        if existing:
            return jsonify({"error": f"Panel #{panel} already exists in this competition"}), 409

        # Try to enrich from registry
        reg = None
        if usars:
            reg = session.query(AthleteRegistry).filter_by(usars_no=usars).first()
        if not reg and first and last:
            reg = session.query(AthleteRegistry).filter(
                AthleteRegistry.first.ilike(first),
                AthleteRegistry.last.ilike(last)).first()

        sk = Skater(
            competition_id=comp_id,
            panel=panel,
            usars_no=(reg.usars_no if reg else usars) or "",
            first=reg.first if reg else first,
            last=reg.last  if reg else last,
            gender=reg.gender if reg else data.get("gender",""),
            age=data.get("age", 0) or 0,
            club=(reg.club if reg else data.get("club","")) or "",
            group_name=group,
            status="standby",  # will be None on old DBs until migration
        )
        session.add(sk)
        session.commit()
        return jsonify({"ok": True, "skater": sk.to_dict()})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/<int:comp_id>/standbys/<path:group>")
def get_standbys(comp_id, group):
    """Return standby athletes for a group."""
    session = db()
    all_sk2 = session.query(Skater).filter_by(
        competition_id=comp_id, group_name=group).all()
    standbys = [s for s in all_sk2 if (s.status or 'active') == 'standby']
    session.close()
    return jsonify([s.to_dict() for s in standbys])


@app.route("/api/<int:comp_id>/scratches/<path:group>")
def get_scratches(comp_id, group):
    """Return scratched athletes for a group with their scratch records."""
    session = db()
    all_sk = session.query(Skater).filter_by(
        competition_id=comp_id, group_name=group).all()
    scratched = [s for s in all_sk if (s.status or 'active') == 'scratched']
    result = []
    for sk in scratched:
        rec = session.query(ScratchRecord).filter_by(
            competition_id=comp_id, panel=sk.panel, action="scratch"
        ).order_by(ScratchRecord.scratched_at.desc()).first()
        d = sk.to_dict()
        d["scratch_reason"] = rec.reason if rec else ""
        d["scratch_scope"]  = rec.scope  if rec else "event"
        result.append(d)
    session.close()
    return jsonify(result)




# ── Relay Pool ────────────────────────────────────────────────────────────────

@app.route("/api/<int:comp_id>/relay_pool")
def get_relay_pool(comp_id):
    session = db()
    athletes = session.query(RelayPoolAthlete).filter_by(competition_id=comp_id)\
        .order_by(RelayPoolAthlete.last, RelayPoolAthlete.first).all()
    session.close()
    return jsonify([a.to_dict() for a in athletes])


@app.route("/api/relay_pool/add", methods=["POST"])
def add_relay_pool():
    data = request.json
    comp_id = data["comp_id"]
    session = db()
    try:
        # Try to enrich from registry
        reg = None
        usars = data.get("usars_no","").strip()
        first = data.get("first","").strip()
        last  = data.get("last","").strip()
        if usars:
            reg = session.query(AthleteRegistry).filter_by(usars_no=usars).first()
        if not reg and first and last:
            reg = session.query(AthleteRegistry).filter(
                AthleteRegistry.first.ilike(first),
                AthleteRegistry.last.ilike(last)).first()
        a = RelayPoolAthlete(
            competition_id=comp_id,
            first=reg.first if reg else first,
            last=reg.last  if reg else last,
            usars_no=(reg.usars_no if reg else usars) or "",
            club=(reg.club if reg else data.get("club","")) or "",
            gender=(reg.gender if reg else data.get("gender","")) or "",
        )
        session.add(a)
        session.commit()
        return jsonify({"ok": True, "athlete": a.to_dict()})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/relay_pool/<int:athlete_id>", methods=["DELETE"])
def delete_relay_pool(athlete_id):
    session = db()
    try:
        a = session.query(RelayPoolAthlete).get(athlete_id)
        if not a: return jsonify({"error": "Not found"}), 404
        session.delete(a)
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


# ── Relay Team Editing ────────────────────────────────────────────────────────

@app.route("/api/relay_team/update_members", methods=["POST"])
def update_relay_team_members():
    """Replace a team's member list and optionally update color.
    If members is null, only the color is updated."""
    data = request.json
    team_id = data["team_id"]
    members = data.get("members")   # None = color-only update
    color   = data.get("color", "").strip()
    session = db()
    try:
        team = session.query(RelayTeam).get(team_id)
        if not team: return jsonify({"error": "Team not found"}), 404
        if color:
            team.color = color
        if members is not None:
            for m in list(team.members):
                session.delete(m)
            session.flush()
            for name in members:
                name = name.strip()
                if name:
                    session.add(RelayMember(team_id=team_id, name=name))
        session.commit()
        return jsonify({"ok": True, "team": team.to_dict()})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/relay_team/add", methods=["POST"])
def add_relay_team():
    """Add a new team to a relay event."""
    data = request.json
    relay_event_id = data["relay_event_id"]
    club    = data.get("club","").strip()
    color   = data.get("color","").strip()
    members = data.get("members", [])
    session = db()
    try:
        re = session.query(RelayEvent).get(relay_event_id)
        if not re: return jsonify({"error": "Relay event not found"}), 404
        team = RelayTeam(relay_event_id=relay_event_id, club=club, color=color, status="active")
        session.add(team)
        session.flush()
        for name in members:
            name = name.strip()
            if name:
                session.add(RelayMember(team_id=team.id, name=name))
        session.commit()
        return jsonify({"ok": True, "team": team.to_dict()})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/relay_team/<int:team_id>", methods=["DELETE"])
def delete_relay_team(team_id):
    """Remove a team entirely from a relay event."""
    session = db()
    try:
        team = session.query(RelayTeam).get(team_id)
        if not team: return jsonify({"error": "Team not found"}), 404
        session.delete(team)
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()



# ── Registration Links ────────────────────────────────────────────────────────

@app.route("/register/<token>")
def register_page(token):
    session = db()
    link = session.query(RegistrationLink).filter_by(token=token).first()
    if not link:
        session.close()
        return "Registration link not found", 404
    comp = session.query(Competition).get(link.competition_id)
    session.close()
    return render_template("register.html", link=link.to_dict(), comp=comp.to_dict())


@app.route("/api/comp/<int:comp_id>/clubs")
def get_clubs(comp_id):
    """Return distinct clubs from registry that have athletes."""
    session = db()
    clubs = [r[0] for r in session.query(AthleteRegistry.club)
             .filter(AthleteRegistry.club.isnot(None))
             .distinct().order_by(AthleteRegistry.club).all()
             if r[0] and r[0].strip()]
    # Also show clubs that already have links
    existing = {l.club for l in session.query(RegistrationLink)
                .filter_by(competition_id=comp_id).all()}
    session.close()
    return jsonify({"clubs": clubs, "has_links": list(existing)})


@app.route("/api/comp/<int:comp_id>/reg_links", methods=["GET"])
def get_reg_links(comp_id):
    session = db()
    links = session.query(RegistrationLink).filter_by(competition_id=comp_id).all()
    result = []
    for link in links:
        regs = link.registrations
        attending     = sum(1 for r in regs if r.status == "attending")
        not_attending = sum(1 for r in regs if r.status == "not_attending")
        pending       = sum(1 for r in regs if r.status == "pending")
        total         = session.query(AthleteRegistry).filter_by(club=link.club).count()
        d = link.to_dict()
        d.update({"attending": attending, "not_attending": not_attending,
                  "pending": pending, "total_in_registry": total})
        result.append(d)
    session.close()
    return jsonify(result)


@app.route("/api/comp/<int:comp_id>/reg_links", methods=["POST"])
def create_reg_links(comp_id):
    """Generate registration links for selected clubs."""
    data  = request.json
    clubs = data.get("clubs", [])
    session = db()
    try:
        created = []
        for club in clubs:
            # Skip if already exists
            existing = session.query(RegistrationLink).filter_by(
                competition_id=comp_id, club=club).first()
            if existing:
                created.append(existing.to_dict())
                continue
            token = uuid.uuid4().hex
            link  = RegistrationLink(competition_id=comp_id, club=club, token=token)
            session.add(link)
            session.flush()
            # Pre-create pending registrations for all athletes in this club
            athletes = session.query(AthleteRegistry).filter_by(club=club).all()
            for a in athletes:
                session.add(Registration(link_id=link.id, athlete_id=a.id, status="pending"))
            created.append(link.to_dict())
        session.commit()
        return jsonify({"ok": True, "links": created})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/reg_links/<int:link_id>/toggle", methods=["POST"])
def toggle_reg_link(link_id):
    session = db()
    try:
        link = session.query(RegistrationLink).get(link_id)
        if not link: return jsonify({"error": "Not found"}), 404
        link.is_open = not link.is_open
        session.commit()
        return jsonify({"ok": True, "is_open": link.is_open})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/reg_links/<int:link_id>", methods=["DELETE"])
def delete_reg_link(link_id):
    session = db()
    try:
        link = session.query(RegistrationLink).get(link_id)
        if not link: return jsonify({"error": "Not found"}), 404
        session.delete(link)
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


# ── Registration Page API ─────────────────────────────────────────────────────

@app.route("/api/register/<token>/athletes")
def register_athletes(token):
    """Return athletes for this club + their current status."""
    session = db()
    link = session.query(RegistrationLink).filter_by(token=token).first()
    if not link:
        session.close()
        return jsonify({"error": "Invalid link"}), 404
    # Get all athletes in club with their registration status
    athletes = session.query(AthleteRegistry).filter_by(club=link.club)\
        .order_by(AthleteRegistry.last, AthleteRegistry.first).all()
    reg_map = {r.athlete_id: r for r in link.registrations}
    result = []
    for a in athletes:
        reg = reg_map.get(a.id)
        result.append({
            "athlete_id": a.id,
            "full_name":  a.full_name,
            "usars_no":   a.usars_no or "",
            "gender":     a.gender or "",
            "status":     reg.status if reg else "pending",
            "reg_id":     reg.id if reg else None,
        })
    session.close()
    return jsonify({"athletes": result, "is_open": link.is_open, "club": link.club})


@app.route("/api/register/<token>/update", methods=["POST"])
def update_registration(token):
    """Update a single athlete's attendance status."""
    data       = request.json
    athlete_id = data["athlete_id"]
    status     = data["status"]  # attending | not_attending | pending
    if status not in ("attending", "not_attending", "pending"):
        return jsonify({"error": "Invalid status"}), 400
    session = db()
    try:
        link = session.query(RegistrationLink).filter_by(token=token).first()
        if not link: return jsonify({"error": "Invalid link"}), 404
        if not link.is_open: return jsonify({"error": "Registration is closed"}), 403
        reg = session.query(Registration).filter_by(
            link_id=link.id, athlete_id=athlete_id).first()
        if not reg:
            reg = Registration(link_id=link.id, athlete_id=athlete_id, status=status)
            session.add(reg)
        else:
            reg.status = status
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


# ── Operator registration management ─────────────────────────────────────────

@app.route("/api/comp/<int:comp_id>/registrations")
def get_registrations(comp_id):
    """Full registration data for operator view."""
    session = db()
    links = session.query(RegistrationLink).filter_by(competition_id=comp_id).all()
    result = {}
    for link in links:
        regs = sorted(link.registrations, key=lambda r: (r.athlete.last or "", r.athlete.first or ""))
        result[link.club] = {
            "link_id": link.id, "token": link.token, "is_open": link.is_open,
            "athletes": [r.to_dict() for r in regs]
        }
    session.close()
    return jsonify(result)


@app.route("/api/registration/<int:reg_id>/override", methods=["POST"])
def override_registration(reg_id):
    """Operator override of an individual athlete's status."""
    data   = request.json
    status = data.get("status")
    session = db()
    try:
        reg = session.query(Registration).get(reg_id)
        if not reg: return jsonify({"error": "Not found"}), 404
        reg.status = status
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()



# ── Group Assignment ──────────────────────────────────────────────────────────

@app.route("/assign_groups/<int:comp_id>")
def assign_groups_page(comp_id):
    if not is_authed(comp_id):
        return redirect(url_for('comp_view', comp_id=comp_id))
    session = db()
    comp = session.query(Competition).get(comp_id)
    session.close()
    if not comp: return "Not found", 404
    return render_template("assign_groups.html", comp=comp.to_dict())


@app.route("/api/comp/<int:comp_id>/prev_groups")
def get_prev_groups(comp_id):
    """Return distinct group names from the most recent other competition."""
    session = db()
    # Find most recent competition that isn't this one and has skaters
    prev = session.query(Competition)\
        .filter(Competition.id != comp_id)\
        .order_by(Competition.created_at.desc()).first()
    groups = []
    if prev:
        rows = session.query(Skater.group_name)\
            .filter_by(competition_id=prev.id)\
            .distinct().all()
        groups = sorted([r[0] for r in rows if r[0]])
    session.close()
    return jsonify({"groups": groups, "from_comp": prev.name if prev else None})


@app.route("/api/comp/<int:comp_id>/assign_group", methods=["POST"])
def assign_group():
    """Save group assignment for a registration — called on every drag drop."""
    data       = request.json
    comp_id    = data["comp_id"]
    reg_id     = data["reg_id"]
    group_name = data.get("group_name", "")  # empty string = unassign
    session = db()
    try:
        reg = session.query(Registration).get(reg_id)
        if not reg: return jsonify({"error": "Registration not found"}), 404
        reg.group_name = group_name or None
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/comp/<int:comp_id>/group_assignments")
def get_group_assignments(comp_id):
    """Return all attending athletes with their current group assignments."""
    session = db()
    links = session.query(RegistrationLink).filter_by(competition_id=comp_id).all()
    athletes = []
    for link in links:
        for reg in link.registrations:
            if reg.status == "attending":
                athletes.append(reg.to_dict())
    # Sort by club then last name
    athletes.sort(key=lambda a: (a["club"], a["full_name"]))
    session.close()
    return jsonify({"athletes": athletes})


@app.route("/api/comp/<int:comp_id>/save_group_assignments", methods=["POST"])
def save_group_assignments(comp_id):
    """Bulk save all group assignments at once."""
    data = request.json
    assignments = data.get("assignments", [])
    session = db()
    try:
        for item in assignments:
            reg = session.query(Registration).get(item["reg_id"])
            if reg:
                reg.group_name = item.get("group_name") or None
        session.commit()
        return jsonify({"ok": True, "saved": len(assignments)})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


# ── Relay Assignment Page ─────────────────────────────────────────────────────

@app.route("/assign_relay/<int:comp_id>/<int:relay_event_id>")
def assign_relay_page(comp_id, relay_event_id):
    if not is_authed(comp_id):
        return redirect(url_for('comp_view', comp_id=comp_id))
    session = db()
    comp = session.query(Competition).get(comp_id)
    rev  = session.query(RelayEvent).get(relay_event_id)
    session.close()
    if not comp or not rev: return "Not found", 404
    return render_template("assign_relay.html",
                           comp=comp.to_dict(),
                           relay_event={"id": rev.id, "name": rev.name})


@app.route("/api/relay_event/<int:relay_event_id>/team_assignments")
def get_relay_team_assignments(relay_event_id):
    session = db()
    rev = session.query(RelayEvent).get(relay_event_id)
    if not rev: session.close(); return jsonify({"error":"Not found"}), 404
    comp_id = rev.competition_id
    links = session.query(RegistrationLink).filter_by(competition_id=comp_id).all()
    # Build set of names already assigned to a team
    assigned = set()
    for t in rev.teams:
        if getattr(t,'status','active') != 'scratched':
            for m in t.members:
                assigned.add(m.name.strip().lower())
    athletes = []
    for link in links:
        for reg in link.registrations:
            if reg.status == "attending" and reg.athlete:
                a = reg.athlete
                athletes.append({
                    "id": reg.id, "athlete_id": a.id,
                    "full_name": a.full_name, "club": a.club or "",
                    "usars_no": a.usars_no or "", "gender": a.gender or "",
                    "in_team": a.full_name.strip().lower() in assigned,
                })
    athletes.sort(key=lambda a: (a["club"], a["full_name"]))
    COLORS = {"green":"#2E7D32","blue":"#1565C0","yellow":"#F9A825",
               "pink":"#E91E8C","light blue":"#29B6F6","black":"#212121","red":"#C62828"}
    teams = []
    for t in rev.teams:
        if getattr(t,'status','active') == 'scratched': continue
        teams.append({"id":t.id,"club":t.club,"color":t.color or "",
                      "color_hex":COLORS.get((t.color or "").lower(),"#9E9E9E"),
                      "members":[m.name for m in t.members]})
    session.close()
    return jsonify({"teams": teams, "athletes": athletes})


@app.route("/api/relay_event/<int:relay_event_id>/save_team_members", methods=["POST"])
def save_relay_team_members_bulk(relay_event_id):
    data  = request.json
    teams = data.get("teams", [])
    session = db()
    try:
        for item in teams:
            team = session.query(RelayTeam).get(item["team_id"])
            if not team: continue
            for m in list(team.members): session.delete(m)
            session.flush()
            for name in item.get("members", []):
                name = name.strip()
                if name: session.add(RelayMember(team_id=team.id, name=name))
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/relay_event/<int:relay_event_id>/add_team", methods=["POST"])
def add_relay_team_for_event(relay_event_id):
    data  = request.json
    club  = data.get("club","").strip()
    color = data.get("color","").strip()
    session = db()
    try:
        rev = session.query(RelayEvent).get(relay_event_id)
        if not rev: return jsonify({"error":"Not found"}), 404
        team = RelayTeam(relay_event_id=relay_event_id, club=club, color=color, status="active")
        session.add(team); session.commit()
        COLORS = {"green":"#2E7D32","blue":"#1565C0","yellow":"#F9A825",
                   "pink":"#E91E8C","light blue":"#29B6F6","black":"#212121","red":"#C62828"}
        return jsonify({"ok":True,"team":{"id":team.id,"club":club,"color":color,
                        "color_hex":COLORS.get(color.lower(),"#9E9E9E"),"members":[]}})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/relay_event/<int:relay_event_id>/delete_team/<int:team_id>", methods=["DELETE"])
def delete_relay_team_assignment(relay_event_id, team_id):
    session = db()
    try:
        team = session.query(RelayTeam).get(team_id)
        if not team: return jsonify({"error":"Not found"}), 404
        session.delete(team); session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/comp/<int:comp_id>/seed_from_reg", methods=["POST"])
def seed_from_registrations(comp_id):
    """
    Create Skater entries for all 'attending' athletes with group assignments
    and seed heats. Uses registration.group_name set via drag-drop assignment.
    """
    session = db()
    try:
        links = session.query(RegistrationLink).filter_by(competition_id=comp_id).all()

        # Collect attending registrations that have a group assigned
        regs_with_group = []
        for link in links:
            for reg in link.registrations:
                if reg.status == "attending" and reg.athlete:
                    if reg.group_name:
                        regs_with_group.append(reg)
                    # silently skip athletes with no group assigned

        if not regs_with_group:
            return jsonify({"error": "No athletes with group assignments found. Assign groups first."}), 400

        comp_events_q = session.query(CompEvent).filter_by(competition_id=comp_id)\
            .filter(CompEvent.category != 'relay').all()
        event_names = [ce.event_name for ce in comp_events_q]
        points_events = {ce.event_name for ce in comp_events_q if ce.points_event}
        if not event_names:
            return jsonify({"error": "No events configured for this competition"}), 400

        # Clear existing skaters and events
        for s in session.query(Skater).filter_by(competition_id=comp_id).all():
            session.delete(s)
        for e in session.query(Event).filter_by(competition_id=comp_id).all():
            session.delete(e)
        session.flush()

        # Group registrations by group_name
        groups = {}
        for reg in regs_with_group:
            groups.setdefault(reg.group_name, []).append(reg)

        skater_count = 0
        for group, regs in sorted(groups.items()):
            for reg in regs:
                a = reg.athlete
                panel = a.usars_no or str(a.id)
                if not session.query(Skater).filter_by(competition_id=comp_id, panel=panel).first():
                    session.add(Skater(
                        competition_id=comp_id, panel=panel,
                        usars_no=a.usars_no or "", first=a.first, last=a.last,
                        gender=a.gender or "", club=a.club or "",
                        group_name=group, status="active",
                    ))
                    skater_count += 1
            session.flush()

            # Seed heats for each event
            for ev_name in event_names:
                ev = Event(competition_id=comp_id, group_name=group,
                           name=ev_name, event_type="heat_based",
                           points_event=1 if ev_name in points_events else 0)
                session.add(ev)
                session.flush()
                panels = [r.athlete.usars_no or str(r.athlete.id) for r in regs]
                seed_individual_heats(session, ev, panels)

        session.commit()
        return jsonify({"ok": True, "skater_count": skater_count,
                        "groups": list(groups.keys())})

        session.commit()
        return jsonify({"ok": True, "skater_count": skater_count,
                        "groups": list(groups.keys())})
    except Exception as e:
        session.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        session.close()


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
