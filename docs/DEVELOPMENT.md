# Development and reproducibility

Runtime is Python 3.12 / FastAPI with an asynchronous worker, PostgreSQL 16 and Apache-only TimescaleDB 2.19.3. See the architecture and pinned lockfile. The browser uses vendored Apache ECharts 5; no runtime CDN is required.

Build one image at a time on the Linux build/deployment server:

```bash
./scripts/build.sh
./install.sh
./liquidheat.sh test
```

`build.sh` sets conservative per-build memory/CPU limits. No registry credentials are needed. The Timescale source checksum, base digests and Python versions are pinned. Source availability and base security updates still need maintenance; pinning is not a promise of perpetual security.

For actual-data integration checks, wait for real states on all three venues and a real Bybit reported event in a completed 15-minute window plus two minutes, then run inside the project engine:

```bash
docker compose -p liquidheat exec -T engine python -m scripts.verify_deployment
docker compose -p liquidheat exec -T engine python -m scripts.verify_learning
```

These scripts create specifically named disposable databases inside the isolated PostgreSQL container and refuse unexpected existing ones. They test migrations, real feed semantics/deduplication, REST lookbacks/filters, WebSocket events, numerical replay, prospective timing, frozen label audits and guarded training/promotion/rollback. Learning behavior tests use synthetic fixtures only in their disposable database. Do not run concurrent copies with the same test DB names.

Frontend helper tests need Node.js as a development tool and an actual current heatmap JSON export, not a fake fixture:

```bash
curl -f http://127.0.0.1:18443/api/v1/heatmap -o /tmp/liquidheat-market.json
node tests/chart_navigation_test.mjs /tmp/liquidheat-market.json
node tests/frontend_merge_test.mjs /tmp/liquidheat-market.json
```

The installation checks in `tests/test_installer.py` use only the Python standard library and create no containers. For public releases, audit an explicit source allowlist, use a clean Git history with a noreply identity, exclude `.env`/dumps/local inventories, and inspect runtime image files/metadata. Do not publish operational evidence verbatim if it reveals a private network or username.

The full Docker release bundle contains source/documentation, the three saved images, an inner image checksum and an outer release checksum. Release images are built from this public source tree; none contains a populated database. `scripts/package.sh` creates the package from a reviewed Git commit and locally built Linux amd64 images. It does not push or publish anything itself.
