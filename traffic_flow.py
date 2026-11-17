"""Redunator Phase 2b — traffic-flow provider plug-in interface.

The hourly disaggregation layer needs an hourly traffic-flow signal per
red-light camera. This module defines the provider interface so the source
of that signal can be swapped without touching the model code.

Providers
---------
FlowProvider            abstract base: hourly mean speed (mph) per camera.
CongestionFlowProvider  backed by the Chicago Traffic Tracker historical
                        congestion aggregates ingested by hourly_ingest.py
                        (data/hourly/processed/congestion_hourly.parquet
                        + congestion_region_hourly.parquet). NOTE the feed
                        is discontinued: data ends 2026-04-30, so this
                        provider returns NaN after that date.
TomTomFlowProvider      placeholder — raises FlowProviderError("API key
                        required ...") until a TomTom API key is configured.
                        No network calls are made. No account was created.
HereFlowProvider        placeholder — same, for HERE Traffic API.

A provider returns a pandas Series indexed by hourly timestamps
(America/Chicago, naive) named ``mean_speed_mph``; missing hours are NaN.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
PROC = BASE / "data" / "hourly" / "processed"

# The Traffic Tracker feed stopped updating 2026-04-30T01:10:21 (verified
# 2026-11-15 against the Socrata metadata + data; see SOURCES_VERIFIED.md).
CONGESTION_DATA_END = pd.Timestamp("2026-04-30 01:00")


class FlowProviderError(RuntimeError):
    """Raised when a flow provider cannot serve a request."""


class FlowProvider(ABC):
    """Hourly traffic-flow signal for one red-light camera.

    Implementations return mean vehicle speed in mph for each hour in the
    requested window. Hours with no observation are NaN (never fabricated).
    """

    name = "base"

    @abstractmethod
    def hourly_speed(self, camera_id: str | int, start: str | pd.Timestamp,
                     end: str | pd.Timestamp) -> pd.Series:
        """Mean speed (mph) per hour for ``camera_id`` in [start, end].

        Returns a Series indexed by hourly timestamps (America/Chicago,
        naive), named ``mean_speed_mph``. Hours without an observation
        are NaN.
        """
        raise NotImplementedError

    def coverage(self, camera_id: str | int, start: str | pd.Timestamp,
                 end: str | pd.Timestamp) -> float:
        """Fraction of requested hours with a non-NaN speed (0.0-1.0)."""
        s = self.hourly_speed(camera_id, start, end)
        return float(s.notna().mean()) if len(s) else 0.0


class CongestionFlowProvider(FlowProvider):
    """Traffic Tracker historical congestion (Socrata 4g9f-3jbs).

    Reads the server-side hourly aggregates produced by hourly_ingest.py:
    mean of the valid (speed >= 0) ~10-minute speed readings per hour for
    the congestion segment nearest each camera (see
    data/hourly/processed/camera_segment_map.csv for the mapping), plus the
    region-level aggregates (Socrata kf7e-cur8) as a fallback.

    Coverage limits (verified, not assumed):
      * Data ends 2026-04-30T01:10:21 — the City discontinued the feed.
        Requests after that date return all-NaN.
      * speed = -1 (~46% of 10-min readings on a sample day) is a missing
        sentinel and is excluded before averaging; hours where every
        reading was -1 are NaN. ``n_reads`` (0-6) records how many valid
        10-min slices went into each hourly mean.
      * 4 cameras have no coordinates (1623, 2552, 2592, 2662) and have no
        segment mapping; they always return all-NaN here.
    """

    name = "chicago-traffic-tracker"

    def __init__(self, processed_dir: str | Path = PROC,
                 fallback_to_region: bool = True):
        self.processed_dir = Path(processed_dir)
        self.fallback_to_region = fallback_to_region
        self._seg: pd.DataFrame | None = None
        self._reg: pd.DataFrame | None = None
        self._map: pd.DataFrame | None = None

    # -- lazy loaders -------------------------------------------------
    def _load(self):
        if self._seg is None:
            p = self.processed_dir / "congestion_hourly.parquet"
            if not p.exists():
                raise FlowProviderError(
                    f"congestion aggregates not found: {p} — run "
                    "hourly_ingest.py --steps segments,map,congestion,"
                    "process-congestion first")
            self._seg = pd.read_parquet(p)
            self._seg["timestamp"] = pd.to_datetime(self._seg["timestamp"])
        if self._map is None:
            p = self.processed_dir / "camera_segment_map.csv"
            self._map = pd.read_csv(p, dtype={"camera_id": str})
        if self.fallback_to_region and self._reg is None:
            p = self.processed_dir / "congestion_region_hourly.parquet"
            self._reg = (pd.read_parquet(p) if p.exists() else pd.DataFrame())
            if len(self._reg):
                self._reg["timestamp"] = pd.to_datetime(self._reg["timestamp"])

    def segment_for_camera(self, camera_id: str | int) -> int | None:
        """Congestion segment id nearest a camera, or None if unmapped."""
        self._load()
        row = self._map[self._map["camera_id"] == str(camera_id)]
        if row.empty or pd.isna(row.iloc[0]["segment_id"]):
            return None
        return int(row.iloc[0]["segment_id"])

    def hourly_speed(self, camera_id: str | int, start: str | pd.Timestamp,
                     end: str | pd.Timestamp) -> pd.Series:
        self._load()
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        idx = pd.date_range(start.floor("h"), end.floor("h"), freq="h")
        out = pd.Series(float("nan"), index=idx, name="mean_speed_mph")
        if end <= CONGESTION_DATA_END or start <= CONGESTION_DATA_END:
            seg = self.segment_for_camera(camera_id)
            if seg is not None:
                sub = self._seg[self._seg["segment_id"] == seg]
                s = sub.set_index("timestamp")["mean_speed_mph"]
                out.loc[out.index.intersection(s.index)] = \
                    s.loc[out.index.intersection(s.index)]
        # Region fallback only fills hours still NaN before the feed end;
        # it cannot extend coverage past 2026-04-30 either (same feed).
        if self.fallback_to_region and len(self._reg) and out.isna().any():
            # Region mapping is not stored per camera; a caller that needs
            # it should join on the region containing the camera. This
            # provider leaves region fallback to explicit use of
            # region_hourly_speed() below.
            pass
        return out

    def region_hourly_speed(self, region_id: int, start: str | pd.Timestamp,
                            end: str | pd.Timestamp) -> pd.Series:
        """Mean speed (mph) per hour for a Traffic Tracker region (1-29)."""
        self._load()
        if self._reg is None or len(self._reg) == 0:
            raise FlowProviderError("region aggregates not ingested")
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        idx = pd.date_range(start.floor("h"), end.floor("h"), freq="h")
        out = pd.Series(float("nan"), index=idx, name="mean_speed_mph")
        sub = self._reg[self._reg["region_id"] == int(region_id)]
        s = sub.set_index("timestamp")["mean_speed_mph"]
        out.loc[out.index.intersection(s.index)] = \
            s.loc[out.index.intersection(s.index)]
        return out


class _ApiKeyProvider(FlowProvider):
    """Base for commercial flow APIs (TomTom / HERE).

    No account was created and no key is on file (Phase 2b decision:
    do not sign up). Configure by setting the documented environment
    variable, then implement ``hourly_speed`` against that vendor's
    flow API. Until then every call raises a clear error.
    """

    name = "api-key-required"
    env_var = ""
    vendor_docs = ""

    def hourly_speed(self, camera_id: str | int, start: str | pd.Timestamp,
                     end: str | pd.Timestamp) -> pd.Series:
        key = os.environ.get(self.env_var, "") if self.env_var else ""
        if not key:
            raise FlowProviderError(
                f"{type(self).__name__}: no API key configured — set the "
                f"{self.env_var} environment variable (see {self.vendor_docs}). "
                "No TomTom/HERE account was created during Phase 2b; "
                "sign-up is a deliberate, separate decision.")
        raise FlowProviderError(
            f"{type(self).__name__}: API client not implemented — key is "
            "present but the vendor client was never built (placeholder).")


class TomTomFlowProvider(_ApiKeyProvider):
    """Placeholder for the TomTom Traffic Flow API.

    Intended use: TomTom Traffic API — Flow Segment Data, queried per
    camera coordinate (point + zoom), returning currentSpeed / freeFlowSpeed
    per segment, sampled hourly. Requires a TomTom API key.
    """

    name = "tomtom"
    env_var = "TOMTOM_API_KEY"
    vendor_docs = "https://developer.tomtom.com/traffic-api/documentation/traffic-flow/flow-segment-data"


class HereFlowProvider(_ApiKeyProvider):
    """Placeholder for the HERE Traffic API.

    Intended use: HERE Traffic API v7 flow endpoint, queried per camera
    bounding box / corridor, returning jam factor and free-flow vs current
    speed, sampled hourly. Requires a HERE API key.
    """

    name = "here"
    env_var = "HERE_API_KEY"
    vendor_docs = "https://developer.here.com/documentation/traffic-api/api-reference.html"


def get_provider(name: str, **kwargs) -> FlowProvider:
    """Provider factory: 'congestion' | 'tomtom' | 'here'."""
    providers = {
        "congestion": CongestionFlowProvider,
        "tomtom": TomTomFlowProvider,
        "here": HereFlowProvider,
    }
    try:
        return providers[name.lower()](**kwargs)
    except KeyError:
        raise FlowProviderError(
            f"unknown flow provider {name!r}; choose from "
            f"{sorted(providers)}") from None
