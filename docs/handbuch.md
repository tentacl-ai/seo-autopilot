# Handbuch für Betreiber

Dieses Handbuch beschreibt den laufenden Betrieb des SEO-Autopiloten: was das
Werkzeug tut, wie eine Website aufgenommen wird, welche Befehle es gibt, was
automatisch passiert und was nicht, wie ein Bericht zu lesen ist und was bei
Fehlern zu tun ist.

Stand: Version 1.16.0.

Kurzfassung der Einrichtung: [einrichtung.md](einrichtung.md).
Konzept und Ausbaustand: [konzept.md](konzept.md).

---

## 1. Was das Werkzeug tut

Der Autopilot durchläuft für jede betreute Website denselben Kreislauf:

1. **Beobachten** — Website crawlen, Search Console, Google Analytics 4 und
   PageSpeed abfragen.
2. **Bewerten** — 17 Prüfmodule erzeugen Befunde; daraus entsteht die Note.
3. **Handeln** — sichere Reparaturen selbst ausführen, alles andere zur Freigabe
   vorlegen.
4. **Messen** — nach 7, 14, 28 und 56 Tagen prüfen, ob die Änderung etwas
   gebracht hat.
5. **Berichten** — einmal pro Woche eine Mail an den Kunden, täglich eine
   Selbstprüfung an den Betreiber.

Der Unterschied zu einem reinen Prüfwerkzeug liegt in Schritt 3 und 4: Es wird
tatsächlich in die Website geschrieben, und jede Änderung muss sich hinterher an
echten Suchdaten messen lassen.

### Was das Werkzeug ausdrücklich nicht tut

| Nicht enthalten | Grund |
|---|---|
| Backlink-Index | Dafür müsste man das halbe Web crawlen |
| Fremde Platzierungen / SERP-Abfragen | Verstößt gegen Googles Nutzungsbedingungen |
| Google-Unternehmensprofil, Local Pack | Nicht angebunden |
| Umsatz-Zuordnung | Nur mit selbst hinterlegten Werten (`geschaeftswert`), nie geschätzt |
| DataForSEO | Baustein vorhanden, aber abgeschaltet (Kostenentscheidung) |

JavaScript-Seiten werden nur gerendert, wenn Playwright **und** der zugehörige
Browser installiert sind. Fehlt eines davon, meldet der Wächter das als
kritisch — ohne Rendering sieht der Autopilot bei einer React-Website nur ein
leeres Grundgerüst.

---

## 2. Eine Website aufnehmen

Neue Websites werden nicht von Hand in die `projects.yaml` geschrieben, sondern
mit `einrichten` aufgenommen. Der Befehl prüft die Website, sucht die Search
Console- und GA4-Property, testet beide mit einer Mini-Abfrage und gibt einen
vollständigen Projekt-Block aus.

```bash
cd /srv/seo-autopilot

# 1. Nur ansehen, ändert nichts
venv/bin/python -m seo_autopilot.cli.main einrichten \
    --projekt kunde-beispiel --domain https://kunde-beispiel.de \
    --bericht-an berichte@example.com

# 2. Passt? Eintragen (Sicherung wird daneben gelegt) + Historie holen
venv/bin/python -m seo_autopilot.cli.main einrichten \
    --projekt kunde-beispiel --domain https://kunde-beispiel.de \
    --bericht-an berichte@example.com --schreiben

# 3. Die ausgegebene Cron-Zeile von Hand eintragen
sudo crontab -e
```

Alles Live-Zugreifen ist lesend. Geschrieben wird nur die `projects.yaml` und
die eigene Datenbank. Die Cron-Zeile wird **nur ausgegeben**, nie installiert.

Bestehende Kunden prüfen:

```bash
venv/bin/python -m seo_autopilot.cli.main einrichten --pruefen
```

Ausgang 0 = Paket vollständig, 1 = es gibt blockierende Punkte.

Details, inklusive der Anleitungen, die der Kunde für Search Console und
Analytics braucht: [einrichtung.md](einrichtung.md).

---

## 3. Die Befehle

