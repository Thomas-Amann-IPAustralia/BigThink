# Runbook — adding a data source

Two different tasks are often both called "adding a source". Do the right one.

- **Adding a *query*** to an existing source — you want the scan to look for
  something new. Edit `data/strategy/scan_frame.yaml`. No code.
- **Adding a *source*** — a new API. New collector module. Read on.

---

## Adding a query (the common case)

Add a frame to `data/strategy/scan_frame.yaml`:

```yaml
  - key: "ip_licensing_markets"          # stable; recorded on every document
    label: "IP licensing and technology transfer markets"
    steepv: "Economic"                   # must be a valid STEEPV category
    rationale: >-                        # why this is in the frame at all
      Serves Strategic Objective 2.1. Registry data gives IP Australia a
      view of licensing activity that no commercial actor holds.
    queries:
      openalex: "technology transfer OR patent licensing market"
      crossref: "patent licensing technology transfer"
      gdelt: "patent licensing AND (market OR revenue)"
```

Then:

```bash
python -c "
from src.config import load_config
from src.stage1_collect import load_scan_frame
print(len(load_scan_frame(load_config())), 'frames OK')"

python -m src.stage1_collect --run-id $(date -u +%F) --frames ip_licensing_markets --sample
```

Check what came back before committing — a query returning off-topic documents
pollutes every topic it touches:

```sql
SELECT title FROM documents WHERE scan_frame_key = 'ip_licensing_markets' LIMIT 20;
```

If relevance is poor, tighten the query or raise `min_relative_score` for that
source in `bigthink_config.yaml`.

**Adding a frame changes the corpus, which changes every topic and every
score.** Results before and after are not comparable. Note the change and the
date in `PROJECT_STATE.md`.

---

## Adding a new API source

### 1. Check it is worth it

- Free, or free at the volume you need? Check for metering, not just for the
  absence of a key — OpenAlex is keyless *and* metered.
- Does it carry a usable date per record? Without one it cannot contribute to
  emergence detection at all.
- Does it add a signal the existing six do not already carry?

### 2. Write the collector

Create `src/collectors/<name>.py`. Subclass `Collector`, implement `collect`,
decorate with `@register`:

```python
from src.collectors.base import Collector, build_document, register

@register
class MySourceCollector(Collector):
    name = "mysource"          # must match the config key exactly
    request_delay = 1.0        # seconds between requests; honour their terms

    def collect(self, query, frame, start_year, end_year):
        payload = self.fetch_json(API_URL, {"q": query})
        self.save_raw(str(frame.get("key", "query")), 0, payload)
        for item in payload.get("results", []):
            yield build_document(
                source=self.name,
                native_id=str(item["id"]),          # must be stable across runs
                title=item.get("title", ""),
                abstract=item.get("summary", ""),
                published=item.get("date"),
                steepv=self.steepv_for(frame),
                scan_frame_key=str(frame.get("key", "")),
                run_id=self.run_id,
                time_granularity=self.time_granularity,
            )
```

The base class handles HTTP, retry, rate limiting, error classification and raw
payload persistence. Do not reimplement them.

Rules that matter:

- **Raise, don't return, on failure.** `RetryableError` for 429/5xx and
  timeouts; `PermanentError` for anything a retry cannot fix. `fetch_json`
  already classifies HTTP status codes correctly. A collector that swallows
  errors and returns an empty list produces a silent scan.
- **`native_id` must be stable across runs.** Deduplication depends on it. A
  URL with a session token is not stable.
- **Respect their rate limits in `request_delay`,** not by hoping.

### 3. Register it in three places

```python
# src/collectors/__init__.py
from src.collectors import mysource  # noqa: F401
```

```python
# src/config.py
_KNOWN_SOURCES = {..., "mysource"}
```

```yaml
# bigthink_config.yaml
collection:
  sources:
    mysource:
      enabled: true
      rows_per_query: 100
  steepv_default_by_source:
    mysource: "Technological"
```

If the source is one of research / attention / patents, also add it to the
matching set in `src/stage4_opportunity_index.py` so it feeds the right index
component.

### 4. Test it offline, then live

Add parsing tests to `tests/test_collectors.py` — **no network calls in
tests**; the suite must pass with the machine offline. Then:

```bash
python -m pytest tests/ -q
python -m src.stage1_collect --run-id smoke --sources mysource --sample
```

### 5. Document it

Add a row to the source table in `docs/method.md`, and record the addition in
`PROJECT_STATE.md` — including what it is good for and what its lag is.
