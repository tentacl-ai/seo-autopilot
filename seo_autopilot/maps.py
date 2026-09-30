"""
Local SEO: Google-Maps-Eintrag und Platz in der Maps-Suche — ueber die Places API (New).

Was gemessen wird (einmal pro Woche je Projekt mit ``maps:``-Block in projects.yaml):
- Profil: Sterne, Anzahl Bewertungen, Status (geoeffnet/geschlossen), Adresse, Telefon,
  hinterlegte Website — daraus Hinweise (Website fehlt/zeigt woanders hin, kein Telefon).
- Platz je Suchbegriff: Textsuche rund um den Standort (oder weitere Orte), bis Platz 60.

Kosten (Preise seit 03/2025, je SKU eigenes Freikontingent pro Monat):
- Textsuche nur mit ``places.id`` = "Text Search Essentials (IDs Only)": ohne Grenze frei.
- Profil mit Sternen/Telefon/Website = "Place Details Enterprise": 1.000 frei, wir brauchen ~5.
Der Schluessel (``GOOGLE_PLACES_API_KEY``, GCP-Projekt tentacl-seo, nur Places + Hub-IPs)
wird nur als Header gesendet und nie ausgegeben.

projects.yaml (Beispiel)::

    maps:
      place_id: ChIJ...            # Eintrag bei Google (einmalig mit --finden ermitteln)
      region: de                   # Laendercode fuer die Suche
      radius_km: 20                # Umkreis um den Standort
      begriffe: [Finanzberater Gewerbe, Versicherungsmakler]
      orte:                        # optional: zusaetzlich von anderen Orten aus suchen
        - {name: Rosenheim, lat: 47.856, lng: 12.128}

Aufruf: ``python -m seo_autopilot.maps`` (diese Woche, falls noch nicht gemessen)
        ``python -m seo_autopilot.maps --finden "Name Ort"`` (Eintrag suchen, zeigt ID)
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

BASIS = "https://places.googleapis.com/v1"
MAX_PLATZ = 60  # drei Seiten a 20; weiter hinten sucht niemand
PROFIL_FELDER = ",".join(
    [
        "id",
        "displayName",
        "formattedAddress",
        "nationalPhoneNumber",
        "websiteUri",
        "rating",
        "userRatingCount",
        "businessStatus",
        "googleMapsUri",
        "location",
        "primaryTypeDisplayName",
    ]
)

_SCHEMA = """
create table if not exists maps_profil (
    project_id text not null,
    woche text not null,
    place_id text not null,
    name text,
    adresse text,
    telefon text,
    website text,
    sterne real,
    bewertungen integer,
    status text,
    maps_url text,
    art text,
    abgerufen_am text not null,
    unique (project_id, woche)
);
create table if not exists maps_platz (
    project_id text not null,
    woche text not null,
    begriff text not null,
    ort text not null,
    platz integer,
    unique (project_id, woche, begriff, ort)
);
"""

Abruf = Callable[[str, str, Optional[Dict[str, Any]], str], Dict[str, Any]]


def _verbinde(db_pfad: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_pfad)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def wochenschluessel(tag: date) -> str:
    jahr, woche, _ = tag.isocalendar()
    return f"{jahr}-W{woche:02d}"


def _schluessel() -> str:
    import os

    wert = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not wert:
        from .core.config import settings

        wert = settings.GOOGLE_PLACES_API_KEY
    if not wert:
        raise RuntimeError("GOOGLE_PLACES_API_KEY fehlt (.env des Autopiloten)")
    return wert


def google_abruf(
    methode: str, pfad: str, body: Optional[Dict[str, Any]], felder: str
) -> Dict[str, Any]:
    """Ein Aufruf der Places API. Fehlertexte ohne Schluessel (steht nur im Header)."""
    kopf = {"X-Goog-Api-Key": _schluessel(), "X-Goog-FieldMask": felder}
    r = httpx.request(methode, f"{BASIS}/{pfad}", json=body, headers=kopf, timeout=30)
    if r.status_code != 200:
        grund = (r.json().get("error") or {}).get("status", "") if r.content else ""
        raise RuntimeError(
            f"Places API {pfad.split('?')[0]}: HTTP {r.status_code} {grund}"
        )
    return r.json()


# --------------------------------------------------------------------------
# Abrufe
# --------------------------------------------------------------------------


def profil(place_id: str, abruf: Abruf = google_abruf) -> Dict[str, Any]:
    d = abruf("GET", f"places/{place_id}?languageCode=de", None, PROFIL_FELDER)
    ort = d.get("location") or {}
    return {
        "place_id": d.get("id", place_id),
        "name": (d.get("displayName") or {}).get("text"),
        "adresse": d.get("formattedAddress"),
        "telefon": d.get("nationalPhoneNumber"),
        "website": d.get("websiteUri"),
        "sterne": d.get("rating"),
        "bewertungen": d.get("userRatingCount") or 0,
        "status": d.get("businessStatus"),
        "maps_url": d.get("googleMapsUri"),
        "art": (d.get("primaryTypeDisplayName") or {}).get("text"),
        "lat": ort.get("latitude"),
        "lng": ort.get("longitude"),
    }


def platz(
    begriff: str,
    place_id: str,
    lat: float,
    lng: float,
    radius_km: float = 20,
    region: str = "de",
    abruf: Abruf = google_abruf,
) -> Optional[int]:
    """Platz des Eintrags in der Maps-Suche rund um (lat, lng); None = nicht unter den ersten 60."""
    body: Dict[str, Any] = {
        "textQuery": begriff,
        "languageCode": "de",
        "regionCode": region,
        "pageSize": 20,
        "locationBias": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": min(radius_km * 1000, 50000),
            }
        },
    }
    gesehen = 0
    while gesehen < MAX_PLATZ:
        d = abruf("POST", "places:searchText", body, "places.id,nextPageToken")
        ids = [p.get("id") for p in d.get("places") or []]
        if place_id in ids:
            return gesehen + ids.index(place_id) + 1
        gesehen += len(ids)
        if not ids or not d.get("nextPageToken"):
            return None
        body = {**body, "pageToken": d["nextPageToken"]}
    return None


def finden(
    text: str, region: str = "de", abruf: Abruf = google_abruf
) -> List[Dict[str, Any]]:
    """Eintraege zu einem Namen (einmalig beim Einrichten, um die place_id zu bekommen)."""
    body = {
        "textQuery": text,
        "languageCode": "de",
        "regionCode": region,
        "pageSize": 5,
    }
    felder = "places.id,places.displayName,places.formattedAddress"
    d = abruf("POST", "places:searchText", body, felder)
    return [
        {
            "place_id": p.get("id"),
            "name": (p.get("displayName") or {}).get("text"),
            "adresse": p.get("formattedAddress"),
        }
        for p in d.get("places") or []
    ]


# --------------------------------------------------------------------------
# Messen und speichern
# --------------------------------------------------------------------------


def _orte(cfg: Dict[str, Any], p: Dict[str, Any]) -> List[Dict[str, Any]]:
    orte = [{"name": "am Standort", "lat": p["lat"], "lng": p["lng"]}]
    for o in cfg.get("orte") or []:
        if o.get("name") and o.get("lat") is not None and o.get("lng") is not None:
            orte.append(
                {"name": o["name"], "lat": float(o["lat"]), "lng": float(o["lng"])}
            )
    return [o for o in orte if o["lat"] is not None and o["lng"] is not None]


def messen(cfg: Dict[str, Any], abruf: Abruf = google_abruf) -> Dict[str, Any]:
    p = profil(cfg["place_id"], abruf)
    plaetze = []
    for begriff in cfg.get("begriffe") or []:
        for o in _orte(cfg, p):
            n = platz(
                begriff,
                p["place_id"],
                o["lat"],
                o["lng"],
                float(cfg.get("radius_km", 20)),
                cfg.get("region", "de"),
                abruf,
            )
            plaetze.append({"begriff": begriff, "ort": o["name"], "platz": n})
    return {"profil": p, "plaetze": plaetze}


def speichere(db_pfad: str, project_id: str, woche: str, m: Dict[str, Any]) -> None:
    p = m["profil"]
    jetzt = datetime.now(timezone.utc).isoformat(timespec="minutes")
    with _verbinde(db_pfad) as conn:
        conn.execute(
            "delete from maps_platz where project_id = ? and woche = ?",
            (project_id, woche),
        )
        conn.execute(
            "insert or replace into maps_profil values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, woche, p["place_id"], p["name"], p["adresse"], p["telefon"],
             p["website"], p["sterne"], p["bewertungen"], p["status"], p["maps_url"],
             p.get("art"), jetzt),
        )  # fmt: skip
        conn.executemany(
            "insert into maps_platz values (?,?,?,?,?)",
            [
                (project_id, woche, z["begriff"], z["ort"], z["platz"])
                for z in m["plaetze"]
            ],
        )


def gemessen(db_pfad: str, project_id: str, woche: str) -> bool:
    with _verbinde(db_pfad) as conn:
        return bool(conn.execute(
            "select 1 from maps_profil where project_id = ? and woche = ?", (project_id, woche)
        ).fetchone())  # fmt: skip


# --------------------------------------------------------------------------
# Auswertung (Bericht, Steuerzentrale)
# --------------------------------------------------------------------------


def _host(url: Optional[str]) -> str:
    host = urlparse(url or "").hostname or ""
    return host.lower().removeprefix("www.")


def hinweise(p: Dict[str, Any], domain: str = "") -> List[str]:
    """Was am Maps-Eintrag fehlt oder nicht zur Website passt."""
    aus = []
    if p.get("status") and p["status"] != "OPERATIONAL":
        aus.append("Der Eintrag ist bei Google nicht als geöffnet markiert.")
    if not p.get("website"):
        aus.append("Im Eintrag ist keine Website hinterlegt.")
    elif domain and _host(p["website"]) != _host(domain):
        aus.append(
            f"Der Eintrag verweist auf {_host(p['website'])}, nicht auf {_host(domain)}."
        )
    elif urlparse(p["website"]).path.strip("/"):
        aus.append(
            f"Der Eintrag verweist auf eine Unterseite ({p['website']}) statt auf die Startseite."
        )
    if not p.get("art") or p["art"].startswith("POI"):
        aus.append(
            "Keine passende Kategorie gewählt – Google führt den Eintrag nur als allgemeinen Ort."
        )
    if not p.get("telefon"):
        aus.append("Im Eintrag fehlt eine Telefonnummer.")
    if (p.get("bewertungen") or 0) < 5:
        aus.append("Weniger als 5 Bewertungen – Kunden aktiv um eine Bewertung bitten.")
    return aus


def _profile(conn: sqlite3.Connection, project_id: str) -> List[sqlite3.Row]:
    return conn.execute(
        "select * from maps_profil where project_id = ? order by woche desc limit 2",
        (project_id,),
    ).fetchall()


def auswertung(db_pfad: str, project_id: str, domain: str = "") -> Dict[str, Any]:
    """Neueste Woche gegen die davor: Sterne, Bewertungen, Plaetze je Begriff und Ort."""
    with _verbinde(db_pfad) as conn:
        profile = _profile(conn, project_id)
        if not profile:
            return {}
        jetzt, davor = dict(profile[0]), (
            dict(profile[1]) if len(profile) > 1 else None
        )

        def plaetze(woche: str) -> Dict[tuple, Optional[int]]:
            return {(r["begriff"], r["ort"]): r["platz"] for r in conn.execute(
                "select begriff, ort, platz from maps_platz where project_id = ? and woche = ?"
                " order by rowid",
                (project_id, woche))}  # fmt: skip

        a = plaetze(jetzt["woche"])
        b = plaetze(davor["woche"]) if davor else {}
    return {
        "woche": jetzt["woche"],
        "name": jetzt["name"],
        "adresse": jetzt["adresse"],
        "maps_url": jetzt["maps_url"],
        "sterne": jetzt["sterne"],
        "bewertungen": jetzt["bewertungen"],
        "bewertungen_davor": davor["bewertungen"] if davor else None,
        "sterne_davor": davor["sterne"] if davor else None,
        "plaetze": [
            {
                "begriff": k[0],
                "ort": k[1],
                "platz": v,
                "davor": b.get(k),
                "neu": k not in b,
            }
            for k, v in a.items()
        ],
        "hinweise": hinweise(jetzt, domain),
    }


def messen_und_speichern(
    db_pfad: str,
    projekte: Dict[str, Dict[str, Any]],
    heute: Optional[date] = None,
    abruf: Abruf = google_abruf,
    neu: bool = False,
) -> Dict[str, str]:
    woche = wochenschluessel(heute or datetime.now(timezone.utc).date())
    aus: Dict[str, str] = {}
    for pid, cfg in projekte.items():
        m_cfg = cfg.get("maps") or {}
        if not m_cfg.get("place_id"):
            continue
        if not neu and gemessen(db_pfad, pid, woche):
            aus[pid] = "schon gemessen"
            continue
        try:
            m = messen(m_cfg, abruf)
        except Exception as e:  # ein Projekt darf die anderen nicht aufhalten
            logger.error("[maps] %s: %s", pid, e)
            aus[pid] = f"Fehler: {e}"
            continue
        speichere(db_pfad, pid, woche, m)
        p = m["profil"]
        aus[pid] = (
            f"{p['sterne']} Sterne, {p['bewertungen']} Bewertungen, {len(m['plaetze'])} Plaetze"
        )
    return aus


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .historie import standard_db_pfad
    from .kundenbericht import WURZEL, lade_projekte

    ap = argparse.ArgumentParser(prog="python -m seo_autopilot.maps")
    ap.add_argument("--projects", default=str(WURZEL / "projects.yaml"))
    ap.add_argument("--db", default=None)
    ap.add_argument("--neu", action="store_true", help="diese Woche nochmal messen")
    ap.add_argument("--finden", help='Eintrag suchen, z. B. "Firmenname Ort"')
    ap.add_argument("--region", default="de")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.finden:
        for t in finden(args.finden, args.region):
            print(f"{t['place_id']}  {t['name']}  ({t['adresse']})")
        return 0
    projekte = {
        k: v for k, v in lade_projekte(args.projects).items() if v.get("enabled", True)
    }
    ergebnis = messen_und_speichern(
        args.db or standard_db_pfad(), projekte, neu=args.neu
    )
    if not ergebnis:
        print("Kein Projekt mit maps.place_id in projects.yaml.")
    for pid, text in ergebnis.items():
        print(f"{pid}: {text}")
    return 0 if not any(t.startswith("Fehler") for t in ergebnis.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
