"""Diff two snapshots into typed change events.

Every event carries a 'between' window [old_at, new_at]: the change is only
known to have happened somewhere inside it. Honest granularity is the point
of the tool (cf. "a flag with no history is indistinguishable from one that
was wrong yesterday").
"""
from __future__ import annotations

EVENT_TYPES = ("route_removed", "route_added", "safety_retention_changed", "variants_changed")


def diff_snapshots(old, new):
    """old/new: snapshots from model.normalize. Returns a list of event dicts."""
    window = [old["captured_at"], new["captured_at"]]
    events = []
    old_routes, new_routes = old["routes"], new["routes"]

    for key in sorted(old_routes.keys() - new_routes.keys()):
        r = old_routes[key]
        events.append({
            "type": "route_removed",
            "route_key": key,
            "provider": r.provider,
            "model_base": r.model_base,
            "safety_retention": r.safety_retention,
            "variants_lost": sorted(r.variants),
            "between": window,
        })
    for key in sorted(new_routes.keys() - old_routes.keys()):
        r = new_routes[key]
        events.append({
            "type": "route_added",
            "route_key": key,
            "provider": r.provider,
            "model_base": r.model_base,
            "safety_retention": r.safety_retention,
            "variants_gained": sorted(r.variants),
            "between": window,
        })
    for key in sorted(old_routes.keys() & new_routes.keys()):
        o, n = old_routes[key], new_routes[key]
        # Only compare the flag when both snapshots actually carry it.
        if o.safety_retention is not None and n.safety_retention is not None \
                and o.safety_retention != n.safety_retention:
            events.append({
                "type": "safety_retention_changed",
                "route_key": key,
                "provider": n.provider,
                "model_base": n.model_base,
                "from": o.safety_retention,
                "to": n.safety_retention,
                "between": window,
            })
        if o.variants != n.variants:
            events.append({
                "type": "variants_changed",
                "route_key": key,
                "provider": n.provider,
                "model_base": n.model_base,
                "variants_lost": sorted(o.variants - n.variants),
                "variants_gained": sorted(n.variants - o.variants),
                "between": window,
            })
    return events


def summarize(events):
    """Compact counts by type and provider for commit messages / changelogs."""
    from collections import Counter
    by_type = Counter(e["type"] for e in events)
    removed_by_prov = Counter(e["provider"] for e in events if e["type"] == "route_removed")
    added_by_prov = Counter(e["provider"] for e in events if e["type"] == "route_added")
    return {
        "total": len(events),
        "by_type": dict(by_type),
        "removed_by_provider": dict(removed_by_prov.most_common()),
        "added_by_provider": dict(added_by_prov.most_common()),
    }
