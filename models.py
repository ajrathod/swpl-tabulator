"""
SQLAlchemy models — PacWest Race Manager v3
Swap DATABASE_URL env var to postgresql://... for cloud deployment.
"""
from sqlalchemy import (
    create_engine, Column, Integer, String, ForeignKey,
    DateTime, UniqueConstraint, Float, Boolean
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///skating.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# Points awarded per final placement
POINTS = {1: 30, 2: 20, 3: 10, 4: 5}

# ── All available event templates ─────────────────────────────────────────────
# category: "individual" | "relay"
# points_event: True = counts toward group overall standings
ALL_EVENT_TEMPLATES = [
    # Individual distance races — all count for points
    {"name": "Short Distance",   "category": "individual", "event_type": "heat_based", "points_event": True},
    {"name": "Medium Distance",  "category": "individual", "event_type": "heat_based", "points_event": True},
    {"name": "Long Distance",    "category": "individual", "event_type": "heat_based", "points_event": True},
    {"name": "Open",             "category": "individual", "event_type": "direct_final","points_event": False},
    # Relay events — no points, but full heat/semi/final structure
    {"name": "Juvenile 3 Mix Relay",       "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Juvenile 3 Girls Relay",     "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Juvenile 3 Boys Relay",      "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Primary 2 Mix Relay",        "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Primary 2 Boys Relay",       "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Primary 2 Girls Relay",      "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Elementary 2 Girls Relay",   "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Elementary 2 Mix Relay",     "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Elementary 2 Boys Relay",    "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Freshman 3 Mix Relay",       "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Freshman 3 Girls Relay",     "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Freshman 3 Boys Relay",      "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Senior 3 Mix Relay",         "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Senior 2 Ladies Relay",      "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Senior 2 Men Relay",         "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Sophomore 2 Mix Relay",      "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Sophomore 2 Men Relay",      "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Sophomore 2 Ladies Relay",   "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Master 2 Mix Relay",         "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Master 2 Men Relay",         "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Master 2 Ladies Relay",      "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Master 3 Mix Relay",         "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Master 3 Men Relay",         "category": "relay", "event_type": "heat_based", "points_event": False},
    {"name": "Master 4 Ladies Relay",      "category": "relay", "event_type": "heat_based", "points_event": False},
]

INDIVIDUAL_EVENTS = [e for e in ALL_EVENT_TEMPLATES if e["category"] == "individual"]
RELAY_EVENTS_LIST = [e for e in ALL_EVENT_TEMPLATES if e["category"] == "relay"]



# ── Global Athlete Registry (independent of competitions) ─────────────────────

class AthleteRegistry(Base):
    """
    One-time global entry per athlete. USARS No. is the primary unique key.
    Used to validate athletes before they can be added to a competition.
    """
    __tablename__ = "athlete_registry"
    id         = Column(Integer, primary_key=True)
    usars_no   = Column(String(30), nullable=False, unique=True)
    first      = Column(String(100), nullable=False)
    last       = Column(String(100), nullable=False)
    gender     = Column(String(5))
    dob        = Column(String(20))     # YYYY-MM-DD
    club       = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def full_name(self):
        return f"{self.first} {self.last}".strip()

    def to_dict(self):
        return {
            "id": self.id, "usars_no": self.usars_no,
            "first": self.first, "last": self.last,
            "full_name": self.full_name,
            "gender": self.gender or "", "dob": self.dob or "",
            "club": self.club or "",
            "created_at": self.created_at.strftime("%Y-%m-%d") if self.created_at else "",
        }


# ── Models ────────────────────────────────────────────────────────────────────

class Competition(Base):
    __tablename__ = "competitions"
    id         = Column(Integer, primary_key=True)
    name       = Column(String(200), nullable=False)
    venue      = Column(String(200))
    date       = Column(String(20))
    comp_type  = Column(String(20), default="indoor")
    tiebreaker_event = Column(String(200))
    passcode   = Column(String(100))           # operator access code
    created_at = Column(DateTime, default=datetime.utcnow)

    skaters        = relationship("Skater",        back_populates="competition", cascade="all, delete-orphan")
    events         = relationship("Event",         back_populates="competition", cascade="all, delete-orphan")
    relay_events   = relationship("RelayEvent",    back_populates="competition", cascade="all, delete-orphan")
    comp_events    = relationship("CompEvent",     back_populates="competition", cascade="all, delete-orphan")

    def to_dict(self):
        return {"id":self.id,"name":self.name,"venue":self.venue or "",
                "date":self.date or "","type":self.comp_type,
                "tiebreaker_event": self.tiebreaker_event or "",
                "has_passcode": bool(self.passcode),
                "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else ""}


class CompEvent(Base):
    """Which events are enabled for this competition (set at setup time)."""
    __tablename__ = "comp_events"
    id             = Column(Integer, primary_key=True)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    event_name     = Column(String(200), nullable=False)
    category       = Column(String(20))       # individual | relay
    event_type     = Column(String(30))       # heat_based | direct_final
    points_event   = Column(Integer, default=0)  # 1 = counts for overall standings

    competition = relationship("Competition", back_populates="comp_events")
    __table_args__ = (UniqueConstraint("competition_id", "event_name"),)


class Skater(Base):
    __tablename__ = "skaters"
    id             = Column(Integer, primary_key=True)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    usars_no       = Column(String(30))
    panel          = Column(String(20), nullable=False)
    first          = Column(String(100))
    last           = Column(String(100))
    gender         = Column(String(5))
    age            = Column(Integer)
    club           = Column(String(100))
    group_name     = Column(String(200))
    status         = Column(String(20), default="active")  # active | scratched | standby

    competition  = relationship("Competition", back_populates="skaters")
    heat_skaters = relationship("HeatSkater",  back_populates="skater", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("competition_id", "panel"),)

    @property
    def full_name(self):
        return f"{self.first} {self.last}".strip()

    def to_dict(self):
        return {"id":self.id,"panel":self.panel,"usars_no":self.usars_no or "",
                "first":self.first,"last":self.last,
                "full_name":self.full_name,"gender":self.gender,"age":self.age,
                "club":self.club,"group":self.group_name,"status":self.status or "active"}


class Event(Base):
    """Individual-skater event (Short Distance, Open, etc.)"""
    __tablename__ = "events"
    id             = Column(Integer, primary_key=True)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    group_name     = Column(String(200), nullable=False)
    name           = Column(String(200), nullable=False)
    event_type     = Column(String(30), default="heat_based")
    points_event   = Column(Integer, default=1)   # 1 = counts for group standings

    competition = relationship("Competition", back_populates="events")
    heats       = relationship("Heat", back_populates="event", cascade="all, delete-orphan",
                               order_by="Heat.stage,Heat.number")

    def to_dict(self):
        return {"id":self.id,"group":self.group_name,"name":self.name,
                "type":self.event_type,"points_event":bool(self.points_event)}


class Heat(Base):
    __tablename__ = "heats"
    id       = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    stage    = Column(String(20), nullable=False)   # Heat | Semi | Final
    number   = Column(Integer, default=1)

    event   = relationship("Event", back_populates="heats")
    skaters = relationship("HeatSkater", back_populates="heat",
                           cascade="all, delete-orphan", order_by="HeatSkater.seed_order")

    def to_dict(self):
        return {"id":self.id,"stage":self.stage,"number":self.number,
                "skaters":[hs.to_dict() for hs in self.skaters]}


class HeatSkater(Base):
    __tablename__ = "heat_skaters"
    id         = Column(Integer, primary_key=True)
    heat_id    = Column(Integer, ForeignKey("heats.id"), nullable=False)
    skater_id  = Column(Integer, ForeignKey("skaters.id"), nullable=False)
    seed_order = Column(Integer, default=0)
    placement  = Column(String(10))   # "1".."6", "DQ", or NULL

    heat   = relationship("Heat",   back_populates="skaters")
    skater = relationship("Skater", back_populates="heat_skaters")

    def to_dict(self):
        s = self.skater
        return {"panel":s.panel,"full_name":s.full_name,"first":s.first,"last":s.last,
                "club":s.club,"age":s.age,"gender":s.gender,
                "placement":self.placement or "","seed_order":self.seed_order}


# ── Relay models (teams as participants, same heat structure) ─────────────────

class RelayEvent(Base):
    """A relay event (e.g. Juvenile 3 Mix Relay) for a competition."""
    __tablename__ = "relay_events"
    id             = Column(Integer, primary_key=True)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    event_name     = Column(String(200))
    event_type     = Column(String(30), default="heat_based")

    competition  = relationship("Competition", back_populates="relay_events")
    teams        = relationship("RelayTeam",   back_populates="relay_event", cascade="all, delete-orphan")
    relay_heats  = relationship("RelayHeat",   back_populates="relay_event", cascade="all, delete-orphan",
                                order_by="RelayHeat.stage,RelayHeat.number")

    def to_dict(self):
        return {"id":self.id,"event_name":self.event_name,"event_type":self.event_type}


class RelayTeam(Base):
    __tablename__ = "relay_teams"
    id             = Column(Integer, primary_key=True)
    relay_event_id = Column(Integer, ForeignKey("relay_events.id"), nullable=False)
    club           = Column(String(100))
    color          = Column(String(50))
    status         = Column(String(20), default="active")  # active | scratched

    relay_event   = relationship("RelayEvent",    back_populates="teams")
    members       = relationship("RelayMember",   back_populates="team", cascade="all, delete-orphan")
    relay_heats   = relationship("RelayHeatTeam", back_populates="team",  cascade="all, delete-orphan")

    def to_dict(self):
        return {"id":self.id,"club":self.club,"color":self.color or "",
                "status": self.status or "active",
                "members":[m.name for m in self.members]}


class RelayMember(Base):
    __tablename__ = "relay_members"
    id      = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("relay_teams.id"), nullable=False)
    name    = Column(String(200))
    team    = relationship("RelayTeam", back_populates="members")


class RelayHeat(Base):
    """A heat/semi/final within a relay event."""
    __tablename__ = "relay_heats"
    id             = Column(Integer, primary_key=True)
    relay_event_id = Column(Integer, ForeignKey("relay_events.id"), nullable=False)
    stage          = Column(String(20), nullable=False)
    number         = Column(Integer, default=1)

    relay_event = relationship("RelayEvent",    back_populates="relay_heats")
    teams       = relationship("RelayHeatTeam", back_populates="relay_heat",
                               cascade="all, delete-orphan", order_by="RelayHeatTeam.seed_order")

    def to_dict(self):
        return {"id":self.id,"stage":self.stage,"number":self.number,
                "teams":[rht.to_dict() for rht in self.teams]}


class RelayHeatTeam(Base):
    """A team's entry in a relay heat."""
    __tablename__ = "relay_heat_teams"
    id            = Column(Integer, primary_key=True)
    relay_heat_id = Column(Integer, ForeignKey("relay_heats.id"), nullable=False)
    team_id       = Column(Integer, ForeignKey("relay_teams.id"), nullable=False)
    seed_order    = Column(Integer, default=0)
    placement     = Column(String(10))

    relay_heat = relationship("RelayHeat",  back_populates="teams")
    team       = relationship("RelayTeam",  back_populates="relay_heats")

    def to_dict(self):
        t = self.team
        return {"team_id":t.id,"club":t.club,"color":t.color or "",
                "members":[m.name for m in t.members],
                "placement":self.placement or "","seed_order":self.seed_order}


class ScratchRecord(Base):
    """Audit trail of scratches and unscratch actions."""
    __tablename__ = "scratch_records"
    id             = Column(Integer, primary_key=True)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    panel          = Column(String(20))           # skater panel (individual)
    team_id        = Column(Integer)              # relay team id
    event_id       = Column(Integer)              # None = all events
    scope          = Column(String(20))           # "event" | "all"
    entity_type    = Column(String(20))           # "skater" | "relay_team"
    reason         = Column(String(200))
    action         = Column(String(20))           # "scratch" | "unscratch"
    scratched_at   = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id":self.id,"panel":self.panel,"team_id":self.team_id,
                "scope":self.scope,"reason":self.reason or "",
                "action":self.action,
                "scratched_at":self.scratched_at.strftime("%Y-%m-%d %H:%M") if self.scratched_at else ""}


