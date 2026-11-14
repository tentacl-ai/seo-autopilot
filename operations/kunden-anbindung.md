# SEO-Projekte sicher anbinden

Stand: 14.11.2026. Verantwortlich: Robert Bückel.

Zuerst Betreuungsauftrag, Ziel-Domain und kanonische Projektregeln bestimmen.
Der Hub-SEO-Dienst kann öffentliche Seiten prüfen, ohne einen Schreibadapter
für den Kundenserver zu erhalten. Ein vorhandener Google-Zugang benötigt je
Search-Console-Property bzw. GA4-Property die passenden Leserechte; ein gemeinsames
Cloud-Projekt ersetzt diese Rechte nicht.

## Reine Prüfung

`inspection_only: true` in der privaten Projektkonfiguration erzwingt eine reine
Prüfung auch bei API- und manuellen Starts. Alternativ gilt für einen einzelnen
Start `run --project-id <id> --nur-pruefen`. Der Jobstarter bietet
`python -m seo_autopilot.betriebsjob audit-lesend --projekt <id>`.

Enthalten sind Website- und Bildprüfung, vorhandene GSC-/GA4-Quellen,
regelbasierte Priorisierung, Speicherung und lokaler HTML-Bericht. Es erfolgen
keine Textgenerierung, KI-Intent-Abfragen, Trends, Apply, Freigabenbereinigung oder
Benachrichtigungen. Auto-Fix zusammen mit reiner Prüfung wird abgewiesen.
Öffentliche HTTP-/PageSpeed-Prüfungen und vorhandene Statistik-APIs bleiben
Messungen mit deren jeweiligen Kontingenten. Fehlende Quellen bleiben sichtbar.
Diese Betriebsart repariert keine Kundenwebsite und erweitert keine Zugriffsrechte.

Eine neue Cronzeile wird erst nach geprüftem Auftrag und Paketübernahme aktiviert;
Sollzeitplan und tatsächlicher Jobstatus müssen dieselbe Jobkennung verwenden.
Bestehende Prüfjobs können ihre Kennung behalten; die Projektkonfiguration erzwingt
die eingeschränkte Pipeline. Neue Websites erhalten zunächst einen begrenzten Crawl.

## Messlücke schließen

Vorhandene GA4-Property anhand ihrer Web-Datenstreams der Ziel-Domain zuordnen und
mit einem echten lesenden Datenabruf prüfen. Erst dann die Property-ID und den
vorhandenen serverseitigen Zugang in die private Konfiguration übernehmen.
Kein neuer Trackingcode oder Konto ist damit autorisiert. Kundenspezifische
Berichtsquellen, insbesondere bewusst gewählte Alternativen, bleiben erhalten.

## Grenzen und Rückweg

Fehlende Rechte muss ein berechtigter Kontoinhaber vergeben. Fehlende oder
widersprüchliche Projektregeln verhindern Schreibadapter und Kundendeployments.
Beobachter/Copilot werden nicht automatisch hochgestuft. Bing-/IndexNow-Listen
können auch Kontoanlage, Einreichungen und Benachrichtigungen auslösen; eine
Erweiterung benötigt deshalb eine konkrete Prüfung und Freigabe dieses Umfangs.

Vor Übernahme gezielte Dateihashes, Konfiguration und bestehende Zeitpläne sichern.
Unter derselben Jobsperre installieren, fremdes WIP erhalten und nur geprüfte
Dateien übernehmen. Rückweg ist die gesicherte Code-/Konfigurationsfassung;
Messdatenbanken nicht zurücksetzen. Die Integrationsprüfung nutzt isolierte
Daten und gesperrte externe Provider. Die Live-Abnahme umfasst echten lesenden
Abruf, gespeichertes Ergebnis und Jobstatus; ein voller kommender Routinezyklus
ist erst nach dessen tatsächlichem Ablauf bestätigt.
