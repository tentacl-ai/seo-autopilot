"""One authorization and business layer for the UI, API and agent tools."""

from __future__ import annotations

import hmac
import json
import math
import re
import sqlite3
import uuid
from datetime import date, timedelta
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .store import Store, WorkspaceError, fingerprint, identifier, now


@dataclass(frozen=True)
class Principal:
    name: str
    projects: frozenset[str]
    role: str = "viewer"


class Access:
    """Server-owned allowlist containing token hashes, never plaintext tokens."""

    def __init__(self, entries=None, allow_demo=False):
        self.entries = entries or []
        self.allow_demo = allow_demo

    def authenticate(self, token: str) -> Principal:
        import hashlib

        if len(token) < 24 and not (self.allow_demo and token == "demo"):
            raise WorkspaceError("Ungültiger oder fehlender Zugang.", 401)
        digest = hashlib.sha256(token.encode()).hexdigest()
        for item in self.entries:
            if hmac.compare_digest(digest, item.get("sha256", "")):
                if item.get("role") not in {"viewer", "editor", "owner"}:
                    break
                return Principal(
                    item["name"], frozenset(item["projects"]), item["role"]
                )
        raise WorkspaceError("Ungültiger oder fehlender Zugang.", 401)


SECTIONS = {
    "business",
    "goal",
    "positioning",
    "voice",
    "region",
    "competitors",
    "pages",
}


