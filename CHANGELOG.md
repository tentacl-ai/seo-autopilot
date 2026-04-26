# Changelog

All notable changes to this project will be documented in this file.

## [1.2.0] - 2026-04-26

### Added
- **Auto-Fix-Loop** — New ApplyAgent runs after ContentAgent and applies generated fixes to the project's files (via adapter pattern). Initial adapter: `static_files` (HTML meta tags, canonical, schema blocks, robots.txt, sitemap.xml; commits each fix as separate git commit).
- **API endpoints** — `POST /api/audits/run/{id}` accepts `auto_fix:true`; `POST /api/fixes/apply/{audit_id}` re-runs an audit with apply enabled; `GET /api/fixes/applied` lists applied fixes; `POST /api/fixes/revert/{commit_hash}` marks rolled_back.
- **CLI flag** — `seo-autopilot run --auto-fix` forces ApplyAgent regardless of project config.
- **TrendsAgent** — Fetches Google-Trends data (interest_over_time + related_queries.rising) per project. Disk-persistent 24h cache, 429-aware (errors not cached). Configurable via `intel_config.intel_keywords` (max 5 per project).
- **`seo_intel` table** — persists rising/top queries from Google Trends per audit.
- **Telegram blocks** — new "✅ Auto-Fix angewendet" and "🔥 Trends diese Woche" sections in audit notifications.
- **GitHub Actions release workflow** — auto-publishes to PyPI, updates GitHub description, creates Release notes, and sends Telegram notification on every `vX.Y.Z` tag push.

### Changed
- ContentAgent now generates templates for ~7 additional issue types (canonical_missing, missing_robots_txt, missing_sitemap_xml, sitemap_no_lastmod, missing_security_headers, missing_contact_page, missing_about_page, org_schema_no_sameas).
- `audit_context.py` score-cap: `min(50, 3*high) + min(30, 1*medium) + min(20, 0.3*low)` instead of unbounded penalty — keeps the score readable on issue-heavy sites.
- `strategy.py` priority assignment: severity now takes precedence over adj_impact (low stays low even if many of them).

### Database (alembic)
- `002_apply_fields.py` — `seo_projects.auto_fix_enabled`, `seo_projects.auto_fix_config`, `seo_issues.fix_applied_at`, `seo_issues.applied_by`, `seo_issues.git_commit_hash`, `seo_issues.fix_diff`, `seo_issues.fix_error`.
- `003_intel_table.py` — new `seo_intel` table + `seo_projects.intel_config` column.

### Fixed
- `alembic.ini` — restore missing `[alembic]` section header that was lost in a previous edit.

### Dependencies
- Added `pytrends>=4.9.0`.
- Dockerfile: `git` is now installed (required by ApplyAgent's static_files adapter).

## [1.1.0] - 2026-04-14

### Added
- **LLMs.txt Audit** — Validates /llms.txt against the llmstxt.org spec (H1 title, sections, markdown links)
- **llms-full.txt Check** — Detects missing /llms-full.txt (optional extended version)
- **AI.txt Check** — Detects missing /ai.txt (emerging AI permission standard)
- **IndexNow Support** — Checks for IndexNow key at /.well-known/indexnow (Bing/Yandex instant indexing)
- New issue category `llms_ai` with 6 issue types
- 15 new tests (218 total)

### Changed
- Analyzer pipeline now runs 11 analysis modules (was 10)
- **Crawler with Playwright fallback** — auto-detects SPAs (React, Next.js, Vue, Nuxt) and renders via headless Chromium when httpx finds < 50 words
- `PageData.rendered_via` tracks rendering engine ("httpx" or "playwright")
- Dockerfile installs Chromium for JS rendering
- 229 tests total (was 218)
- README updated with new analysis dimensions
- Version bump to 1.1.0

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
