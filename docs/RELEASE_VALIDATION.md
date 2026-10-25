# Release validation — v1.0.0

Verified on 2026-10-25, Linux amd64 / Debian, with a separate Compose project and fresh volume. The development/control workstation ran no application containers. Private host identifiers, raw operational records and populated databases are not included in this repository.

| Check actually run | Result |
|---|---|
| Three Docker images build serially under conservative limits | Passed |
| Checksum-verified image loading and guided fresh install | Passed; all four services healthy |
| Unit/numerical/learning/installer safeguards | 45 tests passed |
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

Initial integration attempts correctly stopped when there was not yet a real Bybit liquidation report in a completed window. After genuinely collected evidence matured, the learning suite passed. Synthetic transition fixtures stayed inside its disposable test database. No such fixtures are shown as market data or used to fit live forecasts.

The main exposure model remains uncalibrated and no learned champion has accumulated a validated future holdout at first release. Software checks establish functioning implementation paths, not predictive accuracy, private account knowledge or trading profitability. Backups of larger accumulated datasets and regional exchange access need operator-specific verification.
