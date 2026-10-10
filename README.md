# 美股强势板块雷达

V4 每个美股交易日收盘后扫描股票池，找出 RSI 强势 Top10、市场主线板块，并在强板块中筛选趋势强但未明显过热的观察标的。它不是自动交易系统，也不构成投资建议。

## 工作流

`股票池（S&P 500 + NASDAQ 100，去重） → RSI 强势 Top10 → 板块强度 → 强板块二次选股 → 每日日报邮件`

- 日线指标：RSI(14)、5/20/60 日涨幅、EMA20/50/200、距 EMA20、20 日均量。
- Top10 要求 `Close > EMA20 > EMA50`、RSI > 50、完整且流动性正常的数据。
- 板块采用项目既有细主题优先，Wikipedia 指数表的行业/sector 为 fallback；成分股快照 7 天内不重复抓取，不会为数百只股票逐个查询昂贵接口。
- 已提交的成分股快照在 `data/index_constituents.json`（当前 503 只，见文件里的 `fetched_at` / `symbol_count`）。没有更新的运行时缓存时，从 `fetched_at` 起 7 天内直接用它，超过 7 天再请求现有的两个 Wikipedia 页面。Actions 若还原了仍在 7 天内的 `.cache/market_metadata.json`，同样不会重复抓取。请求失败，或结果不可信（股票数不在 450–700，或 AAPL、MSFT、AMZN、JPM、XOM 里能对上的不足 3 只）时，继续用这份快照，不用残缺结果替换股票池。成功的刷新写到 `.cache/market_metadata.json`（已 gitignore）；失败的抓取不会把运行时缓存标成新的。
- `Nasdaq-100` 词条目前没有代码表，成功抓取和这份快照都是标普 500 的 503 只。数据源 URL 没改，所以不会把另一页的纳斯达克 100 成分突然并进股票池。不在标普里的主题股仍只来自原有 `THEME_MAP`。
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

## 自动运行

这个仓库就是给自动跑设计的，定时和入口都没有在稳定性改动里调整。

- 2026-06-30 起，`.github/workflows/strong_pullback_screener.yml` 就同时有手动 `workflow_dispatch` 和定时 `0 22 * * 1-5`（UTC 周一至周五 22:00，美股常规收盘之后）。
- 最初定时任务跑的是 `python main.py --send-email`。中间曾经改成先跑 `main.py`、再由 `change_notifier.py` 只在 A 榜状态变化时发信。
- 2026-09-06 起，同一条定时任务改为 `python radar.py --send-email`。cron、手动触发和生产入口都停在这里。
- `main.py` 和 `change_notifier.py` 还在仓库里，当前工作流不调用它们。V4 日报仍然每次运行都发信，不是 V3 那种“有变化才发”。
- 单元测试原先不在 CI 里。现在在安装依赖之后、雷达步骤之前跑 `python -m unittest discover -s tests -v`。这一步不注入 SMTP Secrets。测试通过时，定时和手动运行仍接着跑原来的 `radar.py --send-email`。测试失败则不发股票日报，改用同一套 SMTP 配置另发一封“单元测试失败、日报未发送”的告警，并且工作流失败。
- Pull request 只跑单元测试，不跑扫描、不发日报。定时 `0 22 * * 1-5` 和手动运行不变。

## Actions

工作流：`.github/workflows/strong_pullback_screener.yml`

- 手动运行：GitHub → Actions → **US Market Strength Radar V4** → Run workflow
- 定时：UTC 周一至周五 22:00（美股收盘后）
- 入口：`python radar.py --send-email`

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

`tests/test_radar.py` 用标准库 `unittest`，不需要再装 pytest。`requirements.txt` 只钉直接依赖，上界是当前能跑通测试的下一个大版本。

旧 V3 的 DXDX/A-B-C/change-only 代码保留为遗留实现，但 V4 工作流和邮件不依赖它。
