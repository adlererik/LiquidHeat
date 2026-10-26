# Hyperliquid discovery and honest coverage

LiquidHeat queries public account states. It does not enumerate a verified global universe and never infers a position from a transfer, fill, vault investment, or wallet balance.

## Finding candidates

- **Published leaderboard:** stream the official-host JSON, discard names, retain normalized addresses and account-value scanning scores from the full list within the 100,000-candidate safety budget. Successful downloads are normally six-hourly. The endpoint is undocumented and does not rank BTC positions. A complete download is validated before registering candidates; partial downloads do not mark the discovery checkpoint successful.
- **Public BTC trades:** both counterparties from every valid received fill are candidates, including fills below the old $50,000 threshold. Identity `(event time, trade ID)` deduplicates REST, WebSocket and restart overlap. Received notional is accumulated in **fixed 10-minute windows**, not an asserted complete rolling volume profile. This changes query priority only.
- **Subaccounts:** follow `subAccounts` links from known positions and high-account-value candidates. Query the actual subaccount address, rather than an agent key.
- **Vaults:** follow `userVaultEquities` to vault addresses, then `vaultDetails` parent-to-child links. Query each actual account once by its primary-key address. Investor equity is not added to vault trading exposure, and embedded relationship response positions are not counted a second time.

Links are polled in a separate slow lane (at most one relationship request per 30 seconds). Individual endpoint failures back off without preventing other jobs from running. This collector uses only free public endpoints; it does not scrape social media or depend on paid address attribution.

## Scheduling and persistence

`hl_accounts` is a durable queue, not an unbounded in-memory watchlist. Successful observation time, next check, attempted time, errors, activity, and relationship due times persist across restart. The largest 1,000 **last-known** BTC positions form the preferred refresh cohort; every 30 seconds the cohort is recomputed. API output still selects the largest **fresh** positions and excludes observations older than 15 minutes.

Three of four scan opportunities prefer due refresh-cohort members. Every eighth scan opportunity prefers an unseen candidate ordered by account-value hint, preventing trade traffic from monopolizing all exploration. The alternating candidate opportunity first checks unseen addresses with a received single fill or fixed-window activity of at least $50,000 (Source B). Other opportunities prefer unseen linked subaccounts/vaults, recent trade signals, or the oldest due candidate. Linked accounts therefore do not have to wait behind the whole leaderboard. Unused capacity can serve other lanes. These are scheduling opportunities, not guaranteed service rates.

Preferred refresh delays are 2 minutes near a reported threshold (within 2% of mark), 5 minutes for positions of at least 10 BTC, and 10 minutes for smaller positions. A member outside the preferred cohort normally waits 30 minutes; a newly discovered large position enters the next cohort refresh. Successfully empty accounts normally wait 24 hours. Trade signals can advance due times, with minimum attempted-query spacing of 2 minutes for tracked accounts and 1 minute for other accounts. Actual ages can be longer under load and are reported explicitly. Errors preserve last successful observations and schedule bounded per-account retries; freshness exclusion still applies.

The registry caps at 100,000 accounts. At capacity, successfully checked empty accounts older than six hours, never observed with BTC, without a trade in seven days, and outside the current admission batch may rotate out. Historical BTC accounts and current positions are protected. If no eligible space exists, candidates are rejected and counted. This may still omit relevant accounts and does not guarantee permanent memory of every empty address ever seen.

The collector keeps the existing single REST caller and at least 0.4-second request spacing. A rolling 600-weight/minute reservation budget leaves headroom below Hyperliquid's documented 1,200-weight/IP limit. Account queries reserve weight 2; optional queries reserve their documented base/additional weights. Other clients sharing the same public IP can still cause rate limits. Rate-limit responses delay later requests; no IP rotation or subscription-limit workaround is used.

Pending memory holds at most 4,000 trade events. Durable deduplication retains at most 200,000 events and deletes records older than two hours. Queue overflow is reported. A crash can lose not-yet-flushed discovery signals, and reconnection does not recover every missed trade. Those losses do not create or modify position observations. Database and host-free-space guards still apply to the whole project.

## What the percentages mean

Every contract has both counterparties. The denominator for **gross coverage** is `2 × reported one-sided BTC OI`.

- **Sampled:** absolute BTC size of selected fresh positions divided by that denominator, including null and out-of-grid thresholds.
- **Plotted:** only the selected BTC size with a reported threshold in the approximately 50–150% price grid, divided by the same denominator.
- **Excluded:** null thresholds and out-of-grid positions are counted separately with BTC totals. They are never guessed or forced into edge bins.

Accounts are observed at staggered times. The numerator and market OI are not an atomic exchange-wide snapshot, so these are descriptive coverage measurements rather than statistical confidence bounds. Glassnode's reported coverage of *liquidable* OI uses a different scope and cannot be compared directly to our gross-OI percentage.

## Measuring discovery gains

The dashboard and diagnostics show pending/checked candidates, cohort age, stale positions, capacity losses, and candidate counts by initial discovery source. `first_btc_discoveries` attributes each account's first BTC observation to its original discovery path, with the initial observed absolute BTC size and current fresh BTC size. Accounts already holding BTC when migration 006 runs are labeled `pre-expansion`.

This is **provenance, not causal attribution**: another method might also have found the address. First-observed size is not an observed new opening. Cumulative account counts can increase while current coverage falls because positions close, shrink, or become stale. No percentage improvement is promised before measurement. Broader discovery does not train the liquidation math or alter frozen historical states. The independent Bybit forecast experiment remains separate.

## Official references

- [Public account, subaccount, and vault endpoints](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Public trade WebSocket schema](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)
- [Request weights and subscription limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)
- [Published leaderboard endpoint](https://stats-data.hyperliquid.xyz/Mainnet/leaderboard)
