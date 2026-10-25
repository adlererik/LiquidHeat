# Operations

All commands below run in the Linux server's installation directory. `liquidheat.sh` reads the private local installation JSON and passes the exact project name to Docker Compose. It does not act on another Compose project.

## Start, inspect and stop

```bash
./liquidheat.sh status
./liquidheat.sh doctor
./liquidheat.sh logs
./liquidheat.sh stop
./liquidheat.sh start
```

`stop` retains the volume. Normal startup/migrations are idempotent. Do not use `down -v`, volume deletion, global prune or a global daemon restart for routine project maintenance.

## Boot and daily backups

Containers use `restart: unless-stopped`. The installer creates regular-user systemd units named after the chosen project (`liquidheat` by default) and a daily backup timer around 04:17 local time with randomized delay. Rootless Docker must already be enabled at boot, and the user's manager needs lingering. The installer checks rootless prerequisites and explains missing setup. Rootful daemon startup remains the administrator's existing configuration.

```bash
systemctl --user is-enabled liquidheat.service liquidheat-backup.timer
systemctl --user list-timers
loginctl show-user "$USER" --property=Linger
./liquidheat.sh backup
./liquidheat.sh verify-backup
```

Backups are mode-0600 PostgreSQL custom-format archives in `backups/`. Only the newest three completed `heatmap-*.dump` archives and checksums are retained. Manual archives are not covered by that rotation. Backups on the same disk do not protect against physical disk loss. Copy them to another already-owned disk if needed; no cloud service is required.

The full database contains derived/public observations and operational metadata. It still should not be posted publicly. The generated `.env`, install JSON, backups and build artifacts are ignored by Git and excluded from image contexts.

## Restore safely

First preserve the current volume and backup. Restore into a **separate project-owned test database/stack**, verify it, then deliberately plan a cutover. Never import an unknown dump over a live database. For a compatible fresh Timescale database, use its documented `timescaledb_pre_restore()` and `timescaledb_post_restore()` around `pg_restore`; inspect extension version compatibility first. A custom-format dump can be inspected with `pg_restore --list` inside the database image.

The `verify-backup` helper checks the newest archive checksum, restores the full backup into a randomly named disposable database inside this project's database container, validates migration/snapshot/checkpoint/CLLD/learning invariants, and removes only that test database. It never restores over the live database. The release test successfully exercised this with a fresh installation's complete 12 MiB archive. Operators should also verify their own larger, accumulated database backups before relying on unattended recovery.

## Space and resources

| Service | Memory ceiling | CPU ceiling |
|---|---:|---:|
| Database | 512 MiB | 0.60 |
| Engine + learning | 384 MiB | 0.50 |
| API | 256 MiB | 0.35 |
| Nginx | 64 MiB | 0.10 |

The limits total 1,216 MiB plus Docker/runtime/OS overhead. Kernel/cgroup delegation determines whether each control is enforceable; inspect actual `docker inspect` / `docker stats` values. No hard disk-I/O or network-bandwidth quota is claimed. Logs rotate at two 5 MiB files per service. PostgreSQL has at most 20 connections; application pools are small and numerical libraries use one thread.

Market/model tables retain approximately 35 days, raw trades six hours, transient stream records ten minutes. Whole-chunk expiration can retain more than the nominal interval. Learning receipt/audit evidence retains 35 days, forecasts/outcomes 90 days, inactive models and decisions one year; active candidates/champions are kept. Small metadata registries and indexes also consume space.

The engine checks every five minutes and on startup: DB above **3 GiB**, or `/srv` filesystem free below **4 GiB**, pauses collection through its supervisor restart/startup guard. This is a **soft** guard. Database files/WAL, image layers, archive downloads, backups and other services have separate growth. `/srv` can be on a different filesystem from Docker's volume on some installations, so the guard is not universal protection of that volume. Monitor host free space and the actual Docker storage location too.

```bash
df -h
docker info --format '{{.DockerRootDir}}'
./liquidheat.sh doctor
```

Daily model fitting creates database artifacts, not new Docker images. Rebuilds can leave old image layers; inspect and remove only identified project-owned unused resources when justified. Never use global prune on a shared server.

## Upgrade

Read the new release notes. Back up first. Stop only the engine if you need a stable maintenance window. Keep `.env`, `.liquidheat-install.json`, the Compose project name and persistent volume. Back up your installation directory before replacing tracked release files; never overwrite your settings with someone else's configuration. Load the new release's images, review its Compose/migrations, then run the installer with the same project/bind/port so it preserves settings and performs project-only updates. Verify health and checkpoint recovery afterward.

Do not assume rolling back an image also rolls back a database migration. Restore compatibility needs a reviewed plan and a verified backup. Version 1.0.0 is the first public package; no automatic Internet updater is enabled.

## Learning operation

Keep the server and exchange connections running. Daily review and forecast scheduling are part of the engine. Neither a browser nor an assistant session must remain open. Audited gaps exclude affected outcomes. Do not raise alpha to manufacture apparent learning progress. Promotion affects reported-flow forecasts only, never hidden-position heatmap priors. See [LEARNING.md](docs/LEARNING.md).

## Security and privacy

The web application is read-only and unauthenticated over HTTP. Bind to a private LAN or loopback; use an SSH tunnel if desired. Do not port-forward it publicly without your own access control/TLS. There are no exchange/account/order keys. Public market feeds and software downloads require outbound Internet access. No paid service, cloud infrastructure or runtime telemetry service is required.

Screenshots in this repository contain the application and public market data only. Local addresses, usernames, host inventories, database contents, secrets and private Git history are not release assets. Report issues with redacted errors, not complete dumps or unfiltered operational logs.
