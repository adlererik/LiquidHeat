# Two distinct heatmaps, and our Hyperliquid implementation

Verified against official public documentation on 2026-10-26. Earlier project notes described only one Glassnode method; that was incomplete.

| Glassnode metric | Published basis | Our corresponding view |
| --- | --- | --- |
| `derivatives.LiquidationHeatmap` | Hyperliquid position-level data. Glassnode's September 18, 2025 report specifies the 1,000 largest BTC positions. | **Hyperliquid positions**: public reported thresholds, ranked among our discovered fresh accounts. |
| `derivatives.FuturesLiquidationHeatmap` | Estimated exposure using open-interest flows and price movements; 100 price buckets. Studio lists All Exchanges and venue filters. | **CEX estimates**: an independent, uncalibrated model, not Glassnode's disclosed algorithm. |

These are separate named metrics/endpoints. An exchange selector within the futures metric is not evidence that the Hyperliquid account method is combined with CEX inference. Public pages do not establish every control in the paid Studio interface. LiquidHeat supplies an explicit method selector and never blends the densities.

Sources:

- [Glassnode derivatives API catalog](https://docs.glassnode.com/basic-api/endpoints/derivatives), separate `liquidation_heatmap` and `futures_liquidation_heatmap` endpoints.
- [Futures Liquidation Heatmap in Studio](https://studio.glassnode.com/charts/derivatives.FuturesLiquidationHeatmap?a=BTC), public description and exchange controls.
- [Post-Cut Patience, September 18, 2025](https://research.glassnode.com/the-week-onchain-week-37-2025/), explicit 1,000-position methodology.
- [Pressure Points, September 23, 2025](https://research.glassnode.com/liquidation-heatmaps/), Hyperliquid methodology and separate comparisons with realized CEX liquidations. Its market-share figures describe that publication date, not current verified coverage.

A branded screenshot without a metric identifier does not establish which of these two methods generated it. Horizontal streaks alone do not identify its underlying dataset. We have not accessed paid Glassnode data, its source code, or its complete account universe. We cannot claim identical outputs, private algorithms, or pixel-perfect replication.

## Free account observations

The collector reads public `clearinghouseState` responses at `https://api.hyperliquid.xyz/info`. A signed `szi` is BTC exposure; positive means long and negative means short. It uses reported `entryPx`, leverage mode/value and `liquidationPx`. A null liquidation price is retained as a coverage limitation and excluded from the density; no threshold is guessed. These are mutable account-state observations, not a promise of a future forced execution price.

Account discovery starts with up to 2,000 large-account-value candidates from the official-host public leaderboard and expands with both counterparties from public BTC fills of at least $50,000 observed on the live trade WebSocket. Periodic recent-trade queries remain a limited discovery fallback. Account value does not establish BTC position rank. The leaderboard endpoint is undocumented and may change or fail; live-trade discovery is a limited fallback. Neither source is a full account census. Subaccounts or quiet accounts may be missed. A bounded registry and paced scans protect shared servers. Trade signals are persisted with the account record, so an already checked wallet can be refreshed after a new large fill. A large fill can close or transfer exposure; it does not itself prove a newly opened whale position. A trader splitting orders into smaller fills may be missed by this filter.

At most 1,000 BTC positions are selected by absolute BTC size **among accounts observed within 15 minutes**. This is not a verified global top 1,000. The UI reports checked/discovered counts, selected positions, null thresholds, snapshot age and sampled gross exposure relative to market gross OI. Every futures contract has two sides: the denominator is `2 * one-sided exchange OI`. This fraction measures a staggered observed sample, not completeness or confidence. Sampled longs and shorts need not match each other.

For each selected position, the entire absolute BTC amount goes into its reported liquidation price's $25 bucket. No Gaussian kernel, assumed leverage tiers, CEX alpha, model decay, inferred opening flows, or mark-price sweep is applied. A successful new account response replaces the old observation; an explicitly empty BTC position removes it. Request failures never count as position closures. Stale positions expire from the current sample. Historical snapshots remain frozen.

All notionals use the observed mark price. Hyperliquid BTC perpetuals use USDC collateral; displayed USD is a 1 USDC = 1 USD valuation convention. This view is separately labeled BTC/USDC. The $50/$100/$200 settings sum the underlying bins. The plotted grid covers approximately 50–150% of mark; positions outside it are counted in diagnostics rather than piled into an edge bucket. Display p98 changes colors only, not amounts.

## History, recovery and limits

Compressed matrices and compressed normalized position constituents are recorded every five minutes in a separate Timescale hypertable. Live profiles query the latest fresh account states; marks poll approximately every 30 seconds and account scans are staggered. This is not an atomic exchange-wide snapshot. REST pacing is at most 150 ordinary account requests/minute, plus bounded metadata/trade/candle requests, below the documented 1,200-weight/minute ceiling for this collector alone. Other clients sharing the IP can still cause 429 responses; retries back off.

Native Hyperliquid 5-minute candles are backfilled for one day at startup and gaps are caught up within a bounded three-day request window. Historic account states are **not** reconstructed from those candles. Historical liquidity streaks accumulate as observations are collected; no six-month history is available on a fresh install. A band moving or disappearing can mean margin changes, voluntary closure, stale sampling, or liquidation. It is not automatically classified as an observed forced liquidation.

The account registry, last successful observations and snapshot checkpoints survive restarts. Previously discovered addresses are reused, leaderboard downloads are normally six-hourly (the observed file is about 39 MB), and stored snapshots are not rewritten. Position history has a 35-day retention window with one-day chunks; physical retention can extend by one chunk. Registry cap: 10,000 accounts. Live discovery buffers at most 1,000 distinct addresses and flushes in bounded batches approximately every 10 seconds. Diagnostics report trade-message activity, discovery signals, reconnects and addresses dropped at capacity. Reconnects cannot recover every missed public trade; that is a coverage limitation, not a complete feed guarantee. The existing 3 GiB project database soft guard and 4 GiB free-filesystem guard apply. Backups retain their existing bounded policy.

The separate Bybit reported-flow learning experiment continues unchanged. It does not train, scale or overwrite the reported Hyperliquid thresholds. Whether Hyperliquid bands predict liquidation flow elsewhere requires a separate prospective study; it is not established by visual resemblance or by calling whale positions “smart money.”

Official API references: [perpetual account fields](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals), [rate limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits), [general information endpoints](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint).

The public trade payload and `users` buyer/seller identifiers are documented in Hyperliquid's [WebSocket subscriptions](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions). No user-specific WebSocket subscriptions are used; the separate 10-user subscription limit is not worked around. Account states use paced public REST queries.

On a fresh installation the candles cover much more time than the collected positions. The Hyperliquid view initially shows the full sampled liquidation price range, making distant bands visible. **Fit price** brings the visible candle wicks into view; **Fit liquidity** restores all price bands. Most large accounts can have distant thresholds or null thresholds, so few bands may lie immediately beside the current candles. This is not corrected by inventing near-price exposure.

At overview scales, multiple native price buckets can occupy one screen pixel. Rendering retains the strongest actual bucket in that pixel, without averaging it away or adding amounts. Zooming recalculates the raster to reveal the original detail. Profile totals, exported values and stored matrices do not use this display pooling.
