# Changelog

All notable changes to this project will be documented in this file.

## [1.16.0] - 2026-09-18

"SEO-Profi" - Stufen 2 bis 4 nach dem Rundumschlag vom 18.09.2026. Robert: "Das soll
ein Profi in SEO sein, seine Liste abarbeiten und die Seite nach besten
SEO-Erkenntnissen umsetzen, auch textlich - voll automatisch - und ein Audit abgeben,
wo wir stehen." Vollautomatische Texte zunaechst nur auf tentacl.ai (Robert-Entscheidung).

### Added - Empfehlungen und Texte (Stufe 3)
- **`empfehlungen.py`**: konkrete Empfehlungen je Seite auf Deutsch - `faq_ergaenzen`, `abschnitt_ergaenzen`, `ueberschrift_verbessern`, `interne_links`, `antwort_zuerst`, `neue_seite`, `titel_beschreibung`. Grundlage: Search Console Suchbegriff x Seite (90 Tage) + Seiteninhalt + KI ueber Roberts Abo (`abo_ki`). **Nur aus echter Nachfrage** - ohne Suchdaten keine Empfehlung, keine Begruendung "fuer KI/GEO". Cache ueber Inhalts-Hash.
- **`empfehlungen_umsetzen.py`**: Arbeitsliste nach Nachfrage x Inhaltsluecke. In Betriebsart `autopilot` werden sichtbare Texte automatisch geschrieben (FAQ-Block, Abschnitt, H1, erster Absatz, interner Link), sonst Freigabe. Schutzgelaender: nur Fakten der Website, unbekannte Fachbegriffe blockieren, Platzhalter nie live, Impressum/Datenschutz/AGB + `seo_regeln.gesperrte_seiten` gesperrt, max. 3 Aenderungen je Lauf und 1 je Seite und Woche, zweiter KI-Pruefdurchgang ("stimmt / hilfreich / natuerlich"), Git-Commit je Aenderung, Wirkungsmessung.
- CLI `empfehlungen --projekt X [--erzeugen] [--umsetzen] [--stand]`; Kundenbericht-Abschnitt "Was Sie auf Ihren Seiten verbessern koennen".
- Adapter schreibt nie in Dateien mit offenen fremden Aenderungen (andere Sitzungen bearbeiten tentacl.ai `dist/` von Hand).

### Added - Pruefungen (Stufe 2)
- `broken_internal_link` (auch Ziele ausserhalb des Crawls, Platzhalter wie `home_url`), `soft_404_catchall`, hreflang (Ziel, Rueckverweis, Sprache, x-default), `utility_page_indexable`, `missing_local_business_schema`, `heading_level_skipped`, `duplicate_title`/`duplicate_meta_description`, `mixed_content`, `sitemap_foreign_host`.
- Identitaetsschutz: `erwartet` in projects.yaml - passt die Startseite nicht, bricht der Audit vor jeder Analyse und jedem Auto-Fix ab (Anlass: am 18.09. wurde unter einer Port-Adresse eine fremde Kundenseite als "camping-beispiel" geprueft).

### Added - Einrichtung "grosse Packung" (Stufe 4)
- `einrichtung.py`, CLI `einrichten --projekt X --domain URL [...] [--schreiben]`: Website/Weiterleitungen/Sitemap, `erwartet`-Vorschlag, Search Console + GA4 automatisch gefunden und geprueft, IndexNow/Bing, PageSpeed, vollstaendiger Projekt-Eintrag (mit Sicherung), Cron-Zeile, 16 Monate Historie. Jeder Mangel mit Anweisung und Zustaendigem. DataForSEO bewusst nicht enthalten.
- `einrichten --pruefen`: Paket-Tabelle und To-dos fuer alle bestehenden Kunden. Waechter: "Paket unvollstaendig" (Ausnahme `paket: klein`). `docs/einrichtung.md`.

### Changed
- **Note nach Ursache**: Befund-Arten `fehler` / `empfehlung` / `hinweis` (`befund_arten.py`, `note.py`), gleiche Ursache je Adresse einmal, abnehmender Grenznutzen je Typ, Empfehlungen max. 10 Punkte. Alte Noten sind nicht vergleichbar (beratung-beispiel 91 -> 97, natur-beispiel 72 -> 92, coaching-beispiel 43 -> 84, tentacl.ai 45 -> 79) - die Websites haben sich nicht geaendert, die Rechnung schon.
- **Google-KI-Leitfaden** (Search Central, Stand 10.07.2026): llms.txt/KI-Dateien, "Chunking", Schreiben fuer KI und Structured Data ohne Rich Result haben laut Google keine Wirkung -> Art `hinweis`: keine Punkte, keine Auto-Umsetzung, keine Freigabe. Kein automatisches generisches WebPage-JSON-LD mehr.
- Crawler folgt nur noch dem eigenen Host.
- Wirkungsmessung: Aenderungen an einer Seite am selben Tag werden als ein Paket gemessen; Startseiten-Adresse fuer die Search Console korrigiert.

### Fixed
- Bildgewicht/`image_oversized`: gemessen wird die am Handy geladene srcset-Variante (beratung-beispiel, camping-beispiel). `image_lcp_lazy_loaded` gegen das echte LCP-Element (PageSpeed, sonst Playwright). llms.txt-Links mit Notiz. sameAs erkennt Instagram, Google-Unternehmensprofil, Tripadvisor, TikTok, XING, ProvenExpert.
- `intent_geo_agent` lief nie (erwartete ein Feld, das der Audit nie befuellt) - ersetzt durch `empfehlungen.py`.

### Tests
- +262 Tests; jeder Fehlalarm-Schutz zuerst am alten Code rot.

