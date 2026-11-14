# SEO Autopilot

Verantwortlich: Robert Bückel. Prüfstand: 14.11.2026.

## Zweck und Arbeitsort

Tentacls SEO-Operator verbindet Messdaten, Empfehlungen, kontrollierte Website-Änderungen und deren Wirkung. Kanonische Bestandsquelle: Hub `/opt/odoo/docs/seo-autopilot`, Repository `tentacl-ai/seo-autopilot`, Branch `main`. Von Robert am 14.11.2026 ausdrücklich bestätigt. Globale Regeln und `/opt/monitoring/governance/PROJECT_REGISTRY.md` gelten darüber.

Neue Arbeit erfolgt isoliert unter `/home/claude-user/arbeitskopien/`, ausgehend von einem benannten Commit. Die ältere Parallelkopie `/opt/apps/seo-autopilot` ist kein Ausbauziel. Der Bestandsbaum enthält fremdes WIP und drei unveröffentlichte Commits: nicht zurücksetzen oder pauschal pushen.

Bestehende Jobs und Daten bleiben erhalten. Pulse liest `reports/pulse/` nur lesend. Ausbau im vorhandenen Python-/FastAPI-Stack. Neue Oberflächen zunächst lokal mit Beispieldaten; kein neuer Live-Dienst auf dem Hub. Veröffentlichung oder Produktivübernahme braucht einen geprüften Deployweg und Freigabe.

## Befehle

- Tests: `python scripts/test-isoliert.py`; Betriebshinweise unter `operations/arbeitsplatz.md`.
- Format/Lint: `black --check seo_autopilot/ tests/` und `flake8 seo_autopilot/ --max-line-length=100 --ignore=E501,W503,E203`.
- Dokumentationsabgleich: `bash scripts/check-sync.sh`.
- Lokale Vorschau: `python -m seo_autopilot.arbeitsplatz --demo` (Loopback und temporäre Beispieldaten).

## Projektregeln

- Teuerster Fehler: falscher Mandant, unerlaubte Website-Änderung, erfundener Erfolg oder unkontrollierte Datenkosten.
- Neue Fach-APIs sind ohne Zugang geschlossen. Jeder Zugang ist serverseitig an seine Projekte und Rechte gebunden; fremde IDs liefern keine Daten. Keine Schlüssel in URLs, Browserablage oder Logs.
- Betriebsrechte nicht durch Projektwissen oder Agententexte erweitern. Der Arbeitsplatz hat keine Website-Apply-, Versand- oder Veröffentlichungsfunktion.
- Quelle, Stand und Messart mitliefern. Fehlgeschlagene Messung ist weder null noch Rankingverlust. GSC-Durchschnitt, SERP-Momentaufnahme, Places und KI-API-Antworten bleiben getrennt.
- Kostenpflichtige Daten standardmäßig aus. Aktivierung/Budgeterhöhung nur nach Go; gemeinsamer persistenter Deckel auch bei parallelen Aufträgen. Unklar berechnete Aufgaben nicht automatisch wiederholen.
- Bestandsdaten nur lesen. Lesen legt dort keine Tabellen an. Neue Tabellen nur in ausdrücklich gewähltem Arbeitsplatzspeicher initialisieren.
- Keine Kundenkonfiguration, Secrets, Datenbanken oder Berichte committen. Tests benutzen temporäre Daten und simulierte Provider.
- Annahmen kennzeichnen; bestätigte Fachangaben nicht still überschreiben. Berichte privat und unveränderlich versionieren. Inhalte bei Darstellung als Text behandeln.
- Eigene Änderungen gezielt committen; fremdes WIP erhalten. Push ist kein Produktiv-Deployment.

## Regelmäßiger SEO-Betrieb

Roberts Auftrag vom 14.11.2026 umfasst Zuverlässigkeitskorrekturen, die lesende Bestandsprüfung von `tentacl.ai`, Detailkontrollen neuer Bilder und die Vorbereitung eines festen Nacht-/Wochenablaufs. Sichere eigene Websitekorrekturen folgen dem bestehenden Autopilot-Modus; neue Erkenntnisse heben keine Sperrliste auf. Umfang, Abnahme, Übernahme und Rückweg: `operations/seo-betrieb.md`. Neue öffentliche Dienste, Kundenschreibrechte und Kosten bleiben gesonderte Entscheidungen.

## Offene Freigaben

Öffentliche Zieladresse, Kundenzugänge, neue bezahlte Daten und Übernahme der Arbeitsplatzoberfläche bleiben offen. Das separat freigegebene Betriebspaket wurde am 14.11.2026 produktiv übernommen; Nachweise und verbleibende Störungen: `operations/SEO_UEBERNAHME_20261114.md`.
