# Changelog

All notable changes to this project will be documented in this file.

## [1.8.0] - 2026-08-17

Roadmap-Phase 2. Das Aenderungsbuch (1.7.0) haelt fest, WAS geaendert wurde — jetzt beantwortet das Werkzeug, ob es etwas gebracht hat.

### Added
- **Wirkungsmessung** (`wirkung.py`, CLI `wirkung`) — je Aenderung wird nach **7, 14, 28 und 56 Tagen** das gleich lange Zeitfenster davor gegen das danach gestellt (Search-Console-Daten fuer genau diese Adresse). Der Tag der Aenderung selbst gehoert in kein Fenster: An ihm stand die Seite teils alt, teils neu online.
- **`bilanz()` — was wirkt ueberhaupt.** Trefferquote je Art der Aenderung ("Titel umschreiben hat in 7 von 10 Faellen geholfen, og:image nachruesten in 0 von 4"). Genau diese Quote braucht der spaetere Chancen-Motor (Phase 4), um Aufwand sinnvoll zu verteilen.
- **`GSCDataSource.pull_url_window()`** — Kennzahlen einer einzelnen Adresse in einem frei waehlbaren Zeitfenster. `pull_analytics` konnte nur "die letzten N Tage, ganze Property".

### Sperren gegen Scheinergebnisse
Eine Wirkungsmessung, die zu gern "verbessert" meldet, ist schlimmer als gar keine — sie fuehrt dazu, dass wirkungslose Massnahmen wiederholt werden. Fuenf Sperren faellen lieber kein Urteil als ein schlechtes:
- **Datenmenge skaliert mit der Fensterlaenge** (`5 Einblendungen/Tag`, mindestens 30). 30 Einblendungen sind in 7 Tagen duenn und in 56 Tagen nichts; ohne Skalierung waeren die langen Fenster die unzuverlaessigsten, obwohl sie die wichtigsten sind.
- **Widerspruechliche Signale zaehlen nicht als Erfolg.** Position nach vorn, aber gleichzeitig weniger Einblendungen UND Klicks = meist verschobener Suchbegriff-Mix, kein Gewinn.
- **Mehrere Aenderungen an derselben Seite im Messzeitraum** → `nicht_zurechenbar`, unabhaengig davon, wie eindeutig die Zahlen aussehen.
- **Fremde Aenderungen** (`urheber="mensch"`) werden gemessen, aber in der Bilanz getrennt ausgewiesen — fremde Arbeit darf die eigene Trefferquote nicht schoenen.
- **Ein Abfragefehler ist kein Messergebnis.** Liefert die Search Console `None`, wird nichts gespeichert und die Messung bleibt faellig.

### Verified
- **Live gegen echte Search-Console-Daten** (joseph-Startseite, rueckdatierte Testaenderung in eigener Datenbank — die Live-Daten blieben unberuehrt): Der erste Lauf meldete 2x "besser" bei Position 6,7 → 2,8. Die Gegenprobe zeigte: Einblendungen (43 → 40) und Klicks (8 → 6) waren gleichzeitig **gefallen**. Daraufhin wurde die Widerspruchs-Sperre gebaut; derselbe Lauf meldet jetzt korrekt "unveraendert" mit Begruendung. Ohne diesen Live-Lauf waere ein Scheinerfolg in Produktion gegangen.
- Die drei zentralen Sperren wurden gezielt sabotiert, um zu belegen, dass die Tests sie wirklich abdecken (Datenmengen-Sperre, Zurechenbarkeit, Fehlerbehandlung — jeweils genau die zugehoerigen Tests wurden rot).

### Tests
- +39 Tests (`tests/test_wirkung.py`). Total: **577**.

## [1.7.0] - 2026-08-17

