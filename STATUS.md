# SEO Autopilot – Project Status

**Status:** 🟢 Phase 3 Complete – End-to-end functional, real data, persistable, reportable, containerised.
**Version:** 0.3.0
**Last Updated:** 2026-04-12

---

## Overview

SEO Autopilot is a multi-tenant, API-first SEO automation platform:
- Manage multiple customer websites from a single instance
- Fully automated weekly SEO audits via APScheduler
- Real HTTP crawling + HTML parsing + real Google Search Console data
- 4 sequential agents analyze the site and produce prioritized actions
- Results are persisted to SQLite/PostgreSQL
- HTML reports are generated per audit
- Telegram notifications with summary on every run

---

## Project Structure

```
/opt/odoo/docs/seo-autopilot/
├── seo_autopilot/
│   ├── core/
│   │   ├── config.py               Pydantic settings (.env)
│   │   ├── project_manager.py      YAML-based multi-tenant CRUD
│   │   ├── scheduler.py            APScheduler wrapper
│   │   ├── event_bus.py            Pub/Sub for real-time events
│   │   └── audit_context.py        Shared state between agents
│   ├── sources/
│   │   ├── base.py                 Abstract DataSource
│   │   ├── crawler.py              Real httpx + BeautifulSoup crawler
│   │   └── gsc.py                  Google Search Console integration
│   ├── agents/
│   │   ├── base.py                 Abstract Agent + AgentResult
│   │   ├── analyzer.py             Real crawler-backed SEO analyzer
│   │   ├── keyword.py              Real GSC keyword opportunities
│   │   ├── strategy.py             ROI-based issue prioritization
│   │   └── content.py              Claude API + template fallback
│   ├── db/
│   │   ├── models.py               SQLAlchemy ORM (4 tables)
│   │   ├── database.py             Async session management
│   │   └── persistence.py          AuditContext -> DB
│   ├── reports/
│   │   ├── html.py                 Jinja2 HTML report renderer
│   │   └── templates/report.html   Inline-CSS single-file report
│   ├── notifications/
│   │   └── telegram.py             Telegram summary
│   ├── api/
│   │   └── main.py                 FastAPI REST + WebSocket
│   └── cli/
│       └── main.py                 Click CLI
│
├── alembic/                         DB migrations
├── tests/                           13 pytest unit tests
├── reports/                         Generated HTML reports (per project)
├── Dockerfile                       Production container
├── docker-compose.yml               Standalone compose file
├── requirements.txt
├── projects.yaml                    Live project config
├── projects.yaml.example
├── README.md
├── PHASE2.md
└── STATUS.md                        (this file)
```

---

## What Works Right Now (real, not simulated)

### Crawler (`sources/crawler.py`, ~330 LOC)
- Discovers pages via `sitemap.xml` / `sitemap_index.xml` (recursive, 2 levels)
- Falls back to homepage parse + internal link extraction
- Async httpx, concurrency-limited (5)
- Custom User-Agent: `SEOAutopilotBot/0.3 (+https://tentacl.ai/seo-autopilot)`
- Extracts: title, meta description, canonical, hreflang, lang, viewport, robots meta, H1/H2 list, word count, internal/external link counts, images with/without alt, Open Graph / Twitter cards, JSON-LD schema types + full schema data, HTTPS status, selected security headers (HSTS, XFO, XCTO, CSP, referrer-policy, permissions-policy), response time
- 2 MB HTML body cap, timeouts, graceful error handling

### Analyzer Agent
Detects the following real issues (each returned as an issue dict with severity, category, fix_suggestion, estimated_impact):
- Fetch errors / HTTP 4xx/5xx
- Missing / too short / too long titles
- Missing / too short / too long meta descriptions
- Missing viewport, missing lang attribute
- `noindex` robots meta (downgraded for legal pages)
- Missing H1, multiple H1s
- Missing Open Graph title, og:image, twitter:card
- Images without alt text
- Missing Organization / WebSite schema on homepage
- No JSON-LD at all
- Non-HTTPS pages
- Missing security headers
- Slow response time (> 2.5 s)
- Missing canonical

### Keyword Agent
- Pulls last 28 days from Google Search Console via service account
- Aggregates by query / page / device / country
- Detects **low-CTR opportunities**: rank ≤ 10 but CTR < 3%
- Detects **striking-distance queries**: position 11–20 with ≥ 30 impressions
- Persists top queries, top pages, by-device, total clicks/impressions/CTR/avg position
- Skips gracefully if credentials missing or 403 (service account without property access)

