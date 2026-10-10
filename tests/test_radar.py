from __future__ import annotations

import os
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import radar
from email_sender import send_report_email, send_test_failure_alert


def row(symbol: str, *, rsi: float = 65, rsi5avg: float | None = None, rsi5change: float = 2, days_above_60: int = 7, r20: float = 8, r60: float = 18, distance: float = 4, theme: str = "半导体", us_listed: bool = True, security_type: str = "equity"):
    return {
        "symbol": symbol, "close": 110.0, "rsi14": rsi, "rsi14_5d_avg": rsi if rsi5avg is None else rsi5avg,
        "rsi14_change_5d": rsi5change, "rsi_above_60_days_10d": days_above_60, "return_5d": 3.0,
        "return_20d": r20, "return_60d": r60, "ema20": 105.0, "ema50": 100.0,
        "ema200": 90.0, "distance_ema20_pct": distance, "avg_volume_20d": 2_000_000,
        "sector": "Information Technology", "industry": theme,
        "us_listed": us_listed, "security_type": security_type,
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
        persistent = row("STDY", rsi=70, rsi5avg=70, rsi5change=2, days_above_60=10)
        self.assertEqual([item["symbol"] for item in radar.rank_market_leaders([spike, persistent])], ["STDY", "SPIKE"])

    def test_ten_day_rsi_persistence_has_an_advantage(self):
        persistent = row("TEN", rsi=68, rsi5avg=68, rsi5change=1, days_above_60=10)
        brief = row("ONE", rsi=68, rsi5avg=68, rsi5change=1, days_above_60=1)
        self.assertGreater(radar.persistent_strength_score(persistent), radar.persistent_strength_score(brief))

    def test_rsi_above_80_does_not_gain_unlimited_score(self):
        capped = row("CAP80", rsi=80, rsi5avg=80, rsi5change=10, days_above_60=10)
        extreme = row("CAP95", rsi=95, rsi5avg=95, rsi5change=30, days_above_60=10)
        self.assertEqual(radar.persistent_strength_score(capped), radar.persistent_strength_score(extreme))

    def test_falling_five_day_rsi_lowers_ranking(self):
        rising = row("RISE", rsi=72, rsi5avg=70, rsi5change=5, days_above_60=10)
        falling = row("FALL", rsi=72, rsi5avg=70, rsi5change=-5, days_above_60=10)
        self.assertEqual([item["symbol"] for item in radar.rank_market_leaders([falling, rising])], ["RISE", "FALL"])

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
        rows = [row(symbol, theme="半导体", rsi=60 + index) for index, symbol in enumerate(("SAA", "SAB", "SAC", "SAD"))] + [row("X", theme="网络安全")]
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

    def test_top10_only_contains_us_listed_equity_or_adr(self):
        nyse = row("MPC", rsi=70)
        adr = row("TSM", rsi=69, security_type="adr")
        hong_kong = row("0700.HK", rsi=95, us_listed=False)
        etf = row("SPY", rsi=95, security_type="etf")
        leaders = radar.rank_market_leaders([hong_kong, etf, adr, nyse])
        self.assertEqual([item["symbol"] for item in leaders], ["MPC", "TSM"])
        self.assertTrue(all(radar.is_us_listed_equity(item["symbol"], item) for item in leaders))

    def test_watchlist_only_contains_us_listed_equity_or_adr(self):
        us_stock = row("NVDA", theme="半导体")
        adr = row("ASML", theme="半导体", security_type="adr")
        fund = row("QQQ", theme="半导体", security_type="etf")
        overseas = row("600519.SS", theme="半导体", us_listed=False)
        watchlist = radar.build_watchlist([us_stock, adr, fund, overseas], [{"sector_theme": "半导体"}])
        self.assertEqual({item["symbol"] for item in watchlist}, {"NVDA", "ASML"})
        self.assertTrue(all(radar.is_us_listed_equity(item["symbol"], item) for item in watchlist))

    def test_invalid_metadata_cache_cannot_expand_to_non_us_markets(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "market_metadata.json"
            cache.write_text(json.dumps({"symbols": {
                "0700.HK": {"us_listed": True, "security_type": "equity", "cached_at": "2026-09-06T00:00:00+00:00"},
                "OTCM": {"us_listed": False, "security_type": "equity", "cached_at": "2026-09-06T00:00:00+00:00"},
            }}), encoding="utf-8")
            with patch.object(radar, "fetch_universe_metadata", return_value={}):
                universe = radar.build_universe(cache)
            self.assertNotIn("0700.HK", universe)
            self.assertNotIn("OTCM", universe)
            self.assertTrue(all(radar.is_us_listed_equity(symbol, metadata) for symbol, metadata in universe.items()))

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
        self.assertLess(
            workflow.index("python -m unittest discover -s tests -v"),
            workflow.index("python radar.py --send-email"),
        )
        self.assertNotIn("python main.py", workflow)
        self.assertNotIn("change_notifier.py", workflow)
        self.assertIn("pull_request:", workflow)
        steps = _workflow_steps(workflow)
        unit_tests = steps["Run unit tests"]
        self.assertIn("python -m unittest discover -s tests -v", unit_tests)
        self.assertNotIn("secrets.", unit_tests)
        self.assertNotIn("SMTP_", unit_tests)
        alert = steps["Send test-failure alert"]
        self.assertIn("failure()", alert)
        self.assertIn("steps.unit-tests.conclusion == 'failure'", alert)
        self.assertIn("send_test_failure_alert", alert)
        self.assertNotIn("radar.py", alert)
        self.assertIn("secrets.SMTP_PASSWORD", alert)
        self.assertLess(workflow.index("Send test-failure alert"), workflow.index("Run V4 radar and send daily report"))
        radar_step = steps["Run V4 radar and send daily report"]
        self.assertIn("python radar.py --send-email", radar_step)
        self.assertIn("github.event_name != 'pull_request'", radar_step)
        self.assertNotIn("always()", radar_step)

    def test_failure_alert_uses_smtp_and_is_not_a_stock_report(self):
        captured = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout):
                captured["connect"] = (host, port, timeout)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def ehlo(self):
                pass

            def starttls(self):
                captured["starttls"] = True

            def login(self, user, password):
                captured["login"] = (user, password)

            def send_message(self, message):
                captured["subject"] = message["Subject"]
                captured["body"] = message.get_content()

        env = {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "587",
            "SMTP_USER": "sender@example.test",
            "SMTP_PASSWORD": "secret",
            "EMAIL_TO": "to@example.test",
        }
        with patch.dict(os.environ, env, clear=False), patch("email_sender.smtplib.SMTP", FakeSMTP), patch("email_sender.smtplib.SMTP_SSL", FakeSMTP), patch("email_sender.time.sleep"):
            send_test_failure_alert()
        self.assertEqual(captured["connect"], ("smtp.example.test", 587, 30))
        self.assertIn("单元测试失败", captured["subject"])
        self.assertIn("日报未发送", captured["body"])
        self.assertNotIn("当前主线", captured["body"])
        self.assertNotIn("持续强势", captured["body"])

    def test_size_gate_skips_a_leading_short_symbol_table(self):
        short = _constituent_frame(["AAA", "BBB"])
        full_symbols = _synthetic_symbols(503)
        full = _constituent_frame(full_symbols)
        parsed = radar._metadata_from_tables([short, full], radar.WIKI_SOURCES[0])
        self.assertEqual(set(parsed), set(full_symbols))
        self.assertEqual(radar._metadata_from_tables([short], radar.WIKI_SOURCES[0]), {})
        self.assertEqual(radar._metadata_from_tables([full], radar.WIKI_SOURCES[1]), {})
        nasdaq = _constituent_frame(_synthetic_symbols(101))
        self.assertEqual(len(radar._metadata_from_tables([nasdaq], radar.WIKI_SOURCES[1])), 101)

    def test_plausible_universe_rejects_short_huge_and_anchorless_lists(self):
        self.assertFalse(radar.is_plausible_universe(_synthetic_metadata(radar.MIN_CONSTITUENTS - 1)))
        self.assertTrue(radar.is_plausible_universe(_synthetic_metadata(radar.MIN_CONSTITUENTS)))
        self.assertTrue(radar.is_plausible_universe(_synthetic_metadata(radar.MAX_CONSTITUENTS)))
        self.assertFalse(radar.is_plausible_universe(_synthetic_metadata(radar.MAX_CONSTITUENTS + 1)))
        anchorless = _synthetic_metadata(radar.MIN_CONSTITUENTS + len(radar.ANCHOR_SYMBOLS))
        for symbol in radar.ANCHOR_SYMBOLS:
            del anchorless[symbol]
        self.assertGreaterEqual(len(anchorless), radar.MIN_CONSTITUENTS)
        self.assertFalse(radar.is_plausible_universe(anchorless))

    def test_fetch_keeps_in_range_table_and_rejects_only_short_tables(self):
        short = _constituent_frame(["AAA", "BBB"])
        full_symbols = _synthetic_symbols(503)
        full = _constituent_frame(full_symbols)

        class Body:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"<html></html>"

        with patch("radar.urlopen", return_value=Body()), patch("pandas.read_html", side_effect=[[short, full], [short]]):
            accepted = radar.fetch_universe_metadata()
        self.assertEqual(set(accepted), set(full_symbols))

        with patch("radar.urlopen", return_value=Body()), patch("pandas.read_html", return_value=[short]):
            self.assertEqual(radar.fetch_universe_metadata(), {})

    def test_committed_snapshot_is_a_plausible_us_list(self):
        raw = json.loads(radar.CONSTITUENT_CACHE.read_text(encoding="utf-8"))
        symbols = raw["symbols"]
        self.assertEqual(raw["symbol_count"], len(symbols))
        self.assertIsNotNone(radar._parse_stamp(raw["fetched_at"]))
        self.assertTrue(radar.is_plausible_universe(symbols))
        self.assertTrue(all(radar.is_us_listed_equity(symbol, meta) for symbol, meta in symbols.items()))
        self.assertTrue(all("." not in symbol for symbol in symbols))

    def test_fresh_snapshot_skips_wikipedia_and_keeps_theme_priority(self):
        raw = json.loads(radar.CONSTITUENT_CACHE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            constituent = _stage_constituents(Path(directory), raw["symbols"], _iso(timedelta(0)))
            before = constituent.read_text(encoding="utf-8")
            runtime = Path(directory) / "market_metadata.json"
            with patch.object(radar, "fetch_universe_metadata", side_effect=AssertionError("wikipedia")):
                universe = radar.build_universe(runtime, constituent)
            self.assertEqual(constituent.read_text(encoding="utf-8"), before)
            self.assertTrue(runtime.exists())
            for symbol in raw["symbols"]:
                self.assertIn(symbol, universe)
            self.assertEqual(universe["NVDA"]["industry"], "半导体")
            self.assertEqual(universe["TSM"]["security_type"], "adr")

    def test_failed_or_implausible_refresh_falls_back_without_poisoning_runtime_cache(self):
        raw = json.loads(radar.CONSTITUENT_CACHE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            constituent = _stage_constituents(Path(directory), raw["symbols"], _iso(timedelta(days=30)))
            runtime = Path(directory) / "market_metadata.json"
            with patch.object(radar, "fetch_universe_metadata", side_effect=OSError("down")):
                failed = radar.build_universe(runtime, constituent)
            self.assertFalse(runtime.exists())
            for symbol in raw["symbols"]:
                self.assertIn(symbol, failed)

            fake = {"FAKE1": {"sector": "Energy", "industry": "Oil", "us_listed": True, "security_type": "equity"}}
            with patch.object(radar, "fetch_universe_metadata", return_value=fake):
                rejected = radar.build_universe(runtime, constituent)
            self.assertNotIn("FAKE1", rejected)
            self.assertIn("AAPL", rejected)
            self.assertFalse(runtime.exists())

    def test_plausible_refresh_replaces_a_stale_snapshot(self):
        raw = json.loads(radar.CONSTITUENT_CACHE.read_text(encoding="utf-8"))
        refresh_symbols = _synthetic_symbols(480, extra=("NEWQ",))
        refresh = {symbol: _equity() for symbol in refresh_symbols}
        dropped = next(
            symbol
            for symbol in sorted(raw["symbols"])
            if symbol not in radar.THEME_MAP and symbol not in refresh
        )
        with tempfile.TemporaryDirectory() as directory:
            constituent = _stage_constituents(Path(directory), raw["symbols"], _iso(timedelta(days=30)))
            runtime = Path(directory) / "market_metadata.json"
            with patch.object(radar, "fetch_universe_metadata", return_value=refresh):
                universe = radar.build_universe(runtime, constituent)
            self.assertIn("NEWQ", universe)
            self.assertNotIn(dropped, universe)
            self.assertEqual(universe["NVDA"]["industry"], "半导体")
            self.assertTrue(runtime.exists())
            self.assertNotIn(dropped, json.loads(runtime.read_text(encoding="utf-8"))["symbols"])

    def test_fresh_runtime_cache_skips_fetch_when_committed_snapshot_is_stale(self):
        raw = json.loads(radar.CONSTITUENT_CACHE.read_text(encoding="utf-8"))
        stamp = _iso(timedelta(0))
        runtime_symbols = {symbol: {**_equity(), "cached_at": stamp} for symbol in _synthetic_symbols(480, extra=("RUNQ",))}
        with tempfile.TemporaryDirectory() as directory:
            constituent = _stage_constituents(Path(directory), raw["symbols"], _iso(timedelta(days=30)))
            runtime = Path(directory) / "market_metadata.json"
            radar.save_metadata_cache(runtime_symbols, runtime)
            with patch.object(radar, "fetch_universe_metadata", side_effect=AssertionError("wikipedia")):
                universe = radar.build_universe(runtime, constituent)
            self.assertIn("RUNQ", universe)

    def test_committed_loader_drops_non_us_symbols(self):
        symbols = {symbol: _equity() for symbol in _synthetic_symbols(radar.MIN_CONSTITUENTS)}
        symbols["0700.HK"] = {"us_listed": True, "security_type": "equity", "sector": "其他", "industry": "其他"}
        symbols["OTCM"] = {"us_listed": False, "security_type": "equity", "sector": "其他", "industry": "其他"}
        with tempfile.TemporaryDirectory() as directory:
            path = _stage_constituents(Path(directory), symbols, _iso(timedelta(days=30)))
            loaded = radar.load_constituent_cache(path)
        self.assertNotIn("0700.HK", loaded)
        self.assertNotIn("OTCM", loaded)
        self.assertIn("AAPL", loaded)


def _workflow_steps(text: str) -> dict[str, str]:
    parts = text.split("\n      - name: ")
    steps: dict[str, str] = {}
    for part in parts[1:]:
        name, _, body = part.partition("\n")
        steps[name.strip()] = body
    return steps


def _equity() -> dict[str, object]:
    return {
        "sector": "Industrials",
        "industry": "Industrial Conglomerates",
        "us_listed": True,
        "security_type": "equity",
    }


def _letter_ticker(index: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return alphabet[(index // 676) % 26] + alphabet[(index // 26) % 26] + alphabet[index % 26]


def _synthetic_symbols(count: int, extra: tuple[str, ...] = ()) -> list[str]:
    symbols = list(dict.fromkeys([*sorted(radar.ANCHOR_SYMBOLS), *extra]))
    index = 0
    while len(symbols) < count:
        ticker = _letter_ticker(index)
        index += 1
        if ticker not in symbols:
            symbols.append(ticker)
    return symbols[:count]


def _synthetic_metadata(count: int) -> dict[str, dict[str, object]]:
    return {symbol: _equity() for symbol in _synthetic_symbols(count)}


def _constituent_frame(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Symbol": symbols,
            "GICS Sector": ["Industrials"] * len(symbols),
            "GICS Sub-Industry": ["Industrial Conglomerates"] * len(symbols),
        }
    )


def _iso(age: timedelta) -> str:
    return (datetime.now(timezone.utc) - age).isoformat()


def _stage_constituents(directory: Path, symbols: dict, fetched_at: str) -> Path:
    path = directory / "index_constituents.json"
    path.write_text(
        json.dumps({"fetched_at": fetched_at, "symbol_count": len(symbols), "symbols": symbols}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
