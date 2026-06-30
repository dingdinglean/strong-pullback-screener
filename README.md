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
- `output/strong_pullback_report.txt`

The email body uses the same compact plain text as `strong_pullback_report.txt`.
It does not send the full Markdown report as the message body.
Email attachments include only `strong_pullback_report.txt`; CSV and Markdown are kept in GitHub Actions artifacts for review and debugging.

## V3 Signal Rules

V3 uses two indicator sources with different jobs:

- `cd.docx`: real MACD bottom/sell signals.
  - `bottom_signal = DXDX`
  - `sell_signal = DBJGXC`
- `NXCD002`: blue/yellow trend channel.
  - Blue channel: `UP1 = EMA(HIGH, 23)`, `DW1 = EMA(LOW, 23)`
  - Yellow channel: `UP2 = EMA(HIGH, 89)`, `DW2 = EMA(LOW, 89)`

The formulas are implemented in code. No external `bottom_signals.csv` is required.

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
- daily blue channel above yellow channel
- price near daily blue lower edge `DW1`
- 2H real `DXDX` bottom signal
- 1H real `DXDX` only as early warning
- proxy warning only as auxiliary observation, never as A-list evidence

It avoids weak-stock bottom fishing, crash rebounds, and overheated chase entries.
