import json
import os
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zdrwatch.model import normalize, route_key_for, parse_name, snapshot_to_json, snapshot_from_json
from zdrwatch.diff import diff_snapshots, summarize
from zdrwatch.history import dedupe_events, events_chronological, canonicalize_event
from zdrwatch.render import render_feed, render_changelog, render_index, render_routes_json


def entry(name, **kw):
    e = {"name": name, "provider_name": name.partition(" | ")[0], "tag": "x/fp8"}
    e.update(kw)
    return e


class TestNormalize:
    def test_route_key_strips_dated_suffix(self):
        assert route_key_for("Novita | minimax/minimax-m2-20251022") == "Novita | minimax/minimax-m2"
        assert route_key_for("Groq | qwen/qwen3-32b-04-28") == "Groq | qwen/qwen3-32b-04-28"

    def test_parse_name_without_separator(self):
        assert parse_name("BareName") == ("BareName", "")

    def test_dated_variants_merge_under_one_route(self):
        snap = normalize([
            entry("Krea | z-ai/glm-5.3-20260816"),
            entry("Krea | z-ai/glm-5.3-20260901"),
        ], "2026-11-26T00:00:00Z", "live")
        assert len(snap["routes"]) == 1
        r = snap["routes"]["Krea | z-ai/glm-5.3"]
        assert len(r.variants) == 2

    def test_safety_retention_absent_in_old_schema(self):
        snap = normalize([entry("Phala | deepseek/deepseek-v3")], "2026-10-23T00:00:00Z", "wayback:20251023")
        assert snap["routes"]["Phala | deepseek/deepseek-v3"].safety_retention is None

    def test_safety_retention_flag_preserved(self):
        snap = normalize([entry("Nebius | moonshotai/kimi-k2", safety_retention=True)],
                         "2026-11-26T00:00:00Z", "live")
        assert snap["routes"]["Nebius | moonshotai/kimi-k2"].safety_retention is True

    def test_snapshot_json_roundtrip(self):
        snap = normalize([entry("A | m/x-20260101", safety_retention=False)], "2026-01-02T00:00:00Z", "live")
        snap2 = snapshot_from_json(snapshot_to_json(snap))
        assert snap2["routes"].keys() == snap["routes"].keys()
        assert snap2["routes"]["A | m/x"].variants == snap["routes"]["A | m/x"].variants


