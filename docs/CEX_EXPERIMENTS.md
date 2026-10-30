# CEX band-shape experiments

The CEX view has a **Model** selector. Hyperliquid has no model selector and keeps its reported-position calculation, historical matrices, normalization, controls and colormap.

| Model | Kernel | Leverage weights | Decay per 5m | Extra collateral scenarios |
|---|---|---|---|---|
| Original | Existing broad kernel | .20/.35/.25/.15/.05 | .9996 | 0%, 1%, 4% |
| Replay reference | Same as Original; common captured replay inputs | Same | Same | Same |
| Narrow kernels | Conditional narrower kernel, 4σ support | Same | Same | Same |
| No extra collateral | Narrow | Same | Same | 0% only |
| Higher leverage prior | Narrow | .10/.22/.30/.23/.15 | Same | Same |
| Faster decay | Narrow | Original | .9988 | Same |

Original remains the default. Narrow kernels is the principal comparison. The last three models are individual sensitivity scenarios; their sharper appearance is not evidence that those assumptions are correct. Exchange leverage caps still apply, including zero OKX 125x allocation. Explicit effective-time leverage parameters override the fallback prior.

## Dispersion and colors

When actual price-volume observations cover 95–105% of a candle's native volume, entry prices are already distributed across that observed profile. The narrow scenario uses `sigma=max(entry*.00035,12.5)`. Otherwise it uses the documented weighted typical-price fallback with `sigma=max(min((high-low)/3.5,entry*.0012)/(1+1/leverage),12.5)`. These are conditional scenario assumptions, not identified private-account uncertainty. A narrow fallback can imply more apparent precision than the available entry data justify.

Narrowing is a model calculation change: future mark-price sweeps can remove different amounts, so surviving exposure and CLLD may change even with the same openings, weights and decay. It is not merely an image-sharpening filter.

All kernels retain integrated Gaussian bucket probabilities. Four-sigma support omits at most approximately 0.006335% of normal mass before bucket padding; off-grid and already-crossed mass is never forced back into an edge bucket. Native OI conservation, inferred gross openings, mark-price sweeps and side caps remain in place.

CEX color scaling uses the 98.5th percentile of positive cells at least 1% of the selected matrix's maximum. Smaller cells remain in the matrix and profile; the intensity slider can hide them from rendering. The same rule applies after incremental merges. The existing `normalization_p98` response key remains for compatibility, with the precise rule in `normalization`. The seven-stop palette is unchanged. Hyperliquid continues to use its original 98th percentile over its observed matrix.

## Separate computation and learning

The existing engine continues producing `conservation-v1.0` for the Original model and prospective Bybit learning features. Its global VERSION, checkpoints, calibration and evaluation histories are not switched. No learning history is reset, re-labelled or trained on the alternative matrices.

The separate `cex-worker` reads stored CEX observations and writes only new `cex_runs`, `cex_inputs`, `cex_checkpoints` and `cex_snapshots` tables. Its restricted PostgreSQL role has SELECT permission on necessary source tables and no write permission on raw inputs, original model states, Hyperliquid or learning tables. Database setup applies additive migration 008; it does not clear existing tables. The worker does not contact exchange APIs or start market feeds.

Each captured input includes its price profile or labelled fallback, native OI pair, funding/ratio observations, contract unit, effective leverage overrides and public risk-bracket scenario. Canonical compressed payloads have a SHA256 digest and a cumulative input hash. All five experiments consume the same input. Atomic per-venue batches commit their inputs, histories and five checkpoint states together. Captured checkpoint boundaries allow exact float32 restart/replay verification. Legacy processing retains its original path; experimental cohort calculations are vectorized with bounded temporary arrays.

The initial replay starts at most 30 days back. Only genuinely paired closed trade/mark candles and native OI are eligible. Missing older OKX OI and absent historical trade profiles are not fabricated. Public brackets at capture time are a retrospective historical scenario, not a reconstructed historical schedule.

Narrow-kernel snapshots retain five-minute detail for the latest seven days, with older history sampled every 15 minutes to bound disk. The replay reference and three sensitivity histories are recorded hourly, with one exact batch anchor per venue/UTC six-hour window also retained to conserve shared-server disk; their current profiles use the latest five-minute checkpoints. Metrics compare identical hourly sample times across all five models. Mean peak FWHM and active-bucket counts describe spatial concentration. They are not liquidation forecast scores or proof that prices will move toward a band.

## Operation and limits

The worker uses one database connection, at most 0.50 CPU and 256 MiB RAM, no host port and the existing project network/storage. Existing service ceilings are unchanged. It pauses its own work if the project DB exceeds 2.5 GiB, its tables exceed 512 MiB, or host free disk falls below 5 GiB, leaving headroom before the original engine's guards. Normal history has a 35-day retention window. Exact batch anchors preserve a reproducible state before input expiry; the input ledger keeps a 36-day safety buffer. During initial bootstrap it can retain the original input chain until the first sufficiently old anchor exists (up to approximately 66 days), subject to the same storage guard. These are application guards rather than a filesystem quota.

`GET /api/v1/cex/models` reports configurations, replay coverage, captured-input hashes, progress and shape metrics. Add `cex_model=sharp`, `reference`, `no_extra`, `high_leverage` or `fast_decay` to CEX heatmap/magnet requests. `baseline` uses the original state. CEX scenarios are rejected on Hyperliquid requests. Current worker health and any pause reason are returned with alternative heatmaps.

Tests run inside Debian containers: `python -m scripts.verify_cex_research` uses a disposable DB and copies real CEX samples without changing production source rows; it also verifies the original 150-interval replay. `python -m scripts.verify_cex_replay` reads all captured inputs and compares every scenario checkpoint exactly, including recorded float32 checkpoint boundaries. Existing Hyperliquid regression checks remain separate.

Stopping only `cex-worker` leaves the original model, Hyperliquid and Bybit learner running. Select Original for an immediate model fallback. Backups include the new tables. Migration 008 creates the restricted role; ensure migrations are applied before restoring grants on a fresh PostgreSQL instance. Never restore over the live database as a test.

Live one-interval batches generate exact checkpoints every five minutes. Retention thins redundant anchors to the ordinary history cadence while preserving a complete five-scenario anchor per venue/UTC six-hour window. Timestamp-group deletion prevents partial anchor sets. This keeps alternative storage hourly even during live operation. `python -m scripts.verify_cex_replay --recent` uses a retained anchor at least six hours old when available, otherwise the oldest recorded anchor, and verifies the subsequent real-input chain against current checkpoints; it is not a full-history replay.
