#!/usr/bin/env python3
"""Google Analytics 4 fuer tentacl.ai professionell einrichten - nach Herkunft.

    cd <Installationsordner>
    venv/bin/python scripts/ga4_einrichten.py            # Trockenlauf: zeigt, was passieren wuerde
    venv/bin/python scripts/ga4_einrichten.py --scharf   # legt an / aktualisiert

Was das Skript tut (idempotent, nur Konfiguration, keine Daten):
  1. Kanalgruppe "tentacl Herkunft": jeder unserer Wege ist ein eigener Kanal -
     ChatGPT-Anzeigen, Meta-Anzeigen, Google Ads, LinkedIn, Instagram organisch,
     Facebook organisch, KI-Suche organisch (Besucher aus ChatGPT, Perplexity,
     Copilot, Gemini, Claude), Google organisch, andere Suche, E-Mail, Direkt, Verweise.
     Reihenfolge = Prioritaet (erste Regel, die passt, gewinnt).
  2. Schluesselereignisse: formular_gesendet, anruf_klick, mail_klick,
     terminwahl_geoeffnet - das sind die Kontaktabsichten eines Betreibers.
  3. Kontrolle: Verknuepfungen (Google Ads, Search Console), Datenaufbewahrung.

Voraussetzung: Dienstkonto tentacl-seo-bot@tentacl-seo.iam.gserviceaccount.com
ist "Bearbeiter" auf Property 528143447.
"""

from __future__ import annotations

import argparse
import json
import sys

from google.analytics.admin_v1alpha import AnalyticsAdminServiceClient
from google.analytics.admin_v1alpha.types import (
    ChannelGroup,
    ChannelGroupFilter,
    ChannelGroupFilterExpression,
    ChannelGroupFilterExpressionList,
    EventCreateRule,
    GroupingRule,
    KeyEvent,
    MatchingCondition,
)
from google.oauth2 import service_account

PROPERTY = "properties/528143447"
from seo_autopilot.core.config import settings as _einst  # noqa: E402

CREDENTIALS = _einst.GSC_CREDENTIALS_PATH
GRUPPE = "tentacl Herkunft"
SCHLUESSEL = ["formular_gesendet", "anruf_klick", "mail_klick", "terminwahl_geoeffnet"]

# Die Regeln, in Prioritaet. Jede Zeile: (Kanalname, [(Feld, Art, Werte), ...]) -
# alle Bedingungen einer Zeile muessen gelten (UND). Art: "in" = einer der Werte
# (genau), "enthaelt" = einer der Teilstrings.
# Hoechstens 10 Werte je Bedingung - mehr lehnt Google mit einem 500 ab (14.09.2026 gemessen).
KI_QUELLEN = [
    "chatgpt.com",
    "openai.com",
    "perplexity",
    "copilot.microsoft.com",
    "gemini.google.com",
    "claude.ai",
    "you.com",
    "meta.ai",
    "mistral.ai",
    "bing.com/chat",
]
PAID = ["cpc", "paid", "paid_social", "ppc", "paidsocial"]
REGELN = [
    (
        "ChatGPT-Anzeigen",
        [("eachScopeSource", "in", ["chatgpt"]), ("eachScopeMedium", "in", PAID)],
    ),
    (
        "Meta-Anzeigen",
        [
            ("eachScopeSource", "in", ["meta", "facebook", "instagram", "fb", "ig"]),
            ("eachScopeMedium", "in", PAID),
        ],
    ),
    (
        "Google Ads",
        [("eachScopeSource", "in", ["google"]), ("eachScopeMedium", "in", PAID)],
    ),
    ("LinkedIn", [("eachScopeSource", "enthaelt", ["linkedin", "lnkd.in"])]),
    ("Instagram organisch", [("eachScopeSource", "enthaelt", ["instagram"])]),
    (
        "Facebook organisch",
        [("eachScopeSource", "enthaelt", ["facebook", "fb.com", "fb.me"])],
    ),
    ("KI-Suche organisch", [("eachScopeSource", "enthaelt", KI_QUELLEN)]),
    (
        "Google organisch",
        [("eachScopeSource", "in", ["google"]), ("eachScopeMedium", "in", ["organic"])],
    ),
    ("Andere Suche organisch", [("eachScopeMedium", "in", ["organic"])]),
    ("E-Mail", [("eachScopeMedium", "in", ["email", "e-mail", "mail", "newsletter"])]),
    ("Direkt", [("eachScopeSource", "in", ["(direct)"])]),
    ("Verweise", [("eachScopeMedium", "in", ["referral"])]),
]


