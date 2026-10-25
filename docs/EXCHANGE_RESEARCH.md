# Exchange API research and observed access

Research date: 2026-10-25. Read-only public endpoint probes informed these capability notes. Raw operational probe records are not redistributed. This is API capability research; it is not evidence that calibration or predictive accuracy has been established.

## Non-negotiable accounting and normalization

An outstanding matched contract represents one long and one short position. Maintain a **single-sided matched-contract OI quantity**, then set gross long OI and gross short OI equal to that quantity. Their sum is twice matched OI. Never divide a correctly single-sided exchange OI by two, and never infer unequal aggregate long/short OI from an account ratio.

| Venue / BTC perpetual | Native matched-OI quantity | Trade-volume quantity | Conversion for this linear contract |
| --- | --- | --- | --- |
| Binance `BTCUSDT` | `openInterest` / `sumOpenInterest`, BTC quantity | Candle base volume or aggregate-trade `q`, BTC | Native BTC × contemporaneous mark = USDT notional |
| Bybit linear `BTCUSDT` | **`singleOpenInterest`, BTC** | Kline volume or public-trade size, BTC | Single-sided BTC × mark = USDT notional; retain exchange-reported `singleOpenInterestValue` separately |
| OKX `BTC-USDT-SWAP` | `oi`, contracts | Candle `vol` or trade `sz`, contracts | Contracts × versioned `ctVal` × `ctMult` = BTC; BTC × mark = USDT notional |

The actual OKX instrument probe returned `ctVal=0.01`, `ctMult=1`, `ctValCcy=BTC`, `ctType=linear`. Its example `oi=3005760.71000000873` and `oiCcy=30057.6071000000873` agree with the conversion. Preserve native quantities and specifications rather than hard-coding BTC conversion. USDT amounts are dollar-denominated proxies and should be described as such if no USDT/USD conversion is ingested.

OI and trade volume must refer to the same venue, contract and interval before applying the opening estimator. Differencing USD OI mixes price revaluation with contract creation. Use native OI differences, not changes in `oiUsd` or `openInterestValue`. Taker side is execution aggressor side, not evidence of a newly opened position. Currency-wide statistics are unsuitable substitutes for contract-level inputs without an explicit coverage label.

## Binance USD-M

### Official specifications

The current [market-data reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) specifies aggregate-trade REST history of **48 hours**, maximum 1,000 rows and sub-hour time windows; `q` includes RPI and `nq` excludes it. OI statistics offer about one month, minimum five-minute resolution and 500 rows/page. Missing records can occur. Klines expose taker-buy base and quote volume. `maintMarginPercent` and `requiredMarginPercent` in exchange information are explicitly ignored fields, not usable risk brackets. The top-position ratio describes the highest-margin 20% cohort. This cohort is not aggregate market direction.

The [signed account reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account) defines `leverageBracket` as authenticated. Its tier inputs include notional bounds, maximum leverage, maintenance rate, deduction and possible user-specific multiplier. Public-only ingestion cannot claim to have retrieved those private/account-adjusted brackets.