## [1.15.0] - 2026-09-18

"Ehrlicher Betrieb" - Stufe 1 nach dem Rundumschlag vom 18.09.2026. Vier Pruefer
(Code, Treffsicherheit, Betrieb, Markt) fanden mehrere Bausteine, die seit Wochen
still ausfielen, waehrend alles "ok" meldete.

### Fixed
- **Pillow fehlte im venv**: Bildmasse-Reparatur und Bild-KI liefen nie. Weil der Adapter bei fehlender Bibliothek still "nichts zu tun" meldete, wurden die Befunde als **behoben** gefuehrt (tentacl.ai /campingplatz-software/: 27 von 33 Bildern live ohne Masse, Status "fixed"). Pillow installiert + in requirements.txt.
- **Playwright fehlte im venv**: JavaScript-Seiten wurden nie gerendert (cron.log: 272x "nicht installiert", 0x Erfolg). Installiert; Chromium liegt jetzt in `.browsers/` im Projektordner, damit der root-Cron denselben Browser findet. Gerendert wird in Handygroesse (412x915, mobile-first wie Google). Browser und Playwright werden auch nach einem Fehler sauber beendet.
- **PageSpeed lief in keinem Cronlauf**: Der Schluessel lag in `.env`, die Settings suchten `.env` aber relativ zum Arbeitsordner - und die Audit-Cronzeilen laufen ohne `cd`. Jetzt absoluter Pfad. Der Waechter lief MIT `cd` und sah den Schluessel deshalb - der Fehler war fuer ihn unsichtbar.
- **Abgestuerzte Audits meldeten Erfolg**: `run` gab "done" und Exit 0 aus. Jetzt Exit 1 mit Projekt und Grund; ein fehlgeschlagener Lauf wird gespeichert (Status `failed` + Fehlertext), damit der Waechter ihn meldet. Scheitert das Speichern, gilt der Lauf ebenfalls als Fehler.
- **"Nichts geaendert" hiess immer "schon erledigt"**: Neuer dritter Ausgang `nicht_behebbar` (z. B. Bild ohne lokale Datei, Seite in einem anderen Webroot wie /seo-check/). Der Befund bleibt offen, es gibt keinen Eintrag im Aenderungsbuch und keinen taeglichen Fehlschlag mehr.
- **GA4-Zahlen wurden nie gespeichert**: landen jetzt mit `pagespeed_status` in `seo_audits.analytics_data`.
- Such-Operatoren (`site:`, `inurl:` ...) zaehlen nicht mehr als Chance ("Striking distance: 'site:tentacl.ai'").

### Added
- **Waechter prueft das Werkzeug selbst**: fehlendes Pillow/Playwright/feedparser oder fehlender Browser = kritisch, fehlender PageSpeed-Schluessel = Warnung.
- **Waechter prueft die Freigabe-Schlange**: Vorschlaege aelter als 14 Tage und offene Vorschlaege fuer abgeschaltete Projekte.
- **Freigaben raeumen sich nach jedem Lauf auf** (`erledigte_schliessen`), fuer alle Projekte - vorher nur fuer Websites mit Kundenbericht.
- `freigabe --projekt X --alle-ablehnen` fuer abgeschaltete Projekte.
- Log-Zeilen mit Zeitstempel; woechentliche Log-Rotation (`/etc/logrotate.d/seo-autopilot`).

### Tests
- `tests/test_betrieb_ehrlich.py` (+12), jeder Ausfall zuerst am alten Code rot bewiesen. Ein Bildmasse-Test lief bisher nie (wurde wegen fehlendem Pillow uebersprungen) und hatte eine falsche Pruefung - korrigiert.

## [1.14.1] - 2026-09-16

### Fixed
- **Drei Fehlalarme, am Live-HTML von beratung-beispiel.de widerlegt** (Note 67,1 → 89,6, Befunde 46 → 16, high 2 → 0):
  - `image_missing_dimensions`: Bilder mit Next.js `fill` (`data-nimg="fill"`, `position:absolute` + 100 % Breite/Hoehe) koennen das Layout nicht verschieben — der Container gibt den Platz vor, width/height waeren dort sogar falsch. 5 von 5 gemeldeten Bildern waren solche.
  - `image_lcp_lazy_loaded` / `image_lcp_no_priority`: Ein Bild gilt nur noch als LCP-Kandidat, wenn vor ihm hoechstens `VORTEXT_ERSTER_BILDSCHIRM` (900) Zeichen sichtbarer Text stehen. Auf der Factoring-Seite ist das Heldenbild ein CSS-Hintergrund und das erste `<img>` eine Prozessgrafik weit unten; "lazy" ist dort richtig.
  - `near_duplicate_content`: Nach dem SimHash-Treffer wird jetzt die echte Wortueberschneidung geprueft (`wortueberschneidung`, Jaccard ≥ 0,4). /finanzierung/factoring und /absicherung/vollmacht hatten Abstand genau 10 bei 8 % gemeinsamen Woertern. Ohne Text auf einer Seite bleibt der Befund stehen (im Zweifel behalten).
  - `missing_about_page`: "ueber-mich"/"über-mich" zaehlen als Ueber-Seite (bei Einzelberatern der uebliche Name).
- 9 neue Tests (816), jeder Fehlalarm zuerst rot bewiesen.

## [1.14.0] - 2026-09-16

