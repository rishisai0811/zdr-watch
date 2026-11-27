"""Archived OpenRouter model-catalog captures (Wayback), used as EVIDENCE for
historical removals - never as a cause overwrite. A catalog capture can be
incomplete (pagination, schema change, transient origin errors) and the nearest
capture may lie far outside an event window, so this module records per-capture
integrity metadata (sha256, byte size, model count) and attaches bracketing
observations to events without changing their classification.

Provider-level causes (eligibility lost vs endpoint retired) are not decidable
historically at all: no per-model endpoint captures exist.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.request

from .backfill import ts_to_iso
from .model import DATE_SUFFIX

CDX_MODELS = ("https://web.archive.org/cdx/search/cdx?url=openrouter.ai/api/v1/models"
              "&output=text&fl=timestamp,statuscode&filter=statuscode:200&collapse=timestamp:8")
RAW = "https://web.archive.org/web/{ts}id_/https://openrouter.ai/api/v1/models"


class WaybackChallenge(Exception):
    """HTTP 403/429 or other block signal from Wayback: never retried."""


def list_model_captures(url=CDX_MODELS):
    with urllib.request.urlopen(url, timeout=60) as r:
        lines = r.read().decode().splitlines()
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1] == "200":
            out.append(parts[0])
    return out


def fetch_capture(ts, attempts=3):
    """Return {"ids": [...], "sha256": ..., "bytes": n, "models": n} for one capture.

    curl --compressed so gzip/brotli both decode. HTTP 403/429 raises
    WaybackChallenge (not retried); other failures retry with backoff.
    """
    url = RAW.format(ts=ts)
    for attempt in range(attempts):
        p = subprocess.run(["curl", "-sS", "--compressed", "-w", "\n%{http_code}",
                            "--max-time", "90", url],
                           capture_output=True, timeout=120)
        body, _, code = p.stdout.rpartition(b"\n")
        code = code.decode(errors="replace").strip()
        if code in ("403", "429"):
            raise WaybackChallenge(f"wayback returned HTTP {code} for {ts}")
        if p.returncode != 0 or code != "200":
            if attempt == attempts - 1:
                raise RuntimeError(f"fetch failed for {ts} (http {code}, rc {p.returncode})")
            time.sleep(3 * (attempt + 1))
            continue
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            if attempt == attempts - 1:
                raise RuntimeError(f"capture {ts} did not parse as JSON")
            time.sleep(3 * (attempt + 1))
            continue
        ids = sorted({m.get("id") for m in data.get("data", []) if m.get("id")})
        return {"ids": ids, "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body), "models": len(ids),
                "locator": url}
    raise RuntimeError("unreachable")


def build_index(out_path, sleep=1.0, progress=None, only=None):
    """Fetch every archived /api/v1/models capture once; store integrity metadata
    plus id sets. Resumes from the existing file. Stops (does not retry) on a
    Wayback challenge; transient failures are skipped for a later run."""
    index = {}
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            index = json.load(f)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    wanted = set(only) if only else None
    for ts in list_model_captures():
        if wanted is not None and ts not in wanted:
            continue
        if ts in index and index[ts].get("models"):
            continue
        try:
            rec = fetch_capture(ts)
        except WaybackChallenge as exc:
            if progress:
                progress(f"STOP: {exc}", 0)
            break
        except Exception as exc:
            if progress:
                progress(f"{ts} FAILED ({exc})", 0)
            continue
        if not rec["ids"]:
            continue
        index[ts] = rec
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(index, f, sort_keys=True)
        if progress:
            progress(ts, rec["models"])
        time.sleep(sleep)
    return index


def _neighbor_completeness(index):
    """Mark captures whose model count is plausible against temporal neighbors.

    A truncated or partially replayed catalog would show a count far below its
    neighbors; absence evidence from such a capture is worthless. Rule: a
    capture is complete when its count is >= 60% of each available neighbor's
    (edge captures compare against the single neighbor they have).
    """
    def _count(rec):
        return rec.get("models") or len(rec.get("ids") or [])
    stamps = sorted(ts for ts, rec in index.items() if _count(rec))
    counts = {ts: _count(index[ts]) for ts in stamps}
    complete = {}
    for i, ts in enumerate(stamps):
        neigh = []
        if i > 0:
            neigh.append(counts[stamps[i - 1]])
        if i < len(stamps) - 1:
            neigh.append(counts[stamps[i + 1]])
        complete[ts] = all(counts[ts] >= 0.6 * n for n in neigh) if neigh else True
    return complete


def _present(model, ids):
    if model in ids:
        return True
    base = model.split(":")[0]
    return base in ids or (base + ":free") in ids


def catalog_evidence(events, index):
    """Attach bracketing archived-catalog observations to unclassified removals.

    Adds e["catalog_evidence"] = {"present_at": iso, "absent_at": iso,
    "gap_before_days": float, "gap_after_days": float} ONLY when captures
    bracket the event window AND the model was present in the earlier capture
    and absent in the later one. The event's (absent) cause is untouched:
    sparse global catalogs cannot establish *why* the ZDR flag moved, and a
    model can vanish/reappear between captures. Idempotent.
    """
    stamps = sorted(index)
    if not stamps:
        return events
    complete = _neighbor_completeness(index)
    iso_to_ts = {ts_to_iso(ts): ts for ts in stamps}
    isos = sorted(iso_to_ts)
    out = []
    for e in events:
        if e.get("type") != "route_removed" or e.get("cause"):
            out.append(e)
            continue
        lo, hi = e["between"]
        before = [i for i in isos if i <= lo]
        after = [i for i in isos if i >= hi]
        if not before or not after:
            out.append(e)
            continue
        ts_before, ts_after = iso_to_ts[before[-1]], iso_to_ts[after[0]]
        rec_before, rec_after = index[ts_before], index[ts_after]
        model = e.get("model_base") or e["route_key"].partition(" | ")[2]
        model = DATE_SUFFIX.sub("", model)
        if not (rec_before.get("sha256") and rec_after.get("sha256")):
            out.append(e)  # no integrity provenance -> no note
            continue
        if not (complete.get(ts_before) and complete.get(ts_after)):
            out.append(e)  # implausibly small catalog -> absence may be an artifact
            continue
        if _present(model, rec_before["ids"]) and not _present(model, rec_after["ids"]):
            e = dict(e)
            gap_b = ( _iso_secs(lo) - _iso_secs(before[-1]) ) / 86400
            gap_a = ( _iso_secs(after[0]) - _iso_secs(hi) ) / 86400
            e["catalog_evidence"] = {
                "present_at": before[-1], "absent_at": after[0],
                "gap_before_days": round(gap_b, 1), "gap_after_days": round(gap_a, 1),
            }
        out.append(e)
    return out


def _iso_secs(s):
    import datetime
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
