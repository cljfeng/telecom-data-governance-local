#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL}"
: "${OBJECT_STORAGE_ALIAS:?set OBJECT_STORAGE_ALIAS}"
: "${RESTORE_SOURCE_DIR:?set RESTORE_SOURCE_DIR to one verified backup}"
: "${CONFIRM_RESTORE:?set CONFIRM_RESTORE=RESTORE}"

if [ "$CONFIRM_RESTORE" != "RESTORE" ]; then
  printf '%s\n' "Restore cancelled: CONFIRM_RESTORE must equal RESTORE" >&2
  exit 2
fi

sha256sum --check "$RESTORE_SOURCE_DIR/SHA256SUMS"
pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" \
  "$RESTORE_SOURCE_DIR/governance.dump"
mc mirror --overwrite --remove "$RESTORE_SOURCE_DIR/objects" \
  "$OBJECT_STORAGE_ALIAS"

printf '%s\n' "Restore completed from: $RESTORE_SOURCE_DIR"