### Added
- **Kunden-Wochenbericht** (`kundenbericht.py`, CLI `kundenbericht`) — EIN Bericht, fuer jede Website gleich aufgebaut (beratung-beispiel, natur-beispiel, tentacl-ai), montags 07:40 per Mail: Auffaelliges, Entscheidungen mit Knopf, Google-Suche (Woche/Vorwoche, Suchbegriffe, Seiten), Besuche nach Herkunft (GA4), Neu im Markt, KI-Sichtbarkeit (ChatGPT/Gemini/Claude mit Websuche), Website-Pruefung, Bing, website-eigene Zusaetze (`bericht.extras`) und Zustand des Werkzeugs. Loest ab: natur-beispiel-eigenes Wochenbericht-Skript, Autopilot-`weekly` (nur Telegram) und bei Beispiel-Beratung die alte Lovable-Mail.
- **Marktbeobachter** (`marktradar.py`, CLI `marktradar`, taeglich 06:30) — Fachquellen (neu: Google Ads, Google Analytics, Microsoft Ads, Bing Webmaster, PPC Land, SE Roundtable) plus ChatGPT und Gemini mit Websuche. **KI-Meldungen ohne erreichbare Quell-Adresse werden verworfen**; Startseiten und Weiterleitungsdienste zaehlen nicht als Quelle. Tabelle `markt_meldungen` (CREATE TABLE IF NOT EXISTS).
- **Impulse je Website**: Claude waehlt hoechstens 3 Neuerungen, aus denen fuer die Website etwas zu tun ist. Die Quell-Adresse kommt immer aus der Meldung, nie aus der KI-Antwort.
- **Entscheidungen mit Knopf** (`entscheidungen.py`): Impulse und offene Freigaben (je Befundtyp gebuendelt) landen auf den Entscheidungsseiten tentacl.de/freigabe/e. Ein Klick auf „Ja/Nein“ bei einer Freigabe wird beim naechsten Bericht in die Freigabe-Schlange uebernommen.
- Richtlinien-Radar: Themen „Bezahlte Suche (Google Ads / Microsoft Ads)“ und „Messung / Analytics / Einwilligung“.
- `notifications/mail.py`: Meldungen per Mail an Robert, nur bei geaendertem Inhalt.
- `ProjectConfig.bericht` und `ProjectConfig.geschaeftswert` — vorher verwarf `_save_config()` diese Abschnitte beim naechsten Audit still.

### Changed
- `selfcheck --notify` meldet per Mail statt Telegram (seit 09.09.2026 abgeschafft — Waechter-Meldungen liefen ins Leere).
- Selbstheilung: Freigaben, deren Befundtyp im juengsten Audit nicht mehr vorkommt, werden beim Bericht automatisch geschlossen (Notiz im Datensatz) statt als Knopf zu erscheinen.
- Wirkungsmessung im Waechter: Aenderungen von Projekten, die nie messbar sind (unbekanntes Projekt, Search Console eingeschaltet aber nicht eingerichtet), loesen keinen Dauer-Alarm mehr aus.
- `ki_sichtbarkeit.frage_*` nehmen einen eigenen Hinweistext an (fuer den Marktbeobachter).
- tentacl-ai: GA4 (Property 528143447) als Quelle; beratung-beispiel: IndexNow-Schluessel eingetragen.
- Cron: `weekly`, `radar` (montags) und `freigabe --notify` (taeglich, Telegram) entfernt — ersetzt durch `marktradar` und `kundenbericht`.
- 33 neue Tests (807).

## [1.13.1] - 2026-09-15

### Fixed
- **Fehlalarme bei kleinen Websites** (gefunden an natur-beispiel.at):
  - `missing_datenschutz`: Datenschutz als Abschnitt einer anderen Seite zaehlt jetzt (H2 „Datenschutz…“ oder Link `…#datenschutz`). Vorher nur eine eigene Adresse /datenschutz.
  - `missing_about_page`: Eine Unterseite, die als `url` einer Person oder Organisation im Schema steht (z. B. Person.url = /klaus), gilt als Ueber-Seite.
  - `missing_indexnow`: Mit `source_config.indexnow.key` wird die uebliche Schluesseldatei `/<key>.txt` geprueft (Inhalt = Schluessel). Die Pruefung von `/.well-known/indexnow` wertet HTML-Antworten (Einzelseiten-Apps liefern fuer jede Adresse 200) nicht mehr als Treffer.
- 6 neue Tests (774).

## [1.13.0] - 2026-09-06

### Added
- **Handwerker** (`handwerker.py`) — der Autopilot repariert jetzt wirklich, nicht nur auf dem Papier. Drei Bausteine, die vorher fehlten:
  - **Seitenkontext**: Titel, Beschreibung, H1, Bilder und sichtbarer Text der betroffenen Seite (lokale Datei, sonst HTTP) gehen in jeden KI-Prompt. Vorher bekam die KI nur die Adresse — und ohne Schluessel im Cron liefen ueberhaupt nur Vorlagen, die Dinge wie "20+ Jahre Erfahrung" erfanden.
  - **Plausibilitaetspruefung**: Laengen wie in der Suchergebnisseite, keine unbelegten Behauptungen, keine Projekt-Tabuwoerter (`adapter_config.seo_regeln.verbotene_woerter`), kein HTML, keine Antwort-Artefakte. Im Zweifel wird NICHT geschrieben.
  - **Datei je Seite**: `datei_fuer_seite()` ordnet jede Adresse ihrer HTML-Datei zu. Vorher schrieb der Static-Adapter jede Aenderung in die Startseite, egal welche Seite der Befund meinte; eine unbekannte Adresse fuehrt jetzt zu "nichts geschrieben" statt zur Startseite.