def bounded_evidence(value, max_bytes=2000):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) <= max_bytes:
        return value
    result = {
        "truncated": True,
        "excerpt": raw[: max_bytes // 2].decode("utf-8", errors="ignore"),
    }
    if isinstance(value, dict):
        for key in [
            "position",
            "depth",
            "status",
            "measurement",
            "engine",
            "mentioned",
            "cited",
        ]:
            item = value.get(key)
            if isinstance(item, (str, bool, int, float)) or item is None:
                result[key] = item[:100] if isinstance(item, str) else item
    return result


KINDS = {"context", "keyword", "report", "template", "tracker", "observation"}
# Deliberate column allowlists: no credentials, raw log output or configuration.
TABLES = {
    "audits": (
        "seo_audits",
        "id,started_at,completed_at,status,total_pages,score,gsc_clicks,gsc_impressions,gsc_ctr,gsc_avg_position",
    ),
    "issues": (
        "seo_issues",
        "id,audit_id,category,issue_type,severity,title,description,affected_url,status",
    ),
    "rankings": (
        "rangliste",
        "woche,begriff,geraet,position,klicks,impressionen,abgerufen_am",
    ),
    "history": (
        "gsc_historie",
        "monat,dimension,wert,klicks,impressionen,ctr,position,vollstaendig,abgerufen_am",
    ),
    "ai": ("ki_sichtbarkeit", "datum,ki,frage,genannt,quellen,wettbewerber,modell"),
    "maps": ("maps_platz", "woche,begriff,ort,platz"),
    "profiles": (
        "maps_profil",
        "woche,place_id,name,adresse,telefon,website,sterne,bewertungen,status,art,abgerufen_am",
    ),
    "backlinks": ("backlinks", "graph,domain,rang"),
    "changes": (
        "change_log",
        "id,zeitpunkt,ziel_url,aktion,urheber,status,begruendung,git_commit",
    ),
    "effects": (
        "wirkung_messungen",
        "id,ziel_url,aktion,fenster_tage,gemessen_am,vorher_klicks,nachher_klicks,vorher_position,nachher_position,urteil,notiz",
    ),
    "recommendations": (
        "empfehlungen",
        "id,seite,art,erstellt_am,prioritaet,titel,text,vorschlag,status",
    ),
}


def public_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise WorkspaceError("Eine vollständige HTTP-/HTTPS-Adresse ist erforderlich.")
    return value


class Workspace:
    def __init__(
        self,
        store: Store,
        projects: dict,
        source_db: str | None = None,
        provider=None,
        policies=None,
        assistant=None,
    ):
        self.store = store
        self.projects = projects
        self.source_db = source_db
        self.provider = provider
        self.policies = policies or {}
        self.assistant = assistant

    def authorize(self, user: Principal, project: str, write=False, paid=False):
        if project not in user.projects or project not in self.projects:
            raise WorkspaceError("Projekt nicht gefunden.", 404)
        if write and user.role not in {"editor", "owner"}:
            raise WorkspaceError("Nur lesender Zugang.", 403)
        if paid and user.role != "owner":
            raise WorkspaceError(
                "Datenabruf benötigt einen freigegebenen Betreiberzugang.", 403
            )

    def list_projects(self, user):
        return [
            {
                "id": key,
                "name": value.get("name", key),
                "domain": value.get("domain", ""),
            }
            for key, value in self.projects.items()
            if key in user.projects
        ]

    def source(self, user, project, section, limit=200):
        self.authorize(user, project)
        if section not in TABLES or not 1 <= limit <= 2000:
            raise WorkspaceError("Ungültiger Datenbereich oder Umfang.")
        base = {
            "section": section,
            "source": "stored_audit_data",
            "measurement": section,
            "read_at": now(),
            "rows": [],
            "status": "missing",
        }
        if not self.source_db or not Path(self.source_db).is_file():
            return base
        table, columns = TABLES[section]
        try:
            con = sqlite3.connect(
                Path(self.source_db).resolve().as_uri() + "?mode=ro", uri=True
            )
            con.row_factory = sqlite3.Row
            try:
                available = {
                    row[1] for row in con.execute(f'PRAGMA table_info("{table}")')
                }
                selected = [
                    column for column in columns.split(",") if column in available
                ]
                if not selected or "project_id" not in available:
                    return base
                order = "rowid DESC"
                if (
                    section == "recommendations"
                    and {"status", "prioritaet"} <= available
                ):
                    order = "CASE WHEN status IN ('offen','open') THEN 0 ELSE 1 END,prioritaet DESC,rowid DESC"
                rows = con.execute(
                    f'SELECT {",".join(selected)} FROM "{table}" WHERE project_id=? ORDER BY {order} LIMIT ?',
                    (project, limit),
                ).fetchall()
                base.update(rows=[dict(row) for row in rows], status="ok", limit=limit)
            finally:
                con.close()
        except sqlite3.Error:
            base["status"] = "unavailable"
        return base

    def overview(self, user, project):
        self.authorize(user, project)
        sections = {name: self.source(user, project, name, 100) for name in TABLES}
        return {
            "project": self.list_projects(
                Principal(user.name, frozenset({project}), user.role)
            )[0],
            "sections": sections,
            "context": self.list_documents(user, project, "context"),
            "spending": self.store.spending(project),
            "read_at": now(),
            "data_policy": "Gespeicherte Messungen; keine neuen externen Abrufe.",
        }

    def list_documents(self, user, project, kind):
        self.authorize(user, project)
        if kind not in KINDS:
            raise WorkspaceError("Unbekannter Bereich.")
        return self.store.list(project, kind)

    def get_document(self, user, project, kind, key, revision=None):
        self.authorize(user, project)
        if kind not in KINDS:
            raise WorkspaceError("Unbekannter Bereich.")
        row = self.store.get(project, kind, identifier(key), revision)
        if row is None:
            raise WorkspaceError("Eintrag nicht gefunden.", 404)
        return row

    def save_document(self, user, project, kind, key, payload, expected_revision=0):
        self.authorize(user, project, write=True)
        if kind not in KINDS or kind == "observation" or not isinstance(payload, dict):
            raise WorkspaceError("Ungültiger Inhalt.")
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise WorkspaceError("Ungültige Fassung.")
        payload = self.validate_document(user, project, kind, key, payload)
        return self.store.put(project, kind, key, payload, user.name, expected_revision)

    def validate_document(self, user, project, kind, key, payload):
        # All payload types are constructed from explicit fields, so agent input
        # cannot smuggle operational flags, credentials or publishing permissions.
        if kind == "context":
            if key not in SECTIONS:
                raise WorkspaceError("Unbekannter Abschnitt der Projektakte.")
            text = str(payload.get("text", "")).strip()
            if len(text) > 4000:
                raise WorkspaceError("Projektabschnitt zu lang.")
            status = payload.get("status", "inferred")
            if status not in {"inferred", "confirmed"}:
                raise WorkspaceError("Ungültiger Bestätigungsstatus.")
            old = self.store.get(project, kind, key)
            if (
                status == "confirmed"
                or (old and old["payload"]["status"] == "confirmed")
            ) and user.role != "owner":
                raise WorkspaceError(
                    "Bestätigte Angaben benötigt Betreiberfreigabe.", 403
                )
            return {
                "text": text,
                "status": status,
                "source": str(payload.get("source", "manual"))[:500],
            }
        if kind == "keyword":
            query = str(payload.get("query", "")).strip()
            if not query or len(query) > 200:
                raise WorkspaceError("Suchbegriff fehlt oder ist zu lang.")
            intent = payload.get("intent", "unknown")
            if intent not in {
                "informational",
                "commercial",
                "transactional",
                "navigational",
                "unknown",
            }:
                raise WorkspaceError("Ungültige Suchabsicht.")
            url = str(payload.get("url", "")).strip()
            if url:
                public_url(url)
                own = urlparse(self.projects[project]["domain"]).hostname
                if urlparse(url).hostname != own:
                    raise WorkspaceError("Zielseite gehört nicht zum Projekt.")
            tags = payload.get("tags", [])
            if (
                not isinstance(tags, list)
                or len(tags) > 10
                or not all(isinstance(t, str) and len(t) <= 50 for t in tags)
            ):
                raise WorkspaceError("Ungültige Schlagwörter.")
            return {
                "query": query,
                "intent": intent,
                "url": url,
                "tags": sorted(set(tags)),
                "notes": str(payload.get("notes", ""))[:2000],
                "source": "manual",
                "volume": None,
            }
        if kind in {"report", "template"}:
            title = str(payload.get("title", "")).strip()
            text = str(payload.get("text", ""))
            if not title or len(title) > 160 or len(text) > 100_000:
                raise WorkspaceError("Titel fehlt oder Bericht zu lang.")
            return {
                "title": title,
                "text": text,
                "visibility": "private",
                "period": str(payload.get("period", ""))[:200],
            }
        if kind == "tracker":
            tracker_type = payload.get("type", "serp")
            terms = payload.get("terms", [])
            if (
                tracker_type not in {"serp", "local", "ai"}
                or not isinstance(terms, list)
                or not 1 <= len(terms) <= 30
            ):
                raise WorkspaceError("Ungültiger Tracker oder Begriffsumfang.")
            if not all(isinstance(t, str) and 0 < len(t.strip()) <= 500 for t in terms):
                raise WorkspaceError("Ungültige Begriffe oder Fragen.")
            if tracker_type != "ai" and any(
                re.search(r"\b(site|intitle|inurl|filetype|cache|related):", t, re.I)
                for t in terms
            ):
                raise WorkspaceError(
                    "Suchoperatoren haben eine andere Abrechnung und werden hier nicht verfolgt."
                )
            cadence = payload.get("schedule", "manual")
            if cadence not in {"manual", "daily", "weekly", "monthly"}:
                raise WorkspaceError("Unbekannter Rhythmus.")
            device = payload.get("device", "mobile")
            if device not in {"mobile", "desktop"}:
                raise WorkspaceError("Ungültiges Gerät.")
            location = payload.get("location_code", 2276)
            if not isinstance(location, int) or location <= 0:
                raise WorkspaceError("Ungültiger Markt.")
            clean = {
                "type": tracker_type,
                "terms": list(dict.fromkeys(t.strip() for t in terms)),
                "device": device,
                "location_code": location,
                "language_code": str(payload.get("language_code", "de"))[:8],
                "schedule": cadence,
                "depth": 10,
                "domain": self.projects[project]["domain"],
            }
            if tracker_type == "local":
                clean.update(
                    grid(
                        payload.get("lat"),
                        payload.get("lng"),
                        payload.get("size", 3),
                        payload.get("spacing_km", 1),
                    )
                )
                target = str(payload.get("place_id", "")).strip()
                if not target or len(target) > 150:
                    raise WorkspaceError("Google-Place-ID fehlt.")
                clean["place_id"] = target
            if tracker_type == "ai":
                engines = payload.get("engines", ["chatgpt"])
                if (
                    not isinstance(engines, list)
                    or not engines
                    or not set(engines) <= {"chatgpt", "gemini", "claude"}
                ):
                    raise WorkspaceError("Ungültige KI-Auswahl.")
                clean["engines"] = sorted(set(engines))
            return clean
        raise WorkspaceError("Unbekannter Inhalt.")

    def content_plan(self, user, project):
        keywords = self.list_documents(user, project, "keyword")
        groups = {}
        for item in keywords:
            k = item["payload"]
            cluster = (
                k["url"] or (k["tags"][0] if k["tags"] else k["query"]),
                k["intent"],
            )
            group = groups.setdefault(
                cluster,
                {
                    "target": k["url"],
                    "intent": k["intent"],
                    "keywords": [],
                    "action": "improve" if k["url"] else "review_gap",
                    "basis": "Manuelle Zuordnung, keine gemessene SERP-Ähnlichkeit",
                },
            )
            group["keywords"].append(k["query"])
        return list(groups.values())

    def ranking_comparison(self, user, project):
        source = self.source(user, project, "rankings", 2000)
        rows = source["rows"]
        if not rows:
            return {"status": source["status"], "rows": []}
        week = max(str(r["woche"]) for r in rows)
        try:
            year, number = week.split("-W")
            previous = date.fromisocalendar(int(year), int(number), 1) - timedelta(
                days=7
            )
            year, number, _ = previous.isocalendar()
            previous_week = f"{year}-W{number:02d}"
        except (ValueError, TypeError):
            raise WorkspaceError("Unbekanntes Wochenformat.")
        lookup = {(r["woche"], r["begriff"], r["geraet"]): r for r in rows}
        comparison = []
        for row in rows:
            if row["woche"] != week:
                continue
            before = lookup.get((previous_week, row["begriff"], row["geraet"]), {})
            current_clicks, before_clicks = row.get("klicks"), before.get("klicks")
            comparison.append(
                {
                    "begriff": row["begriff"],
                    "geraet": row["geraet"],
                    "position_current": row.get("position"),
                    "position_previous": before.get("position"),
                    "clicks_current": current_clicks,
                    "clicks_previous": before_clicks,
                    "change": (
                        "Kein Vorwochenwert"
                        if before_clicks is None
                        else (
                            "gleich wie Vorwoche"
                            if current_clicks == before_clicks
                            else f"{current_clicks - before_clicks:+d} Klicks"
                        )
                    ),
                }
            )
        return {
            "status": "ok",
            "week": week,
            "previous_week": previous_week,
            "source": "gsc_weekly_average",
            "rows": comparison,
            "sample_limit": 2000,
        }

    def estimate(self, user, project, key):
        tracker = self.get_document(user, project, "tracker", key)["payload"]
        calls = len(tracker["terms"]) * (
            len(tracker.get("points", [None]))
            if tracker["type"] == "local"
            else len(tracker.get("engines", [None]))
        )
        policy = self.policies.get(project, {})
        if calls > min(int(policy.get("max_calls_per_run", 50)), 50):
            raise WorkspaceError(
                "Zu viele Einzelabrufe für einen Auftrag. Bitte Umfang verkleinern."
            )
        ceiling = policy.get("ceilings", {}).get(tracker["type"], 0)
        return {
            "calls": calls,
            "reserved_microusd": calls * ceiling,
            "monthly_limit_microusd": policy.get("monthly_limit_microusd", 0),
            "enabled": bool(policy.get("enabled", False) and ceiling > 0),
            "notice": "Konservative Betreiber-Kostenschätzung; Providerpreise vor Aktivierung prüfen. Keine automatische Wiederholung.",
        }

    def research_estimate(self, user, project, feature):
        self.authorize(user, project)
        policy = self.policies.get(project, {})
        ceiling = policy.get("ceilings", {}).get(feature, 0)
        return {
            "calls": 1,
            "reserved_microusd": ceiling,
            "enabled": bool(policy.get("enabled") and ceiling > 0),
            "monthly_limit_microusd": policy.get("monthly_limit_microusd", 0),
        }

    def research_log(self, user, project):
        self.authorize(user, project)
        return self.store.research_log(project)

    def import_keywords(self, user, project):
        self.authorize(user, project, write=True)
        rows = self.source(user, project, "history", 2000)["rows"]
        existing = {
            r["payload"]["query"].casefold()
            for r in self.list_documents(user, project, "keyword")
        }
        imported = 0
        for row in rows:
            query = str(row.get("wert", "")).strip()
            if (
                row.get("dimension") not in {"query", "suchbegriff", "begriff"}
                or not query
                or len(query) > 200
                or query.casefold() in existing
            ):
                continue
            self.store.put(
                project,
                "keyword",
                fingerprint(query.casefold())[:24],
                {
                    "query": query,
                    "intent": "unknown",
                    "url": "",
                    "tags": [],
                    "notes": "",
                    "source": "gsc",
                    "volume": None,
                    "impressions": row.get("impressionen"),
                    "position": row.get("position"),
                    "data_at": row.get("monat"),
                },
                user.name,
                0,
            )
            existing.add(query.casefold())
            imported += 1
            if imported >= 100:
                break
        return {"imported": imported, "source": "stored_gsc_history", "paid_calls": 0}

    async def run_tracker(self, user, project, key, request_id, confirmed=False):
        self.authorize(user, project, write=True, paid=True)
        estimate = self.estimate(user, project, key)
        if not confirmed or not estimate["enabled"] or not self.provider:
            raise WorkspaceError(
                "Datenabruf ist nicht freigegeben oder bestätigt.", 403
            )
        tracker = self.get_document(user, project, "tracker", key)
        config = tracker["payload"]
        digest = fingerprint(
            {"tracker": key, "revision": tracker["revision"], "config": config}
        )
        self.store.reserve(
            project,
            request_id,
            digest,
            estimate["reserved_microusd"],
            estimate["monthly_limit_microusd"],
        )
        observations = []
        try:
            for term in config["terms"]:
                variants = (
                    config.get("points", [None])
                    if config["type"] == "local"
                    else config.get("engines", [None])
                )
                for variant in variants:
                    parameters = {**config, "term": term, "variant": variant}
                    try:
                        value = bounded_evidence(
                            await self.provider.collect(config["type"], parameters)
                        )
                        observations.append(
                            {
                                "term": term,
                                "variant": variant,
                                "status": "ok",
                                "value": value,
                            }
                        )
                    except Exception:
                        observations.append(
                            {
                                "term": term,
                                "variant": variant,
                                "status": "failed",
                                "value": None,
                            }
                        )
            passed = sum(row["status"] == "ok" for row in observations)
            status = (
                "completed"
                if passed == len(observations)
                else "partial" if passed else "failed"
            )
            result = {
                "tracker": key,
                "tracker_revision": tracker["revision"],
                "measurement": config["type"],
                "market": config["location_code"],
                "device": config["device"],
                "collected_at": now(),
                "status": status,
                "successful": passed,
                "attempted": len(observations),
                "observations": observations,
            }
            self.store.put(project, "observation", request_id, result, user.name, 0)
            self.store.finish(project, request_id, result, status)
            return result
        except BaseException:
            self.store.finish(
                project,
                request_id,
                {"status": "unknown", "notice": "Nicht automatisch wiederholen."},
                "unknown",
            )
            raise

    async def run_due(self, user, project, today=None):
        self.authorize(user, project, write=True, paid=True)
        if not self.policies.get(project, {}).get("scheduled_allowed", False):
            raise WorkspaceError(
                "Geplante Messungen sind nicht für den Betrieb freigegeben.", 403
            )
        today = today or date.today()
        results = []
        for tracker in self.list_documents(user, project, "tracker"):
            cadence = tracker["payload"]["schedule"]
            if cadence == "manual":
                continue
            period = (
                today.isoformat()
                if cadence == "daily"
                else (
                    f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"
                    if cadence == "weekly"
                    else today.strftime("%Y-%m")
                )
            )
            request_id = (
                "scheduled-"
                + fingerprint([tracker["key"], tracker["revision"], period])[:40]
            )
            try:
                result = await self.run_tracker(
                    user, project, tracker["key"], request_id, True
                )
                results.append({"tracker": tracker["key"], "status": result["status"]})
            except WorkspaceError as exc:
                results.append(
                    {
                        "tracker": tracker["key"],
                        "status": "not_started",
                        "reason": str(exc),
                    }
                )
        return results

    async def research(
        self, user, project, feature, parameters, request_id, confirmed=False
    ):
        self.authorize(user, project, write=True, paid=True)
        if feature not in {
            "keyword_ideas",
            "domain_keywords",
            "domain_overview",
            "backlinks",
            "referring_domains",
            "top_pages",
            "business_profile",
            "reviews",
        }:
            raise WorkspaceError("Unbekannte Recherche.")
        if not isinstance(parameters, dict) or set(parameters) - {
            "target",
            "keyword",
            "location_code",
            "language_code",
        }:
            raise WorkspaceError("Ungültige Rechercheparameter.")
        location = parameters.get("location_code", 2276)
        language = parameters.get("language_code", "de")
        if (
            not isinstance(location, int)
            or isinstance(location, bool)
            or location <= 0
            or not isinstance(language, str)
            or not re.fullmatch(r"[a-zA-Z-]{2,8}", language)
        ):
            raise WorkspaceError("Ungültiger Markt oder Sprache.")
        for name in ["target", "keyword"]:
            if (
                not isinstance(parameters.get(name, ""), str)
                or len(parameters.get(name, "")) > 200
            ):
                raise WorkspaceError("Ungültiges Rechercheziel.")
        cleaned = {
            "target": str(parameters.get("target", ""))[:200],
            "keyword": str(parameters.get("keyword", ""))[:200],
            "location_code": int(parameters.get("location_code", 2276)),
            "language_code": str(parameters.get("language_code", "de"))[:8],
        }
        if feature == "keyword_ideas" and not cleaned["keyword"].strip():
            raise WorkspaceError("Startbegriff fehlt.")
        if feature != "keyword_ideas" and not cleaned["target"].strip():
            raise WorkspaceError("Ziel fehlt.")
        digest = fingerprint({"feature": feature, "parameters": cleaned})
        cached = self.store.cached(project, digest)
        if cached is not None:
            return {**cached, "cached": True}
        policy = self.policies.get(project, {})
        ceiling = policy.get("ceilings", {}).get(feature, 0)
        if (
            not confirmed
            or not policy.get("enabled")
            or not ceiling
            or not self.provider
        ):
            raise WorkspaceError(
                "Neue Recherche benötigt Datenfreigabe und Kostenbestätigung.", 403
            )
        self.store.reserve(
            project,
            request_id,
            digest,
            ceiling,
            policy.get("monthly_limit_microusd", 0),
        )
        try:
            value = bounded_evidence(
                await self.provider.research(feature, cleaned), 180000
            )
            result = {
                "feature": feature,
                "parameters": cleaned,
                "source": "dataforseo",
                "collected_at": now(),
                "data": value,
            }
            queued = isinstance(value, dict) and value.get("status") == "queued"
            self.store.finish(
                project, request_id, result, "queued" if queued else "completed"
            )
            return result
        except BaseException:
            self.store.finish(project, request_id, {"status": "unknown"}, "unknown")
            raise WorkspaceError(
                "Recherche fehlgeschlagen; Auftrag nicht automatisch wiederholen.", 502
            )

    async def review_result(self, user, project, request_id):
        self.authorize(user, project)
        record = next(
            (
                r
                for r in self.store.research_log(project)
                if r["request_id"] == request_id
            ),
            None,
        )
        if (
            record is None
            or not record.get("payload")
            or record["payload"].get("feature") != "reviews"
        ):
            raise WorkspaceError("Bewertungsauftrag nicht gefunden.", 404)
        if record["status"] == "completed":
            return record["payload"]
        if record["status"] != "queued" or not self.provider:
            raise WorkspaceError("Kein abrufbarer Bewertungsauftrag.", 409)
        value = await self.provider.review_result(record["payload"]["data"]["task_id"])
        result = {**record["payload"], "data": value}
        if value["status"] == "completed":
            self.store.queued_result(project, request_id, result)
        return result

    def create_report(self, user, project, title="SEO-Stand", template_key=None):
        overview = self.overview(user, project)
        rows = overview["sections"]
        template = (
            self.get_document(user, project, "template", template_key)["payload"]
            if template_key
            else None
        )
        if template:
            title = template["title"]
        lines = [
            title,
            "Stand: " + overview["read_at"],
            "Quelle: gespeicherte Messungen",
            "",
        ]
        if template:
            lines.extend([template["text"], ""])
        for name, label in [
            ("audits", "Website-Prüfungen"),
            ("rankings", "Google-Positionen"),
            ("ai", "KI-Nennungen"),
            ("recommendations", "Empfehlungen"),
            ("effects", "Wirkungsmessungen"),
        ]:
            section = rows[name]
            lines.extend([label, "Datenstatus: " + section["status"]])
            if not section["rows"]:
                lines.append("Keine gespeicherten Messungen verfügbar.")
            for row in section["rows"][:10]:
                if name == "audits":
                    lines.append(
                        f"Prüfung {row.get('completed_at', 'offen')}: Stand {row.get('score', 'unbekannt')}, {row.get('total_pages', 'unbekannt')} Seiten, Status {row.get('status', 'unbekannt')}."
                    )
                elif name == "rankings":
                    lines.append(
                        f"{row.get('woche')}: {row.get('begriff')} ({row.get('geraet')}), durchschnittliche Position {row.get('position')}, {row.get('klicks')} Klicks."
                    )
                elif name == "ai":
                    lines.append(
                        f"{row.get('datum')} · {row.get('ki')}: {row.get('frage')} — {row.get('genannt')}. Modell: {row.get('modell', 'unbekannt')}."
                    )
                elif name == "recommendations":
                    lines.append(
                        f"{row.get('titel')}: {row.get('text') or row.get('vorschlag', '')} Status: {row.get('status')}."
                    )
                else:
                    lines.append(
                        f"{row.get('ziel_url')}: {row.get('aktion')}, {row.get('fenster_tage')} Tage. Klicks {row.get('vorher_klicks')} → {row.get('nachher_klicks')}, Position {row.get('vorher_position')} → {row.get('nachher_position')}. Urteil: {row.get('urteil')}. {row.get('notiz') or ''}"
                    )
            lines.append("")
        key = uuid.uuid4().hex
        return self.save_document(
            user, project, "report", key, {"title": title, "text": "\n".join(lines)}, 0
        )

    async def ask(self, user, project, question):
        self.authorize(user, project, write=True)
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
            raise WorkspaceError("Frage fehlt oder ist zu lang.")
        if self.assistant is None:
            raise WorkspaceError(
                "Der Gesprächsassistent ist noch nicht eingerichtet. Die Agentenwerkzeuge stehen separat bereit.",
                503,
            )
        snapshot = self.overview(user, project)
        digest = fingerprint(
            {
                "question": question,
                "date": now()[:10],
                "context": snapshot["context"],
                "rows": {k: v["rows"][:5] for k, v in snapshot["sections"].items()},
            }
        )
        cached = self.store.get(project, "assistant", digest)
        if cached:
            if cached["payload"].get("status") != "completed":
                raise WorkspaceError(
                    "Diese Frage wurde bereits begonnen; bitte Status prüfen.", 409
                )
            return {**cached["payload"], "cached": True}
        self.store.claim_assistant(project, digest, user.name)
        evidence = {
            "project": snapshot["project"],
            "context": snapshot["context"],
            "measurements": {
                k: {"status": v["status"], "rows": v["rows"][:5]}
                for k, v in snapshot["sections"].items()
            },
        }
        try:
            answer = await self.assistant(question, evidence)
        except Exception:
            self.store.put(
                project, "assistant", digest, {"status": "failed"}, user.name, 1
            )
            raise WorkspaceError("Gesprächsassistent derzeit nicht verfügbar.", 503)
        if not isinstance(answer, str) or not answer.strip():
            self.store.put(
                project, "assistant", digest, {"status": "failed"}, user.name, 1
            )
            raise WorkspaceError("Keine Antwort erhalten.", 503)
        result = {
            "status": "completed",
            "answer": answer[:12000],
            "source": "stored_project_snapshot",
            "read_at": snapshot["read_at"],
            "notice": "KI-Zusammenfassung; Messwerte im jeweiligen Fachbereich nachprüfen.",
        }
        self.store.put(project, "assistant", digest, result, user.name, 1)
        return result


def grid(lat, lng, size=3, spacing_km=1):
    try:
        lat, lng, spacing_km = float(lat), float(lng), float(spacing_km)
    except (ValueError, TypeError):
        raise WorkspaceError("Ungültiger Standort.")
    if (
        not all(math.isfinite(v) for v in [lat, lng, spacing_km])
        or not -85 <= lat <= 85
        or not -180 <= lng <= 180
        or size not in {3, 5, 7}
        or not 0.1 <= spacing_km <= 10
    ):
        raise WorkspaceError("Ungültiges Standortraster.")
    points = []
    for row in range(size):
        for col in range(size):
            point_lat = lat + (row - size // 2) * spacing_km / 111.32
            point_lng = lng + (col - size // 2) * spacing_km / (
                111.32 * math.cos(math.radians(lat))
            )
            if not -85 <= point_lat <= 85 or not -180 <= point_lng <= 180:
                raise WorkspaceError("Raster überschreitet den gültigen Kartenbereich.")
            points.append(
                {
                    "row": row,
                    "col": col,
                    "lat": round(point_lat, 6),
                    "lng": round(point_lng, 6),
                }
            )
    return {
        "lat": lat,
        "lng": lng,
        "size": size,
        "spacing_km": spacing_km,
        "points": points,
    }
