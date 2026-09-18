"""
Empfehlungen umsetzen — sichtbarer Text auf der Website (Stufe 3, 18.09.2026).

Robert (18.09.2026, ausdruecklich freigegeben): Der Autopilot soll wie ein
SEO-Profi seine Liste abarbeiten und Texte vollautomatisch umsetzen — zunaechst
NUR bei Projekten mit `betriebsart: autopilot` (heute: tentacl.ai). Alle anderen
bleiben beim Freigabe-Weg: Eine Empfehlung wird dort erst umgesetzt, wenn sie
per Knopf freigegeben wurde (Status `freigegeben`), und nur mit Dateizugriff
(statischer Adapter); sonst ist sie ein Auftrag fuer Handarbeit.

Schutzgelaender (alle Pflicht, jedes einzeln getestet):

  1. Nur Fakten von der Website: die Texte wurden beim Erzeugen gegen Seite und
     Belege geprueft (Zahlen, Behauptungen) — hier wird das an der AKTUELLEN
     Seite wiederholt.
  2. Platzhalter gehen nie live; unbeantwortete Fragen fallen weg.
  3. Tabuwoerter, Marke und Ansprache aus `adapter_config.seo_regeln`.
  4. Gesperrte Seiten: `seo_regeln.gesperrte_seiten` + immer Impressum/Datenschutz/AGB.
  5. Hoechstens 3 Textaenderungen je Lauf und 1 je Seite und Woche.
  6. Getrennter zweiter KI-Pruefdurchgang (abo_ki): stimmt jede Aussage mit der
     Website ueberein, klingt es natuerlich, kein Keyword-Stuffing? Nur bei
     "ja" wird geschrieben.
  7. Sprache und Sie/du der Seite bleiben (Pruefung 1 + 6).

Jede Aenderung: eigener Git-Commit (Adapter), Eintrag im Aenderungsbuch
(`aktion = text_<art>`), Wirkungsmessung laeuft darueber automatisch nach
7/14/28/56 Tagen.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import empfehlungen as em
from . import handwerker as hw

logger = logging.getLogger(__name__)

MAX_JE_LAUF = 3
SPERRE_TAGE = 7
MAX_PRUEFUNGEN = 8  # zweite KI-Pruefungen je Lauf (Kostendeckel)
PRUEF_MODELL = "opus"

PRUEF_SYSTEM = """Du bist ein strenger Lektor und Faktenprüfer für die Website '{name}' ({domain}).
Du prüfst eine geplante Textänderung, BEVOR sie automatisch veröffentlicht wird.
Gib "ja" NUR, wenn ALLES zutrifft:
1. Jede Aussage im neuen Text steht so oder sinngemäß im gelieferten Seitentext oder in den Belegen derselben Website. Keine erfundenen Zahlen, Preise, Jahre, Auszeichnungen, Garantien oder Versprechen.
2. Der Text klingt natürlich, wie von einem Menschen für Kunden geschrieben.
3. Kein Keyword-Stuffing (Suchbegriffe nicht gehäuft oder unnatürlich eingebaut).
4. Sprache und Ansprache ({anrede}) passen zur Seite.
5. Keine Platzhalter, kein HTML, keine Werbefloskeln.
6. Die Änderung ist für einen MENSCHEN hilfreich und bringt ihm etwas Neues oder Klareres — sie ist
   keine bloße Keyword-Füllung und kein Umschreiben "für KI/Suchmaschinen".{zusatz}