### Strategy Agent
- Reads **all** previous issues from AuditContext
- Assigns effort (hours) and impact (0–100) per issue type from a tunable table
- Severity boost: high ×1.5, medium ×1.0, low ×0.6
- Computes ROI = impact / effort
- Groups into **quick-wins** (≤ 0.5 h & impact ≥ 40), **this-week** (≤ 4 h), **backlog**
- Sorted by priority then descending ROI

### Content Agent
- Reads high-priority issues from AuditContext
- Calls **Claude API** (`CLAUDE_API_KEY` env var) to generate concrete fixes:
  - Optimized titles for short/long/missing titles
  - Meta descriptions
  - H1 suggestions
  - Title+description combos for low-CTR keywords
  - On-page action list for striking-distance queries
  - Full Organization JSON-LD blocks
- **Deterministic template fallback** when no API key is set
- Always emits generic Organization schema + security headers nginx snippet
- Hard cap of 8 Claude calls per audit (cost control)

### Database Persistence (`db/persistence.py`)
Every completed audit writes:
1. Upserts `seo_projects` row (with latest score + last_run_at)
2. Inserts `seo_audits` row (duration, score, GSC metrics, full analytics JSON, log)
3. Inserts one `seo_issues` row per issue (category, severity, priority, description, fix, impact)
4. Inserts one `seo_keywords` row per top query (up to 50)

