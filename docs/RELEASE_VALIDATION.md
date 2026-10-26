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