Im Zweifel "nein".
Antworte ausschließlich mit JSON:
{{"hilfreich": "ja" oder "nein", "freigabe": "ja" oder "nein", "gruende": ["..."]}}"""


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _pruef_system(cfg: Dict[str, Any], seiten_anrede: Optional[str]) -> str:
    regeln = (cfg.get("adapter_config") or {}).get("seo_regeln") or {}
    zusatz = ""
    if regeln.get("verbotene_woerter"):
        zusatz += "\n7. Diese Wörter sind tabu: " + ", ".join(
            map(str, regeln["verbotene_woerter"])
        )
    for h in regeln.get("hinweise", []) or []:
        zusatz += f"\n- Regel der Website: {h}"
    an = {"sie": "Sie", "du": "du"}.get(seiten_anrede or "", "wie auf der Seite")
    return PRUEF_SYSTEM.format(
        name=cfg.get("name", ""), domain=cfg.get("domain", ""), anrede=an, zusatz=zusatz
    )


def live_texte(e: em.Empfehlung) -> List[str]:
    """Genau die Saetze, die auf die Website gingen (fuer Pruefung und Aenderungsbuch)."""
    a = (e.vorschlag or {}).get("anwendung") or {}
    typ = a.get("typ", "")
    if typ == "empfehlung_faq_ergaenzen":
        return [x for f in a.get("fragen", []) for x in (f["frage"], f["antwort"])]
    if typ == "empfehlung_abschnitt_ergaenzen":
        return [a.get("ueberschrift", ""), a.get("text", "")]
    if typ in ("empfehlung_ueberschrift_verbessern", "empfehlung_antwort_zuerst"):
        return [a.get("neu", "")]
    if typ == "empfehlung_interne_links":
        return [a.get("linktext", "")]
    return []


def aenderung_als_text(e: em.Empfehlung) -> str:
    a = (e.vorschlag or {}).get("anwendung") or {}
    typ = a.get("typ", "")
    if typ == "empfehlung_faq_ergaenzen":
        return "Neuer Abschnitt „Häufige Fragen“:\n" + "\n".join(
            f"Frage: {f['frage']}\nAntwort: {f['antwort']}" for f in a.get("fragen", [])
        )
    if typ == "empfehlung_abschnitt_ergaenzen":
        return f"Neuer Abschnitt „{a.get('ueberschrift')}“:\n{a.get('text')}"
    if typ == "empfehlung_ueberschrift_verbessern":
        return f"H1 alt: {a.get('alt')}\nH1 neu: {a.get('neu')}"
    if typ == "empfehlung_antwort_zuerst":
        return f"Erster Absatz alt: {a.get('alt')}\nErster Absatz neu: {a.get('neu')}"
    if typ == "empfehlung_interne_links":
        return (
            f"Auf {a.get('quelle')} wird das vorhandene Wort „{a.get('linktext')}“ "
            f"auf {a.get('zielseite') or a.get('ziel')} verlinkt (kein neuer Text)."
        )
    return ""


def schutzgelaender(
    e: em.Empfehlung,
    regeln: Dict[str, Any],
    struktur: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Grund, warum diese Empfehlung NICHT automatisch live darf — oder None."""
    a = (e.vorschlag or {}).get("anwendung")
    if not a or not a.get("typ"):
        return "kein anwendbarer Vorschlag"
    if e.art not in em.ANWENDBARE_ARTEN:
        return f"Art {e.art} wird nie automatisch umgesetzt"
    if e.ohne_suchdaten:
        return "Vermutung ohne Suchdaten – nur als Vorschlag"
    for s in {e.seite, a.get("seite", ""), a.get("zielseite", "")} - {""}:
        if em.seite_gesperrt(s, regeln):
            return f"Seite gesperrt: {s}"
    if struktur is None:
        return "Seite nicht lesbar"
    texte = [t for t in live_texte(e) if t is not None]
    if not texte or any(not str(t).strip() for t in texte):
        return "leerer Text"
    if any(
        em.PLATZHALTER in t or "[" in t or "]" in t for t in texte + [a.get("html", "")]
    ):
        return "Platzhalter im Text"
    belege = " ".join(b.get("satz", "") for b in (e.vorschlag or {}).get("belege", []))
    quelle = (
        struktur.get("text_voll", "") + " " + struktur.get("title", "") + " " + belege
    )
    if a["typ"] == "empfehlung_interne_links":
        return None  # vorhandenes Wort wird nur verlinkt — kein neuer Text
    fragen = {f.get("frage") for f in a.get("fragen", [])}
    for t in texte:
        # Fragen duerfen die Worte der Suchenden tragen, Antworten/Texte nicht
        ok, grund = em.pruefe_text(
            t,
            quelle,
            regeln,
            struktur.get("anrede"),
            min_len=3,
            max_len=900,
            streng=t not in fragen,
        )
        if not ok:
            return f"Regelprüfung: {grund} („{t[:50]}“)"
    return None


