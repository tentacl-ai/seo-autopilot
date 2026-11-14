# Arbeitsplatz betreiben und prüfen

## Vorschau ohne echte Daten

Aus der getrennten Arbeitskopie mit dem vorhandenen Python-Environment:

```bash
python -m seo_autopilot.arbeitsplatz --demo --port 8765
```

Nur `127.0.0.1:8765`, Zugang `demo`, erfundene Daten unter einem temporären Verzeichnis. Kein Scheduler, kein Versand und keine neuen Datenabfragen. Beenden mit Ctrl+C entfernt die temporären Daten. Die Demo ist kein neuer öffentlicher Hub-Dienst.

## Konfiguration vor einer späteren Übernahme

`operations/arbeitsplatz-config.example` dokumentiert das Format. Eine private Kopie benötigt Rechte 600. Sie enthält ausschließlich serverseitige Projektzugänge und Betriebsfreigaben. Zugänge und echte Konfiguration niemals in Git oder Konsolenausgaben schreiben. Einen langen Zugang über einen sicheren lokalen Eingabeweg erzeugen; nur dessen SHA-256-Wert in `access[].sha256` hinterlegen. Rohcode über die bestehende sichere Zugangspflege weitergeben, nicht per Agentenmail.

```bash
python -m seo_autopilot.arbeitsplatz --config /privater/pfad/arbeitsplatz.conf --initialize
python -m seo_autopilot.arbeitsplatz --config /privater/pfad/arbeitsplatz.conf --port 8765
```

`--initialize` legt ausschließlich zusätzliche Tabellen im ausdrücklich angegebenen `state_db` an. `source_db` bleibt lesend. Pfade müssen vom Betreiber bestimmt sein. Es gibt keine implizite Migration oder Initialisierung beim Fachzugriff. Der Arbeitsplatz unterstützt in dieser Version SQLite; bestehende PostgreSQL-Installationen werden nicht still auf SQLite umgestellt.

Für die spätere Einbindung in die bestehende FastAPI-Anwendung `SEO_WORKSPACE_CONFIG` auf diese private Datei setzen. Die vorhandene API mountet den Arbeitsplatz dann unter `/arbeitsplatz/`; ohne Variable bleibt das bestehende Verhalten erhalten. Die erlaubten Hosts müssen die echte Zieladresse explizit enthalten. Die Laufzeit wird im aktuellen Ausbau nicht umgestellt oder neu gestartet.

Für Agenten denselben privaten Konfigurationspfad und den Zugang über `SEO_WORKSPACE_TOKEN` aus dem Secret-Speicher geben, dann `--stdio` starten. Keine Schlüssel im Argument, Dateinamen oder URL. Der lokale Test kann den expliziten Demomodus mit `--stdio` verwenden.

## Prüfungen

```bash
python scripts/test-isoliert.py
black --check seo_autopilot/ tests/test_arbeitsplatz.py
flake8 seo_autopilot/ --max-line-length=100 --ignore=E501,W503,E203
node --check seo_autopilot/arbeitsplatz/web/app.js
bash scripts/check-sync.sh
python scripts/browser-arbeitsplatz.py
```

Der Teststarter entfernt echte Anbieter-/Telegram-Zugänge, setzt die Abo-CLI auf einen nicht vorhandenen Testpfad und sperrt externe Socket-Verbindungen. Temporäre Datenbanken und synthetische Providerantworten verwenden. Wenn in der Sandbox die Thread-/Eventloop-Kommunikation hängt, dieselben isolierten Tests mit erlaubter lokaler Prozesskommunikation ausführen; keine Kundenzugänge hinzufügen und keinen hängenden Lauf als Erfolg zählen.

Chromium muss aus der bestehenden Playwright-Installation verfügbar sein. Bei abweichendem Browserort `PLAYWRIGHT_BROWSERS_PATH` auf die Installation setzen. `SEO_BROWSER_ARTIFACTS` bestimmt das Screenshot-Verzeichnis; Standard `/tmp/seo-workspace-browser`.

## Übernahme-Gate

Vor einer Produktivübernahme:

1. Unveröffentlichte Bestandscommits und fremdes WIP gesondert zuordnen. Der Ausbauzweig basiert auf dem veröffentlichten Stand; ihn nicht blind über den neueren Bestandsbaum kopieren.
2. Zielumgebung, Prozess, Hostfreigabe und Zugangskonfiguration festlegen. Neue öffentliche Adresse nur mit Roberts Go. Keine offene Betreiber- oder Kundenoberfläche.
3. Auditdaten unverändert lassen; Arbeitsplatzspeicher ausdrücklich wählen. Backup und Probe-Wiederherstellung vor der ersten Schemaergänzung.
4. Geprüften Commit/Tag übertragen, Prozess nur nach Freigabe umstellen. Anmeldung, Projektgrenzen und Desktop/Handy gegen die echte Zielumgebung prüfen.
5. Zusatzdaten, Gesprächsassistent und Zeitpläne bleiben aus, sofern sie nicht separat eingerichtet/freigegeben wurden. Keine neuen Empfänger, Mails oder Veröffentlichungen.

## Backup und Rückweg

SQLite-Sicherungen über die SQLite-Backup-API erstellen, damit eine laufende WAL korrekt berücksichtigt wird. Für den Arbeitsplatz die gesamte Zustandsdatei sowie die private Konfiguration im bestehenden verschlüsselten Backup sichern; Zugangswerte nicht ins Protokoll schreiben. Restore zuerst in einer getrennten temporären Datei mit Integritätsprüfung und Versions-/Projektzählung nachweisen.

Rollback: Arbeitsplatz-Mount deaktivieren bzw. vorherigen Codecommit zurücknehmen. Die neuen Tabellen bleiben erhalten; keine automatischen DROP-/DELETE-Befehle. Vor einer echten Rücksicherung Prozesse stoppen, neueres Material sichern und Roberts Freigabe zum Überschreiben abwarten. Bestehende Audit-/GSC-/Website-Daten werden vom Ausbau nicht ersetzt.

## Geplante Datenabrufe

Erst nach separat freigegebener Datenpolicy und `scheduled_allowed`:

```bash
python -m seo_autopilot.arbeitsplatz --config /privater/pfad/arbeitsplatz.conf --run-due
```

Der Befehl nutzt einen Betreiberzugang aus `SEO_WORKSPACE_TOKEN`. Es wird kein Cronjob automatisch angelegt. Ein Fehler behält die Kostenreserve und die Auftragskennung; nicht automatisch neu unter anderer Kennung anlegen. Provider-Kontodeckel zusätzlich prüfen. Änderungen der Tarife, Modelle oder Ergebnisumfänge erfordern neue Kostenschätzungen.
