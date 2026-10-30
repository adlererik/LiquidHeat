# Release validation — v1.2.0

Verified on Linux amd64 using isolated Docker containers on Debian. No application containers run on the development workstation.

- 65 application unit/numerical tests passed inside the private Debian image, including streamed leaderboard input above the former 2,000-address cutoff and incomplete-download rejection.
- 76 tests passed against the sanitized public source, including installer safeguards.
- Nine disposable-database discovery integration groups passed: additive/repeated migrations 006/007, queue progress and inactive delays, small-fill accumulation and durable deduplication, refresh/exploration fairness, explicit Source B first-check priority, per-account error preservation, subaccount/vault/child links, optional-job failure isolation, bounded capacity rotation, source attribution and diagnostics privacy.
- Six existing Hyperliquid integration groups passed, including bucket conservation, CLLD, stale exclusion, deterministic frozen-constituent replay and source/filter separation.
- Six existing CEX integration groups passed: migrations, numerical/DB invariants, real public ingestion/deduplication, all supported lookbacks, WebSocket events and deterministic replay of 150 actual input intervals.
- Live Hyperliquid REST checks passed at all five lookbacks with the four supported bucket resolutions represented. Sampled/plotted gross-OI denominator and excluded-BTC accounting were checked; profile totals matched latest CLLD within display rounding.
- Desktop and mobile browser checks passed without horizontal overflow; the discovery panel is hidden in CEX mode, 125x remains available there, and both price-fit controls operate. No browser console errors were recorded.
- JavaScript label/unknown/stale checks, five-resolution raster peak conservation and six navigation checks passed.
- Frozen historical matrix/constituent digests were unchanged across the upgrade. Existing deployment resource limits were retained.

These are engineering checks, not proof of predictive accuracy, complete discovery, exchange feed completeness or statistical equivalence to Glassnode. Broader discovery needs time to scan candidates. Runtime images retain the existing pinned open-source dependencies; no new package was added.

The prebuilt amd64 bundle also installed into a separate disposable four-container project with real exchange ingestion. A full backup restored successfully into a disposable database. Existing production service/container identities were preserved; project engine restart recovery retained the queue and deduplication store without re-downloading the leaderboard.


## v1.2.1 patch verification

The available-history display fix passed 69 application unit/numerical tests on Debian and 80 public-distribution tests. Seven disposable-database Hyperliquid integration groups passed, including native detail for every young-history lookback, a stable interval for incremental tails, and explicit UTC alignment across 70-minute bucket boundaries. The frontend transition test confirms that an interval change replaces the full grid, while unchanged intervals retain incremental updates. Six navigation checks and five-resolution raster conservation on real market output also passed.

The API and frontend were updated independently; the existing collector and database kept running. This patch changes read-time aggregation and browser refresh behavior only. The v1.2.0 cold-install and backup checks above describe that earlier release; they were not repeated for this presentation-only patch.


## v1.3.0 — isolated CEX band-shape comparisons

Narrow kernels is an optional CEX scenario. Original remains the default with unchanged weights, decay, collateral scenarios and prospective learning features. Hyperliquid calculation/discovery/learning modules are byte-identical to the preceding source; the seven-stop palette is unchanged.

Actual engineering evidence:

- 78 application unit/numerical tests and 89 public-distribution tests passed. JavaScript checks covered CEX significant-cell normalization, unchanged Hyperliquid p98, source/model merge isolation, resolution transitions, six navigation cases and diagnostics-request coalescing/throttling.
- Seven disposable-database CEX integration groups passed: repeated additive migrations through 008, real-input eligibility, restricted-role write denials, all-scenario recovery/deduplication, API filters and normalization, Original replay equivalence on 150 real intervals, and rolling retention anchors.
- Seven existing Hyperliquid integration groups passed. Old/new frontend merges were deeply equal on 120,101 real Hyperliquid cells; protected source files and the palette also matched.
- Full captured-input replay reproduced every saved float32 checkpoint exactly for all five scenarios: 8,640 Binance intervals, 8,640 Bybit intervals and 2,906 genuinely available OKX intervals. No missing historical observations were generated. The verifier took about 17 minutes under bounded resources.
- Matched-hourly shape comparisons over 1,685 venue samples gave mean per-tier peak widths of $1,449.84 for the replay reference and $387.28 for narrow kernels (about 73% narrower). The independent no-extra-collateral, higher-leverage and faster-decay cases measured $269.60, $390.01 and $311.17. These are spatial shape metrics, not forecast accuracy or calibrated confidence intervals.
- Candidate worker restart/recovery passed without resetting Original, Hyperliquid or labelled learning history. Original engine and database identities/start times, six protected native service identities, frozen Hyperliquid history and already-labelled Bybit outcomes remained unchanged.

The fifth service uses at most 256 MiB/0.50 CPU and one normal SQL connection. Its storage guards and rolling input anchors are described in CEX_EXPERIMENTS.md. Existing service ceilings, dependencies and licenses are unchanged. Short resource samples do not establish long-term capacity or predictive skill. The v1.2.0 cold-install and full-restore checks above were not repeated as a new v1.3.0 cold-install claim.