def zweite_pruefung(
    e: em.Empfehlung,
    cfg: Dict[str, Any],
    struktur: Dict[str, Any],
    fragen: Callable[..., str],
) -> Tuple[bool, List[str]]:
    """Getrennter KI-Pruefdurchgang. Nur ein eindeutiges "ja" gibt frei."""
    belege = (e.vorschlag or {}).get("belege", [])
    auftrag = "\n".join(
        [
            f"Seite: {struktur.get('seite')}",
            f"Seitentext: {' '.join(struktur.get('text_voll', '').split()[:1600])}",
            (
                "Belege anderer Seiten derselben Website:\n"
                + "\n".join(f"- [{b['seite']}] {b['satz']}" for b in belege)
                if belege
                else "Belege: (keine)"
            ),
            "",
            f"GEPLANTE ÄNDERUNG ({em.ART_KLARTEXT.get(e.art, e.art)}):",
            aenderung_als_text(e),
        ]
    )
    try:
        antwort = em.json_aus(
            fragen(auftrag, _pruef_system(cfg, struktur.get("anrede")))
        )
    except Exception as exc:
        return False, [f"Prüfung nicht erreichbar: {type(exc).__name__}"]
    urteil = str(antwort.get("freigabe", "")).strip().lower()
    hilfreich = str(antwort.get("hilfreich", "")).strip().lower()
    gruende = [str(g)[:200] for g in (antwort.get("gruende") or [])][:5]
    ok = urteil == "ja" and hilfreich == "ja"
    if not ok and not gruende:
        gruende = [
            "nicht hilfreich für Menschen" if hilfreich != "ja" else "kein klares ja"
        ]
    return ok, gruende


def _ki_pruefen(prompt: str, system: str) -> str:
    from . import abo_ki

    return abo_ki.fragen(prompt, system=system, modell=PRUEF_MODELL, zeitlimit=240)


def in_sperrfrist(db: str, projekt: str, seite: str, heute: datetime) -> bool:
    """Wurde diese Seite in den letzten 7 Tagen schon textlich geaendert?"""
    grenze = (heute - timedelta(days=SPERRE_TAGE)).isoformat()
    try:
        con = sqlite3.connect(db)
        r = con.execute(
            f"select 1 from {em.TABELLE} where project_id=? and geaenderte_seite=? "
            "and status=? and umgesetzt_am >= ? limit 1",
            (projekt, seite, em.STATUS_UMGESETZT, grenze),
        ).fetchone()
        con.close()
    except sqlite3.Error:
        return True  # im Zweifel sperren
    return r is not None


def _buchen(
    db: str, projekt: str, e: em.Empfehlung, ar: Any, audit_id: str
) -> Optional[str]:
    from .changelog_book import (
        STATUS_ANGEWENDET,
        URHEBER_AUTOPILOT,
        notiere_aenderung,
    )

    a = e.vorschlag["anwendung"]
    commit = ar.commit_hash
    if commit in ("no-git", "already-applied", "not-tracked"):
        commit = None
    cid = notiere_aenderung(
        db,
        projekt,
        f"text_{e.art}",
        audit_id=audit_id,
        urheber=URHEBER_AUTOPILOT,
        # Gemessen wird die Seite, die profitieren soll (bei Links die Zielseite)
        ziel_url=a.get("zielseite") or e.seite,
        datei_pfad=", ".join(ar.files_changed) or None,
        vorher=a.get("alt") or "",
        nachher=aenderung_als_text(e),
        begruendung=f"{e.titel} (Empfehlung {e.id[:8]}, zweite KI-Prüfung bestanden)",
        issue_type=a["typ"],
        git_commit=commit,
        rueckgaengig_moeglich=bool(commit),
        status=STATUS_ANGEWENDET,
    )
    return cid or None