### Added
- **Aenderungsbuch (`changes`)** — Roadmap-Phase 1 und Grundlage fuer die Wirkungsmessung: Ohne lueckenloses Protokoll laesst sich spaeter nie sauber zurechnen, ob eine Aenderung gewirkt hat. Tabelle `change_log` mit Zeitpunkt, Urheber, Aktion, Ziel-URL, Vorher/Nachher, Begruendung, Git-Commit, Ruecknahmestatus.
- **Fremderkennung.** Beim Crawl werden Titel und Meta-Description gegen den zuletzt protokollierten Stand verglichen. Weicht etwas ab, ohne dass der Autopilot es war, wird es als `urheber="mensch"` gebucht — sonst rechnet die Wirkungsmessung fremde Effekte uns zu. Seiten ohne Historie bekommen einen Vergleichspunkt, sonst waere beim naechsten Lauf nichts erkennbar.
- Protokollierung im ApplyAgent nach jedem angewendeten Fix (Erfolg wie Fehlschlag). Fixes ohne geaenderte Datei ("bereits angewendet") werden bewusst NICHT gebucht — sonst fuellt sich das Buch taeglich mit Nicht-Ereignissen.
- `diff_text()` fuer lesbare Vorher/Nachher-Vergleiche, `als_text()` fuer CLI und Telegram, `markiere_zurueckgenommen()` fuer den Rueckweg.

### Changed
- Eine fehlende `change_log`-Tabelle ist der Normalzustand einer frischen Installation und wird nicht mehr als Warnung geloggt.

### Verified
- ApplyAgent gegen ein echtes Git-Repo: Aenderung angewendet, Commit `bf69e64989ea`, Eintrag mit Diff im Buch. Zweiter Lauf erzeugte korrekt keinen zweiten Eintrag.

### Tests
- +49 Tests (`tests/test_changelog_book.py`). Total: **538**.

## [1.6.0] - 2026-08-17

### Fixed
- 🔴 **Core Web Vitals kamen seit Monaten NIE an.** Der Analyzer las den PageSpeed-Schluessel ausschliesslich aus `source_config.pagespeed.api_key` in `projects.yaml` — dieses Feld war bei **keinem** der 5 Projekte gesetzt, waehrend ein gueltiger Schluessel die ganze Zeit in der `.env` lag (`core/config.py` kannte `PAGESPEED_API_KEY` gar nicht). Jede Anfrage ging unauthentifiziert in Googles gemeinsames Kontingent und kam als `429 Quota exceeded` zurueck; im Log stand nur "PageSpeed unavailable". Erneut derselbe Fehlertyp: eine Messung faellt still aus. Schluessel wird jetzt aus Projekt-Config ODER Umgebung ODER `.env` gelesen, und der Waechter meldet, wenn keiner da ist.

### Added
- **Bild-Audit** (`analyzers/image_audit.py`) — bisher wurde bei Bildern nur der Alt-Text geprueft. Neu: Dateigroessen per echtem HEAD (max. 20/Seite, parallel), veraltete Formate, fehlende `width`/`height` (Layoutspruenge/CLS), Lazy-Loading-Fehler beim ersten grossen Bild (LCP!), fehlendes `srcset`, nichtssagende Dateinamen, Bildlast je Seite, og:image vorhanden/erreichbar/gross genug. Bewusst KEIN Befund fuer fehlendes `title` und fuer `alt=""` (beides korrekt, begruendet im Modul-Docstring).

### Verified
- joseph-hehenwarter.de: 2x **high** `image_lcp_lazy_loaded` (erstes grosses Bild mit `loading="lazy"` auf /finanzierung/factoring und /leasing — manuell bestaetigt), 912 KB Heldenbild auf der Startseite. Erstmals gemessene Core Web Vitals: Startseite LCP **4,6 s**, `/kontakt` Performance **42/100, LCP 18,1 s**.
- tentacl.ai: `https://tentacl.ai/og-home.jpg` liefert **404** (per curl bestaetigt) — soziale Vorschau kaputt.
- Zwei eigene Fehlalarme im Live-Lauf gefunden und behoben: HEAD-Abrufe gaben sich nicht als Browser aus (Next.js lieferte PNG statt WebP -> jedes Bild waere als "veraltet" gemeldet worden), und `/_next/image?url=…` liess jede Datei "image" heissen. Beide als Regressionstest festgenagelt.

