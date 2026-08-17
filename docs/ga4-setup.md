# Google Analytics 4 anschließen

Die Search Console sagt, wie oft eine Seite in Google **angezeigt** wird.
Google Analytics sagt, was die Besucher danach tun — bleiben sie, oder sind
sie sofort wieder weg. Erst beide zusammen ergeben ein Bild.

Drei Schritte, einmal je Projekt.

## 1. Property-ID heraussuchen

In Google Analytics oben rechts auf **Verwaltung** (Zahnrad), dann in der
Spalte "Property" auf **Property-Einstellungen**. Oben rechts steht die
**Property-ID** — eine reine Zahl, zum Beispiel `123456789`.

Wichtig: Das ist **nicht** die Mess-ID, die mit `G-` anfängt. Die `G-`-Nummer
steht im Website-Code und funktioniert hier nicht.

## 2. Dienstkonto freischalten

Der Autopilot liest die Daten mit einem eigenen Konto. Dieses Konto braucht
Leserechte an der Property:

1. **Verwaltung → Property-Zugriffsverwaltung**
2. Oben rechts auf **+** → **Nutzer hinzufügen**
3. E-Mail-Adresse eintragen:
   `tentacl-seo-bot@tentacl-seo.iam.gserviceaccount.com`
4. Rolle **Betrachter** auswählen (mehr braucht es nicht — nur lesen)
5. Den Haken bei "E-Mail-Benachrichtigung senden" kann man wegnehmen
6. **Hinzufügen**

Ist das Konto nicht eingetragen, meldet die Quelle beim Lauf
"nicht verfügbar" und der Bericht läuft ohne Besucherdaten weiter.

## 3. Eintrag in `projects.yaml`

Beim gewünschten Projekt `ga4` in die Liste der Quellen aufnehmen und die
Zugangsdaten ergänzen:

```yaml
projects:
  tentacl-ai:
    domain: https://tentacl.ai
    enabled_sources:
      - gsc
      - ga4
    source_config:
      gsc:
        property_url: sc-domain:tentacl.ai
        credentials_path: /opt/odoo/credentials/tentacl-seo-service-account.json
      ga4:
        property_id: "123456789"
        credentials_path: /opt/odoo/credentials/tentacl-seo-service-account.json
```

Die Schlüsseldatei ist dieselbe wie bei der Search Console.

## Was danach im Bericht steht

- Nutzer, Sitzungen und Seitenaufrufe der letzten 28 Tage
- Absprungrate und Interaktionsrate — gesamt und je Seite
- Zugriffsquellen nach Kanal, inklusive Anteil der organischen Suche
- Ein Hinweis bei Seiten, die viele Besucher bekommen, sie aber sofort
  wieder verlieren (ab 30 Sitzungen und 70 % Absprungrate)

## Wenn nichts ankommt

Der Autopilot bricht wegen GA4 nie ab. Er überspringt die Besucherdaten
still und schreibt den Grund ins Log. Die drei häufigsten Gründe:

| Meldung im Log | Ursache |
|---|---|
| `Google-Analytics-Bibliothek nicht installiert` | `pip install google-analytics-data` im venv nachholen |
| `Keine property_id konfiguriert` | Punkt 3 oben — Eintrag fehlt oder ist leer |
| `Zugangsdaten nicht gefunden` | Pfad in `credentials_path` stimmt nicht |
| `403 permission denied` beim Abruf | Punkt 2 oben — Dienstkonto ist nicht freigeschaltet |

Ein schneller Selbsttest: `python -m seo_autopilot.health` (bzw. der
Selbstcheck-Aufruf des Projekts) meldet eine Warnung, wenn `ga4` aktiviert,
aber nicht vollständig konfiguriert ist.