- Neue automatische Reparaturen im Static-Adapter, je Seite: `og:title` (KI), `twitter:card` + Titel/Beschreibung/Bild aus den `og:`-Angaben (Regel), **Alt-Texte per Bild-KI** (Bild wird auf 1024 px normalisiert und zusammen mit dem Seitentext beschrieben; dekorative Bilder mit `alt=""`/`role=presentation` bleiben unangetastet), `width`/`height` aus den lokalen Bilddateien (Regel), WebPage-JSON-LD mit `datePublished`/`dateModified` (Regel, Datum aus Git-Historie bzw. Dateizeit).
- Vorschlagsquellen sind jetzt unterscheidbar: `claude` (KI mit Kontext), `regel` (deterministisch aus vorhandenen Daten), `template` (Vorlage). **Vorlagen laufen nie automatisch**, auch nicht im Autopilot — sie landen mit Begruendung in der Freigabe-Schlange. Am echten ApplyAgent getestet.
- Befunde mit Schwere "low" duerfen automatisch behoben werden, wenn der Typ in `SICHERE_EINGRIFFE` steht (Meta-Ebene und Auszeichnung, nie sichtbarer Text, nie Struktur, nie Adressen). Die harte Sperrliste in `ausfuehrung.py` bleibt unveraendert davor.
- `standard_og_bild` in `adapter_config`: einziges erlaubtes Bild fuer fehlende `og:image` — geratene Pfade wie `/og-image.webp` gibt es nicht mehr.

### Changed
- `CLAUDE_API_KEY` faellt auf den zentralen `ANTHROPIC_API_KEY` zurueck; Modell per `CLAUDE_MODEL` (Standard `claude-opus-5`). Cron-Zeilen der fuenf Audits laden `<eigene Zugangsdaten-Datei>`.
- Die Vorlagen fuer Titel, Beschreibung, H1, `og:image` und `low_ctr_opportunity` sind weg: Ohne Seitenkontext ist jeder geratene Text schlechter als der bestehende. 30 solcher Alt-Vorschlaege wurden in der Schlange als abgelehnt markiert.
- tentacl-ai: `root_path` auf `/var/www/meine-website/dist` (vorher der Container-Pfad `/var/www/landing`, auf dem Host ohne Dateien), Betriebsart **Autopilot**, Tabuwoerter und Markenregeln hinterlegt.

### Tests
- +24 (`tests/test_handwerker.py`): Datei-Zuordnung inkl. Pfadausbruch, Plausibilitaet (erfundene Erfahrung, Tabuwort, Laenge, HTML), jede neue Reparatur inkl. Idempotenz, und die drei Grenzen im echten Agenten (Vorlage nie, KI ja, sicherer Low-Befund ja, unsicherer nein), Git-Wurzel oberhalb von `root_path` (Commit landet im uebergeordneten Repo; per .gitignore ausgenommene Dateien werden geaendert, aber als `not-tracked` gefuehrt statt als Fehler), Wortgrenzen-Kuerzung fuer twitter:title, kein erfundenes `datePublished`. Ein Bestandstest wurde auf die neue Low-Regel angepasst. **768 gesamt.**

## [1.12.0] - 2026-08-19

### Added
- **Langzeit-Historie aus der Search Console** (`historie.py`, CLI `historie`) — bis **16 Monate** rueckwaerts, monatsweise archiviert in der Tabelle `gsc_historie`. Bisher kannte der Autopilot Suchdaten nur als rollierende 28-Tage-Momentaufnahme; damit war weder Saisonalitaet noch ein Vorjahresvergleich moeglich.
- 🔑 **Der Import ist ein Archiv, keine Abfrage.** Google gibt nur 16 Monate heraus — ein heute nicht geholter Monat ist dauerhaft verloren. Einmal importiert, bleibt ein Monat in der eigenen Datenbank stehen, auch wenn Google ihn Jahre spaeter nicht mehr kennt.
- Bericht in deutschem Klartext: Monatsreihe, Beginn der Sichtbarkeit, Vorjahresvergleich, weggebrochene und dazugewonnene Suchbegriffe und Seiten (letzte 3 Monate gegen die 3 davor).
- CSV-Export fuer Excel (`--export`, Semikolon + BOM + Dezimalkomma).
- `GSCDataSource.pull_range()` — Rohzeilen fuer frei waehlbaren Zeitraum und frei waehlbare Dimensionen. `pull_analytics` konnte nur "letzte N Tage ab heute", `pull_url_window` nur genau eine Adresse.
- **Waechter-Anbindung** (`_pruefe_historie` in `health.py`): fehlender Cron und fehlender abgeschlossener Monat werden gemeldet. Ein Projekt ohne Search Console und ein noch leeres Archiv werden bewusst nicht bemaengelt.
- Cron **taeglich 11:15** (vor dem Waechter 11:30). Kostet im Normalbetrieb nur den laufenden Monat, weil abgeschlossene Monate uebersprungen werden.

### Fixed
- **shop-beispiel war auf die falsche Search-Console-Property verdrahtet** (`sc-domain:tentacl.de`), Zugriff besteht auf `https://shop-beispiel.de/`. Das Projekt hat dadurch seit seiner Aufnahme **nie** Suchdaten bekommen — passend dazu stand in `seo_keywords` kein einziger Eintrag. Aufgefallen ist es erst, weil der Historien-Import 16 fehlgeschlagene Monate meldete statt 16 Nullmonate zu erfinden.