### Tests
- +57 Tests. Total: **489**.

## [1.5.1] - 2026-08-17

### Fixed
- **Score bestrafte gruendlicheres Pruefen.** Die Abzuege waren absolut: Wer mehr Seiten crawlen liess, sammelte zwangslaeufig mehr Befunde und bekam eine schlechtere Note, obwohl sich an der Website nichts geaendert hatte (tentacl.ai 8,9 -> 3,2 und lovebianca 45,7 -> 14,0, nachdem die Crawl-Limits an die echte Seitenzahl angepasst wurden). Jetzt zaehlt die Befunddichte: Befunde je Seite, hochgerechnet auf eine Referenzgroesse von 15 Seiten. Bei genau 15 geprueften Seiten ist das Ergebnis identisch mit der bisherigen Formel; ist die Seitenzahl unbekannt, wird NICHT normiert. Neue Bewertung: tentacl-ai 9,0 (23 Seiten) - joseph 73,4 (17) - skinmatch 18,9 (4) - lovebianca 40,8 (40) - topal 10,8 (18). Kleine Websites mit hoher Befunddichte fallen dadurch zu Recht zurueck.
- **lovebianca-ai: Sitemap wurde nie gelesen.** `https://www.lovebianca.ai/sitemap.xml` antwortet 301 auf die Adresse ohne `www`; der Crawler fiel auf die Startseiten-Linkanalyse zurueck. Projekt-Domain auf die kanonische Adresse `https://lovebianca.ai` korrigiert (Canonical der Startseite bestaetigt das) - der Sitemap-Index mit drei Unterkarten wird jetzt aufgeloest: **40 statt 15 Seiten** erfasst.

### Tests
- +17 Tests (`tests/test_score_normalisierung.py`), inkl. Nachweis, dass gleiche Befunddichte bei doppelter Seitenzahl dieselbe Note ergibt und dass echte Verschlechterungen weiterhin durchschlagen. Total: 432.

## [1.5.0] - 2026-08-17

Ausbau zum selbstueberwachenden, lernenden Werkzeug — Fahrplan-Schritte 3 bis 7.

### Added
- **Lernschleife (`learnings`)** — widerlegte Befunde landen in `refuted_findings` statt zu verpuffen. `muster_bericht()` zeigt, welcher Befundtyp wie oft und bei wie vielen verschiedenen Projekten widerlegt wurde; ab 2 Projekten gilt er als Analyzer-Bug, nicht als Zufall.
- **Richtlinien-Radar (`radar`)** — 8 Themen-Landkarten uebersetzen Meldungen aus den vorhandenen SEO-Feeds in betroffene Pruefbereiche (Core Web Vitals, KI-Suche, KI-Crawler, strukturierte Daten, Inhaltsqualitaet, doppelte Inhalte, Indexierung, Ranking-Updates). Google-eigene Quellen und Mehrfachtreffer bekommen hohe Relevanz. Live: 119 Meldungen ausgewertet, 17 relevante.
- **Wochenbericht (`weekly`)** — verstaendliches Deutsch statt Rohbefunde: Note je Projekt, Veraenderung zur Vorwoche, Suchklicks, und die drei wirkungsvollsten Massnahmen. Aus 134 Rohbefunden werden drei Zeilen. Als Text, als eigenstaendiges HTML (keine externen Verweise) und per Telegram; oberhalb des Telegram-Limits automatisch die kompakte Fassung.
- **Google Analytics 4 als Datenquelle** (`sources/ga4.py`) — Nutzer, Sitzungen, Aufrufe, Absprung-/Interaktionsrate, Kanaele und Top-Seiten. Neuer Befund `high_bounce_page` (ab 30 Sitzungen und 70 % Absprung) beantwortet endlich "wird oft angezeigt, aber die Besucher springen sofort ab". Laeuft unabhaengig von GSC.
- **DataForSEO als Datenquelle** (`sources/dataforseo.py`) — Suchergebnisse, Suchvolumen, Backlinks. Mit **Kostenbremse** (Standard 25 Abfragen pro Lauf, danach wird keine Anfrage mehr abgeschickt) und strikter Geheimhaltung: jede Ausgabe laeuft durch einen Filter, der Login, Passwort und Base64-Token ersetzt.
- Waechter prueft zusaetzlich, ob aktivierte GA4-/DataForSEO-Quellen auch konfiguriert sind.

