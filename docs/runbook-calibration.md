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
    method: "agglomerative"
    similarity_thresholds:
      agglomerative:
        hashing: 0.14    # raise for tighter topics
        bge: 0.45
      leader:
        hashing: 0.30
        bge: 0.62
```

A threshold belongs to a **method and a backend together**, and copying a value
across either axis is the single easiest way to break topic formation while
everything still appears to run.

- **Across backends**, because the backend sets a cosine's scale. Hashed TF-IDF
  puts two closely-related abstracts around 0.25-0.35; BGE puts the same pair
  above 0.8.
- **Across methods**, because the method decides what the cosine is *between*.
  `leader` compares a document to a cluster centroid — an average of many
  vectors, and so similar to almost anything. `agglomerative` compares the mean
  pairwise similarity between two clusters' members, which is far lower on
  identical data. Measured on 2,987 real OpenAlex documents under `hashing`:
  mean pairwise cosine 0.075, 99th percentile 0.191. At the leader value of
  0.30, average linkage assigned **23 of 2,987 documents**.

`python -m src.calibrate threshold` sweeps whichever method is configured, over
a range appropriate to it. Sweeping one method and configuring another produces
a number that looks calibrated and is not.

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

## Step 3 — Real embeddings and BERTopic (already the default)

Both switches were made on 2026-08-31; this step is here for the machine that
does not have them, and for what to do after changing either.

The `hashing` backend matches vocabulary. It does not know that "automated
decision making" and "algorithmic administrative decisions" are the same thing,
and strategic fit is exactly where that matters. `bge` is the shipped default,
along with `bertopic` clustering:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-ml.txt
```

Without those installed, run the cheap pairing explicitly rather than
discovering the ImportError at Stage 2:

```bash
python -m src.pipeline --embedding-backend hashing --clustering-method agglomerative
```

**After changing either, re-do Step 0.** The vector cache is keyed on backend,
so vectors are recomputed rather than mixed — but every threshold needs
revisiting, because the whole similarity scale has changed.

### Sweeping BERTopic

HDBSCAN takes no cosine cut-off, so under `bertopic` the Step 0 threshold sweep
does not apply to clustering. Two different sweeps replace it:

```bash
python -m src.calibrate bertopic --show-labels   # seeds and n_neighbors
python -m src.calibrate attachment               # the attachment threshold
```

Read the two halves of the `bertopic` output differently:

- **`n_neighbors`** genuinely decides what the topics are — it sets how much
  local versus global structure UMAP keeps. Choose it from the topic count, the
  assigned share, the size of the largest cluster, and the labels.
- **The seed spread is a diagnostic, not a leaderboard.** Every seed is equally
  defensible in advance, so a parameter set whose output swings between seeds is
  one whose topics are an artefact of the initialisation rather than of the
  corpus. Small spread is the result you want; then pick a seed and write it
  down. Never pick the seed that produced the prettiest shortlist — that is
  fitting the instrument to the answer.

`python -m src.calibrate attachment` sets
`similarity_thresholds.bertopic.<backend>`, which under `bertopic` is used for
one thing only: attaching GDELT documents to the nearest finished topic. Aim to
attach most of the attention corpus without attaching all of it — at 100% it
has stopped discriminating, and near 0% the Stage 4 attention component is
empty.

**Record the seed you chose in `emergence.topics.bertopic.random_state`, and
why, in `PROJECT_STATE.md`.** A run whose seed is not recorded cannot be
reproduced; a seed recorded without its sweep cannot be argued with.

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
