# Runbook — calibrating the scores

Every weight in this pipeline is currently a considered judgement. None has
been validated against a known outcome. This runbook is how that changes.

Do the work in this order. Each step depends on the one before it.

---

## Step 0 — First, check the topics are real

No weight is worth tuning on top of bad topics.

```bash
python -m src.stage2_emergence --run-id $(date -u +%F) --top 25
```

Open the top ten evidence cards and read the documents. For each topic ask only:
**do these documents belong together?**

- Coherent theme → good.
- Mixed bag → clustering artefact. Note it.

If more than about a third are artefacts, stop and fix clustering before
touching anything else.

**Threshold too low** → few, enormous, incoherent topics.
**Threshold too high** → many tiny topics and most documents unassigned.

```yaml
emergence:
  topics:
    similarity_threshold_by_backend:
      hashing: 0.18      # raise for tighter topics
      bge: 0.62
```

Cosine values are not comparable across backends. Tune the entry for the
backend you are actually running, and never copy a value between them.

---

## Step 1 — The validation test that matters most

**Pick an opportunity IP Australia already pursued, and check whether this
pipeline would have surfaced it.**

Good candidates: the IP First Response pilot, AI patent analytics for the
Critical Technologies Hub, TM Checker, geographical indications work under the
A-EU FTA.

1. Set `collection.end_year` to the year *before* that work began.
2. Re-run the pipeline on that truncated window.
3. Look for the opportunity in the results.

**Where did it land?**

| Outcome | Reading | Action |
|---|---|---|
| Top 15 | The method works for this class of opportunity | Record it. This is your evidence the ranking is worth using |
| Ranked but low | Signal present, weights wrong | Find which axis under-scored it and adjust that one |
| No matching topic | The scan frame never looked there | Fix the frame, not the weights |
| Present but incoherent | Clustering problem | Back to Step 0 |

That fourth column is the point: **a miss caused by the scan frame cannot be
fixed by re-weighting**, and trying is the most common way to overfit a method
like this into uselessness.

Repeat with a second and third known opportunity before changing any weight.
One data point tunes to itself.

---

## Step 2 — Adjust one thing at a time

```bash
# Re-analyse without re-collecting — fast, and the corpus stays fixed
python -m src.pipeline --run-id calib-01 --skip-collect
```

Change one block, re-run, compare `data/outputs/*/topics.csv`. Changing two at
once tells you nothing about either.

### Rotolo weights — what emergence means

```yaml
emergence:
  rotolo_weights:
    novelty: 0.25
    growth: 0.30
    coherence: 0.15
    impact: 0.20
    uncertainty: 0.10
```

Must sum to 1.0 — config validation enforces it, because a bad sum silently
rescales every score.

- Established topics dominating → raise `novelty`, lower `impact`. Citation
  counts inherently favour the old.
- Too much noise at the top → raise `coherence`.
- Want earlier signals → raise `growth` and `uncertainty`, lower `impact`.

### Rank weights — what "opportunity" means

```yaml
synthesis:
  rank_weights:
    emergence: 0.40
    strategic_fit: 0.35
    asset_leverage: 0.25
```

This encodes a policy choice, not a technical one:

- **Raise `strategic_fit`** for a shortlist defensible against "how does this
  serve the Corporate Plan?" — and expect a more conservative list.
- **Raise `asset_leverage`** for things IP Australia could actually start.
- **Raise `emergence`** for a genuine horizon scan — and expect items with no
  obvious owner. That is the point of a horizon scan, and the main reason a
  shortlist gets dismissed in the room.

### Objective and asset weights

`data/strategy/objectives.yaml` and `asset_inventory.yaml` carry per-entry
weights. These are usually a better lever than the global rank weights, because
they express priority without changing what the score *means*.

---

## Step 3 — Switch to real embeddings before you trust the fit scores

The `hashing` backend matches vocabulary. It does not know that "automated
decision making" and "algorithmic administrative decisions" are the same thing,
and strategic fit is exactly where that matters.

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-ml.txt
```

```yaml
embeddings:
  backend: "bge"
```

Then re-run **and re-do Step 0**. The cache is keyed on backend, so vectors are
recomputed rather than mixed — but every threshold needs revisiting, because
the whole similarity scale has changed.

---

## Step 4 — Record what you did

In `PROJECT_STATE.md`, under "Calibration log":

- what you changed, from what to what
- which known opportunity you validated against
- what moved in the results
- what you concluded

**A weight change with no recorded reason is indistinguishable from a bug.**
Six months on, nobody — including you — will be able to tell whether 0.35 was
reasoned or inherited.

---

## Things that are not calibration problems

| Symptom | Actual cause |
|---|---|
| A whole area is missing | Scan frame. `data/strategy/scan_frame.yaml` |
| Every topic scores similar fit | Backend is `hashing`; go to Step 3 |
| Scores jump between runs | Corpus changed. Compare `documents` counts first |
| Opportunity index looks wrong | It is relative, within-run, and not a market size. See `docs/method.md` |
| A source has gone quiet | Check `collection_log`, not the weights |