def _leaf(feld: str, art: str, wert: str) -> ChannelGroupFilterExpression:
    if art == "in":
        return ChannelGroupFilterExpression(
            filter=ChannelGroupFilter(
                field_name=feld,
                string_filter=ChannelGroupFilter.StringFilter(
                    match_type=ChannelGroupFilter.StringFilter.MatchType.EXACT,
                    value=wert,
                ),
            )
        )
    return ChannelGroupFilterExpression(
        filter=ChannelGroupFilter(
            field_name=feld,
            string_filter=ChannelGroupFilter.StringFilter(
                match_type=ChannelGroupFilter.StringFilter.MatchType.CONTAINS,
                value=wert,
            ),
        )
    )


def _bedingung(feld: str, art: str, werte: list[str]) -> ChannelGroupFilterExpression:
    """Eine Bedingung = ODER ueber ihre Werte. Google verlangt: and_group enthaelt
    NUR or_groups, und jede or_group enthaelt nur einfache Filter."""
    return ChannelGroupFilterExpression(
        or_group=ChannelGroupFilterExpressionList(
            filter_expressions=[_leaf(feld, art, w) for w in werte]
        )
    )


def regeln_bauen() -> list[GroupingRule]:
    aus = []
    for name, bedingungen in REGELN:
        gruppen = [_bedingung(f, a, w) for f, a, w in bedingungen]
        # Google verlangt auf oberster Ebene IMMER eine and_group - auch bei einer Bedingung.
        expr = ChannelGroupFilterExpression(
            and_group=ChannelGroupFilterExpressionList(filter_expressions=gruppen)
        )
        aus.append(GroupingRule(display_name=name, expression=expr))
    return aus


def seitenaufruf_ereignis(cl, prop: str, name: str, pfad: str, scharf: bool) -> None:
    """Eigenes Ereignis aus einem Seitenaufruf ableiten (z. B. Danke-Seite = Bewerbung gesendet),
    fuer Seiten, die selbst keine Ereignisse senden."""
    for s in cl.list_data_streams(parent=prop):
        vorhanden = [
            r.destination_event for r in cl.list_event_create_rules(parent=s.name)
        ]
        if name in vorhanden:
            print(f"  Ereignis-Regel '{name}' vorhanden ({s.display_name})")
            continue
        if not scharf:
            print(
                f"  wuerde Ereignis-Regel anlegen: page_view mit '{pfad}' -> {name} ({s.display_name})"
            )
            continue
        regel = EventCreateRule(
            destination_event=name,
            source_copy_parameters=True,
            event_conditions=[
                MatchingCondition(
                    field="event_name",
                    comparison_type=MatchingCondition.ComparisonType.EQUALS,
                    value="page_view",
                ),
                MatchingCondition(
                    field="page_location",
                    comparison_type=MatchingCondition.ComparisonType.CONTAINS,
                    value=pfad,
                ),
            ],
        )
        print(
            "  Ereignis-Regel angelegt:",
            cl.create_event_create_rule(parent=s.name, event_create_rule=regel).name,
        )


