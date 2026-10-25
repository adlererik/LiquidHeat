# Model validation and scientific limits

## What has and has not been established

Software tests establish numerical invariants and functioning data/serving/recovery paths. They do **not** establish that the main map reconstructs hidden positions, predicts price, forecasts complete venue-wide liquidations, or improves trading results. No published long-duration out-of-sample performance result exists at this initial release.

The CEX exposure model uses unvalidated structural priors. Alpha remains `0.04`; leverage mixtures, unknown collateral and survival assumptions are uncertainty sources. Exchange maintenance information is used where accessible, but account size, hedges, cross-margin balances and margin additions are private. A range over assumptions is not a calibrated confidence interval.

## Numerical and data invariants

- One-sided native OI is the gross contract count for each counterparty; converting to USD must not create directional OI imbalance.
- Exposure arrays and Gaussian bucket probabilities are finite/nonnegative, with normalized projected mass.
- Closures/sweeps cannot clear the same exposure repeatedly or create negative balances.
- CLLD equals long minus short modeled USD; oscillator lies between −1 and +1.
- Checkpoint serialization and bounded historical replay reproduce saved model states within numerical tolerance.
- Real exchange input deduplication and migration replay are idempotent.
- Forecast issuance precedes its target and cannot be backdated. Sources use available-at-issuance evidence. Frozen labels exclude insufficient transport coverage.
- Candidate evaluation is fixed in time and compared against immutable reference forecasts; promotion/retirement/rollback changes are transactional and auditable.

## Exchange limitations

Binance and OKX reported liquidation feeds can be sampled. Bybit's documented all-liquidation stream is used for the learning target, while transport audit cannot prove exchange-side completeness. A reported bankruptcy price is not a private position's initial mark trigger. Notional treats USDT approximately as USD. Historical candles, OI, aggregate trades and ratios have different retention/access limits. No absent historical dataset is invented.

## Learning evidence

The initial release contains a working prospective collection, future-test, promotion and rollback pipeline, not an already validated champion. Training needs at least seven days and 600 eligible windows per side, including 30 positive windows. A candidate then faces a fixed 14-day future test with coverage, sample-size and improvement gates. The earliest decision is around three weeks, often later, and a candidate can fail indefinitely.

A passed reported-Bybit-flow holdout would apply to that narrow target/period. It would not calibrate the heatmap, identify private leverage, or guarantee future accuracy. Multiple sequential candidates, regime changes and transport behavior remain selection/generalization concerns. The paired-day bootstrap is an engineering gate, not a fully calibrated interval.

## Verification record

See [release verification](docs/RELEASE_VALIDATION.md) for the checks actually run on this package. Existing development checks included 36 model/learning unit tests, six ingestion/database/API/WS/replay groups, six learning integration groups, restart persistence, targeted learning restore, browser navigation/responsiveness and resource sampling. Synthetic numerical fixtures were restricted to disposable test databases; released charts use real observations only. A broad full-database restore attempt was interrupted and is not claimed to have passed.

Reference-image comparison was qualitative against lawfully public Glassnode research figures. The underlying venues/date ranges differ, so no pixel-perfect or statistical-equivalence claim is justified. Screenshots document actual LiquidHeat output, not a fabricated marketing chart.

## Separate Hyperliquid reported positions

Reported account liquidation prices are distinct from our uncalibrated CEX model. The free leaderboard and large-fill trade stream discover candidate accounts; neither guarantees a complete universe. We select up to 1,000 BTC positions among those checked within 15 minutes, keeping null and out-of-grid thresholds in coverage counts without inventing prices. Each eligible position goes directly into one $25 bin; wider buckets sum these values. Sampled longs/shorts need not match, and sampled gross OI divides by twice one-sided exchange OI.

Tests verify direct-bin conservation, null handling, native signs, ranked selection before exclusions, bounded streaming JSON decoding, qualifying public-fill discovery, fresh-state selection, closure handling, migration idempotence, source isolation, API privacy and deterministic replay of archived constituents. No observations are extrapolated to unknown accounts or CEX venues. Immutable history begins at collection time and does not arise from historic price candles. Read [the methodology and Glassnode comparison](docs/HYPERLIQUID.md).