### Changed
- `adapter_config.max_pages` je Projekt an die echte Seitenzahl angepasst (Standard 15 schnitt systematisch Seiten ab): tentacl-ai 40, joseph 30, lovebianca-ai 40, skinmatch/topal 20.
- `feedparser` als Abhaengigkeit ergaenzt — ohne sie lief das Radar leer.

### Tests
- +127 Tests (Lernschleife 25, Radar 29, Wochenbericht 26, GA4 24, DataForSEO 33). Total: **414**.

## [1.4.0] - 2026-08-17

### Added
- **Auto-Verify: schwere Befunde pruefen sich selbst.** Vor der Meldung wird jeder High-Finding eines bekannten Typs per HTTP gegen die Realitaet geprueft — existiert das Impressum doch? liegt das Organisations-Schema vor? ist die Seite in Wahrheit verlinkt? haben die Bilder ein alt-Attribut? Widerlegte Befunde verschwinden aus dem Bericht und werden mit Begruendung protokolliert (Rohmaterial fuer die Lernschleife).
- **Grundregel: im Zweifel bleibt der Befund.** Netzwerkfehler, unbekannter Typ oder unklares Ergebnis fuehren nie zum Verwerfen — sonst tauscht man falsche Alarme gegen uebersehene Probleme. Bestaetigte Befunde tragen `verified: True` plus Begruendung.
- Geprueft werden: `missing_impressum`, `missing_datenschutz`, `missing_privacy`, `missing_org_schema`, `orphan_page`, `unreachable_page`, `noindex`, `page_noindex`, `images_without_alt`.

### Verified
- Live an tentacl.ai: 2 weitere Fehlalarme (Impressum + Datenschutz, beide unter dem Standardpfad erreichbar, vom Crawl nicht erfasst) automatisch entfernt, high 25 -> 23. Ohne manuellen Eingriff.

### Tests
- +13 Tests (`tests/test_verification.py`): jeder Fehlalarm-Typ verschwindet, jeder echte Befund bleibt, Netzwerkfehler und unbekannte Typen lassen Befunde unangetastet. Total: 278.

## [1.3.0] - 2026-08-17

### Added
- **`selfcheck` — Selbstueberwachung des Autopilot.** Das Tool bemerkte seine eigenen Ausfaelle nicht: `joseph` war nie gelaufen (Domain zeigte auf eine tote Adresse), `topal` hatte gar keinen Cron, und im Mai scheiterte die Persistenz wochenlang still. Der neue Waechter prueft den Betriebszustand statt der Websites: laeuft jedes aktive Projekt (max. 36 h alt)? hat jedes einen Cron? hat der letzte Lauf ueberhaupt Seiten erfasst? steht die DB unter Migrationskontrolle? ist eine aktivierte Datenquelle auch konfiguriert? gab es einen Score-Einbruch (>=15 Punkte)? Exit-Code 0/1/2 fuer Monitoring, `--notify` meldet per Telegram. Cron taeglich 11:30, nach allen Audits.
- `send_plain_message()` fuer kontextfreie Telegram-Meldungen.

