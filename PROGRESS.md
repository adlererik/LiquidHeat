# Public release progress

- v1.0.0 (2026-10-25): isolated Docker distribution, real public Binance/Bybit/OKX ingestion, explicit CEX priors, audited prospective Bybit-flow learning, beginner installer and prebuilt amd64 bundle.
- v1.1.0 (2026-10-26): corrected the distinction between Glassnode's two metrics; added separate sampled Hyperliquid positions, large-account seeding and public large-fill discovery, durable account state, prospective position history, explicit coverage, independent source controls and adaptive peak-preserving rendering.

- v1.2.0 (2026-10-26): broader durable discovery queue, all-fill activity, linked subaccounts/vaults, adaptive checks, bounded rotation, sampled/plotted coverage and discovery provenance.

Actual verification and scientific limits are recorded in [release validation](docs/RELEASE_VALIDATION.md), [model validation](MODEL_VALIDATION.md), and [Hyperliquid methodology](docs/HYPERLIQUID.md). No private deployment identifiers or populated databases are distributed.


## v1.2.1 — available-history display resolution

Long Hyperliquid lookbacks retain five-minute detail while collected account history is short. As history grows, aggregation remains bounded to approximately 288 columns. A resolution change replaces the browser's full grid instead of mixing native and aggregated columns. Candles and snapshots use the same explicit UTC epoch. This changes presentation only; historical matrices, discovery, CEX inference and the forecast learner are unchanged.


## v1.3.0 — separate CEX experiments

Added the bounded CEX comparison worker, common hashed input ledger, exact restart/replay anchors, restricted-role source protection, optional narrow kernels and three separate sensitivity scenarios. Original remains the default. Hyperliquid mathematics and history and the prospective Bybit feature source are unchanged. See docs/CEX_EXPERIMENTS.md and docs/RELEASE_VALIDATION.md for assumptions and actual verification.
