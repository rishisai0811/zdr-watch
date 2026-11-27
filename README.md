# zdrwatch: OpenRouter ZDR eligibility change feed

Watches OpenRouter's published zero-data-retention (ZDR) endpoint list
(`https://openrouter.ai/api/v1/endpoints/zdr`), records every change, and
publishes the history as a static site, an Atom feed and a JSON changelog.

**Live outputs (once published):** `index.html` (current list + recent changes),
`feed.xml` (Atom feed you can point any feed reader or Slack/Discord RSS bot at),
`changes.json` (full event history), `current.json` (latest normalized snapshot).

## Why

In November 2026 OpenRouter off-boarded three providers and removed 125
endpoints from ZDR eligibility after researchers showed prompts were exposed
([OpenRouter's post](https://openrouter.ai/blog/announcements/data-processing-incidents-on-inference-providers/)).
The eligibility flag is a label that changes over time, and the list has no
published history: a flag with no history is indistinguishable from one that
was wrong yesterday. This tool keeps the history.

The backfilled record reproduces the incident window from public Internet
Archive captures:

- MiniMax and Phala routes lost the label in the Nov 12, 2026 capture window
  (matches the sxz.io replay).
- Between the Nov 19 and Nov 21 captures, exactly **eight providers vanished
  from the list in full**: Novita (32 raw endpoints), SiliconFlow (38),
  Z.AI (14), Tencent (9), Seed (9), Fish Audio (7), Moonshot AI (4) and
  StepFun (3). Their raw endpoint total is **exactly 125**, OpenRouter's
  announced number. (zdrwatch merges dated `-YYYYMMDD` endpoint variants into
  routes, so it records 116 route removals for the same set; merging can only
  reduce a count, never inflate it. A further 13 same-window removals -
  Cloudflare 8, Sail Research 2, xAI 1, Liquid 1, Reka 1 - were partial and
  are not part of the announced 125.)
- See `docs/CHANGELOG.md` for the full event list.

## What a disappearance means (removal classification)

A route leaving the ZDR list is not always an eligibility revocation: the
model may have been retired, or the provider may have stopped serving it.
Every live check classifies each removal against OpenRouter's full catalog
(`/models` and per-model `/endpoints`, both keyless). Classification records
the catalog status observed at check time; it cannot prove why a flag
changed, and no classification here is a verdict on any provider's actual
data handling:

- `zdr_eligibility_lost` - the provider still serves the model; only the ZDR
  flag changed. The only case worded "lost ZDR eligibility".
- `endpoint_retired` - the provider no longer serves that model at all.
- `model_retired` - the model is gone from the catalog.
- `unknown` - classification unavailable. All backfilled (archive-derived)
  removals are `unknown` and are worded "dropped from the published ZDR
  list", never as eligibility losses.

Example from the first classified window (Nov 21 -> Nov 26, 2026): of 28
routes that left the list, 2 had the flag removed while the provider still
served the model at check time (two Ant Digital routes), 20 were no longer
served by that provider (the DekaLLM set), and 6 were models retired from
the catalog. None of these classifications, by itself, says anything about
what a provider did with data.

Route identity: a route is `Provider | author/model`, merging dated variant
suffixes. An audit of all archived captures and the live list found 57
routes where a provider lists a bare alias and a dated variant at the same
time (same route, two entries) and zero cases where merged variants differ
by anything other than the date suffix.

## What this is not (read before relying on it)

- It tracks changes in **OpenRouter's published flag only**. It cannot show
  whether any provider actually retains, reads or deletes your data.
- Changes are dated to the **window between two observations**, not an exact
  moment. The intended live cadence is hourly once CI is enabled (GitHub may
  delay scheduled runs); backfilled history uses Internet Archive captures at
  irregular times.
- Entries OpenRouter marks **Safety Retention** appear on the ZDR list but may
  retain data for safety reasons and are only used if the caller allows them;
  they are flagged `safety_retention` in the data. Captures from before the
  field existed carry `safety_retention: null` (unknown).
- Scope is **one router**. Other routers and direct provider APIs are not covered.
- Not affiliated with or endorsed by OpenRouter.

## How it works

- `python -m zdrwatch check` fetches the list, diffs it against the last
  snapshot and appends typed events (`route_added`, `route_removed`,
  `safety_retention_changed`, `variants_changed`) to `data/history/events.jsonl`.
  Dated variant swaps (e.g. `model-20251022` -> `model-20251101`) are reported
  as variant changes, not as eligibility loss.
- Continuous operation (free for public repos): an hourly `watch.yml`
  GitHub Actions workflow commits changes; the site is served from `docs/`
  on this branch, so every such commit redeploys it automatically.
  **Current state:** the workflow is not in the repo yet (the publishing
  token lacks the `workflow` scope GitHub requires for workflow files).
  Interim: a scheduled agent run performs `check` + push **once daily**,
  so the feed refreshes daily, not hourly. The hourly cadence starts once
  `watch.yml` lands (via token scope or a web-UI commit).
- `python -m zdrwatch backfill` seeds history from Internet Archive captures
  (already run; seeded events are committed).

## Alerts

Use the **route history lookup** on the site to answer "was provider X | model Y
on the ZDR list on a given date, and when did that change?" (searches
`routes.json`, covering every current route plus every route with recorded
changes). Subscribe to `feed.xml` in any feed reader (currently refreshed daily; see above), or point a Slack/Discord RSS
integration at it. You can also watch this repository; every eligibility
change lands as a commit.

## Development

Stdlib only. Tests need pytest:

```
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

## License

MIT
