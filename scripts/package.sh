#!/usr/bin/env bash
# Run on the Linux amd64 build server after reviewing/committing the public tree.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
test "$(uname -m)" = x86_64 || { echo 'amd64 release builder required' >&2; exit 1; }
test -z "$(git status --porcelain)" || { echo 'Commit the reviewed public tree before packaging' >&2; exit 1; }
mkdir -p dist
stage=$(mktemp -d "$PWD/dist/package.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT
mkdir "$stage/LiquidHeat"
git archive HEAD | tar -x -C "$stage/LiquidHeat"
docker image inspect liquidheat-app:1.0.0 liquidheat-web:1.0.0 liquidheat-timescale:2.19.3-pg16 >/dev/null
docker save liquidheat-app:1.0.0 liquidheat-web:1.0.0 liquidheat-timescale:2.19.3-pg16 | gzip -1 > "$stage/LiquidHeat/docker-images-linux-amd64.tar.gz"
(cd "$stage/LiquidHeat" && sha256sum docker-images-linux-amd64.tar.gz > IMAGE-SHA256SUMS)
tar -czf dist/LiquidHeat-v1.0.0-linux-amd64.tar.gz -C "$stage" LiquidHeat
(cd dist && sha256sum LiquidHeat-v1.0.0-linux-amd64.tar.gz > SHA256SUMS)
echo 'Created Docker release bundle and SHA256SUMS in dist/'
