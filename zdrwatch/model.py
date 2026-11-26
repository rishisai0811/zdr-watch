"""Snapshot normalization for the OpenRouter ZDR endpoints list.

The list (https://openrouter.ai/api/v1/endpoints/zdr) carries one entry per
endpoint variant. Names look like "Provider | author/model-variant" where the
variant sometimes ends in a dated suffix (-YYYYMMDD). A dated variant change
means the deployment behind the route changed; it is NOT the same thing as a
route gaining or losing ZDR eligibility. We therefore diff on two levels:

- route_key: "Provider | author/model" with the dated suffix stripped.
  Presence of a route key is the eligibility-relevant fact.
- variants: the full dated names under a route key. Changes are reported
  separately as informational events.

safety_retention (bool, newer schema only) marks OpenRouter "Safety Retention"
endpoints: they appear on the ZDR list but may retain data for safety reasons,
and are only used if the caller allows them. Older captures lack the field;
those records carry safety_retention=None ("unknown").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DATE_SUFFIX = re.compile(r"-\d{8}(?=:|$)")


@dataclass
class Route:
    route_key: str
    provider: str
    model_base: str
    variants: set = field(default_factory=set)
    safety_retention: object = None  # True / False / None (unknown in old captures)

    def to_dict(self):
        return {
            "route_key": self.route_key,
            "provider": self.provider,
            "model_base": self.model_base,
            "variants": sorted(self.variants),
            "safety_retention": self.safety_retention,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            route_key=d["route_key"],
            provider=d["provider"],
            model_base=d["model_base"],
            variants=set(d.get("variants", [])),
            safety_retention=d.get("safety_retention"),
        )


def parse_name(name):
    """Split "Provider | author/model-variant" into (provider, model_variant)."""
    provider, sep, model = name.partition(" | ")
    if not sep:
        return name.strip(), ""
    return provider.strip(), model.strip()


def route_key_for(name):
    provider, model = parse_name(name)
    base = DATE_SUFFIX.sub("", model)
    return provider + " | " + base if base else provider


def normalize(entries, captured_at, source):
    """Normalize raw endpoint entries into a snapshot dict.

    captured_at: ISO-8601 UTC string of when this list was observed.
    source: "live" or "wayback:<timestamp>".
    Returns {"captured_at":..., "source":..., "routes": {route_key: Route}}.
    """
    routes = {}
    for e in entries:
        name = e.get("name") or ""
        if not name:
            continue
        key = route_key_for(name)
        provider, model = parse_name(name)
        base = DATE_SUFFIX.sub("", model)
        sr = e.get("safety_retention")  # absent in old captures -> None
        r = routes.get(key)
        if r is None:
            r = Route(route_key=key, provider=provider or e.get("provider_name", ""),
                      model_base=base, safety_retention=sr)
            routes[key] = r
        r.variants.add(name)
        # If any variant is flagged safety_retention, keep True; if schemas
        # disagree inside one route, prefer the flagged (conservative) value.
        if sr is True:
            r.safety_retention = True
        elif r.safety_retention is None and sr is False:
            r.safety_retention = False
    return {"captured_at": captured_at, "source": source, "routes": routes}


def snapshot_to_json(snap):
    return {
        "captured_at": snap["captured_at"],
        "source": snap["source"],
        "route_count": len(snap["routes"]),
        "routes": [snap["routes"][k].to_dict() for k in sorted(snap["routes"])],
    }


def snapshot_from_json(d):
    return {
        "captured_at": d["captured_at"],
        "source": d.get("source", "unknown"),
        "routes": {r["route_key"]: Route.from_dict(r) for r in d["routes"]},
    }
