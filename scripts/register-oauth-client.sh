#!/usr/bin/env bash
# Register usage-web as a public OAuth client (PKCE) with the MCP gateway and create the
# Secret `usage-web` (OAUTH_CLIENT_ID, SESSION_SECRET). Run on the primary node. Idempotent:
# does nothing if the Secret already exists. Never prints the secret values.
# Env: ISSUER (default https://gateway.lab), CA (default ~/lab-tls/lab-ca.pem),
#      NAMESPACE (default lab), PUBLIC_URL (default https://usage.lab),
#      GATEWAY_IP (optional: resolve the issuer host to this address when DNS lacks it).
set -euo pipefail

ISSUER="${ISSUER:-https://gateway.lab}"
CA="${CA:-$HOME/lab-tls/lab-ca.pem}"
NAMESPACE="${NAMESPACE:-lab}"
PUBLIC_URL="${PUBLIC_URL:-https://usage.lab}"
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

if kubectl -n "$NAMESPACE" get secret usage-web >/dev/null 2>&1; then
  echo "Secret usage-web already exists in $NAMESPACE; nothing to do."
  exit 0
fi

host="${ISSUER#*://}"; host="${host%%/*}"
resolve=()
if ! getent hosts "$host" >/dev/null 2>&1; then
  ip="${GATEWAY_IP:-$(hostname -I | awk '{print $1}')}"
  resolve=(--resolve "${host}:443:${ip}")
fi

body=$(printf '{"client_name":"usage-web","redirect_uris":["%s/auth/callback"],"grant_types":["authorization_code"],"response_types":["code"],"token_endpoint_auth_method":"none"}' "$PUBLIC_URL")
resp=$(curl -fsS --cacert "$CA" "${resolve[@]}" -H 'Content-Type: application/json' -d "$body" "$ISSUER/register")
client_id=$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["client_id"])')

kubectl -n "$NAMESPACE" create secret generic usage-web \
  --from-literal=OAUTH_CLIENT_ID="$client_id" \
  --from-literal=SESSION_SECRET="$(openssl rand -hex 32)" >/dev/null
echo "Registered client and created Secret usage-web in $NAMESPACE."
