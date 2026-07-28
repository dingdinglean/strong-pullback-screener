"""Send compact strong-pullback emails only when the actionable state changes.

The full screener still writes CSV/Markdown/TXT reports on every run. This
module reads the CSV after the scan and sends email only for these events:

- a symbol newly enters A-list;
- a symbol upgrades from B-list to A-list;
- a new real 2H DXDX signal appears;
- a previous A-list symbol leaves A-list.

B/C lists and unchanged daily observations stay in GitHub Actions logs and
artifacts, so they no longer generate repetitive emails.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

CSV_PATH = Path(os.getenv("STRONG_PULLBACK_CSV", "output/strong_pullback_candidates.csv"))
REPORT_PATH = Path(os.getenv("STRONG_PULLBACK_REPORT", "output/strong_pullback_report.md"))
STATE_PATH = Path(os.getenv("STRONG_PULLBACK_STATE_PATH", "strong_pullback_state.json"))
STATE_VERSION = 1
MAX_LINES_PER_SECTION = 5


@dataclass(frozen=True)
class Candidate:
    symbol: str
    list_name: str
    theme: str
    distance_dw1_pct: float
    two_hour_bottom_signal: bool
    setup_class: str
    downgrade_reason: str
    price_invalid: bool
    data_warning: str

    @property
    def reliable(self) -> bool:
        return self.list_name != "DATA_WARNING" and not self.price_invalid

    def state_dict(self) -> dict[str, object]:
        return {
            "list_name": self.list_name,
            "theme": self.theme,
            "distance_dw1_pct": round(self.distance_dw1_pct, 4),
            "two_hour_bottom_signal": self.two_hour_bottom_signal,
            "setup_class": self.setup_class,
        }


@dataclass(frozen=True)
class Changes:
    new_a: tuple[Candidate, ...]
    upgraded_b_to_a: tuple[Candidate, ...]
    new_dxdx: tuple[Candidate, ...]
    invalidated_a: tuple[tuple[str, Candidate | None], ...]

    @property
    def has_changes(self) -> bool:
        return any((self.new_a, self.upgraded_b_to_a, self.new_dxdx, self.invalidated_a))


def _as_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_float(value: object) -> float:
    try:
        return float(str(value or "0").strip())
    except (TypeError, ValueError):
        return 0.0


def load_candidates(path: Path = CSV_PATH) -> dict[str, Candidate]:
    if not path.exists():
        raise RuntimeError(f"candidate CSV not found: {path}")

    candidates: dict[str, Candidate] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            symbol = (row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            candidates[symbol] = Candidate(
                symbol=symbol,
                list_name=(row.get("list") or "").strip().upper(),
                theme=(row.get("theme") or "未分类").strip() or "未分类",
                distance_dw1_pct=_as_float(row.get("distance_blue_lower_abs_pct")),
                two_hour_bottom_signal=_as_bool(row.get("two_hour_bottom_signal")),
                setup_class=(row.get("setup_class") or "").strip(),
                downgrade_reason=(row.get("downgrade_reason") or "").strip(),
                price_invalid=_as_bool(row.get("price_invalid")),
                data_warning=(row.get("data_warning") or "").strip(),
            )

    # A normal run screens the market's top-strength set. An empty file is more
    # likely a data/system failure than a genuine zero-candidate day; do not
    # erase state or issue false A-list invalidation alerts in that case.
    if not candidates:
        raise RuntimeError(f"candidate CSV contains no rows: {path}")
    return candidates


def load_state(path: Path = STATE_PATH) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    symbols = payload.get("symbols", {}) if isinstance(payload, dict) else {}
    if not isinstance(symbols, dict):
        return {}
    return {
        str(symbol).upper(): value
        for symbol, value in symbols.items()
        if isinstance(value, dict)
    }


def failed_symbols_from_report(path: Path = REPORT_PATH) -> set[str]:
    """Find symbols with explicit data failures so temporary errors stay quiet."""
    if not path.exists():
        return set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()

    failed: set[str] = set()
    keywords = ("数据不可用", "价格异常", "数据异常", "禁止进入A榜")
    pattern = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0-9.\-]{0,9})[：:]")
    for line in text.splitlines():
        if not any(keyword in line for keyword in keywords):
            continue
        match = pattern.search(line)
        if match:
            failed.add(match.group(1).upper())
    return failed


def detect_changes(
    current: dict[str, Candidate],
    previous: dict[str, dict[str, object]],
    failed_symbols: set[str] | None = None,
) -> Changes:
    failed_symbols = failed_symbols or set()
    reliable = {symbol: row for symbol, row in current.items() if row.reliable}

    new_a: list[Candidate] = []
    upgraded: list[Candidate] = []
    new_dxdx_all: list[Candidate] = []

    for symbol, row in reliable.items():
        old = previous.get(symbol, {})
        old_list = str(old.get("list_name") or "").upper()
        old_dxdx = bool(old.get("two_hour_bottom_signal", False))

        if row.list_name == "A" and old_list != "A":
            if old_list == "B":
                upgraded.append(row)
            else:
                new_a.append(row)

        if row.two_hour_bottom_signal and not old_dxdx:
            new_dxdx_all.append(row)

    # An A-entry normally also carries the new 2H signal. Keep the message
    # compact by tagging it on the A line instead of repeating the symbol.
    already_featured = {row.symbol for row in [*new_a, *upgraded]}
    new_dxdx = [row for row in new_dxdx_all if row.symbol not in already_featured]

    invalidated: list[tuple[str, Candidate | None]] = []
    for symbol, old in previous.items():
        if str(old.get("list_name") or "").upper() != "A":
            continue
        if symbol in failed_symbols:
            continue
        row = reliable.get(symbol)
        if row is None or row.list_name != "A":
            invalidated.append((symbol, row))

    sort_key = lambda row: (row.distance_dw1_pct, row.symbol)
    new_a.sort(key=sort_key)
    upgraded.sort(key=sort_key)
    new_dxdx.sort(key=sort_key)
    invalidated.sort(key=lambda item: item[0])

    return Changes(
        new_a=tuple(new_a),
        upgraded_b_to_a=tuple(upgraded),
        new_dxdx=tuple(new_dxdx),
        invalidated_a=tuple(invalidated),
    )


def build_snapshot(
    current: dict[str, Candidate],
    previous: dict[str, dict[str, object]],
    failed_symbols: set[str] | None = None,
) -> dict[str, dict[str, object]]:
    failed_symbols = failed_symbols or set()
    snapshot = {
        symbol: row.state_dict()
        for symbol, row in current.items()
        if row.reliable
    }

    # Preserve the last known state when the report explicitly says a symbol's
    # data is broken. That prevents a temporary fetch error from creating a
    # false A-list exit and a false re-entry on the following run.
    for symbol in failed_symbols:
        if symbol in previous:
            snapshot[symbol] = previous[symbol]
    for symbol, row in current.items():
        if not row.reliable and symbol in previous:
            snapshot[symbol] = previous[symbol]
    return snapshot


def _candidate_line(row: Candidate, include_new_signal: bool = False) -> str:
    signal = "｜新2H DXDX" if include_new_signal and row.two_hour_bottom_signal else ""
    return f"{row.symbol}｜{row.theme}｜距DW1 {row.distance_dw1_pct:.1f}%{signal}"


def _append_section(lines: list[str], title: str, entries: list[str]) -> None:
    if not entries:
        return
    lines.append(title)
    lines.extend(entries[:MAX_LINES_PER_SECTION])
    overflow = len(entries) - MAX_LINES_PER_SECTION
    if overflow > 0:
        lines.append(f"另有 {overflow} 只，详见GitHub运行报告。")


def build_email_body(changes: Changes) -> str:
    lines = ["【强势股回踩提醒】"]
    _append_section(
        lines,
        "新增A榜：",
        [_candidate_line(row, include_new_signal=True) for row in changes.new_a],
    )
    _append_section(
        lines,
        "升级：",
        [f"{_candidate_line(row, include_new_signal=True)}｜B→A" for row in changes.upgraded_b_to_a],
    )
    _append_section(
        lines,
        "新2H DXDX：",
        [f"{_candidate_line(row)}｜当前{row.list_name or '观察'}榜" for row in changes.new_dxdx],
    )

    invalidated_lines: list[str] = []
    for symbol, row in changes.invalidated_a:
        if row is None:
            invalidated_lines.append(f"{symbol}｜离开A榜/观察范围")
        else:
            target = row.list_name or "观察"
            invalidated_lines.append(
                f"{symbol}｜A→{target}｜距DW1 {row.distance_dw1_pct:.1f}%"
            )
    _append_section(lines, "失效：", invalidated_lines)
    return "\n".join(lines) + "\n"


def build_email_subject(changes: Changes) -> str:
    parts = []
    if changes.new_a:
        parts.append(f"新增A {len(changes.new_a)}")
    if changes.upgraded_b_to_a:
        parts.append(f"升级 {len(changes.upgraded_b_to_a)}")
    if changes.new_dxdx:
        parts.append(f"DXDX {len(changes.new_dxdx)}")
    if changes.invalidated_a:
        parts.append(f"失效 {len(changes.invalidated_a)}")
    return "【强势回踩】" + "｜".join(parts)


def save_state(
    snapshot: dict[str, dict[str, object]],
    path: Path = STATE_PATH,
) -> None:
    payload = {
        "version": STATE_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": snapshot,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp_path.replace(path)


def main() -> int:
    try:
        current = load_candidates()
        previous = load_state()
        failed_symbols = failed_symbols_from_report()
        changes = detect_changes(current, previous, failed_symbols)
        snapshot = build_snapshot(current, previous, failed_symbols)
    except Exception as exc:
        print(f"Change notifier aborted: {exc}", file=sys.stderr)
        return 1

    print(
        "Material changes: "
        f"new_A={len(changes.new_a)}, "
        f"B_to_A={len(changes.upgraded_b_to_a)}, "
        f"new_DXDX={len(changes.new_dxdx)}, "
        f"invalidated_A={len(changes.invalidated_a)}"
    )

    if not changes.has_changes:
        save_state(snapshot)
        print("No actionable change; email skipped. Full report remains in logs/artifacts.")
        return 0

    body = build_email_body(changes)
    subject = build_email_subject(changes)
    print(body)

    try:
        from email_sender import EmailConfigError, send_report_email

        current_a_count = sum(1 for row in current.values() if row.list_name == "A")
        send_report_email(
            report_body=body,
            report_paths=[],
            a_candidate_count=current_a_count,
            subject_override=subject,
        )
    except EmailConfigError as exc:
        print(f"Missing SMTP config: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Do not advance state after a failed email. The same event can retry on
        # the next scheduled run instead of being silently lost.
        print(f"Change-only email failed: {exc!r}", file=sys.stderr)
        return 1

    save_state(snapshot)
    print(f"Saved alert state to {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