### Sperren (jede zuerst rot bewiesen, dann gezielt sabotiert)
1. **Abfragefehler wird nie als Null gespeichert.** Schlaegt eine Abfrage fehl, entsteht kein Eintrag — der Monat bleibt offen und wird beim naechsten Lauf erneut versucht. Ein als "0 Klicks" verbuchter Netzwerkfehler erzeugt sonst einen Einbruch, den es nie gab, und der Autopilot sucht anschliessend nach dessen Ursache. Auch ein **Teilfehler** speichert nichts: lieber kein Monat als ein halber.
2. **Der laufende Monat gilt als unvollstaendig** (`vollstaendig=0`) und faellt aus jedem Vergleich heraus. Am 3. des Monats saehe er sonst immer wie ein Absturz aus.
3. **Abgeschlossene Monate werden nicht neu geholt** — ausser innerhalb der Nachziehfrist von 5 Tagen, weil die Search Console rund drei Tage hinterherhinkt.
4. **Mindest-Datenmenge fuer Vergleiche** (30 Einblendungen) — darunter ist jede Prozentangabe Zufall.
5. **Kein Vorjahresvergleich gegen eine Website, die es noch nicht gab** (100 Einblendungen im Vorjahreszeitraum noetig).

### Verified
- Erster Live-Import: **tentacl-ai, beratung-beispiel, coaching-beispiel, shop-beispiel je 16 Monate** archiviert (742 Zeilen), handel-beispiel hat weiterhin kein GSC.
- 🔑 **Der Live-Lauf deckte Sperre 5 erst auf:** Der Bericht meldete fuer tentacl.ai "+732 Einblendungen gegenueber Vorjahr" — rechnerisch richtig, als Aussage wertlos, weil die Domain erst ab Maerz 2026 ueberhaupt sichtbar ist (10 Nullmonate davor). Solche Zahlen landen sonst in einer Kundenmail. Der Bericht weist jetzt stattdessen den Beginn der Sichtbarkeit aus.
- Cron-Befehl exakt so als root getestet (Exit 0), Dateibesitz der Datenbank unveraendert.
- Waechter am Live-System: keine Historien-Befunde, der bekannte handel-beispiel-Befund bleibt.
- Echte Erkenntnis aus den Daten (beratung-beispiel): Einblendungen von 334 auf 629 fast verdoppelt, aber CTR von 11,7 % auf 4,6 % gefallen und Position von 17,6 auf 24,7 verschlechtert — mehr Sichtbarkeit fuer schlechter passende Suchbegriffe.

### Tests
- +43 Tests (`tests/test_historie.py` 37, `tests/test_health_historie.py` 6). Total: **744**.
- Alle fuenf Sperren wurden nach dem Gruenwerden gezielt sabotiert, um zu belegen, dass die Tests sie wirklich abdecken — jeweils wurden genau die zugehoerigen Tests rot.


## [1.11.0] - 2026-08-18

### Added
- **Wettbewerbsvergleich mit dem eigenen Crawler** (`wettbewerb.py`, CLI `wettbewerb`) — statt Wettbewerbsdaten zu kaufen. Verglichen wird, was oeffentlich im Quelltext steht: Inhaltstiefe, strukturierte Daten (auch welche Auszeichnungen dem Wettbewerb gehoeren und uns nicht), Meta-Angaben. Gemessen wird gegen den **staerksten** Wettbewerber, nicht gegen den Durchschnitt.
- 🔑 **Fremde `robots.txt` wird gelesen und befolgt.** Auf eigenen Projekten crawlt der Autopilot Seiten des Kunden — hier sind es fremde Server. Verbotene Adressen werden nicht abgerufen, es werden wenige Seiten geholt, und der Bot nennt sich beim Namen. (Der normale Audit-Crawler wertete bisher nur das robots-**Meta-Tag** aus, nicht die robots.txt.)

### Warum kein Datenanbieter
Der Kreislauf braucht DataForSEO **nicht**: Eigene Platzierungen, Einblendungen und Klicks liefert die Search Console genauer als jede Schaetzung, Keyword-Chancen (Platz 11–20) sind laengst gebaut. Was ein Anbieter zusaetzlich koennte — **fremde** Platzierungen und ein Backlink-Index — ist mit eigenen Mitteln nicht seriös herstellbar: Googles Ergebnisseiten abzugreifen verstoesst gegen deren Nutzungsbedingungen, und ein Verlinkungs-Index hiesse, das halbe Web zu crawlen. Das Modul `sources/dataforseo.py` bleibt einsatzbereit, ist aber bewusst in keinem Projekt aktiviert.

### Verified
- Live gegen zwei echte Wettbewerber von beratung-beispiel.de: **485 Woerter je Seite gegen 1.152** beim staerksten; fehlende Auszeichnungen `Question`, `VideoObject`, `ImageObject`, `CollectionPage` benannt.
- robots.txt-Befolgung am echten Server nachgewiesen: `/wp-admin/` von compeon.de wurde ausgeschlossen, eine 308-Weiterleitung der robots.txt korrekt verfolgt.

### Tests
- +15 Tests (`tests/test_wettbewerb.py`). Total: **701**.


## [1.10.1] - 2026-08-18

### Fixed
- 🔴 **Freigegebene Vorschlaege wurden nie ausgefuehrt.** Die Freigabe-Schlange aus 1.10.0 nahm Zustimmungen entgegen, der ApplyAgent las sie aber nicht — die CLI versprach "wird beim naechsten Lauf ausgefuehrt", und nichts passierte. Der Kreislauf endete in einer Sackgasse. Jetzt sammelt der Agent freigegebene Eintraege ein, fuehrt sie aus und markiert sie als erledigt. Eine Zustimmung wirkt nur, wenn der Befund im aktuellen Lauf noch besteht — sonst wuerde eine drei Wochen alte Freigabe etwas anfassen, das laengst erledigt ist.

