# Console metrics strip — live verification

Captured from the running stack on 2026-08-17 (ego-browser / Chromium,
app on `:8766`, post-`bcbd4a1` + escape/quantile fixes). The
recent-runs card's strip rendered:

| SAFE | REVIEW | BLOCK | FAIL | p50 | p95 |
|---|---|---|---|---|---|
| 2 | 0 | 0 | 0 | 10.00s | 10.00s |

- Verdict counters: `msb_v3_actiongate_decisions_total` verdict labels
  (allowed/indeterminate/denied/failed), parsed in-page from the public
  `/metrics/prometheus` scrape — no bearer header (token stays on gated
  calls).
- Latency: bucket-histogram quantile estimation; the sampled run (17.2s)
  exceeded the histogram's max bucket (10s), so both quantiles report the
  last-bucket bound as a lower-bound estimate rather than "—".
- Also verified live: `/metrics/prometheus` serves `text/plain` (real
  exposition format, pinned by `response_class=PlainTextResponse`).

See `metrics-strip-verified.txt` for the raw rendered text.
