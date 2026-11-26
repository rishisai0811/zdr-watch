"""Render the static site (GitHub Pages), Atom feed and CHANGELOG from history."""
from __future__ import annotations

import html
import json
from collections import Counter
from xml.etree import ElementTree as ET


LIMITS_TEXT = (
    "This tracks changes in OpenRouter's published ZDR-eligibility list only. "
    "It cannot show whether any provider actually retains or deletes data; the flag is a claim, "
    "and this feed records when the claim appears, moves or disappears. "
    "History before the first live snapshot comes from Internet Archive captures at irregular times, "
    "so each change is dated to the window between two observations, not an exact moment. "
    "Scope is one router (OpenRouter). Not affiliated with OpenRouter."
)

TITLES = {
    "route_added": "gained ZDR eligibility",
    "safety_retention_changed": "changed safety-retention flag",
    "variants_changed": "changed endpoint variants",
}

CAUSE_TITLES = {
    "zdr_eligibility_lost": "lost ZDR eligibility (provider still served the model at check time)",
    "endpoint_retired": "dropped from the ZDR list (provider did not serve the model at check time)",
    "model_retired": "dropped from the ZDR list (model absent from the catalog at check time)",
    None: "dropped from the published ZDR list",
    "unknown": "dropped from the published ZDR list",
}


def title_for(e):
    if e["type"] == "route_removed":
        return CAUSE_TITLES.get(e.get("cause"), CAUSE_TITLES[None])
    return TITLES.get(e["type"], e["type"])


def event_line(e):
    verb = title_for(e)
    sr = e.get("safety_retention")
    tag = " [safety-retention]" if sr else ""
    lo, hi = e["between"]
    day = hi[:10]
    if lo[:10] != hi[:10]:
        window = f"between {lo[:10]} and {hi[:10]}"
    else:
        window = f"on {day} (observed {hi[11:16]}Z)"
    return f"- **{e['route_key']}** {verb} {window}{tag}"


def render_changelog(events, out_path, max_days=None):
    lines = [
        "# ZDR eligibility change log",
        "",
        "Each entry is dated to the window between two observations of OpenRouter's",
        "published ZDR endpoint list. See README.md for what this can and cannot show.",
        "",
    ]
    by_hi = {}
    for e in events:
        by_hi.setdefault(e["between"][1][:10], []).append(e)
    for day in sorted(by_hi, reverse=True):
        lines.append(f"## {day}")
        lines.append("")
        for e in sorted(by_hi[day], key=lambda x: (x["type"], x["route_key"])):
            lines.append(event_line(e))
        lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def render_feed(events, out_path, site_url, title="OpenRouter ZDR eligibility changes"):
    ET.register_namespace("", "http://www.w3.org/2005/Atom")
    feed = ET.Element("{http://www.w3.org/2005/Atom}feed")
    def sub(tag, text, parent=feed):
        el = ET.SubElement(parent, "{http://www.w3.org/2005/Atom}" + tag)
        el.text = text
        return el
    sub("title", title)
    sub("id", site_url.rstrip("/") + "/feed.xml")
    link = ET.SubElement(feed, "{http://www.w3.org/2005/Atom}link")
    link.set("href", site_url.rstrip("/") + "/feed.xml"); link.set("rel", "self")
    recent = sorted(events, key=lambda e: e["between"][1])[-50:]
    updated = recent[-1]["between"][1] if recent else "1970-01-01T00:00:00Z"
    sub("updated", updated)
    for e in reversed(recent):
        entry = ET.SubElement(feed, "{http://www.w3.org/2005/Atom}entry")
        verb = title_for(e)
        sub("title", f"{e['route_key']} {verb}", entry)
        sig = f"{e['type']}:{e['route_key']}:{e['between'][0]}:{e['between'][1]}"
        sub("id", f"urn:zdrwatch:{sig}", entry)
        sub("updated", e["between"][1], entry)
        lo, hi = e["between"]
        sub("summary", f"Observed {verb} between {lo} and {hi} (observation window).", entry)
    ET.ElementTree(feed).write(out_path, encoding="utf-8", xml_declaration=True)


def render_index(current_snap, events, out_path, site_url):
    routes = current_snap["routes"]
    total = len(routes)
    sr_count = sum(1 for r in routes.values() if r.safety_retention is True)
    prov_counts = Counter(r.provider for r in routes.values())
    recent = sorted(events, key=lambda e: e["between"][1])[-30:]
    rows = []
    for e in reversed(recent):
        verb = title_for(e)
        lo, hi = e["between"]
        window = (html.escape(lo[:10]) if lo[:10] == hi[:10]
                  else html.escape(lo[:10]) + " &rarr; " + html.escape(hi[:10]))
        rows.append(
            f"<tr><td>{window}</td><td class=\"{e['type']}\">"
            f"{html.escape(e['route_key'])}</td><td>{html.escape(verb)}</td></tr>")
    prov_rows = "".join(
        f"<tr><td>{html.escape(p)}</td><td>{c}</td></tr>"
        for p, c in prov_counts.most_common())
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenRouter ZDR eligibility watch</title>
<link rel="alternate" type="application/atom+xml" href="feed.xml">
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
td, th {{ border: 1px solid #ddd; padding: 0.35rem 0.6rem; text-align: left; font-size: 0.9rem; }}
.route_removed td, td.route_removed {{ background: #fdecea; }}
.route_added td, td.route_added {{ background: #e8f5e9; }}
.safety_retention_changed td, td.safety_retention_changed {{ background: #fff8e1; }}
.limits {{ background: #f5f5f5; border-left: 4px solid #999; padding: 0.6rem 1rem; font-size: 0.9rem; }}
</style></head><body>
<h1>OpenRouter ZDR eligibility watch</h1>
<p>Last observed <strong>{html.escape(current_snap['captured_at'])}</strong>:
<strong>{total}</strong> routes on the published ZDR list
(<strong>{sr_count}</strong> flagged safety-retention).
<a href="feed.xml">Atom feed</a> &middot; <a href="changes.json">changes.json</a> &middot;
<a href="current.json">current.json</a></p>
<div class="limits"><strong>What this is not:</strong> {html.escape(LIMITS_TEXT)}</div>
<h2>Recent changes</h2>
<p><small>Removal notes reflect OpenRouter's catalog as observed at check time.
They cannot prove why a flag changed, and say nothing about what any provider
did with data. Archive-derived removals predate classification and are only
known to have left the published list.</small></p>
<table><tr><th>Observation window</th><th>Route</th><th>Change</th></tr>
{''.join(rows) if rows else '<tr><td colspan="3">No changes recorded yet.</td></tr>'}
</table>
<h2>Current routes by provider</h2>
<table><tr><th>Provider</th><th>Routes on ZDR list</th></tr>{prov_rows}</table>
<p><small>Generated by zdrwatch. Times are UTC.</small></p>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
