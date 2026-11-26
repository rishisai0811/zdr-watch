"""Fetch the live ZDR endpoint list. Stdlib only, no API key needed as of 2026-11."""
from __future__ import annotations

import gzip
import io
import json
import urllib.request

ZDR_URL = "https://openrouter.ai/api/v1/endpoints/zdr"
UA = "zdrwatch/0.1 (+https://github.com/rishisai0811/zdr-watch) snapshot bot"


def _maybe_gunzip(raw):
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    return raw


def fetch_json(url=ZDR_URL, timeout=60):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(_maybe_gunzip(raw).decode("utf-8"))


def fetch_live():
    return fetch_json(ZDR_URL)["data"]


MODELS_URL = "https://openrouter.ai/api/v1/models"
MODEL_ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model_id}/endpoints"


def fetch_catalog_model_ids():
    """All model ids currently in the catalog (one keyless request)."""
    return {m["id"] for m in fetch_json(MODELS_URL)["data"]}


def fetch_model_providers(model_id):
    """Provider names currently serving a model, or None if it has no endpoints entry."""
    data = fetch_json(MODEL_ENDPOINTS_URL.format(model_id=model_id))["data"]
    return {e.get("provider_name") for e in data.get("endpoints") or []}
