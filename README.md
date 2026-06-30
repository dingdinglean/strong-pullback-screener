# Strong Trend Pullback Screener

This project runs a strong-trend pullback stock screener, writes CSV and Markdown reports, and can email the report through SMTP.

## Local Run

```bash
pip install -r requirements.txt
python main.py --send-email --top-buy 10 --top-watch 20 --min-rs-score 70
```

Without arguments, defaults are:

- `top_buy = 10`
- `top_watch = 20`
- `min_rs_score = 70`
- `send_email = SEND_EMAIL` environment variable

Reports are written to:

- `output/strong_pullback_candidates.csv`
- `output/strong_pullback_report.md`

## Custom Bottom Signals

V2 uses daily EMA23/EMA89 for position, 2H bottom signals for A-list entries, and 1H bottom signals only as early warnings.

If you can export your own indicator's bottom signals, provide a CSV with:

```csv
symbol,timeframe,bottom_signal
MU,2H,true
MU,1H,true
AMD,2H,false
AMD,1H,true
```

Run with:

```bash
python main.py --bottom-signal-file data/bottom_signals.csv
```

If `--bottom-signal-file` is not passed, the script automatically looks for:

- `data/bottom_signals.csv`
- `signals/bottom_signals.csv`
- `bottom_signals.csv`

If no signal file is found, the system uses a proxy signal based on RSI recovery and reclaiming EMA23. Proxy signals are marked in the report and are not treated as real bottom signals.

## GitHub Actions

The workflow is in `.github/workflows/strong_pullback_screener.yml`.

Manual run:

`Actions -> Strong Trend Pullback Screener -> Run workflow`

Scheduled run:

- UTC Monday to Friday 22:00
- Beijing time Tuesday to Saturday 06:00
- Roughly after the US market close

## GitHub Secrets

Set these at:

`Settings -> Secrets and variables -> Actions -> New repository secret`

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `EMAIL_TO`
- `EMAIL_SUBJECT`

Example:

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-sender@example.com
SMTP_PASSWORD=your email app password
EMAIL_TO=your-recipient@example.com
EMAIL_SUBJECT=Strong Pullback Screener Report
```

Use an email app password for `SMTP_PASSWORD`, not the normal login password.

## Screening Logic

The market itself identifies strong themes by looking at the top 50 relative-strength stocks in the universe and grouping them by theme.

The screener focuses on:

- strong themes
- strong individual stocks
- daily EMA23 above EMA89
- price near daily EMA23
- 2H bottom confirmation
- 1H early warning only

It avoids weak-stock bottom fishing, crash rebounds, and overheated chase entries.