class TestDiff:
    def snap(self, names, at, **kw):
        return normalize([entry(n, **kw) for n in names], at, "live")

    def test_route_removed_and_added(self):
        old = self.snap(["A | m/one", "B | m/two"], "2026-11-01T00:00:00Z")
        new = self.snap(["B | m/two", "C | m/three"], "2026-11-02T00:00:00Z")
        events = diff_snapshots(old, new)
        kinds = {(e["type"], e["route_key"]) for e in events}
        assert ("route_removed", "A | m/one") in kinds
        assert ("route_added", "C | m/three") in kinds
        assert all(e["between"] == ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"] for e in events)

    def test_dated_variant_swap_is_not_removal(self):
        old = self.snap(["Novita | minimax/minimax-m2-20251022"], "2026-11-01T00:00:00Z")
        new = self.snap(["Novita | minimax/minimax-m2-20251101"], "2026-11-02T00:00:00Z")
        events = diff_snapshots(old, new)
        assert [e["type"] for e in events] == ["variants_changed"]
        assert events[0]["variants_lost"] == ["Novita | minimax/minimax-m2-20251022"]

    def test_safety_retention_flip_detected_only_when_both_known(self):
        old = self.snap(["N | m/k"], "2026-11-01T00:00:00Z", safety_retention=False)
        new = self.snap(["N | m/k"], "2026-11-02T00:00:00Z", safety_retention=True)
        events = diff_snapshots(old, new)
        assert any(e["type"] == "safety_retention_changed" and e["to"] is True for e in events)

    def test_unknown_to_known_flag_is_not_a_flip(self):
        old = self.snap(["N | m/k"], "2026-11-01T00:00:00Z")  # old schema: no field
        new = self.snap(["N | m/k"], "2026-11-02T00:00:00Z", safety_retention=True)
        events = diff_snapshots(old, new)
        assert not any(e["type"] == "safety_retention_changed" for e in events)

    def test_identical_snapshots_no_events(self):
        a = self.snap(["A | m/x"], "2026-11-01T00:00:00Z", safety_retention=False)
        b = self.snap(["A | m/x"], "2026-11-02T00:00:00Z", safety_retention=False)
        assert diff_snapshots(a, b) == []

    def test_summarize(self):
        old = self.snap(["A | m/one", "A | m/two", "B | m/x"], "2026-11-01T00:00:00Z")
        new = self.snap(["B | m/x"], "2026-11-02T00:00:00Z")
        s = summarize(diff_snapshots(old, new))
        assert s["by_type"]["route_removed"] == 2
        assert s["removed_by_provider"] == {"A": 2}


class TestHistoryAndRender:
    def test_canonicalize_merges_dated_keys(self):
        e = {"type": "route_added", "route_key": "Novita | apodex/apodex-1.1-mini-20261001:free",
             "model_base": "apodex/apodex-1.1-mini-20261001:free",
             "between": ["2026-11-12T00:00:00Z", "2026-11-13T00:00:00Z"]}
        c = canonicalize_event(e)
        assert c["route_key"] == "Novita | apodex/apodex-1.1-mini:free"
        assert c["model_base"] == "apodex/apodex-1.1-mini:free"
        assert e["route_key"].endswith("-20261001:free")  # original untouched
        plain = {"type": "route_added", "route_key": "Novita | x/y", "between": ["a", "b"]}
        assert canonicalize_event(plain) is plain  # no copy when already canonical

    def test_dedupe_and_order(self):
        evs = [
            {"type": "route_added", "route_key": "A | m/x", "provider": "A", "model_base": "m/x",
             "safety_retention": False, "variants_gained": [], "between": ["2026-11-01T00:00:00Z", "2026-11-03T00:00:00Z"]},
            {"type": "route_removed", "route_key": "B | m/y", "provider": "B", "model_base": "m/y",
             "safety_retention": None, "variants_lost": [], "between": ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"]},
        ]
        dup = evs + [evs[0]]
        assert len(dedupe_events(dup)) == 2
        ordered = events_chronological(evs)
        assert ordered[0]["type"] == "route_removed"

    def test_feed_is_wellformed_atom(self, tmp_path):
        evs = [{"type": "route_removed", "route_key": "A | m/x", "provider": "A", "model_base": "m/x",
                "safety_retention": False, "variants_lost": ["A | m/x-1"],
                "between": ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"]}]
        out = tmp_path / "feed.xml"
        render_feed(evs, str(out), "https://example.github.io/zdr-watch")
        root = ET.parse(str(out)).getroot()
        assert root.tag == "{http://www.w3.org/2005/Atom}feed"
        assert len(root.findall("{http://www.w3.org/2005/Atom}entry")) == 1

    def test_changelog_and_index(self, tmp_path):
        evs = [{"type": "route_removed", "route_key": "A | m/x", "provider": "A", "model_base": "m/x",
                "safety_retention": False, "variants_lost": ["A | m/x-1"], "cause": "zdr_eligibility_lost",
                "between": ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"]}]
        cl = tmp_path / "CHANGELOG.md"
        render_changelog(evs, str(cl))
        text = cl.read_text()
        assert "A | m/x" in text and "lost ZDR eligibility" in text
        snap = normalize([entry("B | m/y", safety_retention=False)], "2026-11-02T00:00:00Z", "live")
        idx = tmp_path / "index.html"
        render_index(snap, evs, str(idx), "https://example.github.io/zdr-watch")
        page = idx.read_text()
        assert "OpenRouter ZDR eligibility watch" in page and "A | m/x" in page

    def test_routes_json_lookup_contract(self, tmp_path):
        evs = [
            {"type": "route_removed", "route_key": "A | m/x", "provider": "A", "model_base": "m/x",
             "safety_retention": False, "variants_lost": ["A | m/x-1"], "cause": "zdr_eligibility_lost",
             "between": ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"]},
            {"type": "route_added", "route_key": "A | m/x", "provider": "A", "model_base": "m/x",
             "safety_retention": False, "variants_gained": ["A | m/x-2"],
             "between": ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"]},
        ]
        snap = normalize([entry("B | m/y", safety_retention=True)], "2026-11-02T00:00:00Z", "live")
        out = tmp_path / "routes.json"
        render_routes_json(snap, evs, str(out))
        data = json.loads(out.read_text())
        current = data["routes"]["B | m/y"]
        assert current["now"] is True and current["sr"] is True and current["ev"] == []
        removed = data["routes"]["A | m/x"]
        assert removed["now"] is False
        assert [e["t"] for e in removed["ev"]] == ["route_removed", "route_added"]  # newest first
        assert removed["ev"][0]["w"].startswith("lost ZDR eligibility")
        assert removed["ev"][0]["b"] == ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"]
        assert data["archive_start"] == "2026-10-01T00:00:00Z"
        assert data["generated_at"] == "2026-11-02T00:00:00Z"
        assert "cannot show whether any provider actually retains" in data["caveat"]

    def test_index_has_history_lookup(self, tmp_path):
        evs = [{"type": "route_added", "route_key": "A | m/x", "provider": "A", "model_base": "m/x",
                "safety_retention": False, "variants_gained": [],
                "between": ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"]}]
        snap = normalize([entry("A | m/x", safety_retention=False)], "2026-11-02T00:00:00Z", "live")
        idx = tmp_path / "index.html"
        render_index(snap, evs, str(idx), "https://example.github.io/zdr-watch")
        page = idx.read_text()
        assert "Route history lookup" in page
        assert 'id="q"' in page and "routes.json" in page
        assert "stable" in page  # archive-stability caveat wording
        assert "2026-11-01" in page  # archive start date shown


from zdrwatch.classify import classify_removals
from zdrwatch.render import title_for


def _removal(provider, model_base):
    return {"type": "route_removed", "route_key": provider + " | " + model_base,
            "provider": provider, "model_base": model_base, "safety_retention": False,
            "variants_lost": [], "between": ["2026-11-19T00:00:00Z", "2026-11-21T00:00:00Z"]}


class TestClassify:
    IDS = {"minimax/minimax-m2", "z-ai/glm-5.3", "inclusionai/ling-3.2-flash:free"}

    def endpoints(self, model):
        return {"minimax/minimax-m2": {"Novita", "SiliconFlow"},
                "z-ai/glm-5.3": {"Fireworks"},
                "inclusionai/ling-3.2-flash:free": {"Ant Digital"}}.get(model)

    def test_eligibility_lost_only_when_provider_still_serves(self):
        e = _removal("Novita", "minimax/minimax-m2")
        classify_removals([e], self.IDS, self.endpoints)
        assert e["cause"] == "zdr_eligibility_lost"

    def test_endpoint_retired_when_provider_gone(self):
        e = _removal("Phala", "minimax/minimax-m2")
        classify_removals([e], self.IDS, self.endpoints)
        assert e["cause"] == "endpoint_retired"

    def test_model_retired_when_absent_from_catalog(self):
        e = _removal("X", "old/model-1")
        classify_removals([e], self.IDS, self.endpoints)
        assert e["cause"] == "model_retired"

    def test_free_suffix_matches_catalog_id_first(self):
        e = _removal("Ant Digital", "inclusionai/ling-3.2-flash:free")
        classify_removals([e], self.IDS, self.endpoints)
        assert e["cause"] == "zdr_eligibility_lost"

    def test_fetch_failure_is_unknown(self):
        def boom(model):
            raise RuntimeError("net down")
        e = _removal("Novita", "minimax/minimax-m2")
        classify_removals([e], self.IDS, boom)
        assert e["cause"] == "unknown"


class TestCauseWording:
    def test_unclassified_removal_never_says_lost(self):
        e = _removal("A", "m/x")
        assert title_for(e) == "dropped from the published ZDR list"
        e["cause"] = "zdr_eligibility_lost"
        assert "lost ZDR eligibility" in title_for(e)
        e["cause"] = "model_retired"
        assert "nearest catalog observation" in title_for(e)
        assert "retired" not in title_for(e) or "nearest catalog observation" in title_for(e)

    def test_free_variant_merging(self):
        snap = normalize([
            entry("Ant Digital | inclusionai/ling-3.2-flash-20261113:free"),
            entry("Ant Digital | inclusionai/ling-3.2-flash-20261201:free"),
        ], "2026-12-02T00:00:00Z", "live")
        assert list(snap["routes"]) == ["Ant Digital | inclusionai/ling-3.2-flash:free"]
        r = snap["routes"]["Ant Digital | inclusionai/ling-3.2-flash:free"]
        assert len(r.variants) == 2


from zdrwatch.catalog_archive import catalog_evidence, _present


class TestCatalogEvidence:
    def _idx(self, entries):
        return {ts: {"ids": ids, "sha256": "x", "bytes": 1, "models": len(ids)}
                for ts, ids in entries.items()}

    INDEX = None

    def setUp(self):
        pass

    def _rem(self, model, lo, hi):
        return {"type": "route_removed", "route_key": "P | " + model, "provider": "P",
                "model_base": model, "safety_retention": False, "variants_lost": [],
                "between": [lo, hi]}

    def _index(self):
        return self._idx({
            "20250901000000": ["a/m-1", "b/m-2", "c/m-3:free"],
            "20251001000000": ["a/m-1", "c/m-3:free"],
            "20251101000000": ["a/m-1", "c/m-3:free"],
        })

    def test_evidence_attached_not_cause(self):
        evs = [self._rem("b/m-2", "2025-09-15T00:00:00Z", "2025-09-20T00:00:00Z")]
        out = catalog_evidence(evs, self._index())
        assert "cause" not in out[0]  # cause is never overwritten
        ev = out[0]["catalog_evidence"]
        assert ev["present_at"] == "2025-09-01T00:00:00Z"
        assert ev["absent_at"] == "2025-10-01T00:00:00Z"
        assert ev["gap_before_days"] == 14.0 and ev["gap_after_days"] == 11.0

    def test_still_present_gets_no_evidence(self):
        evs = [self._rem("a/m-1", "2025-09-15T00:00:00Z", "2025-09-20T00:00:00Z")]
        assert "catalog_evidence" not in catalog_evidence(evs, self._index())[0]

    def test_no_bracketing_capture_no_evidence(self):
        evs = [self._rem("b/m-2", "2025-07-01T00:00:00Z", "2025-07-02T00:00:00Z")]
        assert "catalog_evidence" not in catalog_evidence(evs, self._index())[0]

    def test_free_suffix_matches_base_id(self):
        assert _present("c/m-3", ["c/m-3:free"]) and _present("c/m-3:free", ["c/m-3"])

    def test_idempotent_and_preserves_existing_cause(self):
        e = self._rem("b/m-2", "2025-09-15T00:00:00Z", "2025-09-20T00:00:00Z")
        e["cause"] = "model_retired"
        out = catalog_evidence([e], self._index())[0]
        assert out["cause"] == "model_retired" and "catalog_evidence" not in out

    def test_dated_model_base_canonicalized(self):
        evs = [self._rem("b/m-2-20250910", "2025-09-15T00:00:00Z", "2025-09-20T00:00:00Z")]
        assert "catalog_evidence" in catalog_evidence(evs, self._index())[0]


class TestEvidenceProvenance:
    def _rem(self, model, lo, hi):
        return {"type": "route_removed", "route_key": "P | " + model, "provider": "P",
                "model_base": model, "safety_retention": False, "variants_lost": [],
                "between": [lo, hi]}

    def _rec(self, ids, hashed=True):
        r = {"ids": ids, "bytes": 1, "models": len(ids)}
        if hashed:
            r["sha256"] = "abc"
        return r

    def test_unhashed_captures_give_no_note(self):
        idx = {"20250901000000": self._rec(["a/m-1", "b/m-2"], hashed=False),
               "20251001000000": self._rec(["a/m-1"])}
        evs = [self._rem("b/m-2", "2025-09-15T00:00:00Z", "2025-09-20T00:00:00Z")]
        assert "catalog_evidence" not in catalog_evidence(evs, idx)[0]

    def test_implausibly_small_absent_capture_gives_no_note(self):
        # absent-capture with 1 model next to 2-model and 300-model neighbors:
        # 1 < 0.6*300 -> incomplete -> no absence evidence
        idx = {"20250901000000": self._rec(["a/m-1", "b/m-2"]),
               "20251001000000": self._rec(["a/m-1"]),
               "20251101000000": self._rec(["a/m-1"] + [f"x/m{i}" for i in range(300)])}
        evs = [self._rem("b/m-2", "2025-09-15T00:00:00Z", "2025-09-20T00:00:00Z")]
        assert "catalog_evidence" not in catalog_evidence(evs, idx)[0]

    def test_plausible_captures_give_note(self):
        idx = {"20250901000000": self._rec(["a/m-1", "b/m-2"] + [f"x/m{i}" for i in range(300)]),
               "20251001000000": self._rec(["a/m-1"] + [f"x/m{i}" for i in range(298)]),
               "20251101000000": self._rec(["a/m-1"] + [f"x/m{i}" for i in range(301)])}
        evs = [self._rem("b/m-2", "2025-09-15T00:00:00Z", "2025-09-20T00:00:00Z")]
        assert "catalog_evidence" in catalog_evidence(evs, idx)[0]
