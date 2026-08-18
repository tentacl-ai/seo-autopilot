"""
Bild- und Ladezeit-Prüfung ("Image Audit").

Bilder sind auf fast jeder Website der mit Abstand grösste Teil der
übertragenen Datenmenge — und damit der Hebel Nr. 1 für die Ladezeit. Google
misst das seit 2021 direkt (Core Web Vitals) und lässt es in das Ranking
einfliessen. Dieses Modul prüft, was sich **ohne** externe API am HTML und per
HEAD-Abruf feststellen lässt. Das ist wichtig, weil die PageSpeed-Messung von
einem Google-Kontingent abhängt und ausfallen kann; die Prüfungen hier laufen
immer.

Warum jede einzelne Prüfung zählt:

* **Fehlendes ``alt``** — Suchmaschinen und Screenreader haben sonst keinerlei
  Anhalt, was das Bild zeigt. Wichtig: ein **leeres** ``alt=""`` ist kein
  Mangel, sondern die *korrekte* Auszeichnung für rein dekorative Bilder
  (WCAG 1.1.1). Nur ein komplett fehlendes Attribut ist ein Defekt. Genau
  dieser Unterschied wurde am 2026-08-17 bereits einmal falsch gemeldet.
* **Fehlende ``width``/``height``** — ohne die kann der Browser vor dem Laden
  keinen Platz reservieren; der Inhalt springt beim Nachladen. Das ist die mit
  Abstand häufigste Ursache für einen schlechten CLS-Wert, und CLS ist ein
  offizieller Rankingfaktor.
* **``loading="lazy"`` am ersten grossen Bild** — das Bild oben im sichtbaren
  Bereich ist fast immer das LCP-Element. Wird es verzögert geladen, startet
  der Browser den Abruf erst spät und der LCP-Wert bricht ein. Umgekehrt gilt:
  Bilder **unterhalb** des sichtbaren Bereichs *sollten* verzögert laden, sonst
  konkurrieren sie mit dem, was der Besucher sofort sieht.
* **Veraltete Formate** — WebP/AVIF liefern bei gleicher Bildqualität
  typischerweise 25–50 % weniger Daten als JPEG/PNG. Wer ein modernes Format
  bereits per ``<picture><source>`` ausliefert, wird hier nicht gemeldet.
* **Dateigrösse** — wird per echtem HEAD-Abruf gemessen, nicht geschätzt. Über
  200 KB ist ein Hinweis, über 500 KB ein echter Befund, über 1 MB auf Mobil
  ein spürbarer Schaden.
* **Fehlendes ``srcset``/``sizes``** — ohne die lädt ein Handy dieselbe
  Desktop-Datei wie ein 27-Zoll-Monitor.
* **``og:image``** — fehlt es oder liefert es keine 200, sieht jeder Beitrag in
  sozialen Netzwerken und in Messengern kaputt aus.

Zwei bewusste Nicht-Prüfungen, damit keine Fehlalarme entstehen:

* Ein fehlendes ``title``-Attribut am Bild ist **kein** Mangel. ``title`` wird
  von Screenreadern uneinheitlich behandelt und auf Touchgeräten gar nicht
  angezeigt; es zu fordern würde nur Rauschen erzeugen. Wir erfassen es und
  nutzen es als *entlastendes* Signal.
* Eine ``<figcaption>`` ersetzt für den Leser die Bildbeschreibung. Ein Bild in
  einer ``<figure>`` mit Bildunterschrift wird deshalb nie wegen des
  alt-Textes gemeldet. Gemeldet wird der umgekehrte Fall: eine ``<figure>``
  ganz **ohne** ``<figcaption>`` ist unvollständiges Markup.

Grundhaltung wie im ganzen Projekt: **Im Zweifel nichts melden.** Schlägt ein
HEAD-Abruf fehl, bleibt die Grösse unbekannt und es entsteht kein Befund —
ein Netzwerkproblem ist kein Website-Mangel.

    from seo_autopilot.analyzers.image_audit import ImageAuditor

    auditor = ImageAuditor()
    befunde = await auditor.audit_pages(
        [{"url": "https://example.com/", "html": html, "og_tags": {...}}]
    )
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Schwellenwerte — zentral, damit sie ohne Codesuche nachjustierbar sind
# --------------------------------------------------------------------------

# Dateigrösse: Hinweis / echter Befund / spürbarer Schaden auf Mobil
GROESSE_HINWEIS = 200 * 1024
GROESSE_BEFUND = 500 * 1024
GROESSE_SCHWER = 1024 * 1024

# Gesamtgewicht aller Bilder einer Seite, ab dem die Ladezeit leidet
SEITENGEWICHT_BEFUND = 2 * 1024 * 1024

# Ab dieser Position im Dokument liegt ein Bild erfahrungsgemäss unterhalb des
# sichtbaren Bereichs. Bewusst grosszügig: lieber ein Befund weniger.
ERSTER_BILDSCHIRM = 3

# Ein Bild gilt als "gross" (und damit srcset-bedürftig) ab dieser Breite bzw.
# ab dieser Dateigrösse.
GROSSES_BILD_BREITE = 800
GROSSES_BILD_BYTES = 150 * 1024

# Unter dieser deklarierten Kantenlänge ist ein Bild ein Logo/Icon und kommt
# als LCP-Element nicht in Frage.
KLEINBILD_KANTE = 200

# HEAD-Abrufe: begrenzt, parallel, kurzer Timeout — die Prüfung darf einen
# Audit nie ausbremsen.
MAX_HEAD_PRUEFUNGEN = 20
HEAD_TIMEOUT = 6.0
HEAD_PARALLEL = 8

# og:image sollte mindestens 1200x630 haben (Facebook/LinkedIn/X-Empfehlung).
OG_MIN_BREITE = 1200
OG_MIN_HOEHE = 630

MODERNE_FORMATE = {"webp", "avif", "jxl"}
ALTFORMATE = {"jpg", "jpeg", "png", "gif", "bmp", "tiff"}
# SVG ist bereits optimal komprimierbar und hat keine "Grösse" im Rasterinn.
FORMATE_OHNE_PRUEFUNG = {"svg", "ico"}

# Dateinamen ohne jede Aussage. Vor dem Abgleich werden von Bildgrössen-Suffixen
# wie "-1024x768" oder WordPress' "-scaled" befreit.
_GENERISCHE_NAMEN = re.compile(
    r"^(img|image|bild|foto|photo|pic|picture|dsc|dscn|dscf|pxl|gopr|"
    r"screenshot|bildschirmfoto|unnamed|untitled|download|kopie|copy|"
    r"asset|file|datei|upload|temp|tmp)[-_ ]?\d*$",
    re.IGNORECASE,
)
_NUR_ZIFFERN = re.compile(r"^\d{3,}$")
_NUR_HEX = re.compile(r"^[0-9a-f]{12,}$", re.IGNORECASE)
_GROESSEN_SUFFIX = re.compile(r"([-_]\d{2,4}x\d{2,4})|([-_]scaled)$", re.IGNORECASE)

_ASPECT_RATIO = re.compile(r"aspect-ratio\s*:", re.IGNORECASE)
_ZAHL = re.compile(r"\d+")

# Query-Parameter, in denen Bilddienste (Next.js /_next/image, imgproxy,
# thumbor, WordPress-Plugins) die eigentliche Bilddatei mitgeben.
_PROXY_PARAMETER = ("url", "src", "image", "img", "file", "path", "source")

# So fragt ein moderner Browser Bilder an. Der Header ist NICHT kosmetisch:
# Bilddienste liefern abhängig davon WebP statt PNG aus. Ohne ihn misst der
# Autopilot eine Datei, die kein echter Besucher je bekommt — bei
# joseph-hehenwarter.de waren das 3062 KB (PNG) statt 912 KB (WebP), und
# jedes Bild wäre fälschlich als "veraltetes Format" gemeldet worden.
BROWSER_ACCEPT = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"


# --------------------------------------------------------------------------
# Datenhaltung
# --------------------------------------------------------------------------


@dataclass
class BildInfo:
    """Alles, was sich über ein einzelnes ``<img>`` sagen lässt."""

    src: str = ""
    position: int = 0  # 0 = erstes Bild im Dokument
    alt: Optional[str] = None  # None = Attribut fehlt, "" = bewusst leer
    title: Optional[str] = None
    breite: Optional[int] = None
    hoehe: Optional[int] = None
    hat_masse: bool = False  # width UND height bzw. aspect-ratio gesetzt
    loading: str = ""
    fetchpriority: str = ""
    srcset: str = ""
    sizes: str = ""
    dekorativ: bool = False
    in_figure: bool = False
    hat_figcaption: bool = False
    moderne_quelle: bool = False  # <picture> liefert WebP/AVIF-Alternative
    bytes: Optional[int] = None  # aus HEAD; None = unbekannt
    content_type: str = ""

    @property
    def quell_url(self) -> str:
        """Die eigentliche Bilddatei hinter einem Optimierungsdienst.

        Next.js (``/_next/image?url=...``), imgproxy, thumbor und diverse
        WordPress-Plugins liefern jedes Bild über denselben Zwischenpfad aus.
        Der Dateiname im Pfad heisst dann für ALLE Bilder gleich ("image") —
        die Namensprüfung würde eine komplette Website als "generisch"
        melden. Der echte Name steht im Query-Parameter.
        """
        zerlegt = urlparse(self.src)
        if not zerlegt.query:
            return self.src
        try:
            parameter = parse_qs(zerlegt.query)
        except Exception:  # pragma: no cover - defensiv
            return self.src
        for name in _PROXY_PARAMETER:
            werte = parameter.get(name) or []
            if werte and werte[0].strip():
                kandidat = unquote(werte[0].strip())
                # Nur übernehmen, wenn es tatsächlich nach einem Pfad aussieht.
                if "/" in kandidat or "." in kandidat:
                    return kandidat
        return self.src

    @property
    def pfad_endung(self) -> str:
        """Dateiendung aus der Adresse, in Kleinbuchstaben, ohne Query."""
        pfad = urlparse(self.quell_url).path
        _, _, endung = pfad.rpartition(".")
        endung = endung.lower().strip()
        if not endung or "/" in endung or len(endung) > 5:
            return ""
        return "jpg" if endung in ("jpe", "jpeg") else endung

    @property
    def endung(self) -> str:
        """Das Format, das ein Browser tatsächlich geliefert bekommt.

        Der Content-Type aus dem HEAD-Abruf hat Vorrang: Ein Bilddienst kann
        ein PNG als WebP ausspielen — dann ist "png" im Pfad die falsche
        Antwort. Ohne Abruf zählt die Endung im Pfad.
        """
        if "/" in self.content_type:
            aus_header = (
                self.content_type.split("/", 1)[1].split(";")[0].strip().lower()
            )
            if aus_header:
                return "jpg" if aus_header == "jpeg" else aus_header
        return self.pfad_endung

    @property
    def formate(self) -> set:
        """Alle Formathinweise zusammen: Adresse UND ausgelieferter Typ."""
        return {f for f in (self.pfad_endung, self.endung) if f}

    @property
    def ist_modernes_format(self) -> bool:
        return bool(self.formate & MODERNE_FORMATE)

    @property
    def ist_altformat(self) -> bool:
        """Nur wenn KEIN Hinweis auf ein modernes Format vorliegt.

        Widersprechen sich Adresse und Content-Type (``bild.webp`` mit
        ``Content-Type: image/jpeg`` — eine verbreitete Server-Fehlkonfiguration,
        die Browser einfach wegstecken), entscheiden wir zugunsten der Website.
        Ein Formatbefund ist eine Optimierungsempfehlung; die darf nicht auf
        einem widersprüchlichen Signal beruhen.
        """
        return not self.ist_modernes_format and bool(self.formate & ALTFORMATE)

    @property
    def dateiname(self) -> str:
        return urlparse(self.quell_url).path.rsplit("/", 1)[-1]

    @property
    def ist_gross(self) -> bool:
        if self.breite and self.breite >= GROSSES_BILD_BREITE:
            return True
        return self.bytes is not None and self.bytes >= GROSSES_BILD_BYTES

    @property
    def messbar(self) -> bool:
        """Lässt sich für dieses Bild überhaupt sinnvoll eine Grösse messen?"""
        if not self.src or self.src.startswith("data:"):
            return False
        return not (self.formate & FORMATE_OHNE_PRUEFUNG)


@dataclass
class SeitenBildBefund:
    """Ergebnis einer Seite — Befunde plus die Zahlen dahinter."""

    url: str
    bilder: List[BildInfo] = field(default_factory=list)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    gemessene_bytes: int = 0
    gemessene_bilder: int = 0


# --------------------------------------------------------------------------
# HTML auslesen
# --------------------------------------------------------------------------


def _als_zahl(wert: Any) -> Optional[int]:
    """``width="800"`` und ``width="800px"`` ergeben 800; ``100%`` ergibt None."""
    if wert is None:
        return None
    text = str(wert).strip()
    if not text or "%" in text:
        return None
    treffer = _ZAHL.search(text)
    if not treffer:
        return None
    try:
        return int(treffer.group(0))
    except ValueError:
        return None


def _ist_dekorativ(img) -> bool:
    """Ist das Bild ausdrücklich als schmückendes Beiwerk markiert?

    Gleiche Regel wie im Crawler: ``role="presentation"``/``role="none"`` und
    ``aria-hidden="true"`` weisen Hilfstechnik an, das Bild zu überspringen.
    Ein alt-Text ist dort nicht nur unnötig, sondern falsch.
    """
    rolle = (img.get("role") or "").strip().lower()
    if rolle in ("presentation", "none"):
        return True
    return (img.get("aria-hidden") or "").strip().lower() == "true"


def _moderne_quelle_vorhanden(img) -> bool:
    """Liefert ein umgebendes ``<picture>`` bereits WebP/AVIF?

    Wenn ja, ist das ``<img src="...jpg">`` nur noch der Rückfall für alte
    Browser — dann wäre eine Meldung "veraltetes Format" schlicht falsch.
    """
    picture = img.find_parent("picture")
    if picture is None:
        return False
    for source in picture.find_all("source"):
        typ = (source.get("type") or "").lower()
        if any(f"image/{f}" == typ for f in MODERNE_FORMATE):
            return True
        srcset = (source.get("srcset") or "").lower()
        if any(f".{f}" in srcset for f in MODERNE_FORMATE):
            return True
    return False


def extract_images(html: str, base_url: str = "") -> List[BildInfo]:
    """Liest alle ``<img>`` einer Seite in strukturierte Form aus.

    Relative Adressen werden gegen ``base_url`` aufgelöst, damit später ein
    HEAD-Abruf möglich ist. Reihenfolge = Dokumentreihenfolge; die Position ist
    der einzige Anhalt dafür, was oberhalb des sichtbaren Bereichs liegt.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    bilder: List[BildInfo] = []

    for position, img in enumerate(soup.find_all("img")):
        roh_src = (img.get("src") or img.get("data-src") or "").strip()
        src = urljoin(base_url, roh_src) if base_url and roh_src else roh_src

        stil = (img.get("style") or "").strip()
        breite = _als_zahl(img.get("width"))
        hoehe = _als_zahl(img.get("height"))
        hat_masse = bool(
            (breite is not None and hoehe is not None) or _ASPECT_RATIO.search(stil)
        )

        figure = img.find_parent("figure")
        bilder.append(
            BildInfo(
                src=src,
                position=position,
                alt=img.get("alt"),
                title=img.get("title"),
                breite=breite,
                hoehe=hoehe,
                hat_masse=hat_masse,
                loading=(img.get("loading") or "").strip().lower(),
                fetchpriority=(img.get("fetchpriority") or "").strip().lower(),
                srcset=(img.get("srcset") or "").strip(),
                sizes=(img.get("sizes") or "").strip(),
                dekorativ=_ist_dekorativ(img),
                in_figure=figure is not None,
                hat_figcaption=(
                    figure.find("figcaption") is not None
                    if figure is not None
                    else False
                ),
                moderne_quelle=_moderne_quelle_vorhanden(img),
            )
        )
    return bilder