### Tests
- +11 Tests (`tests/test_health_selfcheck.py`). Jeder Ausfall-Fall wird zuerst ROT nachgewiesen; die Crontab ist injizierbar, damit Tests nicht von der Server-Umgebung abhaengen. Total: 265.

## [1.2.3] - 2026-08-17

### Fixed
- **Trust pages cut off by the crawl limit** — `discover_pages()` truncated the sitemap at `max_pages` (default 15) in raw sitemap order. Impressum/Datenschutz/Kontakt usually sit at the END, so on a 17-URL site they were never fetched. The E-E-A-T analyzer then correctly reported "No Impressum found" for a site that has one, and the same gap made the sitemap audit count those URLs as "non-canonical". Root URL + trust pages are now pulled to the front before truncation (`SEOCrawler._prioritize`). Verified on joseph-hehenwarter.de: E-E-A-T 45→85, high-severity findings 5→0.
- **Organization schema subtypes ignored** — only `Organization`/`Corporation` counted, so a valid `FinancialService`, `LocalBusiness` etc. was reported as "No Organization schema found". A `@type` **array** (`["Organization","FinancialService"]`, valid schema.org) was missed too. New `_is_organization()` accepts 30+ documented subtypes and both notations.
- **Phantom non-canonical sitemap URLs** — every sitemap entry outside the crawl counted as non-canonical, and a trailing slash alone (`https://site.de` vs `https://site.de/`) was enough to trigger it. Uncrawled URLs are now treated as unknown, and comparison is slash-insensitive. Genuine mismatches (e.g. `?ref=` params) are still reported.
- **Decorative images flagged as accessibility defects** — `alt=""` is the CORRECT markup for decorative images (WCAG 1.1.1: screen readers must skip them); only a MISSING alt attribute is a defect. `role="presentation"`, `role="none"` and `aria-hidden="true"` are now honoured as well.

### Tests
- +15 tests in `tests/test_false_positive_fixes.py`, one per corrected behaviour plus guards that genuine findings still fire. Total: 254.

## [1.2.2] - 2026-05-30

### Fixed
- **Phantom "unreachable from homepage" issues** — `link_graph._normalize()` collapsed `https://site` (project domain) and `https://site/` (crawled homepage) into different nodes, so the BFS started at a node with no outlinks and reported *every* page — including the homepage itself — as unreachable. Root URLs now normalize to a single canonical key. Affected every project, worst on SPAs.
- **False near-duplicate flags on SPAs** — the duplicate detector had no `text_content` and fell back to `title + h1 + meta`; with a shared brand suffix this collapsed unrelated pages (e.g. `/start` vs `/impressum`) into "near-duplicates". The crawler now captures the semantic `<main>`/`<article>` text (boilerplate stripped) as `PageData.text_content`, and SimHash is skipped below `MIN_SIMHASH_WORDS` (25). On a real lovebianca.ai crawl: unreachable 15→1, near-duplicate 12→0, overall score 17→58, high-severity 29→3.

### Tests
- +5 tests (link-graph homepage normalization + genuine-orphan retention, crawler main-region text extraction + fallback, short-page duplicate guard). Total: 238.

## [1.2.1] - 2026-05-30

### Fixed
- **JSON-LD `@graph` false positives** — The crawler now flattens `@graph` wrappers into their individual entity nodes before analysis (`_expand_jsonld_graph` in `sources/crawler.py`). Previously a single `<script>` containing `{"@context": ..., "@graph": [...]}` surfaced as one block without a top-level `@type`, so the Schema Validator, E-E-A-T and GEO analyzers all missed the real entities and falsely reported "JSON-LD block without @type". Affects modern SSR sites (Yoast/RankMath-style graphs). On a real lovebianca.ai crawl this removed ~10 phantom schema issues and lifted GEO 74→84 and E-E-A-T 65→75.

### Tests
- +4 crawler tests for `@graph` flattening (passthrough, outer-type retention, nested graphs, end-to-end parse). Total: 233.

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
