# Changelog

All notable changes to this project will be documented in this file.

## [1.0.2] - 2026-04-13

### Added
- `POST /api/intelligence/poll` endpoint for manual feed triggering
- 4 Google News keyword feeds (algo, CWV, GEO, AI crawlers)
- Intelligence agent with impact analysis + Telegram alerts
- Scheduler integration for intelligence jobs (6h poll + daily check)
- 2 new tests for poll endpoint (203 total)

## [1.0.1] - 2026-04-13

### Fixed
- MCP server: AttributeError on startup fixed
- README: all feature claims verified and corrected
- Adapter references removed (HTTP crawling works on any site)

## [1.0.0] - 2026-04-13

### Added
- **Canonical Engine** — Signal hierarchy (HTTP Header > HTML > Sitemap), chain detection, 7 conflict types
- **Redirect Audit** — Chains, loops, 302 vs 301, cross-domain redirects, soft-404 detection, 5xx clusters
- **Schema Validator** — JSON-LD required field checks for 13 schema types (Product, Article, FAQ, etc.)
- **GEO Audit** — AI-crawler blocking detection, answer-first structure, fact density, entity clarity, GEO score 0-100
- **Topical Authority** — Topic cluster detection via URL paths + keyword overlap, pillar identification, coverage gaps
- **Duplicate Content** — SimHash near-duplicate detection (canonical-aware, cluster-aware), thin content, keyword cannibalization
- **Link Graph** — Custom PageRank, orphan pages, click depth, broken internal links, link equity sinks
- **Delta Engine** — Audit-over-audit comparison, regression detection, severity tracking, alert messages
- **Intelligence Feed** — RSS feed monitor for algorithm updates (12 sources, 2-source confirmation)
- **PageSpeed CrUX** — Real user metrics (INP, LCP, CLS) from Chrome UX Report field data
- CONTRIBUTING.md
- CHANGELOG.md

### Changed
- PageSpeed source rewritten with CrUX field data support (INP replaces deprecated FID)
- Analyzer agent now orchestrates all 10 analysis modules
- Crawler stores internal link URLs (not just counts) for link graph analysis
- All code, comments, docstrings translated to English
- Removed all hardcoded paths — uses relative paths via `Path(__file__)`

### Fixed
- Score claim in README corrected from 97.5 to actual 77/100
- `.env` file loading now graceful when file is missing or unreadable
- Bare `except: pass` replaced with proper error handling

## [0.3.0] - 2026-04-12

### Added
- Real HTTP crawler (httpx + BeautifulSoup, sitemap discovery)
- Google Search Console integration (28-day analytics)
- PageSpeed Insights integration (Lighthouse scores)
- 4-agent pipeline: Analyzer → Keyword → Strategy → Content
- HTML report generation (Jinja2)
- Telegram notifications
- SQLite/PostgreSQL persistence
- FastAPI REST API + WebSocket events
- Click CLI
- Docker support
- 13 unit tests

## [0.1.0] - 2026-03-01

### Added
- Initial project structure
- Multi-tenant project configuration (YAML)
- APScheduler cron integration
- Event bus (pub/sub)
