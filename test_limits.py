import json
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from limits import (COLORS, CRITICAL_COLOR, UNKNOWN_COLOR, WARNING_COLOR,
                    ProviderState, Snapshot, UsageError, Window, color_for,
                    parse_claude, parse_codex, percent_label, remaining_percent, tray_svg)


class LimitsTests(unittest.TestCase):
    def test_codex_week_can_be_primary_or_secondary(self):
        week = {"usedPercent": 69, "windowDurationMins": 10080, "resetsAt": 1900000000}
        session = {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 1800000000}
        for primary, secondary in ((week, session), (session, week)):
            result = parse_codex({"rateLimits": {"primary": primary, "secondary": secondary}})
            self.assertEqual(result.weekly.remaining, 31)
            self.assertEqual(result.session.remaining, 90)

    def test_codex_missing_week_is_unknown_not_full(self):
        with self.assertRaises(UsageError):
            parse_codex({"rateLimits": {"primary": {"usedPercent": 0, "windowDurationMins": 300}}})

    def test_codex_uses_general_pool_not_model_pool(self):
        week = {"primary": {"usedPercent": 80, "windowDurationMins": 10080}}
        result = parse_codex({"rateLimits": {"limitId": "other"}, "rateLimitsByLimitId": {"codex": week}})
        self.assertEqual(result.weekly.remaining, 20)
        with self.assertRaises(UsageError):
            parse_codex({"rateLimits": {"limitId": "other", **week}})

    def test_claude_all_models_weekly_and_iso_reset(self):
        result = parse_claude({"seven_day": {"utilization": 99, "resets_at": "2026-10-16T04:59:59+00:00"},
                               "seven_day_sonnet": {"utilization": 5}, "extra_usage": {"is_enabled": True}})
        self.assertEqual(result.weekly.remaining, 1)
        self.assertEqual(result.weekly.resets_at, 1792126799)
        self.assertTrue(result.extra_usage)

    def test_missing_or_invalid_values_do_not_claim_capacity(self):
        for value in (None, "12", True, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(UsageError):
                remaining_percent(value)
        with self.assertRaises(UsageError):
            parse_claude({"seven_day": None})

    def test_empty_full_and_fractional_display(self):
        self.assertEqual(remaining_percent(100), 0)
        self.assertEqual(remaining_percent(0), 100)
        self.assertEqual(remaining_percent(101), 0)
        self.assertEqual(percent_label(.1), "<1%")
        self.assertEqual(percent_label(99.9), "99%")
        self.assertEqual(percent_label(None), "–")

    def test_color_boundaries_are_inclusive(self):
        for provider in COLORS:
            for value, expected in ((100, COLORS[provider]), (25.1, COLORS[provider]),
                                    (25, WARNING_COLOR), (10.1, WARNING_COLOR),
                                    (10, CRITICAL_COLOR), (0, CRITICAL_COLOR)):
                self.assertEqual(color_for(provider, value), expected)
            self.assertEqual(color_for(provider, 100, True), UNKNOWN_COLOR)
            self.assertEqual(color_for(provider, None), UNKNOWN_COLOR)

    def test_offline_keeps_last_value_but_marks_it_stale(self):
        state = ProviderState()
        snapshot = Snapshot(Window(30, 2000), fetched_at=1000)
        state.success(snapshot, now=100)
        self.assertFalse(state.stale(1100))
        state.failure(UsageError("Offline"), now=110)
        self.assertEqual(state.snapshot.weekly.remaining, 30)
        self.assertTrue(state.stale(1100))
        self.assertEqual(state.next_poll, 170)
        state.success(snapshot, now=120)
        self.assertEqual(state.failures, 0)
        self.assertIsNone(state.error)

    def test_age_and_reset_expiry_never_assume_refilled(self):
        state = ProviderState(snapshot=Snapshot(Window(0, 1200), fetched_at=1000))
        self.assertTrue(state.stale(1181))
        state.snapshot.fetched_at = 1190
        self.assertTrue(state.stale(1200))
        self.assertEqual(state.snapshot.weekly.remaining, 0)

    def test_rate_limit_backoff_and_server_retry_after(self):
        state = ProviderState()
        state.failure(UsageError("429", 7200), now=100)
        self.assertEqual(state.next_poll, 7300)
        for _ in range(20):
            state.failure(UsageError("Offline"), now=100)
        self.assertEqual(state.next_poll, 3700)

    def test_battery_svg_has_real_zero_full_and_valid_fraction_label(self):
        now = time.time()
        states = {"claude": ProviderState(snapshot=Snapshot(Window(0, None), fetched_at=now)),
                  "codex": ProviderState(snapshot=Snapshot(Window(100, None), fetched_at=now))}
        svg = tray_svg(states)
        ET.fromstring(svg)
        self.assertIn('width="0.000"', svg)
        self.assertIn('width="65.000"', svg)
        states["claude"].snapshot.weekly.remaining = .1
        ET.fromstring(tray_svg(states))
        self.assertIn('&lt;1%', tray_svg(states))


if __name__ == "__main__":
    unittest.main()