Alle Befehle laufen als `venv/bin/python -m seo_autopilot.cli.main <befehl>`.
Jeder Befehl kennt `--help`.

### Täglicher Betrieb

| Befehl | Wozu |
|---|---|
| `run --project-id X` | Ein Audit ausführen (Crawl, Analyse, Reparatur, Bericht) |
| `empfehlungen --erzeugen --umsetzen` | Empfehlungen berechnen und abarbeiten |
| `historie --importieren` | Fehlende Monate aus der Search Console nachholen |
| `marktradar --sammeln` | Neuerungen im Markt einsammeln |
| `lernschleife --senden` | Wöchentlich: Neuerungen bewerten, höchstens 3 Anpassungen am Werkzeug vorschlagen (Go per Knopf, ändert nie Code) |
| `lernschleife --erledigt <id>` | Umgesetzten Vorschlag abhaken |
| `selfcheck` | Selbstprüfung des Werkzeugs |

### Ansehen und entscheiden

| Befehl | Wozu |
|---|---|
| `config list` | Welche Projekte gibt es |
| `betrieb` | Welches Projekt darf was (Beobachter / Copilot / Autopilot) |
| `freigabe` | Offene Vorschläge; entscheiden mit `--ja`/`--nein` |
| `chancen --projekt X` | Womit anfangen — nach Geschäftswert sortiert |
| `wert --projekt X` | Was die Seiten einbringen; fehlt die Angabe, wird nicht geschätzt |
| `changes --projekt X --tage 14 --diff` | Änderungsbuch: wer hat wann was geändert |
| `wirkung --bilanz` | Welche Art von Änderung wirkt überhaupt |
| `learnings` | Wiederkehrende Fehlalarme — Hinweis auf eine kaputte Prüfregel |
| `wettbewerb --projekt X` | Vergleich mit dem eigenen Crawler, ohne Datenanbieter |

### Berichte

| Befehl | Wozu |
|---|---|
| `kundenbericht --projekt X --trocken` | Wochenbericht erzeugen, ohne zu versenden |
| `kundenbericht --senden` | Wochenbericht an alle Empfänger schicken |
| `weekly` | Kurze Übersicht über alle Projekte |
| `radar` | Neue Google-/KI-Suchrichtlinien und was daran hängt |

### Beispiele

```bash
# Ein einzelnes Audit, mit Blick auf den Ausgang
venv/bin/python -m seo_autopilot.cli.main run --project-id kunde-beispiel; echo $?

# Empfehlungen erst einmal nur ansehen
venv/bin/python -m seo_autopilot.cli.main empfehlungen --projekt kunde-beispiel

# Empfehlungen prüfen, aber nichts schreiben
venv/bin/python -m seo_autopilot.cli.main empfehlungen --projekt kunde-beispiel \
    --umsetzen --trocken

# Eine einzelne Empfehlung freigeben (die ersten Zeichen der Kennung genügen)
venv/bin/python -m seo_autopilot.cli.main empfehlungen --freigeben 3f2a1b

# Einen Vorschlag aus der Freigabe-Schlange ablehnen
venv/bin/python -m seo_autopilot.cli.main freigabe --nein 9c11 --notiz "so nicht"

# Fällige Wirkungsmessungen nachholen
venv/bin/python -m seo_autopilot.cli.main wirkung --messen
```

---

## 4. Was automatisch passiert — und was nicht

### Die drei Betriebsarten

In der `projects.yaml` steht je Projekt `betriebsart`. Fehlt das Feld, gilt die
sicherste Einstellung.

| Betriebsart | Verhalten |
|---|---|
| `beobachter` | Ändert nichts. Standard für neue Projekte |
| `copilot` | Bereitet jede Änderung vor und legt sie zur Freigabe |
| `autopilot` | Führt Unbedenkliches selbst aus, legt alles andere trotzdem vor |

### Was auch im Autopilot-Modus nie automatisch läuft

Vierzehn Eingriffe sind **im Code** gesperrt. Ein Eintrag in `whitelist_extra`
hebelt sie nicht aus:

- `noindex` setzen oder entfernen
- Kanonisierung ändern (`missing_canonical`, `wrong_canonical`, `canonical_chain`)
- `robots.txt` ändern
- Seiten löschen
- Seiten zusammenlegen (`thin_content`, `near_duplicate`)
- Adressumzüge (`url_migration`)
- Weiterleitungsketten auflösen (`redirect_chain`, `redirect_loop`)

Jede Sperre trägt ihre Begründung im Klartext und erscheint mit dieser
Begründung in der Freigabe-Schlange.

### Was automatisch repariert wird

Meta-Ebene und Auszeichnung — nie Struktur, nie Adressen:

- Titel, Meta-Beschreibung, H1 (mit Seitenkontext, per KI)
- `og:title`, `twitter:card` und die zugehörigen Felder
- Alt-Texte per Bild-KI (dekorative Bilder mit `alt=""` bleiben unangetastet)
- `width`/`height` aus den lokalen Bilddateien
- JSON-LD-Datumsangaben aus der Git-Historie

Vorlagen ohne Seitenkontext laufen **nie** automatisch, auch nicht im
Autopilot-Modus. Sie landen mit Begründung in der Freigabe-Schlange.

### Sichtbarer Text

Sichtbaren Text schreibt der Autopilot nur bei `betriebsart: autopilot`. Überall
sonst wird eine Empfehlung erst nach Freigabe umgesetzt.

Sieben Schutzgeländer gelten immer:

1. Nur Fakten von der Website; jede Aussage wird an der **aktuellen** Seite
   erneut geprüft.
2. Platzhalter gehen nie live; unbeantwortete Fragen fallen weg.
3. Tabuwörter, Marke und Ansprache aus `adapter_config.seo_regeln`.
4. Gesperrte Seiten: `seo_regeln.gesperrte_seiten` plus immer Impressum,
   Datenschutz und AGB.
5. Höchstens 3 Textänderungen je Lauf und 1 je Seite und Woche.
6. Ein getrennter zweiter KI-Prüfdurchgang: stimmt jede Aussage, klingt es
   natürlich, ist es kein Keyword-Stuffing. Nur bei "ja" wird geschrieben.
7. Sprache und Sie/du der Seite bleiben erhalten.

HTML-Fragmente und JSON-LD baut immer der Code, nie die KI.

### Der Identitätsschutz

Steht in der `projects.yaml` ein `erwartet`, muss mindestens einer dieser Texte
auf der Startseite vorkommen — in Titel, H1, `og:site_name`, einem Schema-Namen
oder im sichtbaren Text. Passt nichts davon, bricht der Audit **vor** jeder
Analyse und jedem Auto-Fix ab (Status `failed`, Exit 1).

Ist die Startseite nicht abrufbar, gilt die Identität als nicht bestätigt — auf
einer unbestätigten Website wird nichts verändert.

Der Schutz existiert, weil einmal unter einer Adresse eine ganz andere Website
geprüft und bearbeitet wurde.

---

## 5. Den Wochenbericht lesen

Der Bericht geht montags per Mail an die Empfänger aus `bericht.empfaenger`. Er
ist für jede Website gleich aufgebaut:

| Abschnitt | Was drinsteht |
|---|---|
| Wo wir stehen | Ein Absatz Gesamtlage |
| Auffällig | Alles Ungewöhnliche aus den Abschnitten darunter, oben zusammengezogen |
| Entscheidungen | Knöpfe: Impulse aus dem Markt und offene Freigaben |
| Google-Suche | Search Console, Woche gegen Vorwoche, Suchbegriffe, Seiten |
| Besuche nach Herkunft | Google Analytics 4 |
| Neu im Markt | Marktbeobachter, auf diese Website bezogen |
| KI-Sichtbarkeit | ChatGPT, Gemini, Claude — jeweils mit Websuche |
| Website-Prüfung | Letztes Audit, Note und Trend, die wichtigsten Punkte |
| Bing | Stand aus dem Bing-Abgleich |
| Zusatz der Website | Projekteigene Abschnitte (`bericht.extras`) |
| Zustand des Werkzeugs | Selbstprüfung: läuft alles, was ist kaputt |

