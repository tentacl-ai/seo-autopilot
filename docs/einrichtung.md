# Eine Website einrichten — die „große Packung"

Seit Stufe 4 wird **jede** Website gleich eingerichtet: mit Anbindung an die
Google Search Console **und** an Google Analytics, weil der Autopilot die Daten
auch auswerten soll. Dazu kommen Wochenbericht, Bing/IndexNow,
Geschwindigkeitsmessung, täglicher Lauf und 16 Monate Suchhistorie.

DataForSEO gehört **nicht** zur Packung.

Der laufende Betrieb danach — Befehle, Berichte, Note, Fehlerfälle, Kosten —
steht im [Handbuch](handbuch.md).

| Baustein | Wozu |
|---|---|
| Search Console | Wie oft erscheint die Website bei Google, mit welchen Suchbegriffen, wie oft wird geklickt |
| Google Analytics 4 | Was die Besucher danach tun — und woher sie kommen |
| Wochenbericht | Montags 07:40 per Mail, für jede Website gleich aufgebaut |
| Bing / IndexNow | Neue Seiten sofort an Bing und Copilot melden |
| PageSpeed-Schlüssel | Ladezeit-Messung (Core Web Vitals) |
| Cron | Der Autopilot prüft die Website täglich |
| Suchhistorie | Google gibt nur 16 Monate heraus — einmal geholt, bleibt sie im eigenen Archiv |

## Kurzfassung

```bash
cd /srv/seo-autopilot

# 1. Nur ansehen — ändert nichts
venv/bin/python -m seo_autopilot.cli.main einrichten \
    --projekt kunde-beispiel --domain https://kunde-beispiel.de \
    --bericht-an berichte@example.com

# 2. Wenn alles passt: eintragen (mit Sicherung) + Historie holen
venv/bin/python -m seo_autopilot.cli.main einrichten \
    --projekt kunde-beispiel --domain https://kunde-beispiel.de \
    --bericht-an berichte@example.com --schreiben

# 3. Die ausgegebene Cron-Zeile von Hand eintragen
sudo crontab -e
```

Weitere Angaben, falls die automatische Suche nicht reicht:

| Option | Bedeutung |
|---|---|
| `--name` | Anzeigename (sonst der Host) |
| `--gsc-property` | z. B. `sc-domain:kunde-beispiel.de` oder `https://kunde-beispiel.de/` — sonst automatisch gesucht |
| `--ga4-property` | Property-ID, nur Ziffern — sonst über den Datenstream der Website gesucht |
| `--adapter static --root-path /var/www/meine-website/dist` | Die Website liegt als Dateien auf diesem Server (der Autopilot darf dort reparieren). Standard: `generic` (nur lesen) |
| `--projects`, `--db` | Andere Projektliste / Datenbank (zum Testen) |

Ausgang 0 = Paket vollständig, 1 = es gibt blockierende Punkte (❌).

## Was der Befehl Schritt für Schritt tut

Jeder Schritt endet mit ✅ (in Ordnung), ⚠️ (läuft, aber verbessern), ❌ (fehlt,
blockiert) oder ℹ️ (nur zur Info). Bei ⚠️/❌ steht darunter **was zu tun ist und
wer es tun muss**: `[Kunde]`, `[Website-Betreuer]` oder `[wir]`.

1. **Website** — erreichbar? Leiten `www.` und `http://` dauerhaft (301) auf eine
   Adresse um? Diese kanonische Adresse wird als Domain eingetragen.
   Beispiel coaching-beispiel.de: `www.coaching-beispiel.de` leitet per 301 auf `coaching-beispiel.de`.
   - **1b Sitemap**: Anzahl der Seiten (bestimmt `max_pages`), fremde Hosts in der
     Sitemap (Warnzeichen für eine falsch konfigurierte oder falsche Website),
     robots.txt, die auf eine andere Schreibweise zeigt.
   - **1c Erkennung (`erwartet`)**: Ein Text, der auf der Startseite in Titel, H1
     oder Schema-Namen stehen muss — sonst bricht der Audit ab. Vorschlag: Name der
     Organisation im Schema, sonst der Seitentitel ohne angehängten Zusatz.
     Hintergrund: Am 18.09.2026 wurde unter einer Port-Adresse eine ganz andere
     Kundenseite geprüft.
2. **Search Console** — sucht die Property im Dienstkonto (Domain-Property
   `sc-domain:` vor URL-Präfix), prüft die Berechtigung und fragt die letzten
   7 Tage ab: „Daten kommen an" oder „Zugriff fehlt".
3. **Google Analytics 4** — findet die Property über den Datenstream der Website
   (oder nimmt `--ga4-property`) und ruft eine einzige Zeile ab.
   Hinweis auf `scripts/ga4_einrichten.py` (Kanalgruppe „Herkunft" +
   Schlüsselereignisse) — **wird nie automatisch ausgeführt**, das Skript ist auf
   tentacl.ai zugeschnitten und braucht „Bearbeiter"-Rechte.
4. **IndexNow** — gibt es schon einen Schlüssel (projects.yaml oder
   `<INDEXNOW_SITES aus der .env>`), wird die Datei `https://kunde-beispiel.de/<schlüssel>.txt`
   geprüft. Sonst wird ein neuer Schlüssel erzeugt und gesagt, **wohin die Datei
   muss** — hochgeladen wird nichts automatisch.
   - **4b Bing Webmaster**: steht die Website im täglichen Bing-Abgleich
     (`<INDEXNOW_SITES aus der .env>`)?