def umsetzen(
    projekt: str,
    cfg: Dict[str, Any],
    db: str,
    fragen: Optional[Callable[..., str]] = None,
    lade: Optional[Callable[[str], Optional[str]]] = None,
    adapter: Any = None,
    max_aenderungen: int = MAX_JE_LAUF,
    trocken: bool = False,
    jetzt: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Arbeitsliste abarbeiten. Rueckgabe: was geschrieben, geprueft, uebersprungen wurde."""
    from .ausfuehrung import (
        BETRIEBSART_AUTOPILOT,
        BETRIEBSART_BEOBACHTER,
        betriebsart_von,
    )

    jetzt = jetzt or _jetzt()
    art = betriebsart_von(cfg)
    bericht: Dict[str, Any] = {
        "projekt": projekt,
        "betriebsart": art,
        "geschrieben": [],
        "pruefung_nein": [],
        "nicht_behebbar": [],
        "uebersprungen": [],
        "trocken": [],
    }
    if art == BETRIEBSART_BEOBACHTER:
        bericht["hinweis"] = "Beobachter – es wird nichts geändert"
        return bericht
    status = (
        (em.STATUS_FREIGEGEBEN, em.STATUS_OFFEN)
        if art == BETRIEBSART_AUTOPILOT
        else (em.STATUS_FREIGEGEBEN,)  # Copilot: nur was ein Mensch freigegeben hat
    )
    kandidaten = [
        e
        for e in em.laden(db, projekt, status=status)
        if e.anwendbar and e.art in em.ANWENDBARE_ARTEN
    ]
    # Freigegebenes zuerst (ein Mensch hat entschieden), dann nach Nutzen
    kandidaten.sort(key=lambda e: (e.status != em.STATUS_FREIGEGEBEN, -e.prioritaet))
    if not kandidaten:
        return bericht

    regeln = (cfg.get("adapter_config") or {}).get("seo_regeln") or {}
    if adapter is None:
        if cfg.get("adapter_type") != "static":
            for e in kandidaten:
                if e.status == em.STATUS_FREIGEGEBEN:
                    em.status_setzen(
                        db,
                        e.id,
                        em.STATUS_NICHT_BEHEBBAR,
                        notiz="freigegeben – kein Dateizugriff auf diese Website, von Hand umsetzen",
                    )
                    bericht["nicht_behebbar"].append((e.id, "kein Dateizugriff"))
            bericht["hinweis"] = "kein Dateizugriff (Adapter nicht statisch)"
            return bericht
        from .adapters.static_files import StaticFilesAdapter

        adapter = StaticFilesAdapter(cfg.get("adapter_config") or {})
    root = getattr(adapter, "root", None)
    lade = lade or (lambda u: em.html_laden(u, root))
    fragen = fragen or _ki_pruefen

    geaendert_diesmal: set = set()
    pruefungen = 0
    for e in kandidaten:
        if len(bericht["geschrieben"]) >= max_aenderungen:
            bericht["uebersprungen"].append((e.id, "Deckel je Lauf erreicht"))
            continue
        a = e.vorschlag["anwendung"]
        datei_seite = a.get("seite") or e.seite
        if datei_seite in geaendert_diesmal or in_sperrfrist(
            db, projekt, datei_seite, jetzt
        ):
            bericht["uebersprungen"].append((e.id, "1 Änderung je Seite und Woche"))
            continue
        if root is not None and hw.datei_fuer_seite(root, datei_seite) is None:
            grund = (
                f"keine Datei für {datei_seite} unter dem Webroot – von Hand umsetzen"
            )
            em.status_setzen(db, e.id, em.STATUS_NICHT_BEHEBBAR, notiz=grund)
            bericht["nicht_behebbar"].append((e.id, grund))
            continue  # vor der KI-Pruefung: spart den Aufruf
        roh = lade(datei_seite)
        struktur = em.seiten_struktur(roh, datei_seite) if roh else None
        grund = schutzgelaender(e, regeln, struktur)
        if grund:
            em.status_setzen(db, e.id, em.STATUS_NICHT_BEHEBBAR, notiz=grund)
            bericht["nicht_behebbar"].append((e.id, grund))
            continue
        if pruefungen >= MAX_PRUEFUNGEN:
            bericht["uebersprungen"].append((e.id, "Prüf-Deckel je Lauf erreicht"))
            continue
        pruefungen += 1
        ok, gruende = zweite_pruefung(e, cfg, struktur, fragen)
        if not ok:
            em.status_setzen(
                db, e.id, em.STATUS_PRUEFUNG_NEIN, notiz="; ".join(gruende)[:500]
            )
            bericht["pruefung_nein"].append((e.id, gruende))
            continue
        if trocken:
            bericht["trocken"].append((e.id, aenderung_als_text(e)))
            continue
        fix = dict(a)
        fix.update(
            {
                "type": a["typ"],
                "issue_title": e.titel,
                "source": "empfehlung",
                "url": a.get("zielseite") or e.seite,
            }
        )
        audit_id = f"empfehlung-{e.id[:8]}"
        ar = adapter.apply_fix(fix, audit_id=audit_id)
        if ar.nicht_behebbar:
            em.status_setzen(
                db, e.id, em.STATUS_NICHT_BEHEBBAR, notiz=ar.nicht_behebbar
            )
            bericht["nicht_behebbar"].append((e.id, ar.nicht_behebbar))
            continue
        if not ar.success:
            em.status_setzen(db, e.id, e.status, notiz=f"Fehler: {ar.error}"[:500])
            bericht["uebersprungen"].append((e.id, f"Fehler: {ar.error}"))
            continue
        if not ar.files_changed:
            em.status_setzen(
                db,
                e.id,
                em.STATUS_UMGESETZT,
                umgesetzt_am=jetzt.isoformat(timespec="seconds"),
                notiz="stand bereits so auf der Seite",
            )
            continue
        cid = _buchen(db, projekt, e, ar, audit_id)
        em.status_setzen(
            db,
            e.id,
            em.STATUS_UMGESETZT,
            umgesetzt_am=jetzt.isoformat(timespec="seconds"),
            geaenderte_seite=datei_seite,
            change_id=cid,
            git_commit=ar.commit_hash,
            notiz="zweite KI-Prüfung: ja",
        )
        geaendert_diesmal.add(datei_seite)
        bericht["geschrieben"].append(
            {"id": e.id, "art": e.art, "seite": datei_seite, "commit": ar.commit_hash}
        )
        logger.info(
            f"[Empfehlungen] {projekt}: {e.art} auf {datei_seite} umgesetzt ({ar.commit_hash})"
        )
    return bericht


def als_text(b: Dict[str, Any]) -> str:
    z = [
        f"{b['projekt']} ({b['betriebsart']}): {len(b['geschrieben'])} umgesetzt, "
        f"{len(b['pruefung_nein'])} von der Prüfung abgelehnt, "
        f"{len(b['nicht_behebbar'])} von Hand, {len(b['uebersprungen'])} verschoben"
    ]
    if b.get("hinweis"):
        z.append(f"   {b['hinweis']}")
    for g in b["geschrieben"]:
        z.append(f"   ✓ {g['art']} auf {g['seite']} (Commit {g['commit']})")
    for eid, gruende in b["pruefung_nein"]:
        z.append(f"   ✗ {eid[:8]}: {'; '.join(gruende)[:160]}")
    for eid, text in b["trocken"]:
        z.append(f"   (trocken) {eid[:8]}: {text[:160]}")
    return "\n".join(z)