def ist_generischer_dateiname(dateiname: str) -> bool:
    """``IMG_1234.jpg`` sagt nichts aus, ``roter-traktor.jpg`` schon.

    Der Dateiname ist eines der wenigen Textsignale, die Google der Bildsuche
    entnehmen kann. Grössen-Suffixe wie ``-1024x768`` oder ``-scaled`` (setzt
    WordPress automatisch) werden vorher entfernt — sonst würde ein guter Name
    fälschlich als generisch gelten.
    """
    if not dateiname:
        return False
    stamm = dateiname.rsplit(".", 1)[0]
    vorher = None
    while stamm != vorher:  # mehrfache Suffixe abtragen
        vorher = stamm
        stamm = _GROESSEN_SUFFIX.sub("", stamm)
    stamm = stamm.strip()
    if not stamm:
        return False
    return bool(
        _GENERISCHE_NAMEN.match(stamm)
        or _NUR_ZIFFERN.match(stamm)
        or _NUR_HEX.match(stamm)
    )


# --------------------------------------------------------------------------
# Prüfer
# --------------------------------------------------------------------------


class ImageAuditor:
    """Prüft Bild-Metadaten, Layout-Stabilität, Ladeverhalten und Grösse.

    ``doppelte_befunde_vermeiden`` schaltet die beiden Prüfungen ab, die der
    AnalyzerAgent bereits unter etablierten Befundtypen meldet
    (``images_without_alt`` und ``missing_og_image``). Ohne diesen Schalter
    stünde derselbe Mangel zweimal im Bericht — mit unterschiedlicher
    Gewichtung, weil die alten Typen in Scoring, Gegenprobe und Wochenbericht
    fest verdrahtet sind.
    """

    def __init__(
        self,
        doppelte_befunde_vermeiden: bool = False,
        max_head_pruefungen: int = MAX_HEAD_PRUEFUNGEN,
    ):
        self.doppelte_befunde_vermeiden = doppelte_befunde_vermeiden
        self.max_head_pruefungen = max_head_pruefungen

    # -- öffentliche Schnittstelle -----------------------------------------

    async def audit_pages(
        self,
        seiten: List[Dict[str, Any]],
        client: Optional[httpx.AsyncClient] = None,
    ) -> List[Dict[str, Any]]:
        """Prüft mehrere Seiten und liefert die gesammelten Befunde.

        ``seiten`` erwartet je Eintrag ``{"url": ..., "html": ..., "og_tags": {...}}``.
        Seiten ohne HTML werden übersprungen — daraus lässt sich nichts ableiten.
        """
        ergebnisse = await self.audit_pages_detailliert(seiten, client=client)
        return [issue for e in ergebnisse for issue in e.issues]

    async def audit_pages_detailliert(
        self,
        seiten: List[Dict[str, Any]],
        client: Optional[httpx.AsyncClient] = None,
    ) -> List[SeitenBildBefund]:
        """Wie ``audit_pages``, liefert aber auch die Messwerte je Seite."""
        eigener_client = client is None
        if eigener_client:
            client = httpx.AsyncClient(
                timeout=HEAD_TIMEOUT,
                follow_redirects=True,
                headers={
                    "User-Agent": "SEOAutopilotBot/image-audit",
                    "Accept": BROWSER_ACCEPT,
                },
            )
        try:
            ergebnisse = []
            for seite in seiten:
                try:
                    ergebnisse.append(await self._audit_seite(seite, client))
                except Exception as exc:  # eine Seite darf nie den Lauf kippen
                    logger.warning(
                        f"[image-audit] Seite {seite.get('url')} übersprungen: {exc}"
                    )
            return ergebnisse
        finally:
            if eigener_client:
                await client.aclose()

    # -- Ablauf je Seite ----------------------------------------------------

    async def _audit_seite(
        self, seite: Dict[str, Any], client: httpx.AsyncClient
    ) -> SeitenBildBefund:
        url = seite.get("url") or ""
        html = seite.get("html") or ""
        og_tags = seite.get("og_tags") or {}

        bilder = extract_images(html, base_url=seite.get("final_url") or url)
        ergebnis = SeitenBildBefund(url=url, bilder=bilder)

        # Dateigrössen per HEAD — nur für messbare Bilder, gedeckelt.
        messbare = [b for b in bilder if b.messbar][: self.max_head_pruefungen]
        await self._groessen_ermitteln(messbare, client)
        gemessen = [b for b in bilder if b.bytes is not None]
        ergebnis.gemessene_bilder = len(gemessen)
        ergebnis.gemessene_bytes = sum(b.bytes or 0 for b in gemessen)

        ergebnis.issues.extend(self._pruefe_metadaten(url, bilder))
        ergebnis.issues.extend(self._pruefe_layoutstabilitaet(url, bilder))
        ergebnis.issues.extend(self._pruefe_ladeverhalten(url, bilder))
        ergebnis.issues.extend(self._pruefe_format(url, bilder))
        ergebnis.issues.extend(self._pruefe_groesse(url, bilder, ergebnis))
        ergebnis.issues.extend(self._pruefe_responsive(url, bilder))
        ergebnis.issues.extend(await self._pruefe_og_image(url, og_tags, client))
        return ergebnis

    # -- 1. Bild-Metadaten --------------------------------------------------

    def _pruefe_metadaten(
        self, url: str, bilder: List[BildInfo]
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []

        if not self.doppelte_befunde_vermeiden:
            # NUR ein fehlendes Attribut ist ein Mangel. alt="" ist bei
            # dekorativen Bildern die richtige Auszeichnung, und eine
            # Bildunterschrift trägt die Bedeutung ebenfalls.
            ohne_alt = [
                b
                for b in bilder
                if b.alt is None and not b.dekorativ and not b.hat_figcaption
            ]
            if ohne_alt:
                issues.append(
                    _issue(
                        "accessibility",
                        "image_missing_alt",
                        "medium" if len(ohne_alt) > 3 else "low",
                        url,
                        f"{len(ohne_alt)} Bilder ohne alt-Attribut",
                        f"Von {len(bilder)} Bildern fehlt bei {len(ohne_alt)} das "
                        "alt-Attribut vollständig. Beispiele: "
                        + ", ".join(b.dateiname or b.src for b in ohne_alt[:3])
                        + '. (Ein leeres alt="" ist bei dekorativen Bildern korrekt '
                        "und wird hier nicht gezählt.)",
                        "Jedem inhaltstragenden Bild ein beschreibendes alt geben; "
                        'rein dekorative Bilder mit alt="" auszeichnen.',
                    )
                )

        generisch = [b for b in bilder if ist_generischer_dateiname(b.dateiname)]
        if generisch:
            issues.append(
                _issue(
                    "images",
                    "image_generic_filename",
                    "low",
                    url,
                    f"{len(generisch)} Bilder mit nichtssagendem Dateinamen",
                    "Dateinamen wie "
                    + ", ".join(b.dateiname for b in generisch[:3])
                    + " geben der Bildersuche keinerlei Anhalt zum Inhalt.",
                    "Bilder vor dem Hochladen sprechend benennen "
                    "(z. B. roter-traktor-baujahr-1968.jpg).",
                )
            )

        ohne_bildunterschrift = [
            b for b in bilder if b.in_figure and not b.hat_figcaption
        ]
        if ohne_bildunterschrift:
            issues.append(
                _issue(
                    "images",
                    "image_figure_without_caption",
                    "low",
                    url,
                    f"{len(ohne_bildunterschrift)} <figure> ohne <figcaption>",
                    "Eine <figure> ist laut HTML-Standard für Inhalte mit "
                    "Bildunterschrift gedacht. Ohne <figcaption> geht die "
                    "Zusatzinformation verloren, die Google gern als Kontext liest.",
                    "Entweder eine <figcaption> ergänzen oder das <figure> durch "
                    "ein einfaches <img> ersetzen.",
                )
            )
        return issues

    # -- 2. Layout-Stabilität (CLS) ----------------------------------------

    def _pruefe_layoutstabilitaet(
        self, url: str, bilder: List[BildInfo]
    ) -> List[Dict[str, Any]]:
        ohne_masse = [b for b in bilder if b.src and not b.hat_masse]
        if not ohne_masse:
            return []
        # Zurückhaltend: erst wenn es mehrere Bilder betrifft, springt das
        # Layout sichtbar. Ein einzelnes Icon ohne Masse ist kein Drama.
        schwere = "medium" if len(ohne_masse) >= 3 else "low"
        return [
            _issue(
                "performance",
                "image_missing_dimensions",
                schwere,
                url,
                f"{len(ohne_masse)} Bilder ohne width/height",
                f"Von {len(bilder)} Bildern haben {len(ohne_masse)} weder "
                "width- und height-Attribut noch ein aspect-ratio im style. "
                "Der Browser kann keinen Platz reservieren — der Inhalt springt "
                "beim Nachladen (CLS). Betroffen u. a.: "
                + ", ".join(b.dateiname or b.src for b in ohne_masse[:3]),
                "width und height in Originalpixeln am <img> setzen (CSS darf "
                "weiterhin skalieren) oder aspect-ratio im Stylesheet hinterlegen.",
                "CLS ist ein offizieller Google-Rankingfaktor.",
            )
        ]

    # -- 3. Ladeverhalten (LCP) --------------------------------------------

    def _lcp_kandidat(self, bilder: List[BildInfo]) -> Optional[BildInfo]:
        """Das erste Bild, das als LCP-Element in Frage kommt.

        Übersprungen werden dekorative Bilder und alles, was sich per
        deklarierter Kantenlänge als Logo/Icon zu erkennen gibt.

        Wichtige Einschränkung (2026-08-18): Ein grosses Bild ist nur dann ein
        LCP-Kandidat, wenn es auch weit genug oben steht. Auf
        joseph-hehenwarter.de/finanzierung/factoring lag das erste grosse Bild
        an Bildposition 4, weit unterhalb mehrerer Textabschnitte — gemeldet
        wurde trotzdem "LCP verzögert geladen", während Google dieselbe Seite
        mit 98/100 und 2,4 s LCP bewertete. Ein Bild, das der Besucher erst
        nach dem Scrollen sieht, SOLL verzögert laden; dort ist "lazy" richtig
        und kein Mangel.
        """
        for b in bilder:
            if not b.src or b.dekorativ:
                continue
            klein = (b.breite is not None and b.breite < KLEINBILD_KANTE) and (
                b.hoehe is not None and b.hoehe < KLEINBILD_KANTE
            )
            if klein:
                continue
            # Steht das Bild so weit unten, dass es den ersten Bildschirm gar
            # nicht mehr erreicht, ist es kein LCP-Element.
            if b.position >= ERSTER_BILDSCHIRM:
                return None
            return b
        return None

    def _pruefe_ladeverhalten(
        self, url: str, bilder: List[BildInfo]
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []

        lcp = self._lcp_kandidat(bilder)
        if lcp is not None and lcp.loading == "lazy":
            issues.append(
                _issue(
                    "performance",
                    "image_lcp_lazy_loaded",
                    "high",
                    url,
                    "Erstes grosses Bild wird verzögert geladen (lazy)",
                    f"{lcp.dateiname or lcp.src} steht an Position "
                    f"{lcp.position + 1} und trägt damit sehr wahrscheinlich den "
                    'LCP. Mit loading="lazy" beginnt der Browser den Abruf erst '
                    "nach dem Layout — der LCP-Wert verschlechtert sich messbar.",
                    'loading="lazy" am ersten sichtbaren Bild entfernen bzw. auf '
                    '"eager" setzen und fetchpriority="high" ergänzen.',
                    "LCP ist einer der drei Core Web Vitals.",
                )
            )
        elif lcp is not None and lcp.fetchpriority != "high":
            issues.append(
                _issue(
                    "performance",
                    "image_lcp_no_priority",
                    "low",
                    url,
                    'Erstes grosses Bild ohne fetchpriority="high"',
                    f"{lcp.dateiname or lcp.src} lädt korrekt sofort, konkurriert "
                    "aber mit allen anderen Ressourcen um Bandbreite.",
                    'fetchpriority="high" am LCP-Bild setzen (und es ggf. per '
                    "<link rel=preload> vorladen).",
                )
            )

        # Bilder weiter unten SOLLEN verzögert laden.
        ohne_lazy = [
            b
            for b in bilder
            if b.src and b.position >= ERSTER_BILDSCHIRM and b.loading not in ("lazy",)
        ]
        if len(ohne_lazy) >= 3:
            issues.append(
                _issue(
                    "performance",
                    "image_no_lazy_loading",
                    "medium" if len(ohne_lazy) >= 8 else "low",
                    url,
                    f"{len(ohne_lazy)} Bilder unterhalb des sichtbaren Bereichs "
                    "ohne lazy loading",
                    "Diese Bilder werden sofort mitgeladen, obwohl der Besucher "
                    "sie erst nach dem Scrollen sieht. Sie nehmen dem sichtbaren "
                    "Bereich Bandbreite weg.",
                    'loading="lazy" an allen Bildern ab dem zweiten Bildschirm '
                    "setzen (nicht am ersten!).",
                )
            )
        return issues

    # -- 4. Format ----------------------------------------------------------

    def _pruefe_format(self, url: str, bilder: List[BildInfo]) -> List[Dict[str, Any]]:
        altformat = [
            b for b in bilder if b.ist_altformat and not b.moderne_quelle and b.src
        ]
        modern = [b for b in bilder if b.ist_modernes_format]
        # Wer bereits überwiegend WebP/AVIF ausliefert, bekommt keine Meldung —
        # ein einzelnes altes Logo ist kein Befund.
        if len(altformat) < 3 or modern:
            return []
        eingespart = sum(b.bytes or 0 for b in altformat)
        detail = (
            f"{len(altformat)} von {len(bilder)} Bildern liegen als "
            f"{', '.join(sorted({f for b in altformat for f in b.formate & ALTFORMATE}))}"
            " vor."
        )
        if eingespart:
            detail += (
                f" Gemessen sind das {eingespart / 1024:.0f} KB; WebP/AVIF spart "
                f"davon erfahrungsgemäss 25–50 % "
                f"(rund {eingespart * 0.35 / 1024:.0f} KB)."
            )
        return [
            _issue(
                "performance",
                "image_legacy_format",
                "low",
                url,
                f"{len(altformat)} Bilder in veraltetem Format",
                detail,
                "Bilder zusätzlich als WebP/AVIF ausliefern und per "
                '<picture><source type="image/webp"> einbinden — der alte '
                "JPEG/PNG-Pfad bleibt als Rückfall erhalten.",
            )
        ]

    # -- 5. Dateigrösse -----------------------------------------------------

    async def _groessen_ermitteln(
        self, bilder: List[BildInfo], client: httpx.AsyncClient
    ) -> None:
        """Echte Dateigrössen per HEAD holen — parallel und mit kurzer Leine.

        Fehler bleiben folgenlos: ``bytes`` bleibt ``None`` und erzeugt später
        keinen Befund. Ein nicht erreichbarer Bildserver ist ein
        Verfügbarkeitsproblem, kein Grössenproblem — und wir raten nicht.
        """
        if not bilder:
            return
        semaphore = asyncio.Semaphore(HEAD_PARALLEL)

        async def _eins(bild: BildInfo) -> None:
            async with semaphore:
                try:
                    # Der Accept-Header wird bewusst je Abruf mitgegeben: Der
                    # Aufrufer darf einen eigenen Client übergeben, und ohne
                    # diesen Header messen wir das falsche Format.
                    resp = await client.head(
                        bild.src,
                        timeout=HEAD_TIMEOUT,
                        headers={"Accept": BROWSER_ACCEPT},
                    )
                    if resp.status_code >= 400:
                        return
                    bild.content_type = resp.headers.get("content-type", "")
                    laenge = resp.headers.get("content-length")
                    if laenge and str(laenge).strip().isdigit():
                        bild.bytes = int(laenge)
                except Exception as exc:
                    logger.debug(f"[image-audit] HEAD fehlgeschlagen {bild.src}: {exc}")

        await asyncio.gather(*[_eins(b) for b in bilder])

    def _pruefe_groesse(
        self, url: str, bilder: List[BildInfo], ergebnis: SeitenBildBefund
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        zu_gross = [
            b for b in bilder if b.bytes is not None and b.bytes > GROESSE_HINWEIS
        ]
        if zu_gross:
            groesstes = max(zu_gross, key=lambda b: b.bytes or 0)
            schwerste = groesstes.bytes or 0
            if schwerste > GROESSE_SCHWER:
                schwere = "high"
            elif schwerste > GROESSE_BEFUND:
                schwere = "medium"
            else:
                schwere = "low"
            liste = ", ".join(
                f"{b.dateiname or b.src} ({(b.bytes or 0) / 1024:.0f} KB)"
                for b in sorted(zu_gross, key=lambda b: -(b.bytes or 0))[:3]
            )
            issues.append(
                _issue(
                    "performance",
                    "image_oversized",
                    schwere,
                    url,
                    f"{len(zu_gross)} zu grosse Bilddateien "
                    f"(grösstes: {schwerste / 1024:.0f} KB)",
                    f"Gemessen per HEAD-Abruf. Ab {GROESSE_HINWEIS // 1024} KB ist "
                    f"ein Bild auffällig, ab {GROESSE_BEFUND // 1024} KB bremst es "
                    f"die Seite spürbar. Grösste Dateien: {liste}",
                    "Bilder auf die tatsächlich angezeigte Grösse herunterrechnen, "
                    "als WebP/AVIF speichern und mit Qualität ~80 komprimieren.",
                )
            )

        if ergebnis.gemessene_bytes > SEITENGEWICHT_BEFUND:
            issues.append(
                _issue(
                    "performance",
                    "image_page_weight",
                    "medium",
                    url,
                    f"Bilder der Seite wiegen zusammen "
                    f"{ergebnis.gemessene_bytes / 1024 / 1024:.1f} MB",
                    f"{ergebnis.gemessene_bilder} gemessene Bilder ergeben "
                    f"{ergebnis.gemessene_bytes / 1024:.0f} KB. Über Mobilfunk "
                    "sind das mehrere Sekunden allein für die Bilder.",
                    "Grösste Bilder zuerst optimieren, Rest per lazy loading aus "
                    "dem ersten Bildschirm nehmen.",
                )
            )
        return issues

    # -- 6. Responsive ------------------------------------------------------

    def _pruefe_responsive(
        self, url: str, bilder: List[BildInfo]
    ) -> List[Dict[str, Any]]:
        ohne_srcset = [b for b in bilder if b.ist_gross and not b.srcset]
        if not ohne_srcset:
            return []
        return [
            _issue(
                "performance",
                "image_missing_srcset",
                "low",
                url,
                f"{len(ohne_srcset)} grosse Bilder ohne srcset/sizes",
                "Diese Bilder werden auf dem Handy in voller Desktop-Auflösung "
                "geladen: " + ", ".join(b.dateiname or b.src for b in ohne_srcset[:3]),
                "srcset mit mehreren Breiten und ein passendes sizes-Attribut "
                "angeben, damit mobile Geräte die kleine Fassung laden.",
            )
        ]

    # -- 7. Soziale Vorschau ------------------------------------------------

    async def _pruefe_og_image(
        self, url: str, og_tags: Dict[str, str], client: httpx.AsyncClient
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        og_image = (og_tags or {}).get("og:image", "").strip()

        if not og_image:
            if not self.doppelte_befunde_vermeiden:
                issues.append(
                    _issue(
                        "social",
                        "og_image_missing",
                        "medium",
                        url,
                        "Kein og:image gesetzt",
                        "Beim Teilen in sozialen Netzwerken und Messengern "
                        "erscheint kein Vorschaubild — geteilte Links wirken "
                        "dadurch deutlich unattraktiver.",
                        '<meta property="og:image" content="https://.../vorschau.jpg"> '
                        "mit mindestens 1200x630 Pixeln ergänzen.",
                    )
                )
            return issues

        ziel = urljoin(url, og_image)
        erreichbar: Optional[bool] = None
        try:
            resp = await client.head(
                ziel, timeout=HEAD_TIMEOUT, headers={"Accept": BROWSER_ACCEPT}
            )
            erreichbar = resp.status_code < 400
        except Exception as exc:
            # Im Zweifel nichts melden: ein Netzwerkfehler auf unserer Seite
            # ist kein Mangel der Website.
            logger.debug(f"[image-audit] og:image HEAD fehlgeschlagen {ziel}: {exc}")

        if erreichbar is False:
            issues.append(
                _issue(
                    "social",
                    "og_image_unreachable",
                    "medium",
                    url,
                    "og:image ist nicht abrufbar",
                    f"{ziel} liefert keinen Erfolgsstatus. Jede Vorschau in "
                    "Facebook, LinkedIn, X, WhatsApp und Slack bleibt leer.",
                    "Adresse korrigieren (absolute https-URL) oder Datei "
                    "bereitstellen.",
                )
            )

        breite = _als_zahl((og_tags or {}).get("og:image:width"))
        hoehe = _als_zahl((og_tags or {}).get("og:image:height"))
        if (breite is not None and breite < OG_MIN_BREITE) or (
            hoehe is not None and hoehe < OG_MIN_HOEHE
        ):
            issues.append(
                _issue(
                    "social",
                    "og_image_too_small",
                    "low",
                    url,
                    f"og:image zu klein ({breite or '?'}x{hoehe or '?'})",
                    f"Empfohlen sind mindestens {OG_MIN_BREITE}x{OG_MIN_HOEHE} "
                    "Pixel. Kleinere Bilder werden von Facebook und LinkedIn als "
                    "kleines Quadrat statt als grosse Vorschau dargestellt.",
                    "Vorschaubild in 1200x630 (Seitenverhältnis 1.91:1) neu "
                    "anlegen und og:image:width/height mitgeben.",
                )
            )
        return issues


# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------


def _issue(
    kategorie: str,
    typ: str,
    schwere: str,
    url: str,
    titel: str,
    beschreibung: str,
    abhilfe: str,
    wirkung: str = "",
) -> Dict[str, Any]:
    """Einheitliches Befundformat — identisch zu den übrigen Analyzern."""
    return {
        "category": kategorie,
        "type": typ,
        "severity": schwere,
        "title": titel,
        "affected_url": url,
        "description": beschreibung,
        "fix_suggestion": abhilfe,
        "estimated_impact": wirkung,
    }
