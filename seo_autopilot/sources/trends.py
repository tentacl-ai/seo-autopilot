"""Google-Trends-Source via PyTrends (Welle 3 des SEO-Autopilots).

Liefert pro Project:
- interest_over_time pro keyword (letzte 7 Tage)
- rising_queries (was steigt gerade)
- top_queries (was wird allgemein gesucht)

Caching: Ergebnisse werden 24h gecached, weil Google PyTrends rate-limited
(insbesondere bei vielen Keywords schnell hintereinander).

Konfig (project.intel_config):
    {
      "intel_keywords": ["KI Hautanalyse", "Niacinamid", "Mischhaut"],
      "geo": "DE",                    # 2-Letter Country Code (default DE)
      "timeframe": "now 7-d"          # PyTrends format (default 7 Tage)
    }
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from pytrends.request import TrendReq

    HAS_PYTRENDS = True
except ImportError:
    HAS_PYTRENDS = False
    logger.info("pytrends not installed — TrendsSource disabled (pip install pytrends)")


# Disk-persistierter Cache (24h TTL). Ueberlebt Container-Restarts.
# In-memory Mirror fuer Speed.
_CACHE: Dict[str, "TrendBundle"] = {}
CACHE_TTL = timedelta(hours=24)
CACHE_DIR = Path(os.getenv("TRENDS_CACHE_DIR", "/app/data/trends_cache"))


def _cache_path(key: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in key)[:200]
    return CACHE_DIR / f"{safe}.pkl"


def _cache_load(key: str) -> Optional["TrendBundle"]:
    if key in _CACHE:
        return _CACHE[key]
    p = _cache_path(key)
    if p.exists():
        try:
            with p.open("rb") as f:
                bundle = pickle.load(f)
            _CACHE[key] = bundle
            return bundle
        except Exception as e:
            logger.warning(f"[trends] cache-read failed: {e}")
    return None


def _cache_save(key: str, bundle: "TrendBundle") -> None:
    _CACHE[key] = bundle
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _cache_path(key).open("wb") as f:
            pickle.dump(bundle, f)
    except Exception as e:
        logger.warning(f"[trends] cache-write failed: {e}")


@dataclass
class RisingQuery:
    query: str
    growth_pct: int  # z.B. 1500 fuer +1500%, oder PyTrends-Sentinel "Breakout"
    is_breakout: bool = False


@dataclass
class TrendBundle:
    """Trends-Daten fuer EIN Project (mehrere Keywords kombiniert)."""

    fetched_at: datetime
    keywords: List[str] = field(default_factory=list)
    interest: Dict[str, List[int]] = field(default_factory=dict)  # kw -> 0..100 series
    rising: List[RisingQuery] = field(default_factory=list)
    top: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fetched_at": self.fetched_at.isoformat(),
            "keywords": self.keywords,
            "interest": self.interest,
            "rising": [
                {
                    "query": r.query,
                    "growth_pct": r.growth_pct,
                    "is_breakout": r.is_breakout,
                }
                for r in self.rising
            ],
            "top": self.top,
            "error": self.error,
        }

    def insights(self) -> List[str]:
        """Generiert kurze Text-Insights ('XYZ steigt um 200%')."""
        out = []
        for r in self.rising[:5]:
            if r.is_breakout:
                out.append(f"🚀 BREAKOUT: '{r.query}' (Suche explodiert)")
            elif r.growth_pct >= 100:
                out.append(f"🔥 '{r.query}' +{r.growth_pct}%")
            elif r.growth_pct >= 50:
                out.append(f"📈 '{r.query}' +{r.growth_pct}%")
        return out


class TrendsSource:
    """Wrapper um PyTrends — fetcht und cached pro Project."""

    def __init__(self, geo: str = "DE", timeframe: str = "now 7-d"):
        self.geo = geo
        self.timeframe = timeframe

    def fetch(self, project_id: str, keywords: List[str]) -> TrendBundle:
        """Fetch trends fuer eine Keywords-Liste. Maximum 5 keywords pro PyTrends-Aufruf."""
        if not HAS_PYTRENDS:
            return TrendBundle(
                fetched_at=datetime.now(timezone.utc),
                keywords=keywords,
                error="pytrends not installed",
            )
        if not keywords:
            return TrendBundle(
                fetched_at=datetime.now(timezone.utc),
                keywords=[],
                error="no intel_keywords configured for project",
            )

        cache_key = f"{project_id}::{','.join(sorted(keywords))[:200]}::{self.geo}::{self.timeframe}"
        cached = _cache_load(cache_key)
        if cached and (datetime.now(timezone.utc) - cached.fetched_at) < CACHE_TTL:
            # 429-Fehler nicht cachen — sofort retry beim naechsten Audit erlauben
            if cached.error and "429" in str(cached.error):
                logger.info(
                    "[trends] cached entry was 429 — bypassing cache, will retry"
                )
            else:
                logger.info(f"[trends] cache hit for {project_id}")
                return cached

        bundle = TrendBundle(
            fetched_at=datetime.now(timezone.utc),
            keywords=keywords[:5],
        )

        try:
            trends = TrendReq(hl="de-DE", tz=60, timeout=(5, 15))
            kw_subset = keywords[:5]  # PyTrends-Limit
            trends.build_payload(
                kw_subset, cat=0, timeframe=self.timeframe, geo=self.geo
            )

            # Interest over time
            try:
                df = trends.interest_over_time()
                if not df.empty:
                    for kw in kw_subset:
                        if kw in df.columns:
                            series = [int(v) for v in df[kw].tolist()]
                            bundle.interest[kw] = series
                            # Rising-Heuristik: vergleiche Mittel der letzten 24h vs. erste 24h
                            n = len(series)
                            if n >= 8:
                                first = sum(series[: n // 4]) / max(1, n // 4)
                                last = sum(series[-(n // 4) :]) / max(1, n // 4)
                                # Schwellen weniger strikt: first>=0.3 reicht (Beauty-Nische ist klein)
                                if first >= 0.3 and last > first * 1.5:
                                    growth = int(((last - first) / first) * 100)
                                    bundle.rising.append(
                                        RisingQuery(
                                            query=kw,
                                            growth_pct=min(growth, 9999),
                                            is_breakout=growth >= 500,
                                        )
                                    )
            except Exception as e:
                logger.warning(f"[trends] interest_over_time failed: {e}")

            # Related Queries (rising) — PyTrends gibt das pro Keyword
            try:
                related = trends.related_queries() or {}
                rising_set: List[RisingQuery] = []
                for kw, blob in related.items():
                    if not blob:
                        continue
                    rising_df = blob.get("rising")
                    if rising_df is None or rising_df.empty:
                        continue
                    for _, row in rising_df.head(5).iterrows():
                        q = str(row.get("query", "")).strip()
                        v = row.get("value", 0)
                        is_breakout = isinstance(v, str) and "breakout" in v.lower()
                        try:
                            growth = int(v) if not is_breakout else 9999
                        except (ValueError, TypeError):
                            growth = 0
                            is_breakout = True
                        if q:
                            rising_set.append(
                                RisingQuery(
                                    query=q, growth_pct=growth, is_breakout=is_breakout
                                )
                            )
                # Mit Heuristik-Output mergen, dedupe by query, Top-N
                seen = {r.query for r in bundle.rising}
                for r in rising_set:
                    if r.query not in seen:
                        bundle.rising.append(r)
                        seen.add(r.query)
                bundle.rising.sort(key=lambda r: (not r.is_breakout, -r.growth_pct))
                bundle.rising = bundle.rising[:10]
            except Exception as e:
                logger.warning(f"[trends] related_queries failed: {e}")

            # Trending searches (Top, regional, ohne Keyword-Filter — Bonus)
            try:
                trending_df = trends.trending_searches(pn="germany")
                if not trending_df.empty:
                    bundle.top = [str(s) for s in trending_df[0].head(10).tolist()]
            except Exception as e:
                logger.debug(f"[trends] trending_searches failed: {e}")

            logger.info(
                f"[trends] {project_id}: {len(bundle.interest)} keywords tracked, "
                f"{len(bundle.rising)} rising, {len(bundle.top)} trending"
            )

        except Exception as e:
            logger.error(f"[trends] PyTrends call failed: {e}")
            bundle.error = str(e)

        # 429-Errors NICHT persistieren (nur in-memory schmeißen, nicht auf Disk)
        if bundle.error and "429" in str(bundle.error):
            return bundle
        _cache_save(cache_key, bundle)
        return bundle