def main() -> int:
    global PROPERTY, SCHLUESSEL, GRUPPE
    p = argparse.ArgumentParser()
    p.add_argument("--scharf", action="store_true", help="wirklich anlegen/aendern")
    p.add_argument(
        "--property",
        default=PROPERTY.split("/")[1],
        help="GA4-Property-ID (Standard tentacl.ai)",
    )
    p.add_argument(
        "--website",
        default="tentacl.ai",
        help="Name fuer die Beschreibung der Kanalgruppe",
    )
    p.add_argument(
        "--schluessel",
        default=",".join(SCHLUESSEL),
        help="Schluesselereignisse, kommagetrennt",
    )
    p.add_argument(
        "--danke-ereignis",
        default="",
        help="NAME=PFAD: Ereignis aus Seitenaufruf ableiten",
    )
    p.add_argument("--gruppe", default=GRUPPE, help="Name der Kanalgruppe")
    p.add_argument(
        "--aufbewahrung-14m",
        action="store_true",
        help="Datenaufbewahrung auf 14 Monate setzen",
    )
    a = p.parse_args()
    PROPERTY = f"properties/{a.property}"
    GRUPPE = a.gruppe
    SCHLUESSEL = [s for s in a.schluessel.split(",") if s]
    beschreibung = (
        f"Alle Wege zu {a.website} nach Herkunft (Anzeigen, Social, KI-Suche, Suche)."
    )
    cred = service_account.Credentials.from_service_account_file(
        CREDENTIALS, scopes=["https://www.googleapis.com/auth/analytics.edit"]
    )
    cl = AnalyticsAdminServiceClient(credentials=cred)

    try:
        vorhandene = list(cl.list_channel_groups(parent=PROPERTY))
    except Exception as e:  # noqa: BLE001
        print(f"Kein Zugriff auf {PROPERTY}: {str(e)[:160]}")
        print(
            "Dienstkonto tentacl-seo-bot@tentacl-seo.iam.gserviceaccount.com muss 'Bearbeiter' sein."
        )
        return 2
    print(
        "Vorhandene Kanalgruppen:",
        [(g.display_name, len(g.grouping_rule)) for g in vorhandene],
    )

    regeln = regeln_bauen()
    print(f"\nKanalgruppe '{GRUPPE}' mit {len(regeln)} Kanaelen:")
    for r in regeln:
        print("  -", r.display_name)
    bestehend = next((g for g in vorhandene if g.display_name == GRUPPE), None)
    if a.scharf:
        if bestehend:
            bestehend.grouping_rule = regeln
            bestehend.description = beschreibung
            cl.update_channel_group(
                channel_group=bestehend,
                update_mask={"paths": ["grouping_rule", "description"]},
            )
            print("  aktualisiert:", bestehend.name)
        else:
            neu = cl.create_channel_group(
                parent=PROPERTY,
                channel_group=ChannelGroup(
                    display_name=GRUPPE, grouping_rule=regeln, description=beschreibung
                ),
            )
            print("  angelegt:", neu.name)
    else:
        print("  (Trockenlauf - nichts geaendert)")

    if a.danke_ereignis:
        name, pfad = a.danke_ereignis.split("=", 1)
        print("\nEreignis aus Seitenaufruf:")
        seitenaufruf_ereignis(cl, PROPERTY, name, pfad, a.scharf)

    vorhanden = {k.event_name for k in cl.list_key_events(parent=PROPERTY)}
    print("\nSchluesselereignisse vorhanden:", sorted(vorhanden))
    for name in SCHLUESSEL:
        if name in vorhanden:
            continue
        if a.scharf:
            cl.create_key_event(
                parent=PROPERTY,
                key_event=KeyEvent(
                    event_name=name,
                    counting_method=KeyEvent.CountingMethod.ONCE_PER_EVENT,
                ),
            )
            print("  angelegt:", name)
        else:
            print("  wuerde anlegen:", name)

    print("\nVerknuepfungen:")
    try:
        print(
            "  Google Ads:",
            [l.customer_id for l in cl.list_google_ads_links(parent=PROPERTY)],
        )
    except Exception as e:  # noqa: BLE001
        print("  Google Ads: nicht lesbar", str(e)[:80])
    try:
        print(
            "  Search Console:",
            [l.site_uri for l in cl.list_search_ads_360_links(parent=PROPERTY)]
            or "(SA360 keine)",
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        ds = cl.get_data_retention_settings(name=f"{PROPERTY}/dataRetentionSettings")
        print("  Datenaufbewahrung:", ds.event_data_retention.name)
        if a.aufbewahrung_14m and ds.event_data_retention.name != "FOURTEEN_MONTHS":
            if a.scharf:
                ds.event_data_retention = ds.RetentionDuration.FOURTEEN_MONTHS
                cl.update_data_retention_settings(
                    data_retention_settings=ds,
                    update_mask={"paths": ["event_data_retention"]},
                )
                print("  Datenaufbewahrung jetzt: FOURTEEN_MONTHS")
            else:
                print("  wuerde Datenaufbewahrung auf 14 Monate setzen")
    except Exception as e:  # noqa: BLE001
        print("  Datenaufbewahrung: nicht lesbar", str(e)[:80])
    try:
        for s in cl.list_data_streams(parent=PROPERTY):
            print(
                "  Datenstream:",
                s.display_name,
                s.web_stream_data.measurement_id if s.web_stream_data else "",
            )
    except Exception as e:  # noqa: BLE001
        print("  Datenstreams: nicht lesbar", str(e)[:80])
    return 0


if __name__ == "__main__":
    sys.exit(main())
