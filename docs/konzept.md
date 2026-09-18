# SEO-Operator — Das Konzept

> **Nicht** ein Semrush-Klon mit KI-Chat.
> Sondern ein Operator, der einen geschlossenen Kreislauf beherrscht:
>
> **Beobachten → Potenzial erkennen → Änderung ausführen → Wirkung messen → daraus lernen**

Festgehalten am 2026-08-18. Die Umsetzungsreihenfolge steht in der Roadmap,
nicht hier — dieses Dokument beschreibt das **Ziel** und den Abstand dorthin.

Stand-Markierungen: ✅ gebaut · 🟡 teilweise · ⬜ offen
*Zuletzt abgeglichen: 2026-09-18, Version 1.16.0.*

Verfügbare Befehle: `run` · `einrichten` · `empfehlungen` · `selfcheck` ·
`changes` · `wirkung` · `wert` · `chancen` · `betrieb` · `freigabe` ·
`historie` · `kundenbericht` · `weekly` · `marktradar` · `radar` ·
`wettbewerb` · `learnings` · `config` · `api`

Für den laufenden Betrieb: [Handbuch](handbuch.md).

---

## Der eigentliche USP

> Das System findet selbstständig die wirtschaftlich sinnvollste SEO-Maßnahme,
> setzt sie kontrolliert um und beweist anschließend, ob sie funktioniert hat.

Nicht „KI schreibt SEO-Texte". Der Unterschied zu jedem Standardwerkzeug ist,
dass am Ende ein Nachweis steht.

---

## 1. Alle relevanten Daten zusammenführen

| Quelle | Stand |
|---|---|
| Google Search Console | ✅ (28 Tage + 16 Monate Archiv) |
| Google Analytics 4 | ✅ (gehört seit Stufe 4 zur Standard-Einrichtung) |
| PageSpeed Insights / CrUX | ✅ |
| DataForSEO (SERP, Volumen, Backlinks) | 🟡 gebaut, bewusst abgeschaltet (Kosten) |
| Bing Webmaster Tools / IndexNow | ✅ |
| CMS: WordPress, Shopify, Webflow, Odoo | ⬜ |
| GitHub / GitLab für Änderungen per Pull Request | ⬜ |
| Serverlogs (echte Crawler-Zugriffe) | ⬜ |
| Google Business Profile (Local SEO) | ⬜ |
| Merchant Center (Shops) | ⬜ |
| CRM / Buchungssystem / Shop-Umsatz | 🟡 Werte erfassbar über `wert`, Anbindung offen |
| Search-Console-Rohdaten nach BigQuery | ⬜ (nur für große Projekte nötig) |

**Der entscheidende Unterschied zu normalen SEO-Tools:** Es bewertet nicht
Traffic, sondern **Geschäftswert**. Für einen Campingplatz ist
„Campingplatz am See buchen" plus tatsächliche Buchung mehr wert als
5.000 Besucher auf einem allgemeinen Ratgeberartikel.