### Verified
- Kompletter Kreislauf an einem echten Git-Repo: Copilot legt vor (Datei unveraendert) → Freigabe → naechster Lauf schreibt den neuen Titel, erzeugt Commit `563a6d9` und setzt die Freigabe auf "ausgefuehrt".

### Tests
- +4 Tests. Total: **686**.

## [1.10.0] - 2026-08-18

Roadmap-Phase 5, Kern: Der Autopilot darf jetzt ausfuehren — aber nur mit Grenzen, die im Code stehen statt in der Dokumentation.

### Added
- **Drei Betriebsarten** (`ausfuehrung.py`, CLI `betrieb`): **Beobachter** (aendert nichts, Standard fuer neue Projekte), **Copilot** (legt jede Aenderung zur Freigabe), **Autopilot** (fuehrt Unbedenkliches aus, legt alles andere trotzdem vor). Ein Tippfehler in der Konfiguration faellt immer auf die sichere Seite.
- **Harte Sperrliste** — 14 Eingriffe laufen **NIE** automatisch, auch nicht im Autopilot-Modus und auch nicht, wenn jemand sie in `whitelist_extra` eintraegt: Kanonisierung, `noindex`, `robots.txt`, Seiten loeschen, Zusammenlegen, Adressumzuege, Weiterleitungsketten. Jede Sperre traegt ihre Begruendung im Klartext.
- **Freigabe-Schlange** (CLI `freigabe`): Vorgelegte Aenderungen mit Begruendung, Entscheidung per `--ja`/`--nein`. Derselbe Befund kommt nicht taeglich wieder, und ein abgelehnter Vorschlag wird nicht erneut gefragt. Taegliche Telegram-Meldung um 12:00.

### Fixed
- 🔴 **Bot-Token stand im Klartext in den Logs.** `httpx` protokolliert jede Anfrage samt vollstaendiger URL auf INFO-Ebene — und der Telegram-Token steht in der URL. In `cron.log` waren es **314 Zeilen**. Protokollierung fuer `httpx`/`httpcore` auf WARNING gehoben, vorhandene Logs bereinigt. **Token-Rotation empfohlen.**
- 🔴 **Ein unbekanntes Feld in `projects.yaml` liess ALLE Projekte verschwinden.** `ProjectConfig(**cfg)` warf, der `except` fing alles ab, und der Autopilot lief danach scheinbar normal weiter — mit null Projekten, sichtbar nur an einer Zeile im Log. Jetzt werden unbekannte Felder mit Warnung uebersprungen und Fehler **je Projekt** isoliert. Genau dieser Fehler trat beim Einbau der Betriebsart auf und haette den gesamten Betrieb lahmgelegt.
- Die Sicherheitsgrenzen standen bisher **nur in der Dokumentation**: `missing_canonical` und `missing_robots_txt` waren in der Standard-Whitelist und wurden automatisch angewendet.

### Verified
- **Umgehungsversuch am echten ApplyAgent**: Autopilot eingeschaltet UND `missing_canonical`, `missing_robots_txt`, `delete_page` ausdruecklich in `whitelist_extra` — alle drei landeten trotzdem in der Freigabe, mit Begruendung.
- Echter Lauf gegen beratung-beispiel im Copilot-Modus: zwei Vorschlaege vorgelegt, Ablehnung per CLI protokolliert, abgelehnter Vorschlag kam nicht wieder.
- Alle fuenf Projekte auf **Copilot** gestellt: Der Autopilot arbeitet den Kreislauf autonom durch, aendert aber nichts ohne Zustimmung.

### Tests
- +41 Tests (`test_ausfuehrung.py` 26, `test_apply_betriebsarten.py` 6, `test_projektladen_robust.py` 6, `test_telegram_kein_token_im_log.py` 3). Total: **682**.

## [1.9.1] - 2026-08-18

### Fixed
- **LCP-Bildbefunde widersprachen der echten Messung.** Der Bildpruefer nimmt das erste grosse Bild im Quelltext als LCP-Kandidaten — die Reihenfolge im HTML sagt aber nichts darueber, wo ein Bild optisch landet. Auf beratung-beispiel.de/finanzierung/factoring stand es an HTML-Position 0 und trotzdem weit unterhalb des ersten Bildschirms; gemeldet wurde "LCP verzoegert geladen", waehrend Google dieselbe Seite mit **98/100 und 2,4 s LCP** bewertete. Jetzt gilt: Liegt fuer die Adresse eine Messung im gruenen Bereich vor, gewinnt die Messung. Und bei Verdacht auf einer bisher **ungemessenen** Seite wird gezielt nachgemessen (max. 3 zusaetzliche Abrufe, Kontingent), statt den Befund ungeprueft stehen zu lassen. Ohne Messwerte bleibt der Befund — im Zweifel lieber melden.
-  nutzte . Das brach, sobald vorher ein anderer Test  aufrief und damit den Loop schloss — der Fehler trat nur im Gesamtlauf auf, nie einzeln. Auf  umgestellt.

### Verified
- beratung-beispiel.de: schwere Befunde **2 → 0**.

## [1.9.0] - 2026-08-18

Roadmap-Phase 3 und 4 — plus die Wächter-Lücke aus 1.8.0 geschlossen. Der Kreislauf steht damit bis zur Priorisierung: beobachten → protokollieren → Wirkung messen → daraus priorisieren.

