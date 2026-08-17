# DataForSEO einrichten

## Wozu das gut ist

Die Google Search Console zeigt nur die **eigenen** Zahlen: eigene Klicks,
eigene Positionen. Sie beantwortet nie die Frage, die eigentlich zählt —
*wer steht bei diesem Suchbegriff vor uns, und warum?*

DataForSEO liefert genau das, was wir selbst nicht messen können:

| Was | Was man damit sieht |
|-----|---------------------|
| **Suchergebnisse (SERP)** | Wer auf den vorderen Plätzen steht: Position, Domain, Titel, Adresse |
| **Suchvolumen** | Wie oft ein Begriff pro Monat gesucht wird |
| **Backlinks** | Wie viele fremde Seiten auf eine Domain verweisen |

## Schritt 1: Konto anlegen

1. Auf <https://app.dataforseo.com/register> ein Konto anlegen.
2. Guthaben aufladen. Es gibt kein Abo — man zahlt nur, was man abfragt.
3. Unter **API Access** stehen zwei Werte:
   - **API Login** (die E-Mail-Adresse des Kontos)
   - **API Password** (ein generiertes Passwort, *nicht* das Login-Passwort
     der Website)

Diese beiden Werte brauchen wir. Sonst nichts.

## Schritt 2: Zugangsdaten hinterlegen

Zwei Wege — der erste ist der übliche.

### Weg A: Umgebungsvariablen (empfohlen)

In die `.env` des Autopiloten oder in die Umgebung des Dienstes:

```bash
DATAFORSEO_LOGIN=deine-mail@example.com
DATAFORSEO_PASSWORD=das-api-passwort
```

Danach den Dienst neu starten, damit er die Werte sieht.

### Weg B: Datei

Wenn die Zugangsdaten lieber in einer Datei liegen sollen, den Pfad in
`projects.yaml` eintragen (siehe Schritt 3). Drei Formate werden gelesen:

```json
{"login": "deine-mail@example.com", "password": "das-api-passwort"}
```

oder

```
DATAFORSEO_LOGIN=deine-mail@example.com
DATAFORSEO_PASSWORD=das-api-passwort
```

oder eine einzige Zeile `login:passwort`.

Die Datei gehört **nicht** ins Git-Repository. Sinnvoller Ort:
`/opt/odoo/credentials/dataforseo.json`, Rechte `chmod 600`.

> **Wichtig:** Zugangsdaten stehen niemals im Programmcode. Sie tauchen auch
> in keiner Log-Zeile und in keiner Fehlermeldung auf — auch nicht gekürzt.
> Das ist getestet.

## Schritt 3: Eintrag in `projects.yaml`

Beim gewünschten Projekt `dataforseo` in `enabled_sources` aufnehmen und
optional konfigurieren:

```yaml
projects:
  mein-projekt:
    domain: https://example.com
    enabled_sources:
      - gsc
      - dataforseo
    source_config:
      gsc:
        property_url: sc-domain:example.com
        credentials_path: /opt/odoo/credentials/tentacl-seo-service-account.json
      dataforseo:
        max_abfragen_pro_lauf: 25        # Kostenbremse (Standard: 25)
        location_code: 2276              # 2276 = Deutschland (Standard)
        language_code: de                # Standard
        # credentials_path: /opt/odoo/credentials/dataforseo.json   # nur für Weg B
```

Alle vier Einträge sind freiwillig. Ohne sie gilt: Deutschland, Deutsch,
höchstens 25 Abfragen pro Lauf, Zugangsdaten aus der Umgebung.

## Schritt 4: Prüfen, ob es sitzt

```bash
cd /opt/odoo/docs/seo-autopilot
./venv/bin/python3 -c "
import asyncio
from seo_autopilot.sources.dataforseo import DataForSEODataSource
q = DataForSEODataSource()
print(q.status())
print('Verbindung ok:', asyncio.run(q.test_connection()))
"
```

`konfiguriert: True` heißt: Zugangsdaten gefunden. Der Verbindungstest fragt
die Kontoauskunft ab — die ist bei DataForSEO kostenlos.

Zusätzlich meldet der Wächter (`run_selfcheck`) von selbst eine Warnung, wenn
`dataforseo` bei einem Projekt aktiviert ist, aber keine Zugangsdaten
erreichbar sind. Dann läuft der Audit sonst still ohne diese Daten weiter,
und das würde sonst niemandem auffallen.

## Was das kostet

DataForSEO rechnet **pro Abfrage** ab, nicht pauschal. Grobe Hausnummern
(Stand 2026, tagesaktuelle Preise stehen auf dataforseo.com):

| Abfrage | ungefähr |
|---------|----------|
| Ein Suchbegriff, Top 10 (SERP) | ~0,002 $ |
| Suchvolumen (bis 1.000 Begriffe in **einer** Abfrage) | ~0,05 $ |
| Backlink-Übersicht einer Domain | ~0,02 $ |

Einzeln also Kleingeld. Gefährlich wird nur eines: eine Schleife, die
tausendfach fragt. Deshalb gibt es die Bremse.

## Die eingebaute Obergrenze

Jeder Lauf darf höchstens **25 bezahlte Abfragen** auslösen. Wird die Grenze
erreicht, läuft der Lauf **nicht** einfach weiter — er bricht an dieser Stelle
mit einem Fehler ab und schreibt ihn ins Log:

```
[DataForSEO] Kostenbremse: Obergrenze von 25 DataForSEO-Abfragen pro Lauf
erreicht — 'SERP ...' wird NICHT ausgeführt.
```

Das ist Absicht. Lieber fehlen ein paar Daten, als dass eine Rechnung wächst,
die niemand bestellt hat. Wer mehr braucht, hebt die Grenze bewusst an:

```yaml
    source_config:
      dataforseo:
        max_abfragen_pro_lauf: 100
```

Der kostenlose Verbindungstest zählt nicht mit.

## Wenn nichts eingerichtet ist

Dann passiert nichts Schlimmes. Die Quelle meldet sich als *nicht
konfiguriert*, liefert leere Ergebnisse und der Audit läuft ganz normal
weiter — nur eben ohne SERP-, Suchvolumen- und Backlink-Daten. Ein fehlender
Zusatzdienst darf einen Audit nie umbringen.
