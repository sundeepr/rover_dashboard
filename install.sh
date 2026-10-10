#!/usr/bin/env bash
set -euo pipefail
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$SOURCE/deploy/install.sh" "$@"
