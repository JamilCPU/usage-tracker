#!/usr/bin/env bash
# Apply every migrations/*.sql in filename order as usage_owner.
# Env: NAMESPACE (default: lab). The password is read from the cluster secret
# and handed to psql inside the exec; it is never printed.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lab}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PASSWORD="$(kubectl -n "$NAMESPACE" get secret lab-data-store -o jsonpath='{.data.USAGE_OWNER_PASSWORD}' | base64 -d)"

for f in migrations/*.sql; do
  echo "Applying $f"
  kubectl -n "$NAMESPACE" exec -i postgres-0 -- env PGPASSWORD="$PASSWORD" \
    psql -U usage_owner -d lab -v ON_ERROR_STOP=1 < "$f"
done
echo "Done."
