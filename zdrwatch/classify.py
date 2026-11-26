"""Classify ZDR-list removals against OpenRouter's full catalog.

A route disappearing from the ZDR list is not always an eligibility
revocation: the model may have been retired from OpenRouter entirely, or the
provider may have stopped serving that model. Live checks classify each
removal against the catalog:

- model_retired: the model is no longer in the /models catalog at all.
- endpoint_retired: the model exists, but this provider no longer serves it.
- zdr_eligibility_lost: the provider still serves the model; only the ZDR
  flag changed. This is the only case we call an eligibility loss.
- unknown: classification data unavailable (e.g. fetch failed, backfilled
  history).

Backfilled events from archive captures keep cause=unknown and are worded
"dropped from the published ZDR list", never "lost ZDR eligibility".
"""
from __future__ import annotations

CAUSES = ("zdr_eligibility_lost", "endpoint_retired", "model_retired", "unknown")


def classify_removals(events, model_ids, endpoints_for):
    """Annotate route_removed events in place with a 'cause'.

    model_ids: set of model ids currently in the catalog.
    endpoints_for: callable(model_id) -> set of provider names serving it,
                   or None on failure.
    """
    cache = {}
    for e in events:
        if e.get("type") != "route_removed":
            continue
        # Catalog ids keep ":free"-style suffixes; dated variants share one id.
        model = e["model_base"]
        if model not in model_ids and model.split(":")[0] in model_ids:
            model = model.split(":")[0]
        if model not in model_ids:
            e["cause"] = "model_retired"
            continue
        if model not in cache:
            try:
                cache[model] = endpoints_for(model)
            except Exception:
                cache[model] = None
        providers = cache[model]
        if providers is None:
            e["cause"] = "unknown"
        elif e["provider"] in providers:
            e["cause"] = "zdr_eligibility_lost"
        else:
            e["cause"] = "endpoint_retired"
    return events
