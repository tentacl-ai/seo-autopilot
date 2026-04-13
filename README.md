# SEO Autopilot

[![CI](https://github.com/tentacl-ai/seo-autopilot/actions/workflows/ci.yml/badge.svg)](https://github.com/tentacl-ai/seo-autopilot/actions/workflows/ci.yml)
[![GitHub License: MIT](https://img.shields.io/badge/license-MIT-green)](#license)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.110-green)](https://fastapi.tiangolo.com/)
[![Async SQLAlchemy](https://img.shields.io/badge/sqlalchemy-2.0-orange)](https://www.sqlalchemy.org/)

**Production-ready, multi-tenant SEO automation platform** with real crawler, 50+ issue detectors, GEO audit, topical authority analysis, and AI-powered fix generation.

> Crawl your website, detect SEO issues across 10 analysis dimensions, prioritize by ROI, and generate actionable fixes — all from a single CLI command.

## What It Does

SEO Autopilot turns raw web crawl data into a **prioritized action plan**:

1. **Crawls your site** – httpx + BeautifulSoup, sitemap.xml discovery, 14+ HTML attributes parsed
2. **Pulls real data** – Google Search Console (28-day), PageSpeed Insights (INP/LCP/CLS via CrUX), robots.txt
3. **Analyzes 10 dimensions** – On-page, canonical, redirects, schema, GEO, topical authority, duplicates, link graph, CWV, security
4. **Detects 50+ issue types** – From missing titles to AI-crawler blocking, canonical chains, thin content, keyword cannibalization
5. **Prioritizes by ROI** – Quick-wins (< 30min), this-week tasks, backlog — sorted by impact x confidence x ease
6. **Generates fixes** – Claude API writes optimized titles, meta descriptions, JSON-LD snippets
7. **Tracks regressions** – Delta engine compares audits over time, alerts on score drops
8. **Monitors algorithm updates** – Intelligence feed polls Google Search Central + SEO blogs, detects confirmed events
9. **Ships HTML reports** – Self-contained Jinja2 templates with Telegram notifications
10. **Schedules audits** – APScheduler cron jobs, multi-tenant isolation, WebSocket event streaming

**201 tests, 0 external dependencies beyond Python stdlib + httpx/BS4/FastAPI. No Playwright, no scikit-learn, no heavyweight ML.**

---

## Installation

### From PyPI (Recommended)

```bash
pip install seo-autopilot
```

### From Source

```bash
git clone https://github.com/tentacl-ai/seo-autopilot.git
cd seo-autopilot
pip install -e .

# Optional: dev dependencies
pip install -e ".[dev]"
```

### Docker

```bash
docker build -t seo-autopilot .
docker run -p 8002:8002 \
  -e DATABASE_URL="sqlite:///seo.db" \
  -e CLAUDE_API_KEY="sk-..." \
  seo-autopilot
```

---

## Quickstart

### 1. Configure a Project

Create `projects.yaml`:

```yaml
projects:
  my-website:
    domain: https://example.com
    name: Example.com
    tenant_id: default
    enabled_sources:
      - gsc
      - pagespeed
    source_config:
      gsc:
        property_url: sc-domain:example.com
        credentials_path: /path/to/service-account.json
      pagespeed:
        api_key: ${PAGESPEED_API_KEY}  # Optional: for higher quota
    enabled: true
    schedule_cron: "0 7 * * 1"  # Monday 7am
    notifications_enabled: true
    notify_channels:
      - telegram
    notify_config:
      telegram:
        enabled: true
```

### 2. Set Environment Variables

```bash
export CLAUDE_API_KEY="sk-ant-..."
export TELEGRAM_BOT_TOKEN="1234567:ABCdef..."
export TELEGRAM_CHAT_ID="123456789"
export DATABASE_URL="sqlite:///seo.db"  # or postgres://...
export PAGESPEED_API_KEY="AIzaSy..."     # optional
```

### 3. Run Your First Audit

```bash
# List projects
seo-autopilot config list

# Run audit
seo-autopilot run --project-id my-website

# Watch live output
seo-autopilot run --project-id my-website --verbose
```

### 4. View Report

Open `reports/latest.html` in your browser – or access via API:

```bash
seo-autopilot api    # Starts FastAPI at http://localhost:8002
curl http://localhost:8002/api/health
```

---

## Architecture

```
seo_autopilot/
├── core/                        # Framework
│   ├── config.py                  Pydantic Settings + .env
│   ├── project_manager.py         Multi-project CRUD (YAML)
│   ├── scheduler.py               APScheduler cron
│   ├── event_bus.py               Pub/Sub (WebSocket events)
│   └── audit_context.py           Shared state across agents
├── sources/                     # Data collection
│   ├── crawler.py                 httpx + BeautifulSoup (sitemap discovery)
│   ├── pagespeed.py               PageSpeed Insights + CrUX (INP/LCP/CLS)
│   ├── gsc.py                     Google Search Console (28-day)
│   ├── intelligence.py            RSS feed monitor (algorithm updates)
│   └── base.py                    Abstract base
├── analyzers/                   # Rule-based analysis (v1.0)
│   ├── canonical_engine.py        Canonical resolution + conflict detection
│   ├── redirect_audit.py          Chains, loops, 302→301, soft-404
│   ├── schema_validation.py       JSON-LD required fields (13 types)
│   ├── geo_audit.py               GEO / AI Overview readiness (score 0-100)
│   ├── topical_authority.py       Cluster detection, pillar pages, gaps
│   ├── duplicate_content.py       SimHash near-duplicates, thin content
│   ├── link_graph.py              PageRank, orphans, click depth, broken links
│   └── delta.py                   Audit-over-audit regression detection
├── agents/                      # Pipeline agents
│   ├── analyzer.py                Orchestrates all analyzers (50+ checks)
│   ├── keyword.py                 GSC keyword opportunities
│   ├── strategy.py                ROI-based prioritization
│   ├── content.py                 Claude API fix generation
│   └── base.py                    Agent interface
├── db/                          # Persistence
│   ├── models.py                  SQLAlchemy ORM
│   ├── database.py                Async engine (SQLite/Postgres)
│   └── persistence.py             Audit CRUD
├── reports/
│   ├── html.py                    Jinja2 HTML report
│   └── templates/report.html
├── notifications/
│   └── telegram.py                Bot API summary
├── api/main.py                  # FastAPI REST + WebSocket
├── cli/main.py                  # Click CLI
└── mcp/server.py                # Model Context Protocol
```

---

## Analysis Dimensions (v1.0)

| Module | What it checks | Issue types |
|--------|---------------|-------------|
| **On-Page** | Titles, meta descriptions, H1s, viewport, lang, noindex | 12 |
| **Canonical Engine** | Signal hierarchy, chains, conflicts with sitemap/hreflang/noindex | 7 |
| **Redirect Audit** | Chains, loops, 302 vs 301, cross-domain, soft-404, 5xx clusters | 7 |
| **Schema Validator** | JSON-LD required fields for 13 types, FAQ/Product/Breadcrumb validation | 4 |
| **GEO Audit** | AI-crawler blocking, answer-first structure, fact density, entity clarity | 7 |
| **Topical Authority** | Cluster detection, pillar pages, coverage gaps, cannibalization | 5 |
| **Duplicate Content** | SimHash near-duplicates (canonical-aware), thin content, keyword cannibalization | 3 |
| **Link Graph** | Orphan pages, click depth, broken links, PageRank distribution, equity sinks | 5 |
| **Core Web Vitals** | INP, LCP, CLS via CrUX field data + Lighthouse lab data (no FID) | 6 |
| **Security** | HTTPS, HSTS, X-Frame-Options, X-Content-Type-Options | 2 |

**Total: 50+ issue types across 10 analysis dimensions.**

### Additional features

- **Delta Engine** — Compares audits over time, detects regressions, generates alert messages
- **Intelligence Feed** — Polls 8 SEO RSS feeds, detects algorithm updates via 2-source confirmation
- **GEO Score** — 0-100 per page, measures AI citation readiness (Google AI Overviews, ChatGPT, Perplexity)
- **Topical Authority Map** — Cluster detection via URL paths + keyword overlap, pillar identification

---

## Core Features

### 1. Real Web Crawler (sources/crawler.py)

Discovers pages via sitemap.xml or homepage links  
Extracts 14+ HTML attributes (title, h1-h6, meta, OG, Twitter, schema)  
Detects security headers, HTTPS, response times  
- Handles 2MB+ pages gracefully  
- Returns structured `PageData` objects  

```python
from seo_autopilot.sources.crawler import WebCrawler

crawler = WebCrawler(max_pages=20)
pages = await crawler.crawl("https://example.com")

for page in pages:
    print(f"{page.url}: {page.title} ({page.word_count} words)")
```

### 2. Google Search Console (sources/gsc.py)

- 28-day analytics (clicks, impressions, CTR, position)  
- Keyword discovery + ranking opportunities  
- OAuth2 Service Account authentication  
- Row limits: 25,000 queries per property  

```python
from seo_autopilot.sources.gsc import GSCDataSource

gsc = GSCDataSource(
    credentials_path="/path/to/service-account.json",
    property_url="sc-domain:example.com"
)
analytics = await gsc.pull_analytics()
print(f"28-day clicks: {analytics.total_clicks}")
```

### 3. PageSpeed Insights (sources/pagespeed.py)

- Lighthouse scores (Performance, SEO, Accessibility, Best Practices)  
- Core Web Vitals (LCP, CLS, TBT, FCP, TTI, Speed Index)  
- Free tier (limited quota) or paid tier with API key  
- Mobile + Desktop metrics  

```python
from seo_autopilot.sources.pagespeed import PageSpeedSource

psi = PageSpeedSource(api_key="AIzaSy...")
metrics = await psi.get_pagespeed("https://example.com")
print(f"Performance: {metrics['performance_score']}/100")
```

### 4. Analyzer Agent (agents/analyzer.py)

14 detectors + Core Web Vitals:

| Issue | Severity | Effort | Impact |
|-------|----------|--------|--------|
| Missing title | High | 0.25h | 80 |
| Short title (<20) | Medium | 0.25h | 70 |
| Missing h1 | High | 0.5h | 40 |
| Slow response (>2.5s) | High | 8h | 70 |
| Poor LCP (>2.5s) | High | 4h | 80 |
| Poor CLS (>0.1) | Medium | 2h | 60 |
| Missing security headers | Medium | 1h | 35 |
| ... (8 more) |

```python
analyzer = AnalyzerAgent(project_id="my-website", context=audit_context)
result = await analyzer.run()

for issue in result.issues:
    print(f"[{issue['severity']}] {issue['title']}")
```

### 5. Keyword Agent (agents/keyword.py)

- Identifies low-CTR keywords (high volume, <5% CTR)  
- Finds "striking distance" (pos 11-30, fixable to top 10)  
- Ranks by traffic potential  

### 6. Strategy Agent (agents/strategy.py)

ROI-based prioritization:

- **Quick-wins**: Effort ≤ 0.5h AND Impact ≥ 40
- **This week**: Effort ≤ 4h, sorted by ROI
- **Backlog**: Everything else

```python
strategy = StrategyAgent(project_id="my-website", context=audit_context)
result = await strategy.run()

print(f"Quick wins: {result.metrics['quick_wins']}")
print(f"This week: {result.metrics['this_week']}")
print(f"Total effort: {result.metrics['total_effort_hours']}h")
```

### 7. Content Agent (agents/content.py)

Uses Claude API to generate:

- Meta description improvements  
- Title optimization suggestions  
- H1 content recommendations  
- Code snippets for fixes (JSON-LD, security headers, etc.)  

### 8. HTML Reports (reports/html.py)

Jinja2 template with:

- SEO score (0-100)  
- Top 15 actions (prioritized)  
- Issue categories breakdown  
- GSC metrics (clicks, impressions, CTR, position)  
- PageSpeed scores (desktop + mobile)  
- Core Web Vitals visualization  
- Concrete fix suggestions  

Auto-published to `reports/latest.html` symlink + served via API.

### 9. Telegram Notifications (notifications/telegram.py)

```
🚀 SEO Audit Complete: my-website

📊 Score: 77/100 (was 75.5)
Issues: 3 (0 high, 1 medium, 2 low)
Quick-wins: 2 (fix in 30min)

📈 GSC (28 days)
Clicks: 15 | Impressions: 420 | CTR: 3.6% | Pos: 12.3

🔧 Top Actions
1. [HIGH] Missing h1 on /products
2. [MEDIUM] Slow response (2.8s)
3. [LOW] Noindex on legal pages
```

### 10. Scheduler (core/scheduler.py)

APScheduler integration with cron syntax:

```yaml
schedule_cron: "0 7 * * 1"  # Monday 7am
run_interval_days: 7         # Weekly
```

Multi-tenant isolation, event-driven callbacks.

---

## Multi-Tenant Usage

SEO Autopilot is **built for managing SEO across multiple client websites**:

```yaml
projects:
  client-1:
    domain: https://client1.com
    tenant_id: client-1
    # ...
  client-2:
    domain: https://client2.com
    tenant_id: client-2
    # ...
```

Each project has isolated:

- Crawl data  
- GSC credentials  
- Database records  
- API keys  
- Notifications  
- Reports  

---

## REST API

### Health Check

```bash
curl http://localhost:8002/api/health
# { "status": "ok", "version": "1.0.1" }
```

### List Projects

```bash
curl http://localhost:8002/api/projects
# [ { "id": "my-website", "name": "Example.com", "domain": "..." } ]
```

### Run Audit

```bash
curl -X POST http://localhost:8002/api/audits/run/my-website

# Response (long-running):
# {
#   "audit_id": "a1b2c3...",
#   "status": "running",
#   "project_id": "my-website"
# }
```

### Get Audit Results

```bash
curl http://localhost:8002/api/audits/a1b2c3/results
# { "score": 77, "issues": [...], "quick_wins": [...] }
```

### WebSocket Events (Real-time)

```bash
wscat -c ws://localhost:8002/api/ws/events/my-website

# Live events:
# { "type": "crawler_started", "pages_found": 6 }
# { "type": "analyzer_running", "issues_found": 12 }
# { "type": "strategy_complete", "quick_wins": 3 }
# { "type": "audit_complete", "score": 77 }
```

Full API docs: `http://localhost:8002/docs` (auto-generated Swagger)

---

## Claude Integration (MCP Server)

Use SEO Autopilot as a Claude tool via Model Context Protocol:

```python
from seo_autopilot.mcp.server import SEOAutopilotMCPServer

server = SEOAutopilotMCPServer()

# Claude can now call:
# - list_projects() → get available projects
# - run_audit(project_id="my-website") → start full audit pipeline
```

Example Claude conversation:

> **You:** Audit my website and tell me the top 3 fixes.
>
> **Claude:** I'll run a full SEO audit for you.
>
> [Claude calls: run_audit("my-website")]
>
> **Claude:** Your site scores 77/100. Here are the top 3 quick-wins (< 30min each):
> 1. Add missing h1 on /products (40 impact, 30min effort)
> 2. Improve meta description length (70 impact, 15min effort)
> 3. Add security headers (35 impact, 1h effort)

---

## Testing

```bash
# Unit tests
pytest tests/ -v

# With coverage
pytest --cov=seo_autopilot tests/

# Integration tests (requires DB)
pytest tests/ -m integration

# Type checking
mypy seo_autopilot/
```

Example test:

```python
import pytest
from seo_autopilot.agents.analyzer import AnalyzerAgent

@pytest.mark.asyncio
async def test_analyzer_detects_missing_title():
    context = AuditContext(project_id="test", tenant_id="test")
    analyzer = AnalyzerAgent(project_id="test", audit_id=context.audit_id, context=context)
    result = await analyzer.run()
    
    missing_titles = [i for i in result.issues if i['type'] == 'missing_title']
    assert len(missing_titles) > 0
```

---

## Security

- **API Key rotation** – Use API secrets or JWT tokens (configurable)  
- **Tenant isolation** – All queries filtered by `tenant_id`  
- **Credentials management** – Service accounts in `/credentials/`, never in code  
- **Rate limiting** – Built-in throttling on PageSpeed / GSC APIs  
- **HTTPS only** – Production deployments must use TLS  

---

## Performance

| Operation | Time |
|-----------|------|
| Crawl 20 pages (static) | 2-5 seconds |
| Analyze 20 pages | 3-8 seconds |
| GSC fetch (28 days) | 1-2 seconds |
| PageSpeed (mobile + desktop) | 5-10 seconds |
| Full audit pipeline | 15-30 seconds |
| Report generation | 0.5 seconds |

Bottleneck: PageSpeed API rate limiting (200/day free tier).

---

## Deployment

### Local Development

```bash
python -m seo_autopilot.api.main
# Runs on http://localhost:8002
```

### Docker (Recommended)

```bash
docker build -t seo-autopilot:1.0.1 .
docker run -d \
  --name seo-autopilot \
  -p 8002:8002 \
  -v /opt/seo-autopilot/reports:/app/reports \
  -v /opt/seo-autopilot/credentials:/app/credentials \
  -e DATABASE_URL="postgres://user:pass@db:5432/seo" \
  -e CLAUDE_API_KEY="sk-..." \
  -e TELEGRAM_BOT_TOKEN="..." \
  seo-autopilot:1.0.1
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: seo-autopilot
spec:
  replicas: 1
  selector:
    matchLabels:
      app: seo-autopilot
  template:
    metadata:
      labels:
        app: seo-autopilot
    spec:
      containers:
      - name: seo-autopilot
        image: seo-autopilot:1.0.1
        ports:
        - containerPort: 8002
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: seo-autopilot-secrets
              key: database-url
        volumeMounts:
        - name: reports
          mountPath: /app/reports
      volumes:
      - name: reports
        emptyDir: {}
```

---

## Examples

### Example 1: Audit via CLI

```bash
# Install
pip install seo-autopilot

# Create projects.yaml
cat > projects.yaml << EOF
projects:
  mysite:
    domain: https://mysite.com
    name: My Site
    enabled_sources: [gsc]
    source_config:
      gsc:
        property_url: sc-domain:mysite.com
        credentials_path: ./service-account.json
EOF

# Run
seo-autopilot run --project-id mysite

# View report
open reports/latest.html
```

### Example 2: Audit via API

```bash
# Start API
seo-autopilot api &

# List projects
curl http://localhost:8002/api/projects

# Run audit
curl -X POST http://localhost:8002/api/audits/run/mysite

# Monitor WebSocket
wscat -c ws://localhost:8002/api/ws/events/mysite
```

### Example 3: Python Library

```python
import asyncio
from seo_autopilot.core.audit_context import AuditContext
from seo_autopilot.agents.analyzer import AnalyzerAgent
from seo_autopilot.agents.strategy import StrategyAgent

async def audit_website():
    context = AuditContext(project_id="mysite", tenant_id="default")
    
    # Analyze
    analyzer = AnalyzerAgent(
        project_id="mysite",
        audit_id=context.audit_id,
        context=context
    )
    await analyzer.run()
    
    # Prioritize
    strategy = StrategyAgent(
        project_id="mysite",
        audit_id=context.audit_id,
        context=context
    )
    await strategy.run()
    
    print(f"Score: {context.score}")
    print(f"Quick-wins: {context.quick_wins_count}")

asyncio.run(audit_website())
```

---

## Troubleshooting

### PageSpeed Returns 429 (Rate Limited)

**Problem:** `[analyzer] Rate limited (429). Set pagespeed.api_key...`

**Solution:** Get a free Google API Key:
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project
3. Enable "PageSpeed Insights API"
4. Create an API key
5. Set `PAGESPEED_API_KEY=AIzaSy...` in `.env`

### GSC Credentials Error

**Problem:** `[gsc] Error loading credentials: FileNotFoundError`

**Solution:**
1. Create a Service Account in Google Cloud
2. Download JSON key
3. Set in `projects.yaml`: `credentials_path: /path/to/key.json`

### Database Connection Refused

**Problem:** `psycopg2.OperationalError: could not connect to server`

**Solution:**
- Check PostgreSQL is running: `psql -U postgres`
- Or use SQLite: `DATABASE_URL=sqlite:///seo.db`

---

## Roadmap

### v1.0 (Current)
- Real crawler (httpx + BeautifulSoup)
- 10 analyzer modules (50+ issue types)
- GEO audit, topical authority, duplicate detection, link graph
- PageSpeed CrUX field data (INP, LCP, CLS)
- Delta/regression engine
- Intelligence feed (algorithm monitoring)
- Intent/GEO keyword analysis (Claude API)
- E-E-A-T signal detection
- Google Search Console + PageSpeed Insights
- HTML reports + Telegram notifications
- Multi-tenant database (SQLAlchemy + Alembic)
- FastAPI REST API + WebSocket
- MCP Server (Claude integration)
- APScheduler cron jobs
- 201 tests, 0 failures

### v1.1 (Planned)
- [ ] Hreflang / Internationalization audit
- [ ] Optional: DataForSEO backlinks
- [ ] Optional: Playwright JS rendering
- [ ] CI/CD with GitHub Actions

---

## Contributing

Contributions are welcome! This is an open-source project.

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes + tests
4. Run tests: `pytest tests/`
5. Submit a pull request

**Development setup:**

```bash
git clone https://github.com/tentacl-ai/seo-autopilot.git
cd seo-autopilot
pip install -e ".[dev]"

# Code quality
black seo_autopilot/
flake8 seo_autopilot/
mypy seo_autopilot/

# Tests
pytest tests/ -v --cov=seo_autopilot
```

---

## License

MIT License – see [LICENSE](LICENSE) file.

Use freely in commercial and personal projects.

---

## Support

- **Issues:** GitHub Issues on this repo
- **Discussions:** GitHub Discussions (Q&A, ideas, feedback)
- **Email:** hello@tentacl.ai

---

## Why Open Source?

This tool powers production SEO audits at [Tentacl.ai](https://tentacl.ai). We open-sourced it to:

1. **Help the community** – Free, reliable SEO automation tool
2. **Build trust** – Transparency in how we analyze websites
3. **Invite contributions** – Improve the tool together
4. **Integrate with Claude** – Use AI agents for even better analysis

---

## Links

- **GitHub:** https://github.com/tentacl-ai/seo-autopilot
- **Tentacl.ai:** https://tentacl.ai

---

<div align="center">

Built with ❤️ by [Tentacl.ai](https://tentacl.ai)

**Star ⭐ if you find this useful**

</div>
