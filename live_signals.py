#!/usr/bin/env python3
"""Redunator Phase 2 — LIVE SIGNAL PROVIDER module (Worker B).

Real-time (or near-real-time) signals that the hourly prediction layer
can consult at request time, on top of the shipped Phase 2 daily model
and the Phase 2b/2c/2d hourly disaggregation. Nothing here touches the
daily model files (``redunator_brain_v1.*``), ``hourly_model.joblib``,
``hourly_risk.py``, or ``hourly_model.py``.

Common provider interface
--------------------------
Every provider returns a :class:`LiveSignal` with the same shape::

    source           provider name, e.g. "open-meteo-current"
    fetched_at       timezone-aware UTC timestamp of the fetch
    as_of            timezone-aware timestamp the data describes
                     (UTC; for forecasts this is the first forecast hour)
    values           dict of the signal, e.g.
                     {"temperature_c": -0.4, "precipitation_mm": 0.0, ...}
                     or {"avg_speed_mph": 22.5, ...}
    stale            bool, computed against that source's max-age rule
    error            None, or a string describing the failure
    max_age_seconds  the max-age threshold this result was judged against

Every provider carries a fetched timestamp — no signal without one.
A provider that cannot serve (no key, network failure, bad payload)
returns a ``LiveSignal`` with ``error`` set (or raises
:class:`LiveSignalError` on a direct ``fetch()`` call); see
:func:`collect_live_signals`, which never raises on a single provider
failure.

Providers
---------
No key required (verified live 2026-11-16 ~05:20 CST):
  * ``open-meteo-current`` — Open-Meteo forecast API, current weather
    for Chicago (41.8781, -87.6298): temperature, precipitation,
    wind speed, WMO weather code. Max age 1 hour.
  * ``open-meteo-forecast`` — same API, next-24h hourly forecast:
    temperature, precipitation probability, precipitation amount.
    Max age 6 hours (a forecast is a forecast — its age is measured
    from fetch time, and its ``as_of`` must be the current hour or
    in the future).
  * ``divvy-gbfs`` — Divvy public GBFS feed (no key):
    ``station_status.json`` + ``station_information.json``.
    Citywide bike-availability level: bikes available / total dock
    capacity, stations reporting fresh, median report age.
    Max age 15 minutes (newest ``last_reported`` older than that,
    or fewer than half the stations fresh, = stale).
    HONEST LIMIT: this is a WEAK contextual activity signal, not a
    traffic-flow signal. Bike-share availability rises/falls with
    weather, time of day and season; it says nothing direct about
    car speeds or red-light violations. It is included because the
    feed is live, fresh (median report age ~2-3 min at verification)
    and the metric is a real citywide level — but any consumer must
    treat it as context only, and it must never be fed to the model
    as a congestion proxy without its own validation.

Key-ready plug-ins (no keys, no signups — verified key-gated live
2026-11-16; each raises ``LiveSignalError("API key required: ...")``
with the signup URL when its env var is absent):
  * ``tomtom-flow`` — TomTom Traffic Flow API (flowSegmentData).
    Env var ``TOMTOM_API_KEY``. Max age 10 minutes.
  * ``here-flow`` — HERE Traffic API v7 (flow). Env var ``HERE_API_KEY``.
    Max age 10 minutes.
  * ``cta-bus`` — CTA Bus Tracker API v2 (getvehicles). Env var
    ``CTA_BUS_API_KEY``. Max age 5 minutes.
  If a key IS present in the environment, the provider attempts the
  real call. Keys come from environment variables only — never from
  a file, never hardcoded.

Max-age rules (summary — see each provider class for detail)
------------------------------------------------------------
  open-meteo-current   1 hour
  open-meteo-forecast  6 hours (from fetch; as_of must be current/future hour)
  divvy-gbfs           15 minutes (newest last_reported; + fresh-coverage guard)
  tomtom-flow          10 minutes
  here-flow            10 minutes
  cta-bus              5 minutes

Caching: in-memory TTL only (default 15 min, shared by the two
Open-Meteo providers and Divvy). Nothing is written to disk.

Conventions (matching ``hourly_risk.py`` / ``hourly_model.py``):
all timestamps timezone-aware; Chicago local time is
America/Chicago; ``fetched_at``/``as_of`` are stored as UTC-aware
datetimes. Only stdlib + ``requests`` are used.

Relationship to ``traffic_flow.py``: that module's ``FlowProvider``
interface serves *historical hourly mean speed per camera* and its
``TomTomFlowProvider`` / ``HereFlowProvider`` placeholders are the
batch-side counterparts of the ``tomtom-flow`` / ``here-flow``
providers here. Same env vars (``TOMTOM_API_KEY``,
``HERE_API_KEY``), same vendor docs, same "no account was created"
stance — this module adds the live-signal result shape and the
actual HTTP clients for when a key exists.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

CHICAGO_TZ = ZoneInfo("America/Chicago")
UTC = timezone.utc

# Chicago loop coordinates, same as hourly_ingest.py.
CHICAGO_LAT = 41.8781
CHICAGO_LON = -87.6298

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "redunator-phase2-live-signals/1.0"})

# In-memory TTL cache only — nothing is written to disk. Keyed by
# (provider name, params tuple); value is (fetched_epoch, LiveSignal).
_CACHE: dict = {}
DEFAULT_CACHE_TTL_S = 15 * 60  # 15 minutes


class LiveSignalError(RuntimeError):
    """Raised when a live-signal provider cannot serve a request."""


@dataclass
class LiveSignal:
    """One provider's result, in the common shape.

    ``fetched_at`` and ``as_of`` are timezone-aware (UTC). ``stale``
    is computed at fetch time against ``max_age_seconds`` and can be
    recomputed later with :func:`is_stale` (time keeps passing after
    the fetch). ``error`` is None on success.
    """

    source: str
    fetched_at: datetime
    as_of: datetime
    values: dict = field(default_factory=dict)
    stale: bool = False
    error: str | None = None
    max_age_seconds: int = 0

    def to_dict(self) -> dict:
        """JSON-friendly dict (timestamps as ISO strings)."""
        return {
            "source": self.source,
            "fetched_at": self.fetched_at.isoformat(),
            "as_of": self.as_of.isoformat(),
            "values": self.values,
            "stale": self.stale,
            "error": self.error,
            "max_age_seconds": self.max_age_seconds,
        }


def is_stale(result: LiveSignal, now: datetime | None = None) -> bool:
    """Recompute staleness for a provider result at ``now`` (default: now).

    A result is stale if it carries an error, if it is older than its
    own ``max_age_seconds`` (measured from ``fetched_at``), or if the
    data it describes (``as_of``) is itself older than
    ``max_age_seconds``. Forecast results are additionally stale when
    their ``as_of`` (first forecast hour) is before the current hour —
    that check is applied at fetch time by the forecast provider and
    recorded in ``stale``; ``is_stale`` re-applies the two age rules
    here, which subsume it for any forecast fetched more than an hour
    ago, and callers should re-fetch rather than reuse a forecast
    whose first hour has passed.
    """
    if result.error is not None:
        return True
    now = now or datetime.now(UTC)
    if result.max_age_seconds <= 0:
        return result.stale
    if (now - result.fetched_at).total_seconds() > result.max_age_seconds:
        return True
    if (now - result.as_of).total_seconds() > result.max_age_seconds:
        return True
    return False


class LiveSignalProvider:
    """Base class for live-signal providers.

    Subclasses set ``name`` and ``max_age`` (a timedelta) and implement
    ``_fetch()``. ``fetch()`` wraps it: on success it stamps
    ``fetched_at``/``stale``; on failure it raises ``LiveSignalError``
    (direct calls) — see :func:`collect_live_signals` for the
    never-raise aggregation path.
    """

    name = "base"
    max_age = timedelta(hours=1)
    cache_ttl_s = DEFAULT_CACHE_TTL_S  # 0 disables the in-memory cache

    def _fetch(self, now: datetime) -> LiveSignal:
        raise NotImplementedError

    def fetch(self) -> LiveSignal:
        """Fetch once, with the in-memory TTL cache applied if enabled."""
        now = datetime.now(UTC)
        if self.cache_ttl_s > 0:
            key = (self.name,)
            hit = _CACHE.get(key)
            if hit is not None and (time.time() - hit[0]) < self.cache_ttl_s:
                cached = hit[1]
                # Recompute staleness at call time — time has passed.
                # OR, never clear a stale flag the provider set.
                cached.stale = cached.stale or is_stale(cached, now)
                return cached
        try:
            result = self._fetch(now)
        except LiveSignalError:
            raise
        except Exception as e:  # noqa: BLE001 - surfaced as LiveSignalError
            raise LiveSignalError(
                f"{self.name}: fetch failed: {type(e).__name__}: {e}") from e
        result.max_age_seconds = int(self.max_age.total_seconds())
        # OR, never clear a stale flag the provider set (e.g. the
        # forecast provider's first-hour-in-the-past rule).
        result.stale = result.stale or is_stale(result, now)
        if self.cache_ttl_s > 0:
            _CACHE[(self.name,)] = (time.time(), result)
        return result

    @staticmethod
    def _get(url: str, params: dict | None = None,
             timeout: int = 30) -> dict:
        r = SESSION.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()


# ------------------------------------------------------------------
# No-key providers
# ------------------------------------------------------------------

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_SIGNUP_NOTE = ("Open-Meteo is free with no API key "
                          "(https://open-meteo.com/); no account exists.")


class OpenMeteoCurrentProvider(LiveSignalProvider):
    """Current weather for Chicago from the Open-Meteo forecast API.

    Free, no key (https://open-meteo.com/). ``current`` block:
    temperature_2m (°C), precipitation (mm), wind_speed_10m (km/h),
    weather_code (WMO). Max age: 1 hour.
    """

    name = "open-meteo-current"
    max_age = timedelta(hours=1)

    def _fetch(self, now: datetime) -> LiveSignal:
        data = self._get(OPEN_METEO_URL, params={
            "latitude": CHICAGO_LAT, "longitude": CHICAGO_LON,
            "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
            "timezone": "America/Chicago",
        })
        cur = data.get("current") or {}
        if "time" not in cur:
            raise LiveSignalError(
                f"{self.name}: unexpected payload (no 'current.time'): "
                f"{str(data)[:200]}")
        as_of = datetime.fromisoformat(cur["time"]).replace(tzinfo=CHICAGO_TZ)
        return LiveSignal(
            source=self.name, fetched_at=now,
            as_of=as_of.astimezone(UTC),
            values={
                "temperature_c": cur.get("temperature_2m"),
                "precipitation_mm": cur.get("precipitation"),
                "wind_speed_kmh": cur.get("wind_speed_10m"),
                "weather_code": cur.get("weather_code"),
            },
        )


class OpenMeteoForecastProvider(LiveSignalProvider):
    """Next-24h hourly forecast for Chicago from Open-Meteo.

    Free, no key. Hourly temperature_2m (°C),
    precipitation_probability (%), precipitation (mm), timezone
    America/Chicago. Max age: 6 hours, measured from fetch time;
    the forecast is additionally stale when its first hour
    (``as_of``) is before the current hour. ``values`` holds parallel
    lists: ``hours`` (ISO strings, Chicago offset), ``temperature_c``,
    ``precipitation_probability_pct``, ``precipitation_mm``.
    """

    name = "open-meteo-forecast"
    max_age = timedelta(hours=6)
    forecast_hours = 24

    def _fetch(self, now: datetime) -> LiveSignal:
        data = self._get(OPEN_METEO_URL, params={
            "latitude": CHICAGO_LAT, "longitude": CHICAGO_LON,
            "hourly": "temperature_2m,precipitation_probability,precipitation",
            "forecast_days": 2,
            "timezone": "America/Chicago",
        })
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            raise LiveSignalError(
                f"{self.name}: unexpected payload (no 'hourly.time'): "
                f"{str(data)[:200]}")
        now_chicago = now.astimezone(CHICAGO_TZ)
        current_hour = now_chicago.replace(minute=0, second=0, microsecond=0)
        # Keep the next 24 hours starting at the current hour.
        start_idx = 0
        for i, t in enumerate(times):
            tt = datetime.fromisoformat(t).replace(tzinfo=CHICAGO_TZ)
            if tt >= current_hour:
                start_idx = i
                break
        else:
            start_idx = len(times)
        end_idx = min(start_idx + self.forecast_hours, len(times))
        if start_idx >= len(times):
            raise LiveSignalError(
                f"{self.name}: no forecast hours at/after the current hour "
                f"(payload starts {times[0] if times else 'empty'})")
        # as_of is the first hour actually returned (the current hour),
        # not the first hour in the payload (which starts at midnight).
        as_of = datetime.fromisoformat(
            times[start_idx]).replace(tzinfo=CHICAGO_TZ)
        temps = hourly.get("temperature_2m") or []
        probs = hourly.get("precipitation_probability") or []
        precips = hourly.get("precipitation") or []
        result = LiveSignal(
            source=self.name, fetched_at=now,
            as_of=as_of.astimezone(UTC),
            values={
                "hours": times[start_idx:end_idx],
                "temperature_c": temps[start_idx:end_idx],
                "precipitation_probability_pct": probs[start_idx:end_idx],
                "precipitation_mm": precips[start_idx:end_idx],
            },
        )
        # Forecast-specific staleness: first returned hour in the past.
        if as_of < current_hour:
            result.stale = True
        return result


DIVVY_STATUS_URL = "https://gbfs.divvybikes.com/gbfs/en/station_status.json"
DIVVY_INFO_URL = "https://gbfs.divvybikes.com/gbfs/en/station_information.json"


class DivvyProvider(LiveSignalProvider):
    """Divvy bike-share citywide availability from the public GBFS feed.

    Free, no key (https://gbfs.divvybikes.com/gbfs/gbfs.json). Reads
    ``station_status.json`` (bikes/docks available per station,
    ``last_reported`` epoch) and ``station_information.json``
    (per-station ``capacity``). Values: station counts, bikes/docks
    available, total capacity, ``bike_occupancy`` =
    bikes_available / total capacity, ``stations_fresh`` (last_reported
    within 15 min), ``median_report_age_s``.

    Max age: 15 minutes — stale when the newest ``last_reported`` is
    older than 15 minutes, or when fewer than half the stations are
    fresh (fresh-coverage guard: a single live station must not bless
    a dead feed).

    WHAT IT MEASURES: a citywide bike-share availability LEVEL at one
    moment — how much of the dock network currently holds a bike.
    It is a weak contextual activity signal (weather / time-of-day /
    season driven). It is NOT a traffic-flow signal and must not be
    used as a congestion proxy without its own validation.
    """

    name = "divvy-gbfs"
    max_age = timedelta(minutes=15)
    min_fresh_fraction = 0.5

    def _fetch(self, now: datetime) -> LiveSignal:
        status = self._get(DIVVY_STATUS_URL)
        info = self._get(DIVVY_INFO_URL)
        stations = (status.get("data") or {}).get("stations") or []
        infos = (info.get("data") or {}).get("stations") or []
        if not stations or not infos:
            raise LiveSignalError(
                f"{self.name}: empty GBFS payload "
                f"(status stations={len(stations)}, "
                f"info stations={len(infos)})")
        capacity = {s.get("station_id"): s.get("capacity") or 0
                    for s in infos}
        total_capacity = sum(capacity.values())
        now_epoch = now.timestamp()
        ages = []
        fresh = 0
        bikes_available = bikes_disabled = docks_available = 0
        newest_reported = 0.0
        for s in stations:
            lr = s.get("last_reported")
            if lr is not None:
                ages.append(now_epoch - float(lr))
                newest_reported = max(newest_reported, float(lr))
                if now_epoch - float(lr) <= self.max_age.total_seconds():
                    fresh += 1
            bikes_available += s.get("num_bikes_available") or 0
            bikes_disabled += s.get("num_bikes_disabled") or 0
            docks_available += s.get("num_docks_available") or 0
        ages.sort()
        median_age = ages[len(ages) // 2] if ages else None
        as_of = (datetime.fromtimestamp(newest_reported, tz=UTC)
                 if newest_reported else now)
        result = LiveSignal(
            source=self.name, fetched_at=now, as_of=as_of,
            values={
                "stations_total": len(stations),
                "stations_fresh": fresh,
                "stations_fresh_fraction": (fresh / len(stations)
                                           if stations else 0.0),
                "bikes_available": bikes_available,
                "bikes_disabled": bikes_disabled,
                "docks_available": docks_available,
                "capacity_total": total_capacity,
                "bike_occupancy": (bikes_available / total_capacity
                                   if total_capacity else None),
                "median_report_age_s": median_age,
            },
        )
        # Fresh-coverage guard: a mostly-dead feed is stale even if one
        # station reported recently.
        if (not ages or (now_epoch - newest_reported)
                > self.max_age.total_seconds()
                or fresh / len(stations) < self.min_fresh_fraction):
            result.stale = True
        return result


# ------------------------------------------------------------------
# Key-ready plug-ins (no keys, no signups)
# ------------------------------------------------------------------

class _ApiKeyProvider(LiveSignalProvider):
    """Base for key-gated live providers.

    The key comes from an environment variable only — never from a
    file, never hardcoded. With no key, ``fetch()`` raises
    ``LiveSignalError("API key required: ...")`` naming the env var,
    the signup URL, and the free tier. With a key present, the real
    vendor call is attempted.
    """

    env_var = ""
    signup_url = ""
    free_tier_note = ""

    def _require_key(self) -> str:
        key = os.environ.get(self.env_var, "") if self.env_var else ""
        if not key:
            raise LiveSignalError(
                f"{self.name}: API key required: set the {self.env_var} "
                f"environment variable. Sign up at {self.signup_url} "
                f"({self.free_tier_note}). No account was created and no "
                "key is on file.")
        return key

    @staticmethod
    def _kmh_to_mph(kmh: float | None) -> float | None:
        return round(kmh * 0.621371, 2) if kmh is not None else None


def _tomtom_connector_surrogate() -> str:
    """Fresh TomTom key surrogate from the custom.tomtom connector.

    Used only when TOMTOM_API_KEY is not set. The surrogate (``hsurr:``)
    is swapped for the real key by the egress proxy on the approved
    request to api.tomtom.com, so the real key never appears here. The
    destination is fixed to api.tomtom.com by TomTomFlowProvider._fetch.
    Raises LiveSignalError when the connector is unavailable (for
    example off this VM, where the Render deployment runs): the caller
    then reports the env-var route instead.
    """
    skill_bin = "/opt/muse/skills/custom-connector-setup/bin"
    if skill_bin not in sys.path:
        sys.path.insert(0, skill_bin)
    try:
        from dynamic_credentials import dynamic_credential_entry
    except ImportError as e:
        raise LiveSignalError(
            "tomtom-flow: custom.tomtom connector unavailable "
            f"({e}); set TOMTOM_API_KEY instead."
        ) from e
    try:
        entry = dynamic_credential_entry("custom.tomtom")
    except Exception as e:  # noqa: BLE001 - surfaced as LiveSignalError
        raise LiveSignalError(
            "tomtom-flow: custom.tomtom connector unavailable "
            f"({e}); set TOMTOM_API_KEY instead."
        ) from e
    surrogate = str(entry.get("surrogate", "")).strip()
    if not surrogate.startswith("hsurr:"):
        raise LiveSignalError(
            "tomtom-flow: custom.tomtom connector returned no surrogate; "
            "set TOMTOM_API_KEY instead."
        )
    return surrogate


class TomTomFlowProvider(_ApiKeyProvider):
    """TomTom Traffic Flow API — live segment speeds near a point.

    Sign up: https://developer.tomtom.com/ (create a TomTom Developer
    account, then an API key). Free tier: 2,500 transactions/day on
    the free plan (verify current terms at signup — TomTom's free
    allowance has changed before). What the key unlocks: Traffic Flow
    API (flowSegmentData / flowSegmentData absolute), i.e. live
    currentSpeed / freeFlowSpeed per road segment — the real-time
    replacement for the dead Chicago Traffic Tracker congestion feed
    (see traffic_flow.py). Endpoint used here:
    ``https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json``
    with ``point=<lat>,<lon>&key=<key>``; speeds come back in km/h and
    are converted to mph. Max age: 10 minutes.

    Key-gating verified live 2026-11-16 (no key): HTTP 401
    ``{"detailedError": {"code": "Unauthorized", "message": "You are
    missing valid authentication credentials"}}``.

    Key source: TOMTOM_API_KEY first. When it is not set, this
    provider falls back to the custom.tomtom connector
    (``_tomtom_connector_surrogate``): a fresh ``hsurr:`` surrogate
    per fetch, swapped for the real key by the egress proxy. The
    connector holds the "Redunator" key (created 2026-11-16,
    Evaluation plan, 2,500 transactions/day). Off this VM the
    connector is unavailable and the provider reports the env-var
    route, so Render still needs TOMTOM_API_KEY set as an env var.
    """

    name = "tomtom-flow"
    max_age = timedelta(minutes=10)
    env_var = "TOMTOM_API_KEY"
    signup_url = "https://developer.tomtom.com/"
    free_tier_note = "free tier 2,500 transactions/day (verify at signup)"

    def _require_key(self) -> str:
        key = os.environ.get(self.env_var, "") if self.env_var else ""
        if key:
            return key
        return _tomtom_connector_surrogate()

    def _fetch(self, now: datetime) -> LiveSignal:
        key = self._require_key()
        data = self._get(
            "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json",
            params={"point": f"{CHICAGO_LAT},{CHICAGO_LON}", "key": key})
        seg = data.get("flowSegmentData") or {}
        if "currentSpeed" not in seg:
            raise LiveSignalError(
                f"{self.name}: unexpected payload: {str(data)[:200]}")
        return LiveSignal(
            source=self.name, fetched_at=now, as_of=now,
            values={
                "current_speed_mph": self._kmh_to_mph(seg.get("currentSpeed")),
                "free_flow_speed_mph": self._kmh_to_mph(seg.get("freeFlowSpeed")),
                "current_travel_time_s": seg.get("currentTravelTime"),
                "free_flow_travel_time_s": seg.get("freeFlowTravelTime"),
                "confidence": seg.get("confidence"),
                "road_closure": seg.get("roadClosure"),
            },
        )


class HereFlowProvider(_ApiKeyProvider):
    """HERE Traffic API v7 — live flow (speed / jam factor) near a point.

    Sign up: https://developer.here.com/ (create a HERE platform
    account, create a project, generate an API key). Free tier: Base
    plan, 30,000 transactions/month free (verify current terms at
    signup). What the key unlocks: Traffic API v7 flow endpoint —
    live speed, free-flow speed and jam factor for road segments in
    an area — the real-time replacement for the dead Chicago Traffic
    Tracker congestion feed (see traffic_flow.py). Endpoint used here:
    ``https://data.traffic.hereapi.com/v7/flow?in=circle:<lat>,<lon>;r=1000``
    with ``apiKey=<key>``; speeds come back in km/h and are converted
    to mph. Max age: 10 minutes.

    Key-gating verified live 2026-11-16 (no key): HTTP 401
    ``{"error": "Unauthorized", "error_description": "Token or apiKey
    is missing."}``. (The older ``traffic.ls.hereapi.com`` 6.3
    endpoint is retired — it returns an empty reply.)
    """

    name = "here-flow"
    max_age = timedelta(minutes=10)
    env_var = "HERE_API_KEY"
    signup_url = "https://developer.here.com/"
    free_tier_note = "Base plan, 30,000 transactions/month free (verify at signup)"

    def _fetch(self, now: datetime) -> LiveSignal:
        key = self._require_key()
        data = self._get(
            "https://data.traffic.hereapi.com/v7/flow",
            params={
                "in": f"circle:{CHICAGO_LAT},{CHICAGO_LON};r=1000",
                "locationReferencing": "shape",
                "apiKey": key,
            })
        results = data.get("results") or []
        if not results:
            raise LiveSignalError(
                f"{self.name}: unexpected payload (no 'results'): "
                f"{str(data)[:200]}")
        flows = [r.get("currentFlow") or {} for r in results]
        speeds = [f["speed"] for f in flows if f.get("speed") is not None]
        free = [f["freeFlow"] for f in flows if f.get("freeFlow") is not None]
        jams = [f["jamFactor"] for f in flows
                if f.get("jamFactor") is not None]
        return LiveSignal(
            source=self.name, fetched_at=now, as_of=now,
            values={
                "segments": len(results),
                "avg_speed_mph": self._kmh_to_mph(
                    sum(speeds) / len(speeds)) if speeds else None,
                "avg_free_flow_speed_mph": self._kmh_to_mph(
                    sum(free) / len(free)) if free else None,
                "avg_jam_factor": (round(sum(jams) / len(jams), 3)
                                   if jams else None),
            },
        )


class CtaBusProvider(_ApiKeyProvider):
    """CTA Bus Tracker API v2 — live bus positions / speeds.

    Sign up: https://www.ctabustracker.com/bustime/login.jsp — request
    API access there ("Request API Access"); the key is issued by CTA
    for the BusTime API. Free; no published quota (verify at signup).
    What the key unlocks: ``getvehicles`` (live positions + speed for
    every active bus), ``getpredictions`` / ``gettime`` — a live
    transit-activity signal and a rough congestion proxy (bus speeds
    fall when streets jam). Endpoint used here:
    ``http://www.ctabustracker.com/bustime/api/v2/getvehicles``
    with ``key=<key>&format=json`` (no route filter: the full active
    fleet; callers that need one corridor should pass ``rt``).
    ``spd`` is already mph in the BusTime API. Max age: 5 minutes.

    Key-gating verified live 2026-11-16 (no key): HTTP 200 with
    ``{"bustime-response": {"error": [{"msg": "Invalid API access key
    supplied"}]}}``.
    """

    name = "cta-bus"
    max_age = timedelta(minutes=5)
    env_var = "CTA_BUS_API_KEY"
    signup_url = "https://www.ctabustracker.com/bustime/login.jsp"
    free_tier_note = "free; request API access on the BusTime site (no published quota)"

    def _fetch(self, now: datetime) -> LiveSignal:
        key = self._require_key()
        data = self._get(
            "http://www.ctabustracker.com/bustime/api/v2/getvehicles",
            params={"key": key, "format": "json"})
        resp = data.get("bustime-response") or {}
        if "error" in resp:
            msgs = "; ".join(e.get("msg", "") for e in resp["error"])
            raise LiveSignalError(f"{self.name}: CTA API error: {msgs}")
        vehicles = resp.get("vehicle") or []
        speeds = [float(v["spd"]) for v in vehicles
                  if v.get("spd") not in (None, "")]
        return LiveSignal(
            source=self.name, fetched_at=now, as_of=now,
            values={
                "vehicles_active": len(vehicles),
                "vehicles_with_speed": len(speeds),
                "avg_speed_mph": (round(sum(speeds) / len(speeds), 2)
                                  if speeds else None),
            },
        )


# ------------------------------------------------------------------
# Registry + aggregation
# ------------------------------------------------------------------

PROVIDERS: dict[str, type[LiveSignalProvider]] = {
    "open-meteo-current": OpenMeteoCurrentProvider,
    "open-meteo-forecast": OpenMeteoForecastProvider,
    "divvy-gbfs": DivvyProvider,
    "tomtom-flow": TomTomFlowProvider,
    "here-flow": HereFlowProvider,
    "cta-bus": CtaBusProvider,
}

#: Max-age rules, in one place (also on each provider class as
#: ``max_age``). Seconds.
MAX_AGES = {name: int(cls.max_age.total_seconds())
            for name, cls in PROVIDERS.items()}


def get_provider(name: str) -> LiveSignalProvider:
    """Provider instance by name (see ``PROVIDERS``)."""
    try:
        return PROVIDERS[name.lower()]()
    except KeyError:
        raise LiveSignalError(
            f"unknown live-signal provider {name!r}; choose from "
            f"{sorted(PROVIDERS)}") from None


def collect_live_signals(names: list[str] | None = None) -> dict[str, LiveSignal]:
    """Fetch all configured providers; never raises on one failure.

    ``names`` defaults to every registered provider (no-key first,
    then key-ready). Each result carries its ``stale`` flag; a failed
    provider's result has ``error`` set, empty ``values``, and
    ``stale=True``. Returns ``{source: LiveSignal}``.
    """
    selected = names if names is not None else list(PROVIDERS)
    out: dict[str, LiveSignal] = {}
    for name in selected:
        provider = get_provider(name)
        now = datetime.now(UTC)
        try:
            out[provider.name] = provider.fetch()
        except Exception as e:  # noqa: BLE001 - recorded, never raised
            out[provider.name] = LiveSignal(
                source=provider.name, fetched_at=now, as_of=now,
                values={}, stale=True, error=str(e),
                max_age_seconds=int(provider.max_age.total_seconds()))
    return out


if __name__ == "__main__":
    import json

    results = collect_live_signals()
    print(json.dumps({k: v.to_dict() for k, v in results.items()}, indent=2))