### Added
- **Geschäftswert** (`geschaeftswert.py`, CLI `wert`) — Phase 3. Rechnet Besucher gegen Anfragen und den hinterlegten Wert je Anfrage (`wert_pro_abschluss` x `abschlussquote`). Findet **verschenktes Geld** (viele Besucher, keine Anfrage) und **unterschätzte Seiten** (wenige Besucher, hoher Wert je Besucher) — genau die Seiten, die eine Priorisierung nach Besucherzahl übersieht.
  - 🔑 **Es gibt keinen Standardwert und keine Schätzung.** Fehlt die Angabe, sagt das Modul „nicht bezifferbar" und nennt, welche Zahlen beim Kunden fehlen. Eine Seite ohne zugeordnetes Ziel bekommt `wert = None`, nicht `0` — „wissen wir nicht" und „bringt nichts" dürfen nie gleich aussehen.
- **Chancen-Motor** (`chancen.py`, CLI `chancen`) — Phase 4. `Geschäftswert x Potenzial x Sicherheit / Aufwand`. Die **Sicherheit kommt aus der eigenen Wirkungsmessung** (Phase 2): erst ab 3 belastbaren Messungen je Änderungsart, darunter neutral statt geschätzt. Ohne Geschäftswert wird nach Sichtbarkeit gewichtet — und das im Bericht ausdrücklich gesagt.
- **Wächter überwacht die Wirkungsmessung** (`health.py`): fehlender Cron-Eintrag, Eintrag **ohne `cd`** (der Fehler, der beim Einrichten tatsächlich passiert ist), und fällige Messungen, die liegen bleiben.

### Fixed
- **Geldbeträge wurden falsch formatiert** — `1.500.00 EUR` statt `1.500,00 EUR`, weil das Ersetzen des Tausendertrenners auch das Dezimalkomma traf.

### Verified
Fünf Fehler, die erst der Live-Lauf gezeigt hat:
- **Das Impressum belegte fünf von sechs Plätzen** der Vorschlagsliste. Pflichtseiten ranken gut (der Firmenname steht drauf), bringen aber keine Anfragen. Dämpfer eingebaut — er greift nur, solange kein echter Geschäftswert hinterlegt ist, denn der regelt die Gewichtung dann selbst.
- **Klicks waren als Maßstab zu grob** (0–21 bei diesen Websites). Jetzt Einblendungen: feiner aufgelöst und misst Nachfrage statt bisherigen Erfolg. Danach steht `/finanzierung/factoring` oben — 364 Einblendungen, Position 33,4, kleiner Aufwand.
- **Eine Seite flutete die Liste**, weil sie zwölf Befunde hatte. Höchstens zwei je Seite, der Rest wird gezählt und ausgewiesen.
- **Der Wächter meldete auf frischen Installationen Fehlalarm** (kein Cron, obwohl nichts zu messen war). Er greift jetzt erst, wenn das Änderungsbuch nicht leer ist — ein Wächter, der grundlos meckert, erzieht zum Wegsehen.
- Der Geschäftswert-Bericht warnt jetzt, wenn auf **allen** Seiten 0 Anfragen stehen: Das heißt fast immer „Anfragen werden nicht gezählt", nicht „niemand fragt an".

### Tests
- +62 Tests (`test_geschaeftswert.py` 29, `test_chancen.py` 23, `test_health_wirkung.py` 10). Total: **639**.

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
- **Live gegen echte Search-Console-Daten** (beratung-beispiel-Startseite, rueckdatierte Testaenderung in eigener Datenbank — die Live-Daten blieben unberuehrt): Der erste Lauf meldete 2x "besser" bei Position 6,7 → 2,8. Die Gegenprobe zeigte: Einblendungen (43 → 40) und Klicks (8 → 6) waren gleichzeitig **gefallen**. Daraufhin wurde die Widerspruchs-Sperre gebaut; derselbe Lauf meldet jetzt korrekt "unveraendert" mit Begruendung. Ohne diesen Live-Lauf waere ein Scheinerfolg in Produktion gegangen.
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
- beratung-beispiel.de: 2x **high** `image_lcp_lazy_loaded` (erstes grosses Bild mit `loading="lazy"` auf /finanzierung/factoring und /leasing — manuell bestaetigt), 912 KB Heldenbild auf der Startseite. Erstmals gemessene Core Web Vitals: Startseite LCP **4,6 s**, `/kontakt` Performance **42/100, LCP 18,1 s**.
- tentacl.ai: `https://tentacl.ai/og-home.jpg` liefert **404** (per curl bestaetigt) — soziale Vorschau kaputt.
- Zwei eigene Fehlalarme im Live-Lauf gefunden und behoben: HEAD-Abrufe gaben sich nicht als Browser aus (Next.js lieferte PNG statt WebP -> jedes Bild waere als "veraltet" gemeldet worden), und `/_next/image?url=…` liess jede Datei "image" heissen. Beide als Regressionstest festgenagelt.

### Tests
- +57 Tests. Total: **489**.

## [1.5.1] - 2026-08-17