Eine tote Quelle verhindert nie den Bericht. Sie wird als tot ausgewiesen — das
ist wichtiger als eine lückenlos aussehende Mail.

**Vor dem Versand ansehen:**

```bash
venv/bin/python -m seo_autopilot.cli.main kundenbericht --projekt kunde-beispiel \
    --trocken --ohne-knoepfe --html /tmp/bericht.html
```

`--ohne-ki` spart die KI-Aufrufe (und damit die Kosten) für einen Probelauf.

---

## 6. Was die Note bedeutet

Die Note geht von 0 bis 100 und beantwortet "wie viel ist wirklich kaputt", nicht
"wie viele Zeilen können wir drucken".

### Drei Arten von Befunden

| Art | Bedeutung | Zählt in die Note? | Wird automatisch umgesetzt? |
|---|---|---|---|
| `fehler` | Technisch eindeutig, am Live-System nachprüfbar (404, fehlender Titel, `noindex`, schlechter Messwert) | ja, voll | ja, soweit sicher |
| `empfehlung` | Faustregel, Geschmack oder Chance (Textlänge, fehlendes Social-Tag, Suchbegriff mit Potenzial) | ja, zu einem Drittel, zusammen höchstens 10 Punkte | nur nach Freigabe |
| `hinweis` | Sichtbar, aber ohne Folgen — siehe unten | **nein** | **nein** |

Ein unbekannter Befundtyp gilt als `fehler`. Im Zweifel darf ein echter Fehler
nicht in der Empfehlungs-Ecke verschwinden.

### Die Rechnung

```
Note = 100 − min(50; hoch) − min(30; mittel) − min(20; niedrig) − min(10; Empfehlungen / 3)
```

Zwei Regeln davor:

- **Eine Ursache je Adresse zählt einmal.** Befunde derselben Ursachen-Familie
  an derselben Adresse (zum Beispiel "nicht erreichbar" und "verwaist") werden
  auf den schwersten zusammengezogen. Sichtbar bleiben alle.
- **Abnehmender Grenznutzen.** Befunde werden je (Typ, Schwere) gruppiert und
  auf die Seitenzahl normiert. Eine fehlende Navigation, die 17 gleiche Befunde
  erzeugt, kostet ungefähr so viel wie fünf, nicht wie siebzehn.

### Was laut Google keine Wirkung hat

Google schreibt in seinem Leitfaden
["Optimizing your website for generative AI features on Google Search"](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide)
(Search Central, Stand 10.07.2026), dass Folgendes **keine** Wirkung auf die
(KI-)Suche hat:

- `llms.txt`, `ai.txt` und vergleichbare KI-Dateien
- "Content Chunking"
- Texte speziell für KI umschreiben
- künstlich erzeugte Markennennungen
- strukturierte Daten als KI-Hebel (sie bringen Rich Results, mehr nicht)

Solche Befunde haben im Autopiloten die Art `hinweis`: Sie bleiben **sichtbar**,
kosten aber keine Punkte, stehen nicht auf der Arbeitsliste, werden nicht
automatisch umgesetzt und nicht zur Freigabe vorgelegt.

### Alte Noten sind nicht vergleichbar

Die Rechnung wurde mit Version 1.16.0 umgestellt. Eine Note von vorher lässt
sich nicht gegen eine heutige halten — die Websites haben sich nicht geändert,
die Rechnung schon. Der Vergleich beginnt neu ab dem ersten Audit unter 1.16.0.

---

## 7. Wirkung: hat die Änderung etwas gebracht

Für jede Änderung wird das Zeitfenster davor gegen das Fenster danach gestellt —
7, 14, 28 und 56 Tage, Search-Console-Daten für genau diese Adresse. Mehrere
Fenster, weil ein Titel oft binnen einer Woche durchschlägt, Inhaltsarbeit aber
Wochen braucht.

Fünf Sperren fällen lieber kein Urteil als ein schlechtes:

1. Zu dünne Datenlage im Vorher-Fenster → Messung wird gespeichert, aber als
   `zu_wenig_daten` gekennzeichnet.
2. Widersprechende Signale (bessere Position, aber weniger Einblendungen **und**
   weniger Klicks) gelten nicht als Erfolg.
3. Fremde Änderungen — ein Mensch, der dieselbe Seite bearbeitet — stehen
   getrennt im Änderungsbuch und werden dem Autopiloten nicht zugerechnet.
4. Mehrere Änderungen an einer Seite am selben Tag werden als ein Paket gemessen.
5. Projekte, die nie messbar sind (keine Search Console), lösen keinen
   Dauer-Alarm aus.

```bash
venv/bin/python -m seo_autopilot.cli.main wirkung --messen
venv/bin/python -m seo_autopilot.cli.main wirkung --projekt kunde-beispiel --fenster 28
venv/bin/python -m seo_autopilot.cli.main wirkung --bilanz
```

---

## 8. Wenn etwas schiefgeht

### Der Wächter

`selfcheck` prüft nicht die Websites, sondern das Werkzeug.

```bash
venv/bin/python -m seo_autopilot.cli.main selfcheck
```

| Ausgang | Bedeutung |
|---|---|
| 0 | Gesund |
| 1 | Warnungen |
| 2 | Kritisch |

Auch `run` hat einen sprechenden Ausgang: **0** = Audit durchgelaufen, **1** =
abgestürzt oder Identitätsprüfung fehlgeschlagen. Ein fehlgeschlagener Lauf wird
mit Status `failed` und Fehlertext gespeichert, damit der Wächter ihn meldet.
Ein "done" ohne Ergebnis gibt es nicht mehr.

### Die häufigsten Befunde des Wächters

| Meldung | Ursache | Abhilfe |
|---|---|---|
| Pillow fehlt | Bildmaße und Alt-Text-KI arbeiten still nicht | `venv/bin/pip install pillow` |
| Playwright / Browser fehlt | JavaScript-Seiten werden nie gerendert | `venv/bin/pip install playwright` + `PLAYWRIGHT_BROWSERS_PATH=./.browsers venv/bin/python -m playwright install chromium` |
| feedparser fehlt | Markt- und Richtlinien-Radar liefern nichts | `venv/bin/pip install feedparser` |
| PageSpeed-Schlüssel fehlt | Google antwortet mit HTTP 429, es gibt nie Core Web Vitals | `PAGESPEED_API_KEY` in die `.env` |
| Projekt ohne Cron | Die Website wird nie geprüft | Cron-Zeile aus `einrichten` eintragen |
| Paket unvollständig | Search Console, GA4 oder Bericht fehlen | `einrichten --projekt X --schreiben`, oder bewusst `paket: klein` setzen |
| Freigaben älter als 14 Tage | Niemand entscheidet | `freigabe` durchgehen |
| Fehlender Monat im Archiv | Historien-Import lief nicht | `historie --importieren` |

Wichtig: Ein fehlender Baustein hat früher **stillschweigend** zu "nichts zu tun"
geführt — Befunde galten damit als behoben, obwohl nie etwas passiert war.
Deshalb sind Pillow, Playwright und feedparser als kritisch eingestuft.

### Logs

Alle Cron-Läufe schreiben nach `logs/cron.log`, mit Zeitstempel. Die Datei wird
wöchentlich rotiert. Protokollierung von `httpx`/`httpcore` steht auf WARNING —
diese Bibliotheken schreiben sonst vollständige URLs samt Token mit.

---

## 9. Eine automatische Änderung zurücknehmen

Jede Änderung des Autopiloten wird als **eigener Git-Commit** in das Repository
der Website geschrieben. Zurücknehmen heißt deshalb: den Commit zurückrollen.

```bash
cd /var/www/meine-website          # das Repo der Website, nicht der Autopilot

# 1. Welche Änderungen gab es?
git log --oneline -20

# 2. Was genau stand drin?
git show <commit>

# 3. Zurücknehmen (legt einen Gegen-Commit an, Historie bleibt erhalten)
git revert <commit>
```

