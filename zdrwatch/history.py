"""Event history store: append-only JSONL plus derived views."""
from __future__ import annotations

import json
import os

from .model import route_key_for


def canonicalize_event(e):
    """Return the event with its route_key canonicalized (dated suffix stripped).

    Early history was recorded before dated variants (``-YYYYMMDD``) were merged
    under one route key, so ~1% of events carry the dated form. The raw JSONL
    stays untouched; readers see one merged timeline per route. Idempotent.
    """
    canon = route_key_for(e["route_key"])
    if canon == e["route_key"]:
        return e
    e = dict(e)
    e["route_key"] = canon
    if e.get("model_base"):
        e["model_base"] = canon.partition(" | ")[2]
    return e


def load_events(path):
    if not os.path.exists(path):
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(canonicalize_event(json.loads(line)))
    return events


def append_events(path, events):
    if not events:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, sort_keys=True) + "\n")


def dedupe_events(events):
    """Drop exact duplicates (same type, route, window and detail)."""
    seen, out = set(), []
    for e in events:
        sig = json.dumps(e, sort_keys=True)
        if sig not in seen:
            seen.add(sig)
            out.append(e)
    return out


def events_chronological(events):
    return sorted(events, key=lambda e: (e["between"][1], e["type"], e["route_key"]))
