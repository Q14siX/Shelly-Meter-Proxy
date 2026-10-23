"""Vendor independent three-phase measurement model.

Power convention: positive = import from grid, negative = export to grid.
A single total measurement is split evenly only to satisfy the triphase wire
format; this is not a measurement of the actual individual phases.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .const import CONF_MODE, CONF_TOTAL, CONF_PHASE_A, CONF_PHASE_B, CONF_PHASE_C, CONF_INVERT, CONF_TIMEOUT, CONF_SPLIT


def watts(value: object, unit: str | None) -> float:
    """Validate numeric readings and normalize kW/MW to W."""
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Kein numerischer Messwert") from exc
    if not math.isfinite(n):
        raise ValueError("Messwert nicht endlich")
    scale = {"W": 1, "kW": 1000, "MW": 1_000_000}.get((unit or "W").strip())
    if scale is None:
        raise ValueError(f"Unbekannte Leistungseinheit: {unit}")
    return n * scale


def parse_reading(state, now: datetime, max_age: int) -> float:
    if state is None or state.state in ("unknown", "unavailable", "none", ""):
        raise ValueError("Quelle nicht verfügbar")
    stamp = getattr(state, "last_reported", None) or state.last_updated
    if stamp and (now - stamp).total_seconds() > max_age:
        raise ValueError("Messwert ist veraltet")
    return watts(state.state, state.attributes.get("unit_of_measurement"))


@dataclass
class Meter:
    phases: tuple[float, float, float] = (0.0, 0.0, 0.0)
    last_update: float = 0.0
    available: bool = False
    error: str | None = "Noch keine Messung"
    imports: list[float] = field(default_factory=lambda: [0.0] * 3)
    exports: list[float] = field(default_factory=lambda: [0.0] * 3)
    _energy_sample: float | None = None

    def update(self, hass, settings: dict) -> None:
        now = datetime.now(timezone.utc)
        max_age = int(settings.get(CONF_TIMEOUT, 45))
        try:
            if settings[CONF_MODE] == "total":
                total = parse_reading(hass.states.get(settings[CONF_TOTAL]), now, max_age)
                phases = (total, 0.0, 0.0) if settings.get(CONF_SPLIT) == "phase_a" else (total / 3.0,) * 3
            else:
                phases = tuple(parse_reading(hass.states.get(settings[k]), now, max_age)
                               for k in (CONF_PHASE_A, CONF_PHASE_B, CONF_PHASE_C))
            if settings.get(CONF_INVERT, False):
                phases = tuple(-v for v in phases)
        except (ValueError, KeyError) as exc:
            # Zero the invalid values. Never re-use an old power reading.
            self._add_energy(time.monotonic())
            self.phases = (0.0, 0.0, 0.0)
            self.available = False
            self.error = str(exc)
            self._energy_sample = None
            return
        self._add_energy(time.monotonic())
        self.phases = tuple(float(v) for v in phases)
        self.last_update = time.monotonic()
        self.available = True
        self.error = None
        self._energy_sample = self.last_update

    def _add_energy(self, current: float) -> None:
        if self._energy_sample is None or not self.available:
            return
        elapsed = max(0.0, min(current - self._energy_sample, 60.0))
        for i, p in enumerate(self.phases):
            self.imports[i] += max(0.0, p) * elapsed / 3600.0
            self.exports[i] += max(0.0, -p) * elapsed / 3600.0
        self._energy_sample = current

    @property
    def total(self):
        return sum(self.phases)

    def em_status(self):
        result = {"id": 0, "n_current": None, "user_calibrated_phase": []}
        for letter, power in zip("abc", self.phases):
            current = abs(power) / 230.0
            result.update({
                f"{letter}_current": round(current, 3),
                f"{letter}_voltage": 230.0,
                f"{letter}_act_power": round(power, 3),
                f"{letter}_aprt_power": round(abs(power), 3),
                f"{letter}_pf": 1.0 if power else 0.0,
                f"{letter}_freq": 50.0,
            })
        if not self.available:
            result["errors"] = ["power_meter_failure"]
        result.update({
            "total_current": round(sum(abs(p) / 230 for p in self.phases), 3),
            "total_act_power": round(self.total, 3),
            "total_aprt_power": round(sum(abs(p) for p in self.phases), 3),
        })
        return result

    def em_udp(self):
        return {"id": 0, **{f"{letter}_act_power": round(p, 3) for letter,p in zip("abc",self.phases)},
                "total_act_power": round(self.total, 3)}

    def emdata_status(self):
        result = {"id": 0}
        for i,letter in enumerate("abc"):
            result[f"{letter}_total_act_energy"] = round(self.imports[i], 3)
            result[f"{letter}_total_act_ret_energy"] = round(self.exports[i], 3)
        result["total_act"] = round(sum(self.imports), 3)
        result["total_act_ret"] = round(sum(self.exports), 3)
        return result
