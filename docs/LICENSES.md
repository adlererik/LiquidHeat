# Zero-cost and open-source runtime inventory

No new account, payment instrument, subscription, free trial, paid SDK, commercial API, cloud hosting or managed service is used. Market data comes directly from unauthenticated exchange endpoints. Exchange data access is governed by each venue's terms and availability; freely accessible data is not represented as an unrestricted proprietary dataset license.

| Component | Source / license | Deployment choice |
|---|---|---|
| Docker Engine Community | [Moby](https://github.com/moby/moby), Apache-2.0 | Official free Linux static 29.9.2 binaries; no Docker Desktop |
| Docker Compose | [docker/compose](https://github.com/docker/compose), Apache-2.0 | Official v5.6.1 release, checksum verified |
| containerd / runc / RootlessKit | Official Docker bundle; Apache-2.0 | Rootless namespace isolation |
| slirp4netns | Debian main, BSD-3-Clause/GPL compatible components | Debian package; no host firewall changes |
| uidmap / libsubid | Debian main, BSD/GPL licensed shadow project | Only privileged subordinate-ID mapping helpers |
| PostgreSQL 16 | [PostgreSQL License](https://www.postgresql.org/about/licence/) | Official `postgres:16.15-bookworm`, pinned digest |
| TimescaleDB 2.19.3 core | [Apache-2.0 core license](https://github.com/timescale/timescaledb/blob/2.19.3/LICENSE) | Compiled `APACHE_ONLY=1`; **no TSL module**; telemetry excluded |
| Python 3.12 | PSF-2.0 | Official slim Bookworm image, pinned digest |
| FastAPI / Starlette / Uvicorn | MIT / BSD-3-Clause / BSD-3-Clause | Pinned Python packages |
| uvloop / asyncpg | MIT / Apache-2.0 | Pinned Python packages |
| aiohttp / websockets | Apache-2.0 + MIT / BSD-3-Clause | Pinned Python packages |
| NumPy / SciPy | BSD-3-Clause (bundled permissive libraries and GCC runtime exception) | Pinned wheels; one BLAS thread |
| Pydantic / pydantic-core | MIT | Fully pinned transitive dependencies |
| Apache ECharts 5.6.0 | Apache-2.0 | Vendored JS; license alongside asset; no CDN requests at runtime |
| Nginx | BSD-2-Clause | Official 1.28.3 Alpine image, pinned digest |
| zlib | zlib license | Standard-library matrix compression |
| Debian / Alpine base packages | Free/open-source distribution packages | Official image sources; no non-free add-ons |

All Python transitive runtime dependencies are pinned in `requirements.lock`. `scripts/license_audit.py` inventories installed package metadata and packaged license files on the actual server image; the result is retained in artifacts. Unknown or unusually broad metadata must be examined before adopting a new dependency. Build tools come from Debian main and official PostgreSQL apt packages inside the build container. They are not installed on the Debian host or retained in the final database image.

The official Timescale image was rejected because it includes the separately licensed `tsl` shared object. The project instead builds the Apache-only core from a versioned, SHA256-checked source archive. The Dockerfile asserts no `*timescale*tsl*` binary exists. SQL uses no TSL compression or scheduled-policy features. The license header describing both directories does not mean TSL code is linked: only the Apache core is compiled and copied.

The application's own code is Apache-2.0. Glassnode research images are linked as copyrighted references and are not redistributed. The README screenshots show LiquidHeat itself with public market observations. The live interface loads no third-party reference images.
