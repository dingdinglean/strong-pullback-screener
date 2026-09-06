# 美股强势板块雷达

V4 每个美股交易日收盘后扫描股票池，找出 RSI 强势 Top10、市场主线板块，并在强板块中筛选趋势强但未明显过热的观察标的。它不是自动交易系统，也不构成投资建议。

## 工作流

`股票池（S&P 500 + NASDAQ 100，去重） → RSI 强势 Top10 → 板块强度 → 强板块二次选股 → 每日日报邮件`

- 日线指标：RSI(14)、5/20/60 日涨幅、EMA20/50/200、距 EMA20、20 日均量。
- Top10 要求 `Close > EMA20 > EMA50`、RSI > 50、完整且流动性正常的数据。
- 板块采用项目既有细主题优先，Wikipedia 指数表的行业/sector 为 fallback；元数据缓存 7 天，不会为数百只股票逐个查询昂贵接口。
- 强板块按 Top10 席位（最高权重）、平均 RSI、平均 20 日涨幅排序，输出最多前三名；3 席以上标记市场主线。
- 二次选股要求趋势向上、RSI ≥ 55、20 日动量为正。RSI > 80 或距 EMA20 > 10% 标记“过热不追”，不得列为“优先关注”。每个板块最多三只。
- 单个股票失败只跳过并记录 warning；有效数据明显不足时输出“数据异常”日报，不制造排名。

## 输出与邮件

每次正常工作日运行均生成并发送日报，即使没有优先关注标的：

- `output/market_leaders.csv`
- `output/sector_strength.csv`
- `output/watchlist.csv`
- `output/market_radar_report.md`
- `output/market_radar_report.txt`

Actions 会把上述文件作为 artifact 保存。SMTP 临时失败会在 Python 内自动重试一次，并在 Actions log 输出 warning；仍失败则工作流明确失败，不会静默吞掉。

## Actions

工作流：`.github/workflows/strong_pullback_screener.yml`

- 手动运行：GitHub → Actions → **US Market Strength Radar V4** → Run workflow
- 定时：UTC 周一至周五 22:00（美股收盘后）

邮件投递完全由 GitHub Actions Secrets 提供，仓库不会保存邮箱地址或应用密码。使用 Gmail 时设置：

- `SMTP_HOST`：`smtp.gmail.com`
- `SMTP_PORT`：`587`（STARTTLS）
- `SMTP_USER`：Gmail 发件地址
- `SMTP_PASSWORD`：Gmail 应用密码
- `EMAIL_TO`：日报收件地址

代码会先使用 Secret 指定的安全传输方式；临时 SMTP 失败会重试一次，且仍失败时保留报告 artifact 并让工作流明确失败。

## 本地测试

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python radar.py
```

旧 V3 的 DXDX/A-B-C/change-only 代码保留为遗留实现，但 V4 工作流和邮件不依赖它。
