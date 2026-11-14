# SEO-Arbeitsplatz

Der Arbeitsplatz ergänzt den bestehenden Operator um Projektwissen, Recherche, eine deutsche Oberfläche und einen gemeinsamen Agentenzugriff. Bestehende Crawler, Empfehlungen, Änderungspakete und Wirkungsmessungen bleiben zuständig. Die Erweiterung startet keine bestehenden Jobs und verändert keine Kundenwebsite.

## Benutzbare Bereiche

| Bereich | Inhalt |
|---|---|
| Überblick | Letzte Website-Prüfung, Klicks, Empfehlungen und verfügbare Daten |
| Projektakte | Geschäft, Ziel, Positionierung, Sprache, Region, Wettbewerber, wichtige Seiten; Annahme oder bestätigte Angabe mit Quelle |
| Keywords & Seiten | Begriffe speichern, GSC-Begriffe übernehmen, Absicht/Themen/Zielseite pflegen; bestehende Seite verbessern oder Inhaltslücke prüfen |
| Google | Letzte verfügbare Woche und echte Vorwoche mit absoluten Werten nebeneinander; fehlende Vorwoche bleibt leer |
| KI, Local, Backlinks | Gespeicherte Ergebnisse des bisherigen Operators lesen |
| Änderungen und Wirkung | Änderungshistorie, Quellen, Urteile und Vorher-/Nachher-Werte |
| Recherche | Keyword-Ideen, Domain-Begriffe/-Übersicht, einzelne Backlinks, verweisende Domains, stärkste Seiten, Unternehmensprofil und bis zu zehn Bewertungen |
| Messaufträge | SERP, lokales Raster oder KI-Fragen; Gerät, Markt, Kostenvorbehalt und Ergebnisstatus |
| Berichte | Private, versionierte Texte; Vorlagen; vorherige Fassungen lesen; Text oder Browser-PDF exportieren |
| SEO-Agent | Optionaler Textassistent auf Basis gespeicherter Projektmessungen; keine Werkzeuge für Website-Änderungen oder Versand |

## Ein gemeinsamer fachlicher Weg

Weboberfläche, Fach-API und MCP benutzen `arbeitsplatz/service.py`. Das verhindert eine zweite Scoreformel oder abweichende Zahlen im Agenten. Lesen benutzt die Bestandsdatenbank ausschließlich mit SQLite `mode=ro`; fehlende Tabellen werden nicht angelegt. Arbeitsplatzmetadaten liegen in explizit initialisierten Tabellen `workspace_documents` und `workspace_calls`. Sie enthalten keine Kopie aller Audit-/GSC-Daten.

Dokumente sind pro Projekt und Kennung unveränderlich versioniert. Eine Änderung braucht die erwartete Ausgangsfassung; eine zwischenzeitliche Änderung liefert Konflikt 409. Projekttexte können weder Betriebsrechte noch Budget verändern. Bestätigte Angaben dürfen nur Betreiber ändern. Berichte sind immer privat, selbst wenn ein Aufrufer ein zusätzliches `public`-Feld einschleust.

## Rollen und Zugang

Serverseitige Zugänge enthalten einen SHA-256-Wert und eine ausdrückliche Liste erlaubter Projekte. Die Rolle ist `viewer`, `editor` oder `owner`. Der Browser übermittelt den Zugang ausschließlich im Authorization-Header und hält ihn nur im Arbeitsspeicher. Abmelden bricht laufende Anfragen ab und leert die Ansicht. Ohne gültigen Zugang sind sämtliche Fachrouten geschlossen. Zugangscodes brauchen mindestens 24 Zeichen; der bekannte Democode gilt ausschließlich im expliziten lokalen Demomodus.

Der Arbeitsplatz ist eine interne Betreiberoberfläche. Es gibt keine Registrierung, keine Passwort-Wiederherstellung, kein OAuth-Onboarding und noch keinen freigegebenen Kundenlogin. Diese Funktionen werden durch einen Projektschlüssel nicht vorgetäuscht.

## Daten und Grenzen

