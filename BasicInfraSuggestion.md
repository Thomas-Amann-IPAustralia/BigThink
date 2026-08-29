GitHub Actions — the thing that does the work. It's not a place, it just wakes up on a schedule, runs your Python, then switches off. Nothing survives after it finishes unless it's saved elsewhere.

Cloudflare R2 — a storage bucket for the big raw stuff (downloaded patent/publication data). Free up to 10GB, and unlike most storage it doesn't charge you to read the data back out — important since Actions will be re-reading it constantly.

DuckDB — not a separate service, just a tool your script uses while it's running inside Actions. It opens the files sitting in R2 and does the actual number-crunching (joins, scoring, clustering).

GitHub repo — where the small final answers (opportunity scores, evidence tables) get saved. Because it's git, you keep every past run and can see how a score changed over time.

GitHub Pages — turns whatever's in the repo into a webpage automatically, so you've got something to actually look at.