"""
Backlinks: welche Websites auf uns verlinken — aus dem Common-Crawl-Webgraphen, ohne Kosten.

Warum diese Quelle (30.09.2026 geprueft):
- Bing Webmaster (GetLinkCounts/GetUrlLinks) liefert fuer alle 15 Seiten im Konto 0 Links,
  auch fuer Seiten mit monatelangem Bing-Verkehr — die Schnittstelle gibt Backlinks nicht her.
- Search Console hat keine Link-Schnittstelle; Ahrefs/Semrush/DataForSEO kosten Geld.
- Common Crawl veroeffentlicht monatlich einen Domain-Graphen (je drei Monate Crawl, ~118 Mio.
  Domains, ~3 Mrd. Kanten), frei unter https://commoncrawl.org/terms-of-use.

Ablauf je Graph (alles gestreamt, nichts landet auf der Platte, ~11 GB Download):
1. Knoten lesen: unsere Domains -> Knoten-Nr. (in umgekehrter Schreibweise, ``ai.tentacl``)
2. Kanten lesen: alle Kanten, die auf unsere Nummern zeigen
3. Knoten nochmal lesen: Nummern der verlinkenden Domains -> Namen
4. Rangliste lesen: Harmonic-Centrality-Platz der verlinkenden Domains (klein = bedeutend)

Grenzen, ehrlich: Domain-Ebene (welche Website, nicht welche Seite, kein Ankertext); nur was
Common Crawl gecrawlt hat; kleine/neue Domains fehlen oft ganz ("nicht im Graphen").

Aufruf: ``python -m seo_autopilot.backlinks`` (neuester Graph, falls noch nicht eingelesen)
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

BASIS = "https://data.commoncrawl.org/projects/hyperlinkgraph"
MONATE = "jan feb mar apr may jun jul aug sep oct nov dec".split()
TABELLE = "backlinks"
STAND = "backlinks_graph"

_SCHEMA = f"""
create table if not exists {TABELLE} (
    project_id text not null,
    graph text not null,
    domain text not null,
    rang integer,
    unique (project_id, graph, domain)
);
create index if not exists idx_{TABELLE}_projekt on {TABELLE} (project_id, graph);
create table if not exists {STAND} (
    project_id text not null,
    graph text not null,
    im_graphen integer not null,
    eingelesen_am text not null,
    unique (project_id, graph)
);
"""


def _verbinde(db_pfad: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_pfad)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# --------------------------------------------------------------------------
# Namen
# --------------------------------------------------------------------------


def umgekehrt(domain: str) -> str:
    return ".".join(reversed(domain.split(".")))


def domain_von(url_oder_host: str) -> str:
    host = urlparse(url_oder_host).netloc or url_oder_host
    return host.lower().split(":")[0].removeprefix("www.")


def graph_name(bis: date) -> str:
    """Graph aus drei Crawl-Monaten, die mit ``bis`` enden (Common-Crawl-Schreibweise)."""
    monate = [
        ((bis.month - 1 - i) % 12, bis.year - (1 if bis.month - 1 - i < 0 else 0))
        for i in (2, 1, 0)
    ]
    jahre = sorted({j for _, j in monate})
    kuerzel = "-".join(MONATE[m] for m, _ in monate)
    if len(jahre) == 1:
        return f"cc-main-{jahre[0]}-{kuerzel}"
    return f"cc-main-{jahre[0]}-{str(jahre[1])[2:]}-{kuerzel}"  # cc-main-2025-26-nov-dec-jan


def graph_ende(graph: str) -> date:
    """Letzter Crawl-Monat eines Graphen - zum Sortieren (alphabetisch waere "jun" > "jul")."""
    teile = graph.split("-")
    jahr = int(teile[2]) + (1 if len(teile) == 7 else 0)  # cc-main-2025-26-nov-dec-jan
    return date(jahr, MONATE.index(teile[-1]) + 1, 1)


MONATSNAME = "Jan. Feb. März Apr. Mai Juni Juli Aug. Sep. Okt. Nov. Dez.".split()


def graph_text(graph: str) -> str:
    """cc-main-2026-jul-aug-sep -> "Juli bis Sep. 2026" (Crawl-Zeitraum)."""
    ende = graph_ende(graph)
    anfang = (ende.month - 3) % 12
    return f"{MONATSNAME[anfang]} bis {MONATSNAME[ende.month - 1]} {ende.year}"


def kandidaten(heute: date, anzahl: int = 6) -> List[str]:
    """Moegliche Graphen, neuester zuerst."""
    aus = []
    j, m = heute.year, heute.month
    for _ in range(anzahl):
        aus.append(graph_name(date(j, m, 1)))
        j, m = (j, m - 1) if m > 1 else (j - 1, 12)
    return aus


def datei_url(graph: str, art: str) -> str:
    return f"{BASIS}/{graph}/domain/{graph}-domain-{art}.txt.gz"


def gibt_es(graph: str) -> bool:
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-I", "-m", "30",
         datei_url(graph, "vertices")],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    return r.stdout.strip() == "200"


def neuester_graph(
    heute: Optional[date] = None, pruefe: Callable[[str], bool] = gibt_es
) -> Optional[str]:
    for g in kandidaten(heute or date.today()):
        if pruefe(g):
            return g
    return None


# --------------------------------------------------------------------------
# Streams (curl | pigz | mawk) — Python waere fuer 3 Mrd. Zeilen zu langsam
# --------------------------------------------------------------------------


def _awk() -> str:
    return shutil.which("mawk") or "awk"


def _entpacker() -> List[str]:
    return [shutil.which("pigz") or "gzip", "-dc"]


def strom_filtern(
    url: str, schluessel_datei: Path, spalte: int, ausgabe: Sequence[int]
) -> List[List[str]]:
    """Zeilen, deren ``spalte`` (1-basiert) in der Schluesseldatei steht; nur ``ausgabe``-Spalten."""
    felder = ' "\\t" '.join(f"${n}" for n in ausgabe)
    prog = f"NR==FNR {{ w[$1]; next }} (${spalte} in w) {{ print {felder} }}"
    curl = subprocess.Popen(
        ["curl", "-s", "--fail", "-m", "7200", url], stdout=subprocess.PIPE
    )
    entp = subprocess.Popen(_entpacker(), stdin=curl.stdout, stdout=subprocess.PIPE)
    curl.stdout.close()
    awk = subprocess.run([_awk(), "-F", "\t", prog, str(schluessel_datei), "-"],
                         stdin=entp.stdout, capture_output=True, text=True, check=False)  # fmt: skip
    entp.stdout.close()
    if curl.wait() != 0 or entp.wait() != 0 or awk.returncode != 0:
        raise RuntimeError(
            f"Stream abgebrochen: {url} (curl {curl.returncode}, entpacken {entp.returncode}, awk {awk.returncode})"
        )
    return [z.split("\t") for z in awk.stdout.splitlines() if z]


def _schluessel(ordner: Path, name: str, werte: Iterable[str]) -> Path:
    p = ordner / name
    p.write_text("".join(f"{w}\n" for w in werte), encoding="utf-8")
    return p


def aus_graph_lesen(
    graph: str, domains: Sequence[str], filtern: Callable = strom_filtern
) -> Dict[str, Any]:
    """Liefert je Domain die verlinkenden Domains mit Rang; ``None`` = nicht im Graphen."""
    with tempfile.TemporaryDirectory(prefix="backlinks-") as t:
        ordner = Path(t)
        rev = {umgekehrt(d): d for d in domains}
        knoten = filtern(
            datei_url(graph, "vertices"), _schluessel(ordner, "rev", rev), 2, (1, 2)
        )
        nummer = {z[0]: rev[z[1]] for z in knoten}
        logger.info(
            f"[backlinks] {graph}: {len(nummer)} von {len(domains)} Domains im Graphen"
        )
        ergebnis: Dict[str, Any] = {d: None for d in domains}
        if not nummer:
            return ergebnis
        for d in nummer.values():
            ergebnis[d] = {}
        kanten = filtern(
            datei_url(graph, "edges"), _schluessel(ordner, "ziele", nummer), 2, (1, 2)
        )
        von_nach = [(v, n) for v, n in kanten if v != n]
        if not von_nach:
            return ergebnis
        namen = {z[0]: z[1] for z in filtern(
            datei_url(graph, "vertices"), _schluessel(ordner, "quellen", {v for v, _ in von_nach}), 1, (1, 2))}  # fmt: skip
        raenge = {z[1]: int(z[0]) for z in filtern(
            datei_url(graph, "ranks"), _schluessel(ordner, "namen", set(namen.values())), 5, (1, 5))
            if z[0].isdigit()}  # fmt: skip
        for v, n in von_nach:
            if v in namen:
                ergebnis[nummer[n]][umgekehrt(namen[v])] = raenge.get(namen[v])
        return ergebnis


# --------------------------------------------------------------------------
# Speichern und Auswerten
# --------------------------------------------------------------------------


def speichere(
    db_pfad: str, project_id: str, graph: str, links: Optional[Dict[str, Optional[int]]]
) -> int:
    jetzt = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _verbinde(db_pfad) as conn:
        conn.execute(
            f"delete from {TABELLE} where project_id = ? and graph = ?",
            (project_id, graph),
        )
        conn.executemany(
            f"insert into {TABELLE} (project_id, graph, domain, rang) values (?, ?, ?, ?)",
            [(project_id, graph, d, r) for d, r in (links or {}).items()],
        )
        conn.execute(
            f"insert or replace into {STAND} (project_id, graph, im_graphen, eingelesen_am) values (?, ?, ?, ?)",
            (project_id, graph, int(links is not None), jetzt),
        )
    return len(links or {})


def eingelesen(db_pfad: str, graph: str) -> set:
    with _verbinde(db_pfad) as conn:
        return {
            r["project_id"]
            for r in conn.execute(
                f"select project_id from {STAND} where graph = ?", (graph,)
            )
        }


def auswertung(db_pfad: str, project_id: str, top: int = 15) -> Dict[str, Any]:
    """Neuester Graph gegen den davor: Anzahl, neu, weg, bedeutendste verlinkende Domains."""
    with _verbinde(db_pfad) as conn:
        staende = sorted(
            conn.execute(
                f"select graph, im_graphen from {STAND} where project_id = ?",
                (project_id,),
            ).fetchall(),
            key=lambda r: graph_ende(r["graph"]),
            reverse=True,
        )[:2]
        if not staende:
            return {}
        jetzt, davor = staende[0], (staende[1] if len(staende) > 1 else None)

        def links(graph: str) -> Dict[str, Optional[int]]:
            return {r["domain"]: r["rang"] for r in conn.execute(
                f"select domain, rang from {TABELLE} where project_id = ? and graph = ?", (project_id, graph))}  # fmt: skip

        a = links(jetzt["graph"])
        b = links(davor["graph"]) if davor else None
    wichtig = sorted(a.items(), key=lambda x: (x[1] is None, x[1] or 0))
    return {
        "graph": jetzt["graph"],
        "im_graphen": bool(jetzt["im_graphen"]),
        "domains": len(a),
        "davor": len(b) if b is not None else None,
        "graph_davor": davor["graph"] if davor else None,
        "neu": sorted(set(a) - set(b)) if b is not None else [],
        "weg": sorted(set(b) - set(a)) if b is not None else [],
        "wichtigste": [{"domain": d, "rang": r} for d, r in wichtig[:top]],
    }


def einlesen(
    db_pfad: str,
    projekte: Dict[str, Dict[str, Any]],
    graph: str,
    filtern: Callable = strom_filtern,
) -> Dict[str, int]:
    offen = {pid: domain_von(cfg.get("domain", "")) for pid, cfg in projekte.items()
             if cfg.get("domain") and pid not in eingelesen(db_pfad, graph)}  # fmt: skip
    if not offen:
        return {}
    je_domain = aus_graph_lesen(graph, sorted(set(offen.values())), filtern)
    return {
        pid: speichere(db_pfad, pid, graph, je_domain.get(d))
        for pid, d in offen.items()
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .historie import standard_db_pfad
    from .kundenbericht import WURZEL, lade_projekte

    ap = argparse.ArgumentParser(prog="python -m seo_autopilot.backlinks")
    ap.add_argument(
        "--graph",
        help="bestimmter Graph, z. B. cc-main-2026-jul-aug-sep (sonst neuester)",
    )
    ap.add_argument("--projects", default=str(WURZEL / "projects.yaml"))
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    db = args.db or standard_db_pfad()
    graph = args.graph or neuester_graph()
    if not graph:
        print("Kein Common-Crawl-Graph erreichbar.")
        return 1
    projekte = {
        k: v for k, v in lade_projekte(args.projects).items() if v.get("enabled", True)
    }
    ergebnis = einlesen(db, projekte, graph)
    if not ergebnis:
        print(f"{graph}: schon eingelesen.")
    for pid, n in ergebnis.items():
        print(f"{graph}: {pid} {n} verlinkende Domains")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