- GSC-Durchschnittsposition, externe Suchergebnis-Momentaufnahme und Maps-Suche sind unterschiedliche Messungen. Sie werden nicht vermischt.
- Der Wochenvergleich berechnet die echte vorangegangene Kalenderwoche. Eine fehlende Woche wird nicht durch die nächste vorhandene ersetzt. Bei gleichen Klicks steht „gleich wie Vorwoche“.
- Keyword-Gruppen beruhen zunächst auf expliziter Zuordnung, Absicht und Themen. Sie sind keine behauptete Messung von SERP-Ähnlichkeit. Eigene GSC-Einblendungen werden nicht als weltweites Suchvolumen ausgegeben.
- Tabellen zeigen begrenzte Auszüge. Die Fach-API liefert höchstens 2.000 Messzeilen je Aufruf, der Überblick 100 je Bereich. Diese Auszüge sind keine Gesamtsummen.
- Neue feste Rankings sind auf die ersten zehn Ergebnisse begrenzt; `not_in_sample` bedeutet nicht „weltweit verschwunden“. Ein fehlgeschlagener Abruf ist kein Rankingverlust.
- Ein lokales Raster unterstützt 3×3, 5×5 oder 7×7 Punkte; der zusätzliche Aufrufdeckel pro Auftrag begrenzt den tatsächlichen Umfang. Fehlende Punkte bleiben als fehlgeschlagen sichtbar.
- KI-Messungen verwenden die vorhandenen Modell-APIs mit Websuche. Sie simulieren nicht die personalisierte Verbraucheroberfläche und garantieren keine tatsächliche geografische Ausspielung. Ein Markt ist hierbei Kontext, keine bewiesene Suchposition. Die bestehenden Adapter werden aus dem Repository verwendet; für den alleinigen Wheel-Betrieb ist deren Verfügbarkeit gesondert zu prüfen.
- Bewertungen sind asynchrone Provideraufträge: einmal anlegen, Kennung speichern, bestehendes Ergebnis separat abrufen. Kein automatisches erneutes Anlegen nach einem Timeout.
- Berichte und Gesprächsantworten bleiben Zusammenfassungen der gespeicherten Daten. Berichte können von Berechtigten korrigiert werden, behalten dabei ihre alten Fassungen.

## Datenkosten

Zusatzdaten sind standardmäßig ausgeschaltet. Nur der Betreiber kann einen explizit bestätigten Auftrag in einer freigegebenen Projektpolicy starten. Die Policy ist keine Web-Einstellung und wird nicht aus Projekttexten übernommen.

Vor einem Auftrag werden die serverseitig festgelegten Planreserven atomar vom gemeinsamen Monatsbudget reserviert. Auch fehlgeschlagene oder unklare Aufträge behalten ihre Reserve. Zwei parallele Aufträge können diese Reservierungsgrenze nicht überziehen. Eine Auftragskennung wird nie erneut ausgeführt; dieselbe noch laufende Recherche wird auch unter neuer Kennung nicht doppelt angelegt. Frische abgeschlossene Recherche wird aus dem Cache beantwortet.

**Die Planreserve ist keine Preisgarantie des Datenanbieters.** Tarife, ergebnisabhängige Preise und modellabhängiger Verbrauch müssen vor Freigabe geprüft werden; zusätzlich einen Kontodeckel beim Anbieter setzen. Diese Version rechnet mit konservativen Betreiberwerten, nicht mit einer behaupteten exakten nachträglichen Providerabrechnung. Keine Kostenfreigabe wurde im Ausbau erteilt oder aktiviert.

## Zeitpläne und Assistent

Ein gespeicherter Rhythmus allein startet nichts. `--run-due` erfordert zusätzlich `scheduled_allowed` und die normale Datenfreigabe. Wiederholte Aufrufe in derselben Tages-/Wochen-/Monatsperiode erzeugen dieselbe Auftragskennung. Ein eingerichteter Cronjob gehört zur Betriebsfreigabe; der Ausbau legt keinen an.

Der Gesprächsassistent ist separat standardmäßig aus. Nach Einrichtung benutzt er ausschließlich die bestehende Abo-Brücke, keine neue API-Abrechnung und keine Website-Werkzeuge. Pro Projekt gibt es höchstens 20 neue Fragen je Tag; identische Fragen zum selben Datenstand werden wiederverwendet, parallele gleiche Fragen nicht doppelt abgeschickt. Nicht bestätigte oder fehlende Messungen bleiben in der Antwortgrundlage erkennbar. Eine KI-Antwort ersetzt keinen Messnachweis.

## Agenten

Start per `python -m seo_autopilot.arbeitsplatz --config <private-datei> --stdio`. Das ist ein MCP-stdio-Server mit JSON-RPC, Initialisierung und Werkzeugliste. Alternativ gibt es im konfigurierten Arbeitsplatz den authentifizierten Endpunkt `POST /mcp`; Streaming wird nicht angeboten. Quelle zum Protokoll: [MCP-Transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports), [MCP-Werkzeuge](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).

Die 15 Werkzeuge umfassen Projektübersicht, Quelldaten, Dokumente, Wochenvergleich, Keyword-Import, Seitenplan, Recherchelog, Kostenprüfung, Messung, Recherche, Bewertungsstatus und privaten Bericht. Der alte Importpfad `seo_autopilot.mcp.server` delegiert an dieselben Dienste. Er dupliziert keine Auditpipeline mehr.

## Abnahme

`tests/test_arbeitsplatz.py` prüft Mandanten, Bestätigungsrechte, Versionen, Konflikte, read-only Quellen, fehlende Daten, parallele Budgets, Cache, Auftragswiederholung, Provider-Timeout, HTTP-Zugriff, Origin-Schutz, MCP, Wochenvergleich, GSC-Import, Bewertungsaufträge, Assistent und Zeitpläne.

`scripts/browser-arbeitsplatz.py` startet eine temporäre Demo und prüft Anmeldung, Projektakte, Keywords, alle Fachansichten, gesperrte Datenabrufe, Berichte/Fassungen, Abmelden und Handybreite. Browserzugriffe auf andere Hosts sind blockiert. Screenshots sind ausschließlich Beispieldaten.