Zur Einordnung, welcher Commit zu welchem Befund gehört:

```bash
venv/bin/python -m seo_autopilot.cli.main changes --projekt kunde-beispiel \
    --tage 30 --diff
```

Damit dieselbe Änderung nicht beim nächsten Lauf erneut geschrieben wird:

- Den zugehörigen Vorschlag ablehnen (`freigabe --nein <kennung>` bzw.
  `empfehlungen --ablehnen <kennung>`). Ein abgelehnter Vorschlag wird nicht
  erneut gefragt.
- Oder die Seite dauerhaft sperren: `seo_regeln.gesperrte_seiten` in der
  `projects.yaml` ergänzen.
- Oder die Betriebsart des Projekts auf `copilot` zurückstellen.

Der Adapter schreibt **nie** in eine Datei mit offenen fremden Änderungen. Wer
gerade selbst an der Website arbeitet, wird also nicht überschrieben.

---

## 10. Kosten

Kostenpflichtig sind nur die KI-Aufrufe und — oberhalb des Freikontingents —
PageSpeed. Alles andere (Search Console, GA4, Bing/IndexNow, eigener Crawler)
ist kostenlos.

Die Deckel:

| Stelle | Deckel |
|---|---|
| Empfehlungen je Projekt und Lauf | `source_config.empfehlungen.max_seiten`, Standard **6** KI-Aufrufe |
| Texte schreiben je Lauf | höchstens **3** Änderungen, davon **1** je Seite und Woche |
| Reparaturen (Titel, Beschreibung, Alt-Texte) | nur für Befunde, die im aktuellen Lauf bestehen |
| Wochenbericht | KI-Sichtbarkeit und Markt-Impulse; mit `--ohne-ki` abschaltbar |
| Marktbeobachter | zwei KI-Späher pro Lauf; mit `--ohne-ki` abschaltbar |
| Lernschleife | **ein** KI-Aufruf pro Woche, ohne Werkzeuge |

Zusätzlich verhindert ein Inhalts-Hash je Seite, dass Unverändertes neu
berechnet wird. Dadurch rotiert der Lauf von selbst über die Seiten, statt
jeden Tag dieselben sechs zu bezahlen.

Welches Modell verwendet wird, steht in `CLAUDE_MODEL`. Der Schlüssel kommt aus
`CLAUDE_API_KEY`, ersatzweise `ANTHROPIC_API_KEY`.

Für PageSpeed gilt: **mit** eigenem Schlüssel ist die Abfrage im normalen Umfang
kostenlos, ohne Schlüssel läuft sie über ein gemeinsames Google-Projekt, dessen
Tageskontingent praktisch dauerhaft erschöpft ist.

---

## 11. Grenzen und offene Punkte

- **Kein Backlink-Index, keine fremden Platzierungen.** Siehe Abschnitt 1.
- **Kein Google-Unternehmensprofil.** Lokale Sichtbarkeit wird nur über
  strukturierte Daten auf der Website geprüft, nicht über das Profil selbst.
- **JS-Rendering nur mit Playwright.** Ohne installierten Browser sieht der
  Autopilot bei Single-Page-Apps nur das Grundgerüst.
- **CMS-Anbindung fehlt.** Geschrieben wird nur in statische Dateien auf einem
  Server, auf den der Autopilot Zugriff hat. WordPress, Shopify und Webflow sind
  vorgesehen, aber nicht gebaut.
- **Serverlogs werden nicht ausgewertet.** Echte Crawler-Zugriffe sind damit
  nicht sichtbar.
- **Kein Test- und Kontrollgruppen-Verfahren.** Die Wirkungsmessung vergleicht
  vorher gegen nachher, nicht gegen eine unveränderte Vergleichsgruppe.
- **DataForSEO ist abgeschaltet.** Der Baustein existiert und ist beschrieben
  ([dataforseo-setup.md](dataforseo-setup.md)), wird aber in keinem Projekt
  genutzt.

Der vollständige Soll-Ist-Abgleich steht in [konzept.md](konzept.md).
