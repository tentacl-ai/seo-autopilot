# SEO Autopilot

[![CI](https://github.com/tentacl-ai/seo-autopilot/actions/workflows/ci.yml/badge.svg)](https://github.com/tentacl-ai/seo-autopilot/actions/workflows/ci.yml)
[![GitHub License: MIT](https://img.shields.io/badge/license-MIT-green)](#license)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.110-green)](https://fastapi.tiangolo.com/)
[![Async SQLAlchemy](https://img.shields.io/badge/sqlalchemy-2.0-orange)](https://www.sqlalchemy.org/)

**Multi-tenant SEO automation that closes the loop:** crawl → find → fix → measure → report.

> Version 1.16.0 · 1196 tests · 18 analysis dimensions · 136 documented issue types

Most SEO tools stop at a list of warnings. SEO Autopilot repairs what it finds,
writes the change into the site's repository, and then uses Search Console to
prove — 7, 14, 28 and 56 days later — whether the change actually helped.

---

## What It Does

1. **Crawls your site** — httpx + BeautifulSoup, Playwright fallback for
   JavaScript-rendered pages (rendered at phone size, 412×915, mobile-first like
   Google). The crawler follows its own host only.
2. **Pulls real data** — Google Search Console (28-day window plus a 16-month
   archive), Google Analytics 4, PageSpeed Insights / CrUX, robots.txt, Bing.
3. **Analyzes 17 dimensions** — on-page, canonical, redirects, hreflang, schema,
   links, images, duplicates, topical authority, Core Web Vitals, security, GEO.
4. **Scores by cause, not by count** — one root cause per URL counts once,
   diminishing returns per issue type, recommendations capped (see
   [Scoring](#scoring)).
5. **Fixes automatically** — metadata and markup on every project; visible page
   text only where the project is explicitly set to `betriebsart: autopilot`.
   Everything else goes into an approval queue with a plain-language reason.
6. **Recommends what to write** — FAQ blocks, missing sections, headings,
   internal links, answer-first intros, new pages — derived **only** from real
   Search Console demand ([details](#recommendations--auto-writing)).
7. **Measures the effect** — every change is compared against the equivalent
   window before it, with five guard rails that prefer "no verdict" over a
   flattering one.
8. **Reports weekly** — one customer report per site, identical structure for
   every site, delivered by mail.
9. **Watches itself** — `selfcheck` fails loudly when the tool itself is broken
   (missing Pillow, missing Playwright browser, missing PageSpeed key, stale
   approval queue, missing cron).
10. **Watches the market** — daily radar over search/ads/AI-search sources, with
    every AI-reported item discarded unless its source URL actually resolves.

Lightweight stack: httpx / BeautifulSoup / FastAPI / SQLAlchemy. SQLite by
default, PostgreSQL supported.

---

## Installation

### From Source

```bash
git clone https://github.com/tentacl-ai/seo-autopilot.git
cd seo-autopilot
python3 -m venv venv
venv/bin/pip install -e .

# Optional: dev dependencies
venv/bin/pip install -e ".[dev]"
```

Optional components, each of which the watchdog will report as missing:

```bash
venv/bin/pip install pillow feedparser playwright
PLAYWRIGHT_BROWSERS_PATH=./.browsers venv/bin/python -m playwright install chromium
```

`PLAYWRIGHT_BROWSERS_PATH` matters when audits run from a system cron under a
different user — otherwise the browser is installed into a home directory that
the cron job cannot see.

### Docker

```bash
docker build -t seo-autopilot .
docker run -p 8002:8002 \
  -e DATABASE_URL="sqlite+aiosqlite:///seo.db" \
  -e CLAUDE_API_KEY="sk-ant-..." \
  seo-autopilot
```

---

## Quickstart

### 1. Set up a site — the "full package"

Do not hand-write `projects.yaml` for a new site. The `einrichten` command
checks the site, finds the Search Console and GA4 properties, tests them, and
prints a complete project block:

```bash
venv/bin/python -m seo_autopilot.cli.main einrichten \
    --projekt kunde-beispiel --domain https://kunde-beispiel.de \
    --bericht-an reports@example.com

# looks good? write it (a backup of projects.yaml is placed next to it)
venv/bin/python -m seo_autopilot.cli.main einrichten \
    --projekt kunde-beispiel --domain https://kunde-beispiel.de \
    --bericht-an reports@example.com --schreiben
```

Step-by-step guide: [docs/einrichtung.md](docs/einrichtung.md) (German).
Full operator manual: [docs/handbuch.md](docs/handbuch.md) (German).

### 2. Set environment variables

```bash
export CLAUDE_API_KEY="sk-ant-..."          # falls back to ANTHROPIC_API_KEY
export PAGESPEED_API_KEY="AIzaSy..."        # strongly recommended, see below
export DATABASE_URL="sqlite+aiosqlite:///seo_autopilot.db"
```

### 3. Run

```bash
venv/bin/python -m seo_autopilot.cli.main config list
venv/bin/python -m seo_autopilot.cli.main run --project-id kunde-beispiel
```

`run` exits **1** when an audit fails; a failed run is stored with status
`failed` so the watchdog reports it. A green exit code means the audit really
completed.

### 4. Look at the result

```bash
venv/bin/python -m seo_autopilot.cli.main api    # FastAPI on http://localhost:8002
```

Or open `reports/latest.html`.

---

## Analysis Dimensions

17 analyzer modules live in `seo_autopilot/analyzers/`:

| Module | What it checks |
|---|---|
| `canonical_engine` | Canonical resolution across HTTP header, HTML `<link>`, sitemap and internal links; chains, loops and conflicts with hreflang/noindex |
| `redirect_audit` | Redirect chains, loops, 302 where 301 belongs, cross-domain hops, soft-404, 5xx clusters |
| `robots_sitemap` | robots.txt (AI-crawler blocks, CSS/JS blocks, missing sitemap directive, overly broad disallow) and sitemap health (3xx/4xx entries, stale lastmod, foreign hosts) |
| `schema_validation` | JSON-LD syntax plus required fields per schema type, and which pages could unlock which rich result |
| `seiten_checks` | Utility pages (`/login`, `/dashboard`, `/warenkorb`) indexable without `noindex`, duplicate titles and meta descriptions, mixed content, skipped heading levels, missing LocalBusiness schema where an address or phone link is shown |
| `link_check` | Broken internal links — including targets outside the crawl limit and unreplaced template placeholders — and catch-all error pages that answer HTTP 200 with the homepage for every wrong URL |
| `link_graph` | Internal link graph: orphan pages, click depth, broken links, link-equity sinks, PageRank distribution (own implementation, no networkx) |
| `hreflang_audit` | Is the hreflang target reachable, does it link back, does the declared language match the actual content, is there an `x-default` |
| `duplicate_content` | SimHash near-duplicates (canonical-aware, confirmed by real word overlap before reporting), thin content, keyword cannibalization |
| `topical_authority` | Topic clusters from URL paths and title overlap, pillar pages, coverage gaps from Search Console, cluster cannibalization |
| `image_audit` | Missing `alt`, missing `width`/`height`, oversized files, lazy loading or missing priority on the LCP image — all without an external API, so it keeps working when PageSpeed quota runs out |
| `bild_variante` | Which image file a phone actually downloads: evaluates `<picture>`, `srcset` and `sizes` against a fixed reference device (412 CSS px, DPR 2.625, like Lighthouse "mobile") instead of measuring the largest variant in `src` |
| `lcp_abgleich` | Reconciles guessed LCP image findings against the measured LCP element (PageSpeed, or Chrome via Playwright); measurement beats assumption, and in doubt severity is lowered |
| `eeat` | Machine-verifiable trust signals: legal pages, Organization schema with `sameAs`, author schema with dates, reachable contact page |
| `geo_audit` | Structural readiness for AI citations: answer-first intros, question headings, fact density, entity clarity |
| `llms_ai_txt` | `llms.txt`, `llms-full.txt`, `ai.txt` and the IndexNow key file |
| `delta` | Audit-over-audit comparison: new issues, resolved issues, score movement, CWV and GEO trends |

Core Web Vitals (INP, LCP, CLS — field data from CrUX plus lab data) and
security headers are checked in the analyzer agent itself.

`geo_audit` and `llms_ai_txt` findings are classed as `hinweis` — see below.

---

## Scoring

The score (0–100) answers "how much is actually broken", not "how many lines
could we print". Three rules keep it honest:

**1. Every finding has exactly one kind** (`seo_autopilot/befund_arten.py`):

| Kind | Meaning | Counts in the score? | Auto-fixed? |
|---|---|---|---|
| `fehler` | Technically unambiguous and verifiable on the live site (404, missing title, `noindex`, bad measured value) | yes, at full weight | yes, where safe |
| `empfehlung` | Rule of thumb, taste, or an opportunity (text length, missing social tag, keyword with potential) | yes, at one third weight, **max. 10 points total** | only after approval |
| `hinweis` | Visible, but deliberately without consequence — see the next section | **no** | **no** |

Unknown types default to `fehler`: a real defect must never disappear into the
recommendation corner.

**2. One cause per URL counts once.** Findings from the same cause family at the
same address (for example "unreachable" and "orphan") collapse into the most
severe one. All of them stay visible in the report; only the arithmetic changes.

**3. Diminishing returns.** Findings are grouped by (type, severity) and
normalised to the number of pages (`f = 15 / pages`). Above one normalised
occurrence the group grows logarithmically, so one missing navigation that
produces 17 identical findings costs roughly what five would, not seventeen.

```
score = 100 − min(50, high) − min(30, medium) − min(20, low) − min(10, recommendations / 3)
```

Scores from v1.15 and earlier are **not** comparable with current ones. The
websites did not change; the arithmetic did.

### What does NOT move the needle

Google's own guidance —
["Optimizing your website for generative AI features on Google Search"](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide),
Search Central, as of 2026-07-10 — states that the following have no effect on
(AI) search:

- `llms.txt`, `ai.txt` and comparable "AI files"
- "content chunking"
- rewriting text specifically for AI systems
- manufacturing brand mentions
- structured data as an AI lever (it earns rich results, nothing more)

SEO Autopilot therefore classes these findings as `hinweis`: they remain
**visible** in the report, but they cost **no points**, never reach the work
list, are never fixed automatically and are never put up for approval. There is
also no invented "GEO score" driving decisions, and no generic `WebPage`
JSON-LD is written any more.

This is a deliberate refusal to sell a lever that does not exist.

---

## Recommendations & auto-writing

`empfehlungen.py` produces concrete, per-page suggestions in German:

| Kind | When |
|---|---|
| `faq_ergaenzen` | Real questions with impressions that the page does not answer |
| `abschnitt_ergaenzen` | A topic with impressions the page barely covers |
| `ueberschrift_verbessern` | The H1 does not name the main search term |
| `interne_links` | Position 8–20: which of your own pages to link, with which anchor |
| `antwort_zuerst` | The first paragraph does not answer the main question |
| `neue_seite` | Search terms with no matching page at all (project-wide) |
| `titel_beschreibung` | Good position, weak click-through — handed to the repair module |

**Only from real demand.** The input is Search Console query × page over 90
days plus the actual page content. Without search data there is no
recommendation, and "for AI/GEO" is never a justification.

`empfehlungen_umsetzen.py` turns them into text on the site. Guard rails, all
mandatory and each individually tested:

1. Facts only from the website itself; claims are re-checked against the
   **current** page before writing.
2. Placeholders never go live; unanswered questions are dropped.
3. Forbidden words, brand rules and form of address from
   `adapter_config.seo_regeln`.
4. Blocked pages: `seo_regeln.gesperrte_seiten` plus always the legal pages
   (imprint, privacy policy, terms).
5. At most **3 text changes per run** and **1 per page per week**.
6. A separate second AI review pass ("is every statement true, is it helpful,
   does it sound natural, is this keyword stuffing?"). Only "yes" gets written.
7. Language and formal/informal address of the page are preserved.

Every change becomes its own Git commit, lands in the change log, and is
measured after 7/14/28/56 days. Visible text is written automatically **only**
under `betriebsart: autopilot`; under `copilot` a recommendation is written only
after it has been approved.

The AI never produces HTML or JSON-LD — those fragments are built by code.

---

## Operating modes and the hard block list

| Mode | Behaviour |
|---|---|
| `beobachter` | Analyses only, changes nothing (default for new projects) |
| `copilot` | Prepares every change and puts it up for approval |
| `autopilot` | Applies safe changes itself, still submits everything else |

A typo in the configuration always falls to the safe side.

Fourteen interventions are blocked **in code** and never run automatically — not
in autopilot mode, and not if someone adds them to `whitelist_extra`:
`noindex`, canonical changes, `robots.txt`, deleting pages, merging pages, URL
migrations and redirect-chain rewrites. Each carries its reason in plain
language.

```bash
venv/bin/python -m seo_autopilot.cli.main betrieb      # what may each project do?
venv/bin/python -m seo_autopilot.cli.main freigabe     # open approvals
venv/bin/python -m seo_autopilot.cli.main freigabe --ja 3f2a91c4 --notiz "checked"
```

---

## Identity guard

A project can declare what its own homepage must look like:

```yaml
kunde-beispiel:
  domain: https://kunde-beispiel.de
  erwartet: "Kunde Beispiel GmbH"     # or a list of strings
```

At least one of those strings must appear on the homepage — in the title, H1,
`og:site_name`, a schema name or the visible text. If it does not, the audit
aborts **before any analysis and before any auto-fix** (status `failed`, exit
code 1). If the homepage cannot be fetched at all, identity counts as *not*
confirmed, and nothing is changed.

Without `erwartet` nothing changes. The field exists because an audit once ran
against a completely different site that happened to answer on the configured
address.

---

## Effect measurement

`wirkung.py` compares, per change, the window **before** against the window
**after** — 7, 14, 28 and 56 days of Search Console data for exactly that URL.
Several windows, because a title often lands within a week while content work
takes weeks.

Five guard rails prefer no verdict over a bad one:

1. Too little data in the "before" window → stored, but flagged
   `zu_wenig_daten`, no verdict.
2. Contradicting signals (better position but fewer impressions *and* fewer
   clicks) are not a success.
3. Foreign changes (a human editing the same page) are tracked separately in the
   change log so their effect is never credited to the autopilot.
4. Changes to one page on the same day are measured as one package.
5. Projects that can never be measured (no Search Console) do not raise a
   permanent alarm.

```bash
venv/bin/python -m seo_autopilot.cli.main wirkung --messen
venv/bin/python -m seo_autopilot.cli.main wirkung --bilanz   # hit rate per kind of change
```

---

## Weekly customer report

One report per site, identical structure everywhere, delivered by mail:

1. **Wo wir stehen** — headline status
2. Notable items, pulled to the top from every section below
3. Decisions with a button (market impulses and open approvals)
4. Google search: week vs. previous week, queries, pages
5. Visits by source (GA4)
6. New in the market (radar), related to this specific site
7. AI visibility (ChatGPT, Gemini, Claude with web search)
8. Site check: last audit, score and trend, the points that matter
9. Bing / IndexNow status
10. Site-specific extras (`bericht.extras`)
11. State of the tool itself

A dead source never prevents the report; it is reported as dead.

```bash
venv/bin/python -m seo_autopilot.cli.main kundenbericht --projekt kunde-beispiel --trocken
venv/bin/python -m seo_autopilot.cli.main kundenbericht --senden
```

---

## Market radar

Daily. Two paths into the same `markt_meldungen` table:

- **Trade sources (RSS)**: Google Search Central, Google Ads, Google Analytics,
  Bing Webmaster, Microsoft Ads, Search Engine Land/Journal, PPC Land, SE
  Roundtable and others.
- **AI scouts**: ChatGPT and Gemini research recent changes *with web search*.

AI systems invent sources. The rule is therefore hard: **no reachable source
URL, no item.** Every URL named is fetched; anything that does not answer below
HTTP 400 is dropped, and homepages or redirect services do not count as a
source. The source link always comes from the item, never from the AI's answer.

---

## 16-month Search Console archive

Google hands out at most 16 months of Search Console data. A month not fetched
today is lost forever. `historie.py` is therefore built as an **archive**, not a
query: once imported, a month stays in the local database even when Google no
longer knows it.

Five locks:

1. A query error is never stored as zero — the month stays open and is retried.
   A partial failure stores nothing either.
2. The current month counts as incomplete and drops out of every comparison.
3. Completed months are not re-fetched, except inside a 5-day catch-up window
   (Search Console lags roughly three days).
4. Comparisons need a minimum of 30 impressions.
5. No year-over-year comparison against a site that did not exist yet (100
   impressions needed in the prior-year window).

```bash
venv/bin/python -m seo_autopilot.cli.main historie --importieren
venv/bin/python -m seo_autopilot.cli.main historie --projekt kunde-beispiel
venv/bin/python -m seo_autopilot.cli.main historie --export historie.csv
```

---

## Watchdog

A tool that does not notice its own failure is worthless. `selfcheck` checks the
tool, not the websites:

- Missing Pillow, Playwright, feedparser or Playwright browser → **critical**
  (image work and JS rendering silently do nothing without them)
- Missing PageSpeed key → warning (the shared Google quota is permanently
  exhausted; without a key you never get Core Web Vitals)
- Projects without a cron entry, audits that have not run, broken persistence
- Approval queue: proposals older than 14 days, open proposals for disabled
  projects
- Incomplete setup package (unless the project is marked `paket: klein`)
- Missing months in the Search Console archive

```bash
venv/bin/python -m seo_autopilot.cli.main selfcheck
# exit 0 = healthy, 1 = warnings, 2 = critical
```

---

## CLI reference

| Command | Purpose |
|---|---|
| `run` | Run the audit pipeline (`--project-id`, `--auto-fix`); exit 1 on failure |
| `einrichten` | Set up or verify a site's full package (`--projekt`, `--domain`, `--schreiben`, `--pruefen`) |
| `config list` / `add` / `remove` | Manage projects in `projects.yaml` |
| `betrieb` | Show the operating mode of every project |
| `freigabe` | Approval queue (`--ja`, `--nein`, `--notiz`, `--alle-ablehnen`) |
| `empfehlungen` | Per-page recommendations (`--erzeugen`, `--umsetzen`, `--trocken`, `--freigeben`, `--stand`) |
| `chancen` | Opportunity engine: what to start with, ranked by business value |
| `wert` | Business value per page; never estimated when the input is missing |
| `wirkung` | Effect measurement (`--messen`, `--fenster`, `--bilanz`) |
| `changes` | Change log — own and foreign changes, with `--diff` |
| `historie` | 16-month Search Console archive (`--importieren`, `--export`) |
| `kundenbericht` | Weekly customer report (`--senden`, `--trocken`, `--ohne-ki`, `--html`) |
| `weekly` | Short cross-project weekly summary |
| `marktradar` | Market radar (`--sammeln`, `--ohne-ki`, `--tage`) |
| `radar` | Policy radar: new Google/AI search guidelines and what they touch |
| `wettbewerb` | Competitor comparison with our own crawler, obeying their robots.txt |
| `learnings` | Recurring false positives — if a type shows up across projects, the rule is broken |
| `selfcheck` | Watchdog (`--notify`); exit 0/1/2 |
| `api` | Start the FastAPI REST API |
| `version` | Show version |

Every command takes `--help`.

### Example crontab

Audits run before the archive import (11:15) and the watchdog (11:30).

```cron
# Audits, one slot per site
 0  7 * * * /srv/seo-autopilot/venv/bin/python3 -m seo_autopilot.cli.main run --project-id kunde-beispiel  >> /srv/seo-autopilot/logs/cron.log 2>&1
30  7 * * * /srv/seo-autopilot/venv/bin/python3 -m seo_autopilot.cli.main run --project-id zweite-website  >> /srv/seo-autopilot/logs/cron.log 2>&1

# Recommendations: create and apply
15  9 * * * /srv/seo-autopilot/venv/bin/python3 -m seo_autopilot.cli.main empfehlungen --erzeugen --umsetzen >> /srv/seo-autopilot/logs/cron.log 2>&1

# Market radar
30  6 * * * /srv/seo-autopilot/venv/bin/python3 -m seo_autopilot.cli.main marktradar --sammeln >> /srv/seo-autopilot/logs/cron.log 2>&1

# Search Console archive, then watchdog
15 11 * * * /srv/seo-autopilot/venv/bin/python3 -m seo_autopilot.cli.main historie --importieren >> /srv/seo-autopilot/logs/cron.log 2>&1
30 11 * * * /srv/seo-autopilot/venv/bin/python3 -m seo_autopilot.cli.main selfcheck --notify >> /srv/seo-autopilot/logs/cron.log 2>&1

# Weekly customer report, Monday 07:40
40  7 * * 1 /srv/seo-autopilot/venv/bin/python3 -m seo_autopilot.cli.main kundenbericht --senden >> /srv/seo-autopilot/logs/cron.log 2>&1
```

Cron jobs run without `cd`, so `.env` is read from an absolute path inside the
installation directory. Set up log rotation for `logs/cron.log`.

---

## Configuration

### Environment variables (`.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLite by default; `postgresql+asyncpg://…` for PostgreSQL |
| `CLAUDE_API_KEY` | AI access; falls back to `ANTHROPIC_API_KEY` |
| `CLAUDE_MODEL` | Model name (default `claude-opus-5`) |
| `GEMINI_API_KEY` | Second AI, used by the market radar |
| `PAGESPEED_API_KEY` | **Recommended.** Without a key the shared Google quota answers HTTP 429 and you never get Core Web Vitals |
| `GSC_CREDENTIALS_PATH` | Default service-account file for Search Console |
| `PROJECT_CONFIG_PATH` | Path to `projects.yaml` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Optional notifications |
| `API_HOST`, `API_PORT`, `API_SECRET_KEY`, `CORS_ORIGINS` | REST API |
| `LOG_LEVEL`, `LOG_FILE` | Logging |
| `SENTRY_DSN` | Optional error tracking |
| `SEO_MAILER_PFAD` → `MAILER_PFAD` | Path to your own mail sender (empty = mailing disabled) |
| `SEO_MELDUNGS_EMPFAENGER` → `MELDUNGS_EMPFAENGER` | Explicit recipient for watchdog alerts (empty or placeholder domain = mailing disabled) |
| `SEO_INDEXNOW_SITES` → `INDEXNOW_SITES` | Path to your IndexNow site list |
| `SEO_BING_STATE` → `BING_STATE` | Path to the Bing Webmaster state file |
| `SEO_ENTSCHEIDUNGEN_ORDNER` → `ENTSCHEIDUNGEN_ORDNER` | Where the decision pages (report buttons) are written |
| `SEO_CRON_ENV_DATEIEN` → `CRON_ENV_DATEIEN` | Comma-separated env files to source in generated cron lines |
| `SEO_SECRETS_DATEI` → `SECRETS_DATEI` | Path to your secret store |

The last six deliberately hold **paths into your own environment**, not values —
this repository is public, so nothing environment-specific belongs in the code.
An empty value disables the corresponding feature.

### `projects.yaml`

```yaml
projects:
  kunde-beispiel:
    domain: https://kunde-beispiel.de
    name: Kunde Beispiel
    tenant_id: kunde-beispiel
    enabled: true

    # Identity guard: must appear on the homepage, or the audit aborts
    erwartet: "Kunde Beispiel GmbH"

    # beobachter (default) | copilot | autopilot
    betriebsart: copilot
    # gross (default) | klein — "klein" silences the watchdog about GA4/report
    paket: gross

    # How the site is reached for repairs
    adapter_type: static              # static | wordpress | fastapi | generic
    adapter_config:
      root_path: /var/www/meine-website/dist
      max_pages: 60
      standard_og_bild: /bilder/og.jpg     # the ONLY image allowed for a missing og:image
      seo_regeln:
        verbotene_woerter: ["Marktführer", "einzigartig"]
        gesperrte_seiten: ["/preise", "/kontakt"]   # legal pages are always blocked
        hinweise: ["Marke immer klein schreiben"]

    enabled_sources: [gsc, ga4, pagespeed]
    source_config:
      gsc:
        property_url: sc-domain:kunde-beispiel.de
        credentials_path: credentials/service-account.json
      ga4:
        property_id: "123456789"
        credentials_path: credentials/service-account.json
      indexnow:
        key: 0123456789abcdef0123456789abcdef
      empfehlungen:
        max_seiten: 6                 # AI calls per project and run

    # Weekly customer report
    bericht:
      aktiv: true
      empfaenger: reports@example.com
      branche: "Handwerksbetrieb in Oberbayern"
      ki_fragen: konfig/ki-fragen/kunde-beispiel.json
      extras: /srv/seo-autopilot/lokal/kunde_extras.py   # optional, function abschnitte()

    # Business value per goal (used by `wert` and `chancen`)
    geschaeftswert:
      anfrage: 250

    schedule_cron: "0 7 * * *"
    run_interval_days: 1
    auto_fix_enabled: true
    auto_fix_config:
      whitelist_extra: []             # cannot override the hard block list
    notifications_enabled: false
    notify_channels: []
    notify_config: {}
```

`projects.yaml` and `.env` are git-ignored. A template lives in
[`projects.yaml.example`](projects.yaml.example).

Unknown fields are skipped with a warning and errors are isolated per project —
one bad entry no longer makes every project silently disappear.

---

## Architecture

```
seo_autopilot/
├── core/                    Settings, project manager, scheduler, event bus, audit context
├── sources/                 crawler, renderer (Playwright), gsc, ga4, pagespeed, intelligence
├── analyzers/               17 rule-based analysis modules (see above)
├── agents/                  analyzer, keyword, strategy, content (AI fixes), apply
├── adapters/                static_files, wordpress, … — the only place that writes
├── db/                      SQLAlchemy models, async engine, persistence
├── reports/                 Jinja2 HTML report
├── notifications/           mail, telegram
├── befund_arten.py          finding kinds and cause families
├── note.py                  the score
├── handwerker.py            page context, plausibility check, file mapping
├── empfehlungen.py          per-page recommendations from real search demand
├── empfehlungen_umsetzen.py writing visible text, with guard rails
├── ausfuehrung.py           operating modes, hard block list, approval queue
├── einrichtung.py           the "full package" setup command
├── identitaet.py            identity guard
├── historie.py              16-month Search Console archive
├── wirkung.py               effect measurement
├── changelog_book.py        change log (own and foreign changes)
├── kundenbericht.py         weekly customer report
├── marktradar.py            market radar
├── health.py                watchdog
├── api/main.py              FastAPI REST + WebSocket
├── cli/main.py              Click CLI
└── mcp/server.py            MCP server (experimental, unmaintained)
```

---

## REST API

```bash
curl http://localhost:8002/api/health      # { "status": "ok", "version": "1.16.0" }
curl http://localhost:8002/api/projects
curl -X POST http://localhost:8002/api/audits/run/kunde-beispiel
curl http://localhost:8002/api/audits/<audit_id>/results
wscat -c ws://localhost:8002/api/ws/events/kunde-beispiel
```

Swagger UI: `http://localhost:8002/docs`.

---

## MCP server (experimental, currently unmaintained)

`seo_autopilot/mcp/server.py` was written to expose the audit pipeline to Claude
via the Model Context Protocol. It is **not** a supported feature today:

- the `mcp` package is not installed and not in `requirements.txt`
- the wrapper no longer matches the current agent signatures

Treat it as a starting point for a contribution, not as something that runs.

---

## Limits

Deliberately not covered:

- **No own backlink index.** Building one means crawling half the web. Instead,
  `backlinks.py` reads the free monthly Common Crawl domain graph: which websites
  link to you (domain level only, no anchor texts; small or new sites are often missing).
- **No third-party rankings.** Scraping Google's result pages violates their
  terms of service. Your own positions come from Search Console, which is more
  accurate than any estimate.
- **Local SEO only through the official Places API.** `maps.py` reads the Google Maps
  listing (stars, reviews, category, website) and your position in the Maps search per
  term and location, weekly, within Google's free monthly quota. No review replies, no
  profile editing (that needs the Business Profile API and the owner's consent).
- **DataForSEO is built but switched off.** `sources/dataforseo.py` works and the
  setup guide is in [docs/dataforseo-setup.md](docs/dataforseo-setup.md), but it
  is not enabled in any project and is not part of the standard setup.
- **JavaScript rendering only with Playwright** installed, including its browser.
- Conversion and revenue attribution needs values you enter yourself
  (`geschaeftswert`); nothing is estimated.

---

## Testing

```bash
venv/bin/python -m pytest tests/ -v
venv/bin/python -m pytest --cov=seo_autopilot tests/
bash scripts/check-sync.sh      # version, changelog, module count, test count, black
```

`check-sync.sh` runs in the pre-commit hook and in CI. It fails when the numbers
in this README drift away from the code.

The test suite follows one rule: every guard rail is first proven **red** against
the old code before the fix is written. A test that was green from the start
proves nothing about the bug it claims to cover.

---

## Security

- Secrets live in `.env` or a secret store, never in code. `projects.yaml`,
  `.env` and `credentials/` are git-ignored.
- `httpx` request logging is pinned to WARNING — it logs full URLs at INFO, and
  bot tokens live in URLs.
- Service accounts get read-only roles ("Restricted" in Search Console,
  "Viewer" in GA4).
- Tenant isolation: all queries filtered by `tenant_id`.
- Foreign `robots.txt` is read and obeyed by the competitor crawler.

---

## Documentation

| Document | Content |
|---|---|
| [docs/handbuch.md](docs/handbuch.md) | Operator manual (German) — commands, reports, scores, errors, rollback, costs, limits |
| [docs/einrichtung.md](docs/einrichtung.md) | Setting up a site, step by step (German) |
| [docs/ga4-setup.md](docs/ga4-setup.md) | Connecting Google Analytics 4 (German) |
| [docs/konzept.md](docs/konzept.md) | The product concept and what is built vs. open (German) |
| [docs/dataforseo-setup.md](docs/dataforseo-setup.md) | Optional DataForSEO connection — off by default (German) |
| [CHANGELOG.md](CHANGELOG.md) | Full history |

---

## Contributing

1. Fork the repo
2. Create a feature branch
3. Make your changes **plus tests** — prove the test red first
4. `venv/bin/python -m pytest tests/` and `bash scripts/check-sync.sh`
5. Open a pull request

```bash
black seo_autopilot/
flake8 seo_autopilot/
mypy seo_autopilot/
```

---

## License

MIT License – see [LICENSE](LICENSE).

---

## Support

- **Issues / Discussions:** on this repo
- **Email:** hello@tentacl.ai

---

## Why Open Source?

This tool runs production SEO work at [tentacl.ai](https://tentacl.ai). We opened
it up so the method is inspectable: what is measured, what is deliberately *not*
measured, and how a claimed improvement is proven.

Built by [tentacl.ai](https://tentacl.ai).