Belege: [Search Console API](https://developers.google.com/webmaster-tools/v1/api_reference_index) ·
[BigQuery-Export](https://support.google.com/webmasters/answer/12917675?hl=en)

---

## 2. Die Website vollständig verstehen (Onboarding) 🟡

`einrichten` (Stufe 4) nimmt eine Website heute vollständig auf: kanonische
Adresse, Weiterleitungen, Sitemap und Seitenzahl, Search Console, GA4,
IndexNow/Bing, PageSpeed, Projekt-Eintrag, Cron und 16 Monate Historie — samt
Erkennungsmerkmal `erwartet` gegen die falsche Website. Was weiterhin fehlt,
ist das inhaltliche Verständnis:

Der Agent soll beim Einrichten selbstständig erkennen:

- Geschäftsmodell und Angebote
- Zielgruppen und Regionen
- Conversions **und deren Wert**
- Seitenarten (Startseite, Leistung, Standort, Ratgeber, rechtlich …)
- Themen, Leistungen, Produkte, Standorte
- Markenstil und Tonalität
- saisonale Zusammenhänge
- Wettbewerber
- daraus eine interne Wissensbasis zur Website

Ziel: Das System kennt nicht nur Adressen, sondern versteht, **welche Aufgabe
jede Seite erfüllt**.

---

## 3. Permanenter technischer Crawler ✅ (weitgehend)

Prüft HTML **und** gerendertes JavaScript:

| Prüfung | Stand |
|---|---|
| HTTP-Fehler, Soft-404 | ✅ |
| Redirect-Ketten und -Schleifen | ✅ |
| Canonicals | ✅ |
| robots.txt, noindex, X-Robots-Tag | ✅ |
| Sitemaps | ✅ |
| verwaiste Seiten | ✅ |
| Duplicate / Near-Duplicate | ✅ |
| mobile vs. Desktop-Unterschiede | ⬜ |
| interne Linktiefe | ✅ |
| hreflang | ✅ |
| strukturierte Daten | ✅ |
| Metadaten fehlend/widersprüchlich | ✅ |
| Bildgrößen, Alt-Texte, Ladeverhalten | ✅ |
| Core Web Vitals | ✅ |
| JavaScript-Rendering | ✅ |
| Änderungen gegenüber dem letzten Crawl | ✅ (Änderungsbuch) |
| Parameter-/Filter-URL-Explosionen | ⬜ |

**Feld- und Labordaten getrennt halten.** Zielwerte: LCP ≤ 2,5 s ·
INP < 200 ms · CLS < 0,1.
[Core Web Vitals](https://developers.google.com/search/docs/appearance/core-web-vitals) ·
[PageSpeed Insights](https://developers.google.com/speed/docs/insights/v5/about)

---

## 4. Opportunity Engine ✅ — das Herzstück

Zu erkennen:

- viele Einblendungen, schlechte Klickrate
- Positionen 4–20 mit realistischem Wachstumspotenzial
- Rankings, die langsam abrutschen
- neue Suchanfragen und Trends
- falsche Zielseite für eine Suchanfrage
- Keyword-Kannibalisierung
- fehlende Themen
- Inhalte, die aktualisiert werden sollten
- Seiten ohne ausreichende interne Links
- starke Seiten, deren Autorität besser verteilt werden könnte
- saisonale Chancen
- lokale Suchpotenziale
- **Seiten mit Traffic, aber ohne Conversions**
- **Seiten mit wenig Traffic, aber hohem Umsatz**

**Bewertungsformel:**

```
Geschäftswert × realistisches Potenzial × Konfidenz ÷ Aufwand
```

**Risiko ist kein weiterer Zahlenwert, sondern ein Freigabe-Gate.**

---

## 5. Konkrete Änderungen durchführen ✅ (Meta + Text, CMS offen)

Nicht bei Empfehlungen stehen bleiben, sondern Änderungspakete erzeugen:
Title und Meta Description · H1 und Seitenstruktur · Inhalte aktualisieren ·
fehlende Abschnitte ergänzen · interne Links · Ankertexte · strukturierte
Daten · Bilder · Alt-Texte · Redirects · Canonicals · Sitemaps ·
Landingpages · Content-Briefings · Seiten konsolidieren · veraltete Inhalte
kennzeichnen.

**Heute umgesetzt** (nur in statischen Dateien, Adapter mit Dateizugriff):

- Meta-Ebene und Auszeichnung: Title, Meta Description, H1, `og:`/`twitter:`,
  Alt-Texte per Bild-KI, `width`/`height`, JSON-LD-Datumsangaben.
- Sichtbarer Text ab Stufe 3 (`empfehlungen_umsetzen.py`): FAQ-Block, fehlender
  Abschnitt, Überschrift, erster Absatz, interner Link — **nur** bei
  `betriebsart: autopilot`, sonst nach Freigabe. Sieben Schutzgeländer, max. 3
  Änderungen je Lauf und 1 je Seite und Woche.

Jede Änderung braucht: Begründung · verwendete Daten · erwartete Wirkung ·
Risikoeinstufung · Vorher-Nachher-Diff · Vorschau · Rollback. Rollback läuft
heute über den Git-Commit, den jede Änderung erzeugt.

**Offen:** Wahl zwischen **CMS-Draft** und **Pull Request** — geschrieben wird
bisher nur direkt in die Dateien. Redirects, Canonicals und Konsolidierungen
sind bewusst gesperrt (Abschnitt 10).

---

## 6. Kein autonomer Content-Spam ✅ (als Regel verankert)

Vor jeder neuen Seite prüfen:

1. Kann eine bestehende Seite verbessert werden?
2. Gibt es bereits eine Seite mit derselben Suchintention?
3. Hat eine neue Seite echten zusätzlichen Nutzen?
4. Gibt es eigene Erfahrungen, Daten, Bilder oder Expertise?
5. Lässt sich der Inhalt fachlich belegen?

Google erlaubt KI-gestützte Inhalte, warnt aber ausdrücklich vor massenhaft
erzeugten Seiten ohne Mehrwert. **Eine Fabrik für 10.000 Keywordseiten ist
genau die falsche Richtung.**
[Generative KI](https://developers.google.com/search/docs/fundamentals/using-gen-ai-content) ·
[Spam-Richtlinien](https://developers.google.com/search/docs/essentials/spam-policies)

---

## 7. Wirkung jeder Änderung messen ✅

Messfenster **7, 14, 28 und 56 Tage**. Zu prüfen:

- Wurde die Seite neu gecrawlt? Ist die richtige Fassung indexiert?
- Haben sich Einblendungen verändert?
- Hat sich die Klickrate verbessert?
- Haben sich Rankings verändert?
- Entstehen zusätzliche Conversions oder Umsätze? ⬜ *(braucht Punkt 1)*
- Wurden andere Seiten negativ beeinflusst?
- Ist Kannibalisierung entstanden?
- Haben sich Ladezeit oder Fehlerquote verschlechtert?

Bei größeren Websites zusätzlich Test- und Kontrollgruppen. ⬜

**Fernziel:** „Bei diesem Projekt funktionieren regionale Landingpages gut,
allgemeine Ratgeber bringen dagegen kaum Buchungen."

---

## 8. SEO für KI-Suchen 🟡

Für Google AI Overviews, AI Mode, ChatGPT Search, Bing Copilot:

- Erwähnungen und Quellenlinks beobachten
- Marken- und Produktentitäten prüfen
- häufig zitierbare Aussagen erkennen
- Originaldaten, Statistiken und Tabellen fördern
- klare Antworten und Zusammenfassungen verbessern
- Autoren, Quellen und Aktualisierungsdatum sichtbar machen
- Erreichbarkeit für die relevanten Bots prüfen

Gebaut ist heute die **Beobachtung**: Der Wochenbericht fragt ChatGPT, Gemini
und Claude mit Websuche nach der Website und hält fest, ob und wie sie zitiert
wird.

**Kein erfundener „GEO-Score".** Google sagt selbst, dass für AI Overviews
keine speziellen Zusatzmaßnahmen nötig sind — die SEO-Grundlagen bleiben
entscheidend. Seit dem KI-Leitfaden von Google (Stand 10.07.2026) gilt im
Autopiloten härter: `llms.txt`/KI-Dateien, „Chunking", Schreiben speziell für
KI und strukturierte Daten als KI-Hebel sind Befunde der Art `hinweis` —
sichtbar, aber ohne Punktabzug und ohne automatische Umsetzung.
[AI Features](https://developers.google.com/search/docs/appearance/ai-features) ·
[KI-Leitfaden](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide)

---

## 9. Link- und Autoritätsmodul ⬜ (extern) / ✅ (intern)

**Intern** steht das Modul: Der Linkgraph erkennt verwaiste Seiten, Klicktiefe,
kaputte interne Links (auch Ziele außerhalb des Crawls) und Autoritäts-Senken;
`interne_links` schlägt konkrete Verlinkungen mit Ankertext vor.

**Extern offen:** Verlorene Backlinks · kaputte externe Links · unverlinkte
Markenerwähnungen · Linklücken gegenüber Wettbewerbern · relevante Partner und
Branchenverzeichnisse · Outreach-Texte vorbereiten. Ein Backlink-Index ist mit
eigenen Mitteln nicht seriös herstellbar — dafür bräuchte es einen
Datenanbieter.

**Outreach wird niemals vollständig autonom versendet.** Sonst baut man sehr
schnell eine Spammaschine.

---

## 10. Kontrollierte Autonomie ✅ (Betriebsarten stehen)

| Modus | Verhalten |
|---|---|
| **Beobachter** | Analysiert und priorisiert nur |
| **Copilot** | Erstellt fertige Änderungen zur Freigabe |
| **Autopilot** | Führt erlaubte Maßnahmen selbst aus und überwacht sie |

**Automatisch erlaubt:** Monitoring, Crawls, Reports, Sitemaps, IndexNow,
eindeutig technische Reparaturen, Meta-Ebene und Auszeichnung. Sichtbarer Text
nur bei `betriebsart: autopilot` und nur mit den Schutzgeländern aus
Abschnitt 5.

**Freigabepflichtig:** alles andere — Content-Änderungen unter `copilot`,
Vorschläge aus Vorlagen ohne Seitenkontext, Redirects.

**Niemals ungeprüft autonom** (14 Einträge, im Code gesperrt — ein Eintrag in
`whitelist_extra` hebelt sie nicht aus):

- `noindex`, Canonicals oder `robots.txt` verändern
- Seiten löschen
- Domains oder URL-Strukturen migrieren
- massenhaft Seiten veröffentlichen
- externe Links kaufen oder Outreach versenden
- rechtliche, medizinische oder finanzielle Aussagen erstellen

**Indexierung suchmaschinenspezifisch:** IndexNow für unterstützende
Suchmaschinen, Sitemaps und Search Console für Google. Googles Indexing API
darf offiziell **nur** für Jobanzeigen und Livestream-Seiten verwendet werden.
[IndexNow](https://www.indexnow.org/documentation) ·
[Indexing API](https://developers.google.com/search/apis/indexing-api/v3/quickstart)

---

## Das MVP

Zuerst nur diese sechs Dinge perfekt bauen:

1. ✅ GSC, GA4, PageSpeed anbinden — *CMS fehlt weiterhin*
2. ✅ Website crawlen und Seiteninventar erstellen
3. ✅ die zehn wertvollsten Chancen pro Woche priorisieren (`chancen`)
4. ✅ fertige Änderungen mit Vorschau und Diff erzeugen (`changes --diff`)
5. ✅ nach Freigabe direkt veröffentlichen (statische Dateien, Git-Commit je Änderung)
6. ✅ Wirkung messen — *Geschäftswert erfassbar (`wert`), Anbindung an CRM/Shop offen*

---

## Die Oberfläche

Nicht 300 Warnungen, sondern fünf Blöcke:

| Block | Inhalt |
|---|---|
| **Heute sinnvoll** | drei konkrete Aktionen |
| **In Beobachtung** | laufende Optimierungen |
| **Ergebnis** | zusätzlicher Traffic, Leads und Umsatz |
| **Risiken** | Dinge, die menschliche Entscheidung brauchen |
| **Änderungsbuch** | wer oder was wann welche Seite verändert hat ✅ |