### Fixed
- **Score bestrafte gruendlicheres Pruefen.** Die Abzuege waren absolut: Wer mehr Seiten crawlen liess, sammelte zwangslaeufig mehr Befunde und bekam eine schlechtere Note, obwohl sich an der Website nichts geaendert hatte (tentacl.ai 8,9 -> 3,2 und coaching-beispiel 45,7 -> 14,0, nachdem die Crawl-Limits an die echte Seitenzahl angepasst wurden). Jetzt zaehlt die Befunddichte: Befunde je Seite, hochgerechnet auf eine Referenzgroesse von 15 Seiten. Bei genau 15 geprueften Seiten ist das Ergebnis identisch mit der bisherigen Formel; ist die Seitenzahl unbekannt, wird NICHT normiert. Neue Bewertung: tentacl-ai 9,0 (23 Seiten) - beratung-beispiel 73,4 (17) - shop-beispiel 18,9 (4) - coaching-beispiel 40,8 (40) - handel-beispiel 10,8 (18). Kleine Websites mit hoher Befunddichte fallen dadurch zu Recht zurueck.
- **coaching-beispiel: Sitemap wurde nie gelesen.** `https://www.coaching-beispiel.de/sitemap.xml` antwortet 301 auf die Adresse ohne `www`; der Crawler fiel auf die Startseiten-Linkanalyse zurueck. Projekt-Domain auf die kanonische Adresse `https://coaching-beispiel.de` korrigiert (Canonical der Startseite bestaetigt das) - der Sitemap-Index mit drei Unterkarten wird jetzt aufgeloest: **40 statt 15 Seiten** erfasst.

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
- `adapter_config.max_pages` je Projekt an die echte Seitenzahl angepasst (Standard 15 schnitt systematisch Seiten ab): tentacl-ai 40, beratung-beispiel 30, coaching-beispiel 40, shop-beispiel/handel-beispiel 20.
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
- **`selfcheck` — Selbstueberwachung des Autopilot.** Das Tool bemerkte seine eigenen Ausfaelle nicht: `beratung-beispiel` war nie gelaufen (Domain zeigte auf eine tote Adresse), `handel-beispiel` hatte gar keinen Cron, und im Mai scheiterte die Persistenz wochenlang still. Der neue Waechter prueft den Betriebszustand statt der Websites: laeuft jedes aktive Projekt (max. 36 h alt)? hat jedes einen Cron? hat der letzte Lauf ueberhaupt Seiten erfasst? steht die DB unter Migrationskontrolle? ist eine aktivierte Datenquelle auch konfiguriert? gab es einen Score-Einbruch (>=15 Punkte)? Exit-Code 0/1/2 fuer Monitoring, `--notify` meldet per Telegram. Cron taeglich 11:30, nach allen Audits.
- `send_plain_message()` fuer kontextfreie Telegram-Meldungen.

### Tests
- +11 Tests (`tests/test_health_selfcheck.py`). Jeder Ausfall-Fall wird zuerst ROT nachgewiesen; die Crontab ist injizierbar, damit Tests nicht von der Server-Umgebung abhaengen. Total: 265.

## [1.2.3] - 2026-08-17

### Fixed
- **Trust pages cut off by the crawl limit** — `discover_pages()` truncated the sitemap at `max_pages` (default 15) in raw sitemap order. Impressum/Datenschutz/Kontakt usually sit at the END, so on a 17-URL site they were never fetched. The E-E-A-T analyzer then correctly reported "No Impressum found" for a site that has one, and the same gap made the sitemap audit count those URLs as "non-canonical". Root URL + trust pages are now pulled to the front before truncation (`SEOCrawler._prioritize`). Verified on beratung-beispiel.de: E-E-A-T 45→85, high-severity findings 5→0.
- **Organization schema subtypes ignored** — only `Organization`/`Corporation` counted, so a valid `FinancialService`, `LocalBusiness` etc. was reported as "No Organization schema found". A `@type` **array** (`["Organization","FinancialService"]`, valid schema.org) was missed too. New `_is_organization()` accepts 30+ documented subtypes and both notations.
- **Phantom non-canonical sitemap URLs** — every sitemap entry outside the crawl counted as non-canonical, and a trailing slash alone (`https://site.de` vs `https://site.de/`) was enough to trigger it. Uncrawled URLs are now treated as unknown, and comparison is slash-insensitive. Genuine mismatches (e.g. `?ref=` params) are still reported.
- **Decorative images flagged as accessibility defects** — `alt=""` is the CORRECT markup for decorative images (WCAG 1.1.1: screen readers must skip them); only a MISSING alt attribute is a defect. `role="presentation"`, `role="none"` and `aria-hidden="true"` are now honoured as well.

### Tests
- +15 tests in `tests/test_false_positive_fixes.py`, one per corrected behaviour plus guards that genuine findings still fire. Total: 254.

## [1.2.2] - 2026-05-30

### Fixed
- **Phantom "unreachable from homepage" issues** — `link_graph._normalize()` collapsed `https://site` (project domain) and `https://site/` (crawled homepage) into different nodes, so the BFS started at a node with no outlinks and reported *every* page — including the homepage itself — as unreachable. Root URLs now normalize to a single canonical key. Affected every project, worst on SPAs.
- **False near-duplicate flags on SPAs** — the duplicate detector had no `text_content` and fell back to `title + h1 + meta`; with a shared brand suffix this collapsed unrelated pages (e.g. `/start` vs `/impressum`) into "near-duplicates". The crawler now captures the semantic `<main>`/`<article>` text (boilerplate stripped) as `PageData.text_content`, and SimHash is skipped below `MIN_SIMHASH_WORDS` (25). On a real coaching-beispiel.de crawl: unreachable 15→1, near-duplicate 12→0, overall score 17→58, high-severity 29→3.

### Tests
- +5 tests (link-graph homepage normalization + genuine-orphan retention, crawler main-region text extraction + fallback, short-page duplicate guard). Total: 238.

## [1.2.1] - 2026-05-30

### Fixed
- **JSON-LD `@graph` false positives** — The crawler now flattens `@graph` wrappers into their individual entity nodes before analysis (`_expand_jsonld_graph` in `sources/crawler.py`). Previously a single `<script>` containing `{"@context": ..., "@graph": [...]}` surfaced as one block without a top-level `@type`, so the Schema Validator, E-E-A-T and GEO analyzers all missed the real entities and falsely reported "JSON-LD block without @type". Affects modern SSR sites (Yoast/RankMath-style graphs). On a real coaching-beispiel.de crawl this removed ~10 phantom schema issues and lifted GEO 74→84 and E-E-A-T 65→75.

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
