#!/usr/bin/env bash
# Optional source build, on the Linux deployment/build server.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
for spec in 'database/Dockerfile liquidheat-timescale:2.19.3-pg16' 'Dockerfile liquidheat-app:1.2.1' 'frontend/Dockerfile liquidheat-web:1.2.1'; do
  read -r file tag <<< "$spec"
  docker build --memory=768m --cpu-period=100000 --cpu-quota=75000 --file "$file" --tag "$tag" .
done
