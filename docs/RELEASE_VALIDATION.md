# Release validation — v1.1.0

Verified on 2026-10-26 using Linux amd64 containers on a Debian deployment/build server. The control workstation ran no application containers. The following checks were actually run:

- 62 unit, numerical, learning and installer tests passed in the public application image (52 core tests plus 10 installer checks).
- Six Hyperliquid integration groups passed: additive/idempotent migrations, discrete bucket conservation and CLLD, stale-data handling, immutable constituent replay, durable large-fill discovery/deduplication, and source/filter isolation with cached JSON serialization.
- All five supported lookbacks returned real reported-position matrices, candles and profiles. Current profile totals matched the latest CLLD. Invalid source/method-filter combinations returned 422.
- Real public BTC trade WebSocket probe: 50 fills, four at least $50,000, with both counterparties present. Production discovery counters and normal account queries confirmed ongoing large-fill ingestion; no market positions were created from trade direction.
- Actual Hyperliquid mark and snapshot messages arrived through the application's public WebSocket.
- Existing CEX integration suite passed six groups, including real ingestion/deduplication, REST/filter checks, WebSocket events and deterministic replay of 150 real-input intervals. Twenty previously recorded CEX matrix digests were unchanged.
- A project engine restart recovered live discovery in about 21 seconds, preserved the account registry and historical matrix/constituent digests, reused the leaderboard checkpoint and created no duplicate account identifiers.
- A fresh isolated Compose installation using the public images started all four services successfully. Its full backup restored into a disposable database, including migration-ledger, checkpoint/snapshot, CLLD, forecast-time and Apache-only checks. All four test containers also restarted successfully.
- Browser checks covered the separate methods, clear-on-switch behavior, native account versus hypothetical CEX filters, cumulative profiles, Fit price / Fit liquidity, shared axes and crisp overview rendering. Adaptive raster tests preserved genuine peaks across five resolutions using 195 Hyperliquid cells and 100,844 CEX cells without inventing or mutating exposure values.

The screenshot records an early incomplete account sample, not a historical Glassnode dataset. Matching Glassnode's undisclosed account universe, aggregation and normalization has not been established. The CEX exposure priors remain uncalibrated and no predictive benefit is claimed for the newly collected Hyperliquid sample. Large fills are discovery signals, not evidence of newly opened positions or forced liquidation.

Previous release verification is preserved below. v1.0.0 checks describe that earlier version, not additional v1.1.0 runs.

---

# Release validation — v1.0.0

Verified on 2026-10-25, Linux amd64 / Debian, with a separate Compose project and fresh volume. The development/control workstation ran no application containers. Private host identifiers, raw operational records and populated databases are not included in this repository.

| Check actually run | Result |
|---|---|
| Three Docker images build serially under conservative limits | Passed |
| Checksum-verified image loading and guided fresh install | Passed; all four services healthy |
| Unit/numerical/learning/installer safeguards | 46 tests passed |
| Database migrations, invariants, real public ingestion/deduplication, REST lookbacks/filters, WebSocket events, actual-input replay | Six integration groups passed; 150 intervals replayed |
| Learning migrations, future issuance, DB cutoff, receipt exclusion/immutable labels, guarded promotion/retirement/rollback, atomic training audit | Six integration groups passed |
| Price navigation helper regressions | Six tests passed using real current market output |
| Incremental frontend merge | Passed; 311,924 actual matrix points preserved with idempotent updates |
| Browser | Real rendered Canvas, current models from all three venues, $100 bucket selection, cumulative profile and visible learning requirements verified |
| Responsive layout | Desktop and 390 px mobile checks passed without horizontal overflow |
| Project startup/backup user units | Enabled successfully; settings/password preserved on installer rerun |
| Full backup restore | Complete 12 MiB fresh-installation archive restored into a disposable DB; migrations, checkpoint/snapshot consistency, CLLD, forecast timing and Apache-only extension verified |
| Restart/recovery | All four test containers healthy again in about 41 seconds; historical digest, learning activation and forecast rows preserved |
| Runtime licenses | Python dependency inventory reviewed; Timescale Apache-only setting and absence of the TSL library verified |
| Source/privacy review | Clean public Git history, no production settings/dumps/private host records; screenshot EXIF absent; local documentation links checked |
| Shared-host protection | Existing production container IDs and monitored unrelated service process/start records unchanged |

A resource sample during verification observed approximately 120 MiB database, 57 MiB engine, 89 MiB API and 2.5 MiB web memory, below the configured ceilings. More than 2 GiB host memory remained available. This is a fresh-installation observation, not a sustained-load or month-old database benchmark. A full host reboot was deliberately not used on a shared server; boot configuration and container recovery were checked separately.

A cold-bundle check caught and fixed a port preflight edge case: a closed connection in TIME_WAIT no longer blocks reuse, while active listeners remain protected. A regression test covers both conditions.

Initial integration attempts correctly stopped when there was not yet a real Bybit liquidation report in a completed window. After genuinely collected evidence matured, the learning suite passed. Synthetic transition fixtures stayed inside its disposable test database. No such fixtures are shown as market data or used to fit live forecasts.

The main exposure model remains uncalibrated and no learned champion has accumulated a validated future holdout at first release. Software checks establish functioning implementation paths, not predictive accuracy, private account knowledge or trading profitability. Backups of larger accumulated datasets and regional exchange access need operator-specific verification.