The [official March 2026 announcement](https://www.binance.com/en-NG/support/announcement/detail/ebf9b0aa9eca4ff3804eef6fb09ba32a) and [stream migration notice](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice.md) require routed WebSocket URLs. Use `/market` for aggregate trades, candles, mark prices and liquidations. Legacy URLs ceased market-stream support in April 2026. The [connection specification](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect.md) describes 24-hour connection lifetime, heartbeat requirements and inbound subscription limits; reconnect proactively with overlap and deduplication.

### Endpoints actually probed

| Purpose | Path under `https://fapi.binance.com` | Actual result |
| --- | --- | --- |
| Contract metadata | `/fapi/v1/exchangeInfo` | 200; BTCUSDT symbol retained |
| Current OI | `/fapi/v1/openInterest?symbol=BTCUSDT` | 200; native BTC quantity |
| OI history | `/futures/data/openInterestHist?symbol=BTCUSDT&period=5m` | 200; bounded 28-day and 29-day windows returned genuinely historical rows |
| Trade candles | `/fapi/v1/klines?symbol=BTCUSDT&interval=1m` | 200 now and 30 days ago |
| Mark candles | `/fapi/v1/markPriceKlines?symbol=BTCUSDT&interval=1m` | 200 now and 30 days ago |
| Aggregate trades | `/fapi/v1/aggTrades?symbol=BTCUSDT` | 200 now; 3-day-old request rejected with `-4166`, recent two days only |
| Funding | `/fapi/v1/fundingRate?symbol=BTCUSDT` | 200; funding events with associated mark |
| Top-position ratio | `/futures/data/topLongShortPositionRatio?symbol=BTCUSDT&period=5m` | Unauthenticated request returned 200 here, although current reference labels an API-key header required; handle future 401/403 as unavailable |
| Risk brackets | `/fapi/v1/leverageBracket?symbol=BTCUSDT` | 401 / `-2014`; no key supplied or requested |

The exact 30-day OI request failed with invalid `startTime`; move the lower boundary inside the true retention horizon and record the gap. A start-only 29-day request returned the **latest** rows; the explicitly bounded start/end request returned September observations. Validate every returned timestamp and advance checkpoints only over data actually received.

The public BTC symbol returned `liquidationFee=0.012500`, tick size `0.10` and BTC quantity step `0.001`. Record that fee as metadata; do not blindly add the full liquidation fee to a trigger equation. The unknown private collateral balance and account risk tier remain unknown.

Verified live stream:

```text
wss://fstream.binance.com/market/stream?streams=btcusdt@markPrice@1s/btcusdt@aggTrade/btcusdt@forceOrder
```

This delivered real mark updates and aggregate trades containing `st=1` for USD-M. The legacy `wss://fstream.binance.com/ws/btcusdt@markPrice@1s` connected but produced no messages during the bounded probe. A connected socket alone does not establish a healthy feed. For aggregate trades `m=true` means buyer is maker, therefore taker is seller. Store aggregate ID `a` for deduplication and preserve first/last trade IDs. Do not expect consecutive individual-trade IDs within a single aggregate.

Public force-order data is a snapshot feed, not a complete exchange liquidation ledger; calibrate against it as an incomplete observed lower bound. Original order quantity, last executed quantity and cumulative executed quantity are distinct. Avoid counting both cumulative and incremental fills. A quiet short probe did not witness a Binance liquidation, so actual force-order event parsing must still be covered by fixtures and an operational observation.

## Bybit v5

### OI migration verified against live responses

The [official OI specification](https://bybit-exchange.github.io/docs/v5/market/open-interest) now distinguishes bilateral `openInterest` from unilateral `singleOpenInterest`; both are BTC for this linear BTC contract. The [official update announcement](https://announcements.bybit.com/en/article/-oi--blta89d9c88819bfebe/) dates the new fields to June 11, 2026. Use `singleOpenInterest` preferentially, or a **documented** `openInterest / 2` fallback when the field is absent. Tiny rounding differences are possible. Never switch conventions silently at the migration date.

The live ticker returned `openInterest=62671.066`, `singleOpenInterest=31335.533`, with corresponding notional fields roughly 2:1. The history response similarly returned `62671.80500000` versus `31335.903`. These are real samples, not fixtures manufactured to fit the rule.

### Endpoints and history

All probes below used `https://api.bybit.com`, `category=linear`, `symbol=BTCUSDT`; all returned HTTP 200 with `retCode=0`.

| Purpose | Path | Observed / documented handling |
| --- | --- | --- |
| Contract specifications | `/v5/market/instruments-info` | Live max leverage 150; qty step .001 BTC; tick .10; funding interval 480 minutes |
| Current mark, OI and funding estimate | `/v5/market/tickers` | Single-sided OI fields available |
| Trade OHLCV | `/v5/market/kline?interval=1` | Actual 30-day-old sample returned; reverse chronological rows |
| Mark candles | `/v5/market/mark-price-kline?interval=1` | Actual 30-day-old sample returned |
| OI history | `/v5/market/open-interest?intervalTime=5min` | Actual 30-day-old rows, including unilateral OI, returned |
| Recent executions | `/v5/market/recent-trade` | Actual execution IDs, taker side, price and BTC size |
| Funding events | `/v5/market/funding/history` | Actual funding timestamps and rates |
| Account ratios | `/v5/market/account-ratio?period=5min` | Number-of-holder ratios; not top-trader position weights |
| Risk brackets | `/v5/market/risk-limit` | Public current BTC brackets with `maintenanceMargin`, `riskLimitValue`, `maxLeverage`, `mmDeduction` |

The [kline reference](https://bybit-exchange.github.io/docs/v5/market/kline) specifies up to 1,000 rows/page, start/end milliseconds, BTC volume and USDT turnover. A currently open candle's close is provisional. The [mark-kline reference](https://bybit-exchange.github.io/docs/v5/market/mark-kline) provides OHLC without trade volume. The [OI history reference](https://bybit-exchange.github.io/docs/v5/market/open-interest) supports 200 rows/page and cursor pagination. Do not infer tick-level historical OI between five-minute observations.

The [recent-trades API](https://bybit-exchange.github.io/docs/v5/market/recent-trade) offers at most 1,000 derivative trades per call and no historical start/end pagination. The reference links a lawful historical archive, but this research did not download 30 days of archives. Consequently 30-day genuine entry-volume profiles cannot be claimed solely from recent-trade polling. The [account-ratio reference](https://bybit-exchange.github.io/docs/v5/market/long-short-ratio) defines holder-count ratios, supports cursor pagination and dates its available series back to July 2020. Missing top-trader **position** ratios must remain unavailable rather than being relabeled account ratios.

### Risk and liquidation semantics

The [public risk API](https://bybit-exchange.github.io/docs/v5/market/risk-limit) exposes maintenance rate and deduction per position-size tier. The live first bracket was 300,000 USDT, MMR .0033, maximum leverage 150 and blank deduction (zero at the first tier); second bracket capped at 2,000,000 with MMR .005 and deduction 510. These differ from illustrative support-page examples; use timestamped live metadata.

The [current official liquidation calculation](https://www.bybit.com/en/help-center/article/UTA-Trading-Rules-Liquidation-Process) uses mark price and maintenance margin at current position notional. For a linear isolated hypothetical quantity `Q>0`, entry `E`, leverage `L`, extra margin `A`, maintenance rate `m`, deduction `D` and taker fee `f`, the published equations imply:

```text
long  = [E*Q - E*Q/L - A/(1-f) - D] / [Q*(1-m)]
short = [E*Q + E*Q/L + A/(1+f) + D] / [Q*(1+m)]
```

This is conditional on assumed private inputs, not an observation of real positions. Fee-to-close and risk-tier changes can affect the result; cross and portfolio margin depend on other account assets and positions. The [maintenance-margin article](https://www.bybit.com/en/help-center/article/Maintenance-Margin-USDT-Contract) explains the deduction across risk tiers and warns that examples are illustrative. Leverage alone does not determine MMR.

The live WebSocket probe used `wss://stream.bybit.com/v5/public/linear` and subscribed to `tickers.BTCUSDT` and `allLiquidation.BTCUSDT`. Ticker snapshot and subscription acknowledgements arrived. The [ticker specification](https://bybit-exchange.github.io/docs/v5/websocket/public/ticker) defines snapshot/delta updates: absent fields in a delta retain their prior values. Do not replace an absent OI or mark with zero.

The [all-liquidation specification](https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation) describes all events pushed in 500 ms batches, with `S=Buy` indicating the liquidated **long** position and `S=Sell` the liquidated short. Its `p` is **bankruptcy price**, not the mark trigger. Do not reverse this mapping using trade-taker semantics. The bounded probe received no actual liquidation event; loss during disconnections remains a coverage gap, and no public REST liquidation-history endpoint was established here.

For genuine trade profiles, subscribe to [`publicTrade.BTCUSDT`](https://bybit-exchange.github.io/docs/v5/websocket/public/trade). Deduplicate trade IDs, not `seq`: multiple messages may share one sequence. Persist block/RPI flags and measure actual continuity.

## OKX v5

The [official API guide](https://www.okx.com/docs-v5/en/) defines contract OI in contracts, coin and USD, reversed historical pagination, and candle completion flags. Its contract-statistics endpoints expose only the latest 1,440 observations at a selected resolution; five-minute OI therefore spans approximately five days. Public liquidation updates are explicitly incomplete and can arrive out of chronological order. Their size is contracts; `bkPx` is the mark-priced transfer with the liquidation account. Taker statistics must use the instrument-specific endpoint and an explicit unit. The top-position cohort is the largest 5% by open-position value. Historical trades are documented for three months, 100 rows/page. Historical candle pages support 300 rows. The [liquidation FAQ](https://www.okx.com/en-us/help/forced-liquidation-faq) distinguishes the account-tier taker fee and liquidation clearance charge. Private fee tier, margin mode and other collateral remain unknown. The [mark-price explanation](https://www.okx.com/en-gb/help/frequently-issues-of-contracts-for-compulsory-liquidation) confirms mark-based liquidation and possible partial reduction.

### Endpoints actually probed

All paths below are under `https://www.okx.com`. Successful application requests identified themselves with `User-Agent: btc-liq-heatmap/0.1 research-public-read-only`. A batch using the default Python urllib user agent received 403 / Cloudflare 1010; retrying with the application's ordinary identifying header succeeded. These responses and both attempts are preserved. Do not respond to a true restriction by bypassing it; expose persistent denied access as a feed failure.

| Purpose | Path / parameters | Actual result |
| --- | --- | --- |
| Specifications | `/api/v5/public/instruments?instType=SWAP&instId=BTC-USDT-SWAP` | 200; .01 BTC contract, max leverage 100 |
| Current OI | `/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP` | 200; contracts, BTC and USD retained |
| Latest trade candles | `/api/v5/market/candles?instId=BTC-USDT-SWAP&bar=1m` | 200; native contracts, BTC and quote turnover |
| Historical trade candles | `/api/v5/market/history-candles?instId=BTC-USDT-SWAP&bar=1m` | 200; actual 30-day-old candles |
| Latest mark candles | `/api/v5/market/mark-price-candles?instId=BTC-USDT-SWAP&bar=1m` | 200 |
| Historical mark candles | `/api/v5/market/history-mark-price-candles?instId=BTC-USDT-SWAP&bar=1m` | 200; actual 30-day-old candles |
| Recent trades | `/api/v5/market/trades?instId=BTC-USDT-SWAP` | 200; `sz` is contracts, taker side |
| Historical trades | `/api/v5/market/history-trades?instId=BTC-USDT-SWAP` | 200 recent sample; exhaustive three-month traversal not performed |
| Funding history | `/api/v5/public/funding-rate-history?instId=BTC-USDT-SWAP` | 200; preserve realized and scheduled fields distinctly |
| Maintenance tiers | `/api/v5/public/position-tiers?instType=SWAP&tdMode=isolated&instFamily=BTC-USDT` | 200; complete tiers preserved in the later probe |
| OI history | `/api/v5/rubik/stat/contracts/open-interest-history?instId=BTC-USDT-SWAP&period=5m` | 200 now and four days ago; empty at 30 days |
| Contract taker volumes | `/api/v5/rubik/stat/taker-volume-contract?instId=BTC-USDT-SWAP&period=5m&unit=0` | 200; BTC units; array order time, **sell, buy** |
| Top-trader position ratio | `/api/v5/rubik/stat/contracts/long-short-position-ratio-contract-top-trader?instId=BTC-USDT-SWAP&period=5m` | 200; actual ratio below one in probe |
| Liquidations | `/api/v5/public/liquidation-orders?instType=SWAP&instFamily=BTC-USDT&mgnMode=cross&state=filled` | 200; real short liquidation rows recovered after correcting required family parameter |

An earlier liquidation request with only `instId` failed with error `50015` requiring `uly` or `instFamily`. Use `instFamily` and filter returned `instId` so a related contract cannot contaminate the model. The recovered response included actual `posSide=short`, `side=buy`, `sz=.4` and `bkPx=80764.3`; its size converts to .004 BTC with the observed specification. Hash stable semantic fields for deduplication because the liquidation row lacks a canonical execution ID. A `net` position side requires deriving the closed direction from order side.

Current tier 1 has `maxSz=1000` contracts (10 BTC), `mmr=.004`, `maxLever=100`; tier 2 caps at 5,000 contracts with `mmr=.005` and `maxLever=66.66`. There is no public deduction in the returned tier schema; do not invent one by assuming another venue's maintenance schedule. A 125x tier is unsupported by this BTC instrument's live maximum and must receive zero OKX model weight; renormalize allowed weights and retain that attribution.

Verified live public WebSocket `wss://ws.okx.com:8443/ws/v5/public` accepted mark-price and liquidation-orders subscriptions and delivered BTC mark updates. Subscribe to `liquidation-orders` with `instType=SWAP` and filter the multi-instrument payload. Candle channels use `/ws/v5/business`; mark ticks use `/ws/v5/public`. A short probe without a liquidation message is not evidence of complete absence. Save first/last observed event time and disconnect periods independently of model outputs.

Currency-wide `/rubik/stat/taker-volume?ccy=BTC&instType=CONTRACTS` and `/rubik/stat/contracts/long-short-account-ratio?ccy=BTC` also returned data, but mix BTC contracts and/or use holder counts. They are retained as research evidence, not recommended substitutes for BTC-USDT-SWAP-specific measurements.

## Backfill and scientific consequences

| Data | Honest start-up coverage demonstrated |
| --- | --- |
| Trade and mark OHLC, all three venues | Sparse probes successfully reached 30 days; a complete backfill must paginate and verify every interval |
| Binance OI | Sparse bounded probes reached 29 days; the exact 30-day boundary rejected |
| Bybit OI | Sparse bounded probe reached 30 days with single-sided field |
| OKX OI, five-minute | Four-day sample available; 30-day request empty; use finer-resolution coverage only where obtained |
| Historical entry profiles | Binance aggregate REST is two days; Bybit recent REST is only a rolling trade tail; OKX history is paginated and expensive. Use labeled candle approximation outside real profiles |
| Forced liquidations | OKX public REST returned real events; Binance/Bybit current public WebSocket feeds have no historical completeness established by this research |

For a thirty-day view, show observed candles over the full available interval, but keep inferred exposure blank or explicitly provisional before model input coverage begins. Neither native OI between sampling boundaries nor initial private position entries can be recovered exactly. A cold-start prior must be named and versioned; seeding exact-looking liquidation positions from old executed volume would invent private data.

Separate transport liveness, sequence continuity, historical interval coverage and statistical observability. Backoff on 429/5xx with jitter, respect retry headers, checkpoint only after durable writes, request overlapping windows and deduplicate, and store denied/empty/truncated results in coverage diagnostics. Replay in chronological event time with as-of metadata versions; never use future funding outcomes or future risk schedules to explain earlier exposure.

The proposed opening estimator is dimensionally valid only after native-unit normalization. Forced execution is already part of trade volume on feeds that include it, and closure is already reflected in OI, so liquidation subtraction and survival removal need an explicit conservation ledger. Snapshot streams and sparse OI observations do not uniquely identify openings, closures or leverage: calibration against incomplete liquidation counts cannot validate absolute market exposure. Report modeled overhang and uncertainty, not the exchange's private liquidation map.
