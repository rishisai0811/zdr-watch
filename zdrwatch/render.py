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
    "Archived-catalog notes rest on Wayback captures whose completeness is unverified; "
    "they record observations, not causes. "
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
    "model_retired": "dropped from the ZDR list (model absent from the catalog in the nearest catalog observation)",
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


def render_routes_json(current_snap, events, out_path):
    """Per-route history index backing the site lookup (docs/routes.json).

    Every route currently on the list appears (now=true), plus every route
    with recorded events (now=false if absent from the latest snapshot).
    Events are newest-first and pre-worded with title_for so the page's
    JavaScript stays a dumb renderer of the same wording as the feed.
    """
    archive_start = min((e["between"][0] for e in events), default=None)
    routes = {}
    for key, r in current_snap["routes"].items():
        routes[key] = {"now": True, "sr": r.safety_retention, "ev": []}
    for e in events:
        rec = routes.setdefault(e["route_key"], {"now": False, "sr": None, "ev": []})
        item = {"t": e["type"], "w": title_for(e), "b": list(e["between"])}
        if e.get("catalog_evidence"):
            ce = e["catalog_evidence"]
            item["cat"] = (f"archived catalog: model present {ce['present_at'][:10]}, "
                           f"absent {ce['absent_at'][:10]}")
        rec["ev"].append(item)
    for rec in routes.values():
        rec["ev"].sort(key=lambda x: x["b"][1], reverse=True)
    out = {
        "generated_at": current_snap["captured_at"],
        "archive_start": archive_start,
        "caveat": LIMITS_TEXT,
        "routes": routes,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))


def render_index(current_snap, events, out_path, site_url):
    routes = current_snap["routes"]
    archive_day = (min((e["between"][0] for e in events), default="unknown") or "unknown")[:10]
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
.route {{ border: 1px solid #ddd; padding: 0.5rem 0.7rem; margin: 0.5rem 0; }}
.route ul {{ margin: 0.3rem 0 0.1rem; padding-left: 1.2rem; font-size: 0.9rem; }}
</style></head><body>
<h1>OpenRouter ZDR eligibility watch</h1>
<p><small>Updated once daily by a scheduled run (interim). Hourly checks start
once the GitHub Actions workflow is enabled; "Last observed" always shows the
actual freshest observation.</small></p>
<p>Last observed <strong>{html.escape(current_snap['captured_at'])}</strong>:
<strong>{total}</strong> routes on the published ZDR list
(<strong>{sr_count}</strong> flagged safety-retention).
<a href="feed.xml">Atom feed</a> &middot; <a href="changes.json">changes.json</a> &middot;
<a href="current.json">current.json</a></p>
<div class="limits"><strong>What this is not:</strong> {html.escape(LIMITS_TEXT)}</div>
<h2>Route history lookup</h2>
<p><small>Type a provider or model name (e.g. <em>novita</em> or <em>kimi</em>).
Archive starts {archive_day}; a route with no recorded changes was
stable across this archive's observations, which is all this site can say.
History reflects the published list as observed - the caveat above applies.</small></p>
<input id="q" type="search" placeholder="provider or model name" aria-label="provider or model name"
 style="width:100%;padding:0.4rem;box-sizing:border-box" autocomplete="off">
<div id="lookup-results"></div>
<script>
let ROUTES=null;
const q=document.getElementById('q'),res=document.getElementById('lookup-results');
function esc(s){{return s.replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));}}
async function loadRoutes(){{if(ROUTES)return ROUTES;const r=await fetch('routes.json');ROUTES=await r.json();return ROUTES;}}
q.addEventListener('input',async()=>{{
  const v=q.value.trim().toLowerCase();
  if(v.length<2){{res.innerHTML='';return;}}
  const data=await loadRoutes();
  const day=data.generated_at.slice(0,10);
  const toks=v.split(/\s+/);
  const keys=Object.keys(data.routes).filter(k=>{{const lk=k.toLowerCase();return toks.every(t=>lk.includes(t));}}).sort().slice(0,15);
  res.innerHTML=keys.map(k=>{{
    const r=data.routes[k];
    const status=r.now
      ?'On the published list at last observation ('+day+')'+(r.sr?' [safety-retention]':'')
      :'Not on the published list at last observation ('+day+')';
    const evs=r.ev.length
      ?'<ul>'+r.ev.map(e=>{{const lo=e.b[0].slice(0,10),hi=e.b[1].slice(0,10);
        return '<li>'+esc(lo)+(lo!==hi?' &rarr; '+esc(hi):'')+': '+esc(e.w)
          +(e.cat?' <small>('+esc(e.cat)+')</small>':'')+'</li>';}}).join('')+'</ul>'
      :'<p><small>No recorded changes in this archive.</small></p>';
    return '<div class="route"><strong>'+esc(k)+'</strong> <small>'+status+'</small>'+evs+'</div>';
  }}).join('')||'<p><small>No matching route.</small></p>';
}});
q.addEventListener('input',()=>{{const u=new URL(location.href);
  if(q.value.trim())u.searchParams.set('q',q.value.trim());else u.searchParams.delete('q');
  history.replaceState(null,'',u);}});
const qp=new URLSearchParams(location.search).get('q');
if(qp){{q.value=qp;q.dispatchEvent(new Event('input'));}}
</script>
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
