"""Backfill history from Internet Archive captures of the ZDR endpoint.

Captures are irregular (roughly weekly-to-monthly before Nov 2026). Every
backfilled event is bounded by the two capture times around it; that window
is part of the event, not an error to hide.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from .fetch import fetch_json, _maybe_gunzip
from .model import normalize

CDX = ("https://web.archive.org/cdx/search/cdx?url=openrouter.ai/api/v1/endpoints/zdr"
       "&output=json&fl=timestamp,statuscode,digest&filter=statuscode:200&collapse=digest")
WAYBACK_RAW = "https://web.archive.org/web/{ts}id_/https://openrouter.ai/api/v1/endpoints/zdr"


def list_captures(url=CDX):
    rows = fetch_json(url)
    header, data = rows[0], rows[1:]
    return [dict(zip(header, r)) for r in data]


def ts_to_iso(ts):
    return (ts[0:4] + "-" + ts[4:6] + "-" + ts[6:8] + "T" +
            ts[8:10] + ":" + ts[10:12] + ":" + ts[12:14] + "Z")


def fetch_capture(ts):
    return fetch_json(WAYBACK_RAW.format(ts=ts))["data"]


def backfill(limit=None, sleep=1.0, progress=None):
    """Yield (snapshot, entries) for each unique archived capture, oldest first."""
    caps = sorted(list_captures(), key=lambda c: c["timestamp"])
    if limit:
        caps = caps[-limit:]
    snaps = []
    for c in caps:
        ts = c["timestamp"]
        try:
            entries = fetch_capture(ts)
        except Exception as exc:  # keep going; a bad capture just widens a window
            if progress:
                progress(f"capture {ts} failed: {exc}")
            continue
        snap = normalize(entries, ts_to_iso(ts), source=f"wayback:{ts}")
        snaps.append(snap)
        if progress:
            progress(f"capture {ts}: {len(snap['routes'])} routes")
        time.sleep(sleep)
    return snaps