class RelayPoolAthlete(Base):
    """Athletes in the relay pool — available to be assigned to relay teams."""
    __tablename__ = "relay_pool_athletes"
    id             = Column(Integer, primary_key=True)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    first          = Column(String(100))
    last           = Column(String(100))
    usars_no       = Column(String(30))
    club           = Column(String(100))
    gender         = Column(String(5))
    age            = Column(Integer)
    added_at       = Column(DateTime, default=datetime.utcnow)

    @property
    def full_name(self):
        return f"{self.first} {self.last}".strip()

    def to_dict(self):
        return {"id":self.id,"first":self.first,"last":self.last,
                "full_name":self.full_name,"usars_no":self.usars_no or "",
                "club":self.club or "","gender":self.gender or "","age":self.age or 0}


class RegistrationLink(Base):
    __tablename__ = "registration_links"
    id             = Column(Integer, primary_key=True)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    club           = Column(String(100), nullable=False)
    token          = Column(String(64), unique=True, nullable=False)
    is_open        = Column(Boolean, default=True)
    created_at     = Column(DateTime, default=datetime.utcnow)

    registrations  = relationship("Registration", back_populates="link", cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "competition_id": self.competition_id,
                "club": self.club, "token": self.token,
                "is_open": self.is_open,
                "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else ""}


class Registration(Base):
    __tablename__ = "registrations"
    id         = Column(Integer, primary_key=True)
    link_id    = Column(Integer, ForeignKey("registration_links.id"), nullable=False)
    athlete_id = Column(Integer, ForeignKey("athlete_registry.id"), nullable=False)
    status     = Column(String(20), default="pending")  # pending | attending | not_attending
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    link    = relationship("RegistrationLink", back_populates="registrations")
    athlete = relationship("AthleteRegistry")

    def to_dict(self):
        a = self.athlete
        return {"id": self.id, "athlete_id": self.athlete_id,
                "full_name": a.full_name if a else "",
                "panel": "", "club": a.club if a else "",
                "usars_no": a.usars_no if a else "",
                "gender": a.gender if a else "",
                "status": self.status,
                "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else ""}


def init_db():
    Base.metadata.create_all(engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
