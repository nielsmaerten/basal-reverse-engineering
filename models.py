"""Data models for therapy day data."""

from dataclasses import dataclass, field


@dataclass
class CGMEntry:
    timestamp_ms: int
    sgv: int            # mg/dl
    direction: str      # "Flat", "FortyFiveUp", etc.
    delta: float        # mg/dl change from previous reading


@dataclass
class BasalSlot:
    timestamp_ms: int
    duration_ms: int
    rate: float         # effective U/h


@dataclass
class Bolus:
    timestamp_ms: int
    amount: float       # units
    bolus_type: str     # "SMB" or "NORMAL"
    event_type: str     # "Correction Bolus", "Meal Bolus", etc.


@dataclass
class Carbs:
    timestamp_ms: int
    amount: float       # grams


@dataclass
class TempTarget:
    timestamp_ms: int
    duration_ms: int
    target_low: float   # mg/dl
    target_high: float  # mg/dl
    reason: str


@dataclass
class ProfileSwitch:
    timestamp_ms: int
    percentage: int     # 100 = normal
    profile_name: str


@dataclass
class Event:
    timestamp_ms: int
    event_type: str     # "Site Change", "Sensor Change", etc.
    duration_ms: int    # 0 for instant events
    notes: str


@dataclass
class DayData:
    date: str
    timezone: str
    cgm: list[CGMEntry] = field(default_factory=list)
    basal: list[BasalSlot] = field(default_factory=list)
    boluses: list[Bolus] = field(default_factory=list)
    carbs: list[Carbs] = field(default_factory=list)
    temp_targets: list[TempTarget] = field(default_factory=list)
    profile_switches: list[ProfileSwitch] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    tdd: float = 0.0
    total_bolus: float = 0.0
    total_basal: float = 0.0
    total_carbs: float = 0.0