### HTML Report (`reports/html.py`)
- Jinja2 template with inline CSS (Tentacl design colors: #faf9f7 / #e8540a / #1a1a1a)
- Single self-contained `.html` file per audit
- Saved to `reports/<project_id>/<audit_id>.html`
- Automatic `latest.html` symlink (falls back to copy if symlinks aren't supported)
- Sections: score card, severity breakdown, top actions table, category overview, GSC stats, top keywords, concrete fixes (code blocks)

### Telegram Notifier
- Reads `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` from env
- Sends Markdown-formatted summary: score, severity counts, GSC snapshot, top 3 actions, report path
- No-op when not configured

### API (FastAPI on :8002)
- `GET /api/health`
- `GET /api/projects` – list all
- `POST /api/projects` – create new
- `GET /api/projects/{id}` – detail + next-run
- `POST /api/audits/run/{id}` – trigger audit (`run_async` optional)
- `WS /api/ws/events/{id}` – real-time event stream

### CLI (Click)
- `seo-autopilot config add/list/remove`
- `seo-autopilot run [--project-id ID]` – **now actually runs the full pipeline** and persists results
- `seo-autopilot api` – start uvicorn
- `seo-autopilot version`

### Tests (13 passing)
- `tests/test_crawler_parser.py` – HTML parser (titles, meta, schema JSON-LD, links, images)
- `tests/test_analyzer_issues.py` – Analyzer checker methods (meta / headings / security / HTTP)
- `tests/test_audit_context.py` – Context aggregation, strategy replacement, score calculation
- `tests/test_strategy_ranking.py` – Strategy ROI ranking + quick-wins identification

### Docker / Compose
- `Dockerfile` – Python 3.12 slim, builds pip deps first for caching, mounts `/app/data` `/app/reports` `/app/logs` `/app/secrets`, healthcheck on `/api/health`, exposes 8002
- `docker-compose.yml` – Standalone (not merged into `/opt/infra/docker-compose.yml`); mounts GSC credentials read-only from `/opt/odoo/credentials/`

---

## End-to-End Run on tentacl.ai (live, 2026-04-12 00:14 UTC)

```
[analyzer] Analyzed 6/6 pages on https://tentacl.ai, found 24 issues
[keyword]  SKIPPED – GSC 403 (service account needs property access)
[strategy] Prioritized 24 issues: 5 quick-wins, 19 this-week, 0 backlog
[content]  Generated 10 fixes (template mode — CLAUDE_API_KEY not set)
[db]       Persisted audit 1a3631f5-... with 24 issues
[report]   reports/tentacl-ai/audit_tentacl-ai_1775945675.html
[result]   score=77 high=2 med=6 low=16
```

**Real issues surfaced on tentacl.ai:**
- `/projekte/` has no H1 (medium)
- Homepage missing security headers (HSTS, XFO, XCTO) (medium)
- `/claude-code/` title too long at 68 chars (low)
- Meta descriptions at 180+ chars on multiple pages (low)
- Several pages missing canonical links (low)

---

## Known Open Items

1. **GSC property access**
   Service account `tentacl-seo-bot@tentacl-seo.iam.gserviceaccount.com` currently returns 403 on `https://tentacl.ai`. Robert must add this email in Google Search Console → *Property settings → Users and permissions → Add user → Restricted*.

2. **Claude API key**
   `CLAUDE_API_KEY` env var is not set, so ContentAgent runs in deterministic template mode. Setting the key unlocks AI-generated meta tags, titles and striking-distance action lists.

3. **Ahrefs / Semrush**
   Not implemented — skipped on purpose for cost/scope.

4. **Lighthouse integration**
   Core Web Vitals still come from synthetic server-side response time (`fetch_ms`), not real Lighthouse. Adding a Lighthouse runner is a straightforward Phase 4 task.

5. **JWT auth**
   API still uses a shared `API_SECRET_KEY` placeholder; no real authentication.

6. **Production deployment**
   Docker image builds cleanly but is **not yet integrated** into `/opt/infra/docker-compose.yml` (that would be a production infra change requiring explicit approval).

---

## Deployment

### Local (venv)

```bash
cd /opt/odoo/docs/seo-autopilot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# optional env vars
export CLAUDE_API_KEY="sk-..."          # unlocks AI fixes
export TELEGRAM_BOT_TOKEN="..."         # enables notifications
export TELEGRAM_CHAT_ID="..."

# run once
python -m seo_autopilot.cli.main run --project-id tentacl-ai

# or start the API + scheduler
python -m seo_autopilot.cli.main api
```

### Docker

```bash
cd /opt/odoo/docs/seo-autopilot
docker compose up -d
curl http://localhost:8002/api/health
```

### API Usage

```bash
curl http://localhost:8002/api/projects
curl -X POST http://localhost:8002/api/audits/run/tentacl-ai \
     -H "Content-Type: application/json" \
     -d '{"project_id":"tentacl-ai","run_async":false}'
# open HTML report:
xdg-open /opt/odoo/docs/seo-autopilot/reports/tentacl-ai/latest.html
```

---

## Changes from Phase 2

| Area | Phase 2 | Phase 3 (now) |
|------|---------|----------------|
| AnalyzerAgent | Mocked data | Real crawler + 14 issue detectors |
| KeywordAgent | Hardcoded list | Real GSC API with 28-day window |
| StrategyAgent | Placeholder issues | Reads AuditContext, ROI ranking, quick-wins |
| ContentAgent | Static templates | Claude API + template fallback |
| Audit orchestration | Independent agents | AuditContext shared between agents |
| DB persistence | Tables existed | Full persist_audit() writes projects, audits, issues, keywords |
| Reports | None | Self-contained HTML via Jinja2 |
| Notifications | None | Telegram summary (configurable) |
| Tests | None | 13 pytest unit tests |
| Container | None | Dockerfile + standalone compose |
| CLI `run` | TODO stub | Actually runs full pipeline + persists |
| Score formula | `100 - 2*issues` | Weighted severity: `100 - (3h + 1.5m + 0.5l)` |

---

## File Statistics (Phase 3)

| Category | Files | Approx. LOC | Notes |
|----------|------:|------------:|-------|
| Crawler + sources | 3 | ~520 | New HTML parser |
| Agents | 5 | ~850 | Full rewrites |
| Core (incl. AuditContext) | 5 | ~550 | New audit_context |
| DB + persistence | 3 | ~470 | New persistence layer |
| Reports | 3 | ~340 | New Jinja2 renderer + template |
| Notifications | 2 | ~100 | New Telegram notifier |
| API + CLI | 2 | ~500 | Refactored pipeline |
| Tests | 5 | ~260 | New |
| Docker | 2 | ~50 | New |
| **Total new/changed** | ~30 | **~3.6 k** | On top of Phase 2 baseline |

---

## Next Steps (Phase 4 ideas)

1. Add Lighthouse / PageSpeed Insights integration for real Core Web Vitals
2. Add Ahrefs or DataForSEO plugin for keyword difficulty / backlinks
3. PDF export (weasyprint) in addition to HTML
4. Multi-channel notifications (email / Slack)
5. JWT-based API authentication
6. Per-tenant API keys for customer self-service
7. CI workflow (GitHub Actions) running pytest on every push
8. Optional auto-fix: apply the generated meta tags via WordPress REST / git commit
9. Ship as Docker image in the infra compose file once Robert approves
