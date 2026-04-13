"""Tests for PageSpeed Insights Source — INP, CrUX, Ratings."""

import pytest
from seo_autopilot.sources.pagespeed import (
    PageSpeedResult,
    rate_metric,
    _extract_crux_data,
)


class TestRateMetric:
    def test_lcp_good(self):
        assert rate_metric("lcp", 2000) == "good"

    def test_lcp_needs_improvement(self):
        assert rate_metric("lcp", 3000) == "needs-improvement"

    def test_lcp_poor(self):
        assert rate_metric("lcp", 5000) == "poor"

    def test_cls_good(self):
        assert rate_metric("cls", 0.05) == "good"

    def test_cls_poor(self):
        assert rate_metric("cls", 0.3) == "poor"

    def test_inp_good(self):
        assert rate_metric("inp", 150) == "good"

    def test_inp_needs_improvement(self):
        assert rate_metric("inp", 300) == "needs-improvement"

    def test_inp_poor(self):
        assert rate_metric("inp", 600) == "poor"

    def test_unknown_metric(self):
        assert rate_metric("fid", 100) == "unknown"


class TestCruxExtraction:
    def test_extracts_inp_from_loading_experience(self):
        result = PageSpeedResult(url="https://example.com")
        data = {
            "loadingExperience": {
                "metrics": {
                    "INTERACTION_TO_NEXT_PAINT": {
                        "percentile": 250,
                        "category": "AVERAGE",
                    },
                    "LARGEST_CONTENTFUL_PAINT_MS": {
                        "percentile": 2100,
                        "category": "FAST",
                    },
                    "CUMULATIVE_LAYOUT_SHIFT_SCORE": {
                        "percentile": 8,
                        "category": "FAST",
                    },
                }
            }
        }
        _extract_crux_data(result, data)

        assert result.crux_inp_ms == 250
        assert result.crux_inp_rating == "needs-improvement"
        assert result.crux_lcp_ms == 2100
        assert result.crux_lcp_rating == "good"
        # CLS percentile 8 = 0.08
        assert result.crux_cls == 0.08
        assert result.crux_cls_rating == "good"
        assert result.has_field_data is True

    def test_no_field_data_graceful(self):
        result = PageSpeedResult(url="https://example.com")
        _extract_crux_data(result, {})
        assert result.has_field_data is False
        assert result.crux_inp_ms is None

    def test_fid_not_in_result(self):
        """FID has been deprecated since March 2024 — must never appear."""
        result = PageSpeedResult(url="https://example.com")
        result_dict = result.to_dict()
        assert "fid" not in str(result_dict).lower()


class TestPageSpeedResult:
    def test_cwv_summary_prefers_field_data(self):
        result = PageSpeedResult(url="https://example.com")
        result.lcp_ms = 3000  # Lab
        result.crux_lcp_ms = 2200  # Field
        result.crux_lcp_rating = "good"
        result.cls = 0.15  # Lab
        result.crux_inp_ms = 180
        result.crux_inp_rating = "good"

        summary = result.get_cwv_summary()
        assert summary["lcp"]["source"] == "field"
        assert summary["lcp"]["value"] == 2200
        assert summary["cls"]["source"] == "lab"  # no CrUX CLS
        assert summary["inp"]["source"] == "field"

    def test_cwv_summary_fallback_to_lab(self):
        result = PageSpeedResult(url="https://example.com")
        result.lcp_ms = 3000

        summary = result.get_cwv_summary()
        assert summary["lcp"]["source"] == "lab"
        assert summary["lcp"]["rating"] == "needs-improvement"
        assert "inp" not in summary  # No lab INP

    def test_pagespeed_mobile_and_desktop(self):
        mobile = PageSpeedResult(url="https://example.com", strategy="mobile")
        desktop = PageSpeedResult(url="https://example.com", strategy="desktop")
        assert mobile.strategy == "mobile"
        assert desktop.strategy == "desktop"