5. **PageSpeed-Schlüssel** — nur ja/nein.
6. **Projekt-Eintrag** — vollständiger YAML-Block: `enabled_sources: [gsc, ga4]`,
   Zugangsdaten, `adapter`, `max_pages` passend zur Sitemap, `betriebsart: copilot`,
   `bericht` aktiv mit Empfänger, `erwartet`, IndexNow-Schlüssel.
   Ohne `--schreiben` wird er nur angezeigt. Mit `--schreiben` landet er in der
   projects.yaml, **vorher** wird `projects.yaml.bak-einrichtung-<id>-<datum>`
   daneben gelegt. Bei einem bestehenden Projekt werden **nur Lücken gefüllt** —
   vorhandene Werte werden nie überschrieben (einzige Ausnahme: `max_pages` wird
   angehoben, nie gesenkt). Abgeschaltete Projekte (`enabled: false`) werden nicht
   verändert.
7. **Cron** — die Zeile im bestehenden Muster, im nächsten freien Zeitfenster
   zwischen 07:00 und 11:00 (nicht gleichzeitig mit einem anderen SEO-Job).
   **Nur ausgegeben**, eintragen muss sie ein Mensch.
8. **Suchhistorie** — mit `--schreiben` und funktionierender Search Console werden
   16 Monate importiert (dieselbe Funktion wie `historie --importieren`).
9. **Zusammenfassung** — „Paket vollständig" oder die Liste der offenen Punkte.

## Was der Kunde selbst freigeben muss

Das kann nur der Inhaber des jeweiligen Google-Kontos. Wir schicken ihm diese
zwei Anleitungen; das Dienstkonto heißt
`<dienstkonto>@<gcp-projekt>.iam.gserviceaccount.com`.

**Search Console** (search.google.com/search-console)

1. Property der Website auswählen. Gibt es noch keine: „Property hinzufügen" →
   **Domain** → `kunde-beispiel.de` → den angezeigten TXT-Eintrag beim Domain-Anbieter im
   DNS eintragen → „Bestätigen".
2. **Einstellungen → Nutzer und Berechtigungen → Nutzer hinzufügen**
3. E-Mail: `<dienstkonto>@<gcp-projekt>.iam.gserviceaccount.com`,
   Berechtigung **„Eingeschränkt"** (nur lesen) → Hinzufügen.

**Google Analytics** (analytics.google.com)

1. **Verwaltung** (Zahnrad) → **Property-Zugriffsverwaltung** → **+** → Nutzer hinzufügen
2. E-Mail: `<dienstkonto>@<gcp-projekt>.iam.gserviceaccount.com`, Rolle
   **„Betrachter"** → Hinzufügen.
3. Gibt es noch gar keine GA4-Property: anlegen, Web-Datenstream für die Website
   anlegen und das Tag (`G-…`) einbauen lassen — das erledigt der Website-Betreuer.
   Details: [ga4-setup.md](ga4-setup.md).

Danach den Befehl einfach noch einmal laufen lassen — Property-Suche und
Datenprobe laufen automatisch.

**Was der Website-Betreuer erledigt** (wird bei Bedarf angezeigt):
Weiterleitungen www/http per 301, sitemap.xml (nur eigene Adressen), die
IndexNow-Schlüsseldatei hochladen, GA4-Tag einbauen.

**Was wir erledigen**: Eintrag in projects.yaml, Cron-Zeile, Eintrag in
`<INDEXNOW_SITES aus der .env>` und Website in Bing Webmaster Tools
(„Aus Google Search Console importieren"), PageSpeed-Schlüssel.

## Bestehende Kunden prüfen

```bash
venv/bin/python -m seo_autopilot.cli.main einrichten --pruefen            # alle
venv/bin/python -m seo_autopilot.cli.main einrichten --pruefen --projekt beratung-beispiel
```

Prüft für jedes aktive Projekt nur lesend: Domain/kanonisch, Sitemap vs.
`max_pages`, `erwartet`, Search Console (eingerichtet + Daten), GA4, Bericht,
Cron, IndexNow, Bing, PageSpeed — und gibt eine Tabelle plus To-do-Liste je
Kunde aus. Abgeschaltete Projekte werden nur mit Namen aufgeführt und **nicht**
angefragt. Findet die Prüfung eine lesbare GA4- oder Search-Console-Property,
die nur nicht eingetragen ist, nennt sie die ID gleich mit.

Die Lücken schließt man dann mit `einrichten --projekt <id> --schreiben` (füllt
nur fehlende Felder).

## Bewusst kleines Paket

Soll eine Website ausdrücklich **ohne** Analytics und Bericht laufen (z. B. ein
reines Testprojekt), in projects.yaml eintragen:

```yaml
  testprojekt:
    paket: klein
```

Dann mahnt der Wächter (`selfcheck`) nicht. Ohne dieses Feld gilt die große
Packung, und der Wächter meldet jedes aktive Projekt ohne Search Console, GA4
oder Wochenbericht als Warnung „Paket unvollständig" mit Verweis auf
`einrichten --projekt <id>`.
