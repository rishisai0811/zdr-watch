"""CLI: python -m zdrwatch <command>

Commands:
  check      Fetch live list, diff against data/latest.json, update history,
             site and feed. This is what the scheduled workflow runs.
  backfill   Seed history from Internet Archive captures (one-time, polite).
  render     Rebuild site/feed/changelog from existing data files.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

from .backfill import backfill as run_backfill
from .diff import diff_snapshots, summarize
from .classify import classify_removals
from .fetch import fetch_catalog_model_ids, fetch_live, fetch_model_providers
from .history import append_events, dedupe_events, events_chronological, load_events
from .model import normalize, snapshot_from_json, snapshot_to_json
from .render import render_changelog, render_feed, render_index, render_routes_json

DATA_LATEST = "data/latest.json"
EVENTS = "data/history/events.jsonl"
DOCS = "docs"


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_latest():
    if os.path.exists(DATA_LATEST):
        with open(DATA_LATEST, encoding="utf-8") as f:
            return snapshot_from_json(json.load(f))
    return None


def save_latest(snap):
    os.makedirs(os.path.dirname(DATA_LATEST), exist_ok=True)
    with open(DATA_LATEST, "w", encoding="utf-8") as f:
        json.dump(snapshot_to_json(snap), f, indent=1)


def render_all(site_url):
    snap = load_latest()
    if snap is None:
        print("no data/latest.json; run check first", file=sys.stderr)
        return 1
    events = events_chronological(dedupe_events(load_events(EVENTS)))
    os.makedirs(DOCS, exist_ok=True)
    render_index(snap, events, os.path.join(DOCS, "index.html"), site_url)
    render_routes_json(snap, events, os.path.join(DOCS, "routes.json"))
    render_feed(events, os.path.join(DOCS, "feed.xml"), site_url)
    render_changelog(events, os.path.join(DOCS, "CHANGELOG.md"))
    with open(os.path.join(DOCS, "changes.json"), "w", encoding="utf-8") as f:
        json.dump(events, f, indent=1)
    with open(os.path.join(DOCS, "current.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot_to_json(snap), f, indent=1)
    print(f"rendered: {len(events)} events, {len(snap['routes'])} current routes")
    return 0


def save_raw(entries, captured_at):
    raw_dir = os.path.join("data", "raw")
    os.makedirs(raw_dir, exist_ok=True)
    payload = json.dumps({"captured_at": captured_at, "source": "live",
                          "url": "https://openrouter.ai/api/v1/endpoints/zdr",
                          "data": entries})
    with open(os.path.join(raw_dir, "latest.json"), "w", encoding="utf-8") as f:
        f.write(payload)
    return payload


def cmd_check(site_url):
    entries = fetch_live()
    new = normalize(entries, now_iso(), source="live")
    raw_payload = save_raw(entries, new["captured_at"])
    old = load_latest()
    if old is None:
        save_latest(new)
        print(f"first snapshot: {len(new['routes'])} routes; no diff yet")
        render_all(site_url)
        return 0
    events = diff_snapshots(old, new)
    removed = [e for e in events if e["type"] == "route_removed"]
    if removed:
        try:
            classify_removals(events, fetch_catalog_model_ids(), fetch_model_providers)
        except Exception as exc:
            print(f"classification unavailable ({exc}); removals stay unclassified", file=sys.stderr)
    interesting = [e for e in events if e["type"] != "variants_changed"]
    if not events:
        print(f"no changes ({len(new['routes'])} routes)")
        return 0
    append_events(EVENTS, events)
    save_latest(new)
    # keep the raw fetch that produced changes, for reproducibility
    with open(os.path.join("data", "raw", new["captured_at"].replace(":", "") + ".json"),
              "w", encoding="utf-8") as f:
        f.write(raw_payload)
    render_all(site_url)
    s = summarize(events)
    print(f"changes: {json.dumps(s)}")
    # exit 2 when eligibility-affecting changes happened (workflow uses it for notices)
    return 2 if interesting else 0


def cmd_backfill(site_url, limit):
    snaps = run_backfill(limit=limit, progress=lambda m: print(m, file=sys.stderr))
    if not snaps:
        print("no usable captures", file=sys.stderr)
        return 1
    all_events = []
    for old, new in zip(snaps, snaps[1:]):
        all_events.extend(diff_snapshots(old, new))
    append_events(EVENTS, all_events)
    save_latest(snaps[-1])
    print(f"backfilled {len(snaps)} captures, {len(all_events)} events "
          f"({snaps[0]['captured_at']} .. {snaps[-1]['captured_at']})")
    render_all(site_url)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="zdrwatch")
    p.add_argument("command", choices=["check", "backfill", "render"])
    p.add_argument("--site-url", default=os.environ.get("SITE_URL", "https://example.github.io/zdr-watch"))
    p.add_argument("--limit", type=int, default=None, help="backfill: only the N newest captures")
    args = p.parse_args(argv)
    if args.command == "check":
        return cmd_check(args.site_url)
    if args.command == "backfill":
        return cmd_backfill(args.site_url, args.limit)
    return render_all(args.site_url)


if __name__ == "__main__":
    raise SystemExit(main())
