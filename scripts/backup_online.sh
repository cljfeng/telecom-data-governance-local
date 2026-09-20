#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL}"
: "${OBJECT_STORAGE_ALIAS:?set OBJECT_STORAGE_ALIAS}"
: "${BACKUP_OUTPUT_DIR:?set BACKUP_OUTPUT_DIR to a dedicated backup directory}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="${BACKUP_OUTPUT_DIR%/}/${timestamp}"
mkdir -p "$target"

pg_dump --format=custom --file="$target/governance.dump" "$DATABASE_URL"
mc mirror --overwrite "$OBJECT_STORAGE_ALIAS" "$target/objects"
sha256sum "$target/governance.dump" > "$target/SHA256SUMS"

printf '%s\n' "Backup completed: $target"
