from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import radar
from email_sender import send_report_email


def row(symbol: str, *, rsi: float = 65, rsi5avg: float | None = None, rsi5change: float = 2, days_above_60: int = 7, r20: float = 8, r60: float = 18, distance: float = 4, theme: str = "半导体"):
    return {
        "symbol": symbol, "close": 110.0, "rsi14": rsi, "rsi14_5d_avg": rsi if rsi5avg is None else rsi5avg,
        "rsi14_change_5d": rsi5change, "rsi_above_60_days_10d": days_above_60, "return_5d": 3.0,
        "return_20d": r20, "return_60d": r60, "ema20": 105.0, "ema50": 100.0,
        "ema200": 90.0, "distance_ema20_pct": distance, "avg_volume_20d": 2_000_000,
        "sector": "Information Technology", "industry": theme,
    }


class RadarTests(unittest.TestCase):
    def test_rsi14_is_correct_for_uninterrupted_gains(self):
        close = pd.Series(range(1, 40), dtype=float)
        self.assertEqual(radar.rsi14(close).iloc[-1], 100)

    def test_top10_uses_persistent_strength_and_requires_trend(self):
        rows = [row("A", rsi=70), row("B", rsi=80), row("C", rsi=90)]
        rows[2]["ema50"] = 120  # close is not above EMA20 above EMA50
        self.assertEqual([item["symbol"] for item in radar.rank_market_leaders(rows)], ["B", "A"])

    def test_one_day_rsi_spike_does_not_outrank_persistent_strength(self):
        spike = row("SPIKE", rsi=85, rsi5avg=58, rsi5change=25, days_above_60=1)
        persistent = row("STEADY", rsi=70, rsi5avg=70, rsi5change=2, days_above_60=10)
        self.assertEqual([item["symbol"] for item in radar.rank_market_leaders([spike, persistent])], ["STEADY", "SPIKE"])

    def test_ten_day_rsi_persistence_has_an_advantage(self):
        persistent = row("TEN", rsi=68, rsi5avg=68, rsi5change=1, days_above_60=10)
        brief = row("ONE", rsi=68, rsi5avg=68, rsi5change=1, days_above_60=1)
        self.assertGreater(radar.persistent_strength_score(persistent), radar.persistent_strength_score(brief))

    def test_rsi_above_80_does_not_gain_unlimited_score(self):
        capped = row("CAP80", rsi=80, rsi5avg=80, rsi5change=10, days_above_60=10)
        extreme = row("CAP95", rsi=95, rsi5avg=95, rsi5change=30, days_above_60=10)
        self.assertEqual(radar.persistent_strength_score(capped), radar.persistent_strength_score(extreme))

    def test_falling_five_day_rsi_lowers_ranking(self):
        rising = row("RISING", rsi=72, rsi5avg=70, rsi5change=5, days_above_60=10)
        falling = row("FALLING", rsi=72, rsi5avg=70, rsi5change=-5, days_above_60=10)
        self.assertEqual([item["symbol"] for item in radar.rank_market_leaders([falling, rising])], ["RISING", "FALLING"])

    def test_industry_mapping_prefers_existing_theme(self):
        sector, theme = radar.theme_for("NVDA", {"sector": "Information Technology", "industry": "Semiconductors"})
        self.assertEqual(sector, "Information Technology")
        self.assertEqual(theme, "半导体")

    def test_email_industry_labels_are_chinese_first_with_safe_fallback(self):
        self.assertEqual(radar.display_industry("Oil & Gas Refining & Marketing"), "石油炼化")
        self.assertEqual(radar.display_industry("Semiconductors"), "半导体")
        self.assertEqual(radar.display_industry("New English Industry"), "其他（New English Industry）")

    def test_sector_aggregation_counts_top10_members(self):
        sectors = radar.rank_sectors([row("A", theme="半导体", rsi=75), row("B", theme="半导体", rsi=73), row("C", theme="网络安全", rsi=80)])
        self.assertEqual(sectors[0]["sector_theme"], "半导体")
        self.assertEqual(sectors[0]["top10_count"], 2)

    def test_sector_ranking_gives_count_the_highest_weight(self):
        sectors = radar.rank_sectors([row("A", theme="半导体", rsi=60), row("B", theme="半导体", rsi=60), row("C", theme="网络安全", rsi=99, r20=50)])
        self.assertEqual(sectors[0]["sector_theme"], "半导体")

    def test_second_pass_selects_only_strong_sector_and_caps_at_three(self):
        rows = [row(f"S{i}", theme="半导体", rsi=60 + i) for i in range(4)] + [row("X", theme="网络安全")]
        sectors = [{"sector_theme": "半导体"}]
        watchlist = radar.build_watchlist(rows, sectors)
        self.assertEqual(len(watchlist), 3)
        self.assertTrue(all(item["sector_theme"] == "半导体" for item in watchlist))

    def test_rsi_above_80_is_overheated(self):
        self.assertEqual(radar.classify_candidate(row("HOT", rsi=81)), "过热不追")

    def test_distance_above_10_is_overheated(self):
        self.assertEqual(radar.classify_candidate(row("FAR", distance=10.1)), "过热不追")

    def test_missing_or_invalid_data_does_not_pollute_ranking(self):
        frame = pd.DataFrame({"Close": [1.0] * 204, "Volume": [1_000_000] * 204})
        self.assertIsNone(radar.compute_metrics("BAD", frame, {}))
        self.assertEqual(radar.rank_market_leaders([]), [])

    def test_no_candidates_still_generates_a_normal_daily_report(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(radar, "OUTPUT_DIR", Path(directory)), patch.object(radar, "LEADERS_CSV", Path(directory) / "market_leaders.csv"), patch.object(radar, "SECTORS_CSV", Path(directory) / "sector_strength.csv"), patch.object(radar, "WATCHLIST_CSV", Path(directory) / "watchlist.csv"), patch.object(radar, "MARKDOWN_PATH", Path(directory) / "report.md"), patch.object(radar, "TEXT_PATH", Path(directory) / "report.txt"):
            body = radar.write_reports([row("A")], [{"sector_theme": "半导体", "top10_count": 1, "avg_rsi": 65, "avg_return_20d": 8, "strength_score": 1, "market_leader": False}], [], "正常", "2026-09-06")
            self.assertIn("暂无处于理想区间", body)
            self.assertTrue((Path(directory) / "report.txt").exists())

    def test_email_report_is_compact_chinese_first(self):
        leaders = [row("MPC", theme="Oil & Gas Refining & Marketing", rsi=78), row("CRM", theme="Cloud Software", rsi=77)]
        leaders[0]["rsi14_5d_avg"] = 73
        leaders[1]["rsi14_5d_avg"] = 70
        sectors = [
            {"sector_theme": "Oil & Gas Refining & Marketing", "top10_count": 3, "avg_rsi": 76, "avg_persistent_strength": 76, "avg_return_20d": 20, "strength_score": 4000, "market_leader": True},
            {"sector_theme": "Cloud Software", "top10_count": 1, "avg_rsi": 70, "avg_persistent_strength": 70, "avg_return_20d": 10, "strength_score": 1700, "market_leader": False},
        ]
        body = radar.report_body(leaders, sectors, [{**leaders[1], "sector_theme": "Cloud Software", "status": "优先关注", "distance_ema20_pct": 3}], "正常", "2026-09-06")
        self.assertIn("🔥 当前主线", body)
        self.assertIn("石油炼化｜强度", body)
        self.assertIn("MPC｜强度", body)
        self.assertIn("RSI 78｜5日均 73｜石油炼化", body)
        self.assertIn("🎯 值得关注", body)
        self.assertNotIn("Oil & Gas Refining & Marketing", body)

    def test_smtp_temporary_failure_retries_once(self):
        attempts = []
        class FakeSMTP:
            def __init__(self, *args, **kwargs):
                attempts.append(1)
                # First delivery tries 465 then 587; both fail.  The outer
                # retry then succeeds without making a real network request.
                if len(attempts) <= 2:
                    raise OSError("temporary")
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def login(self, *args): pass
            def send_message(self, *args): pass
        env = {"SMTP_HOST": "smtp.example.test", "SMTP_PORT": "465", "SMTP_USER": "sender@test", "SMTP_PASSWORD": "secret", "EMAIL_TO": "to@test"}
        with patch.dict(os.environ, env, clear=False), patch("email_sender.smtplib.SMTP_SSL", FakeSMTP), patch("email_sender.smtplib.SMTP", FakeSMTP), patch("email_sender.time.sleep"):
            send_report_email("daily", [], 0, subject_override="test", retries=1)
        self.assertEqual(len(attempts), 3)

    def test_gmail_submission_port_587_uses_starttls(self):
        calls = []

        class FakeSMTP:
            def __init__(self, host, port, timeout):
                calls.append(("connect", host, port, timeout))
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def ehlo(self): calls.append(("ehlo",))
            def starttls(self): calls.append(("starttls",))
            def login(self, user, password): calls.append(("login", user, password))
            def send_message(self, message): calls.append(("send",))

        env = {"SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587", "SMTP_USER": "sender@example.test", "SMTP_PASSWORD": "app-password", "EMAIL_TO": "recipient@example.test"}
        with patch.dict(os.environ, env, clear=False), patch("email_sender.smtplib.SMTP", FakeSMTP):
            send_report_email("daily", [], 0, subject_override="test", retries=1)
        self.assertEqual(calls[0], ("connect", "smtp.gmail.com", 587, 30))
        self.assertIn(("starttls",), calls)
        self.assertIn(("send",), calls)

    def test_workflow_runs_v4_daily_email_and_archives_outputs(self):
        workflow = Path(".github/workflows/strong_pullback_screener.yml").read_text(encoding="utf-8")
        self.assertIn("python radar.py --send-email", workflow)
        self.assertIn("0 22 * * 1-5", workflow)
        self.assertIn("market_leaders.csv", workflow)
        self.assertIn("market_radar_report.txt", workflow)
        self.assertIn("SMTP_HOST: ${{ secrets.SMTP_HOST }}", workflow)
        self.assertIn("SMTP_PORT: ${{ secrets.SMTP_PORT }}", workflow)


if __name__ == "__main__":
    unittest.main()
