#!/usr/bin/env bash
# Functional check against the PoC SeaweedFS:
#   F1. Filer HTTP API: PUT a file, GET it back, DELETE it.
#   F2. S3 API: list buckets, create a bucket, PUT object, GET it,
#       LIST, delete.
#   F3. Persistence: PUT a file via Filer, restart the container,
#       confirm the file is still there.
#
# Uses `uv run --with` for short-lived boto3 — no permanent dep on the
# host. Filer parts are pure curl.

set -uo pipefail

cd "$(dirname "$0")"

NAME="gapt-poc-seaweedfs"
MASTER=http://127.0.0.1:19333
FILER=http://127.0.0.1:18888
S3=http://127.0.0.1:18333

FAIL=0
ok()   { printf '  [PASS] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; FAIL=1; }
log()  { printf '[check_seaweedfs %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    log "container $NAME not running — boot_seaweedfs.sh first"
    exit 2
fi

# --- F1. Filer HTTP API ---
log "F1. Filer HTTP API"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "hello sweed" >"$TMP/in.txt"

if curl -fsS -F "file=@$TMP/in.txt" "$FILER/poc/f1.txt" -o /dev/null; then
    ok "PUT $FILER/poc/f1.txt"
else
    fail "PUT failed"
fi

if curl -fsS "$FILER/poc/f1.txt" -o "$TMP/out.txt" && diff -q "$TMP/in.txt" "$TMP/out.txt" >/dev/null; then
    ok "GET round-trips identical content"
else
    fail "GET mismatch"
fi

if curl -fsS -X DELETE "$FILER/poc/f1.txt" -o /dev/null; then
    ok "DELETE returns OK"
else
    fail "DELETE failed"
fi

# --- F3. Persistence (run before F2 so bucket survives restart too) ---
log "F3. Persistence across container restart"
echo "persist me" > "$TMP/p.txt"
curl -fsS -F "file=@$TMP/p.txt" "$FILER/poc/persist.txt" -o /dev/null
docker restart "$NAME" >/dev/null
log "  restarted, waiting for filer to listen ..."
# Wait on the filer endpoint specifically (not master) — the healthcheck
# only gates the master process; filer/s3 come up a few seconds later.
for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "$FILER/?type=json" 2>/dev/null; then break; fi
    sleep 1
done
if curl -fsS "$FILER/poc/persist.txt" | grep -q "persist me"; then
    ok "file still readable after restart"
else
    fail "persistence lost"
fi
curl -fsS -X DELETE "$FILER/poc/persist.txt" -o /dev/null || true

# --- F2. S3 API via boto3 (ephemeral via uv) ---
log "F2. S3 API (boto3 via uv run --with)"
if uv run --with boto3 --with botocore python - <<'PY' 2>&1; then
import sys
import boto3
from botocore.client import Config

s3 = boto3.client(
    "s3",
    endpoint_url="http://127.0.0.1:18333",
    aws_access_key_id="poc-key",
    aws_secret_access_key="poc-secret",
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    region_name="us-east-1",
)

bucket = "gapt-poc"
try:
    s3.create_bucket(Bucket=bucket)
except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
    pass

s3.put_object(Bucket=bucket, Key="hello.txt", Body=b"hello s3 from poc\n")

obj = s3.get_object(Bucket=bucket, Key="hello.txt")
body = obj["Body"].read()
assert body == b"hello s3 from poc\n", f"unexpected body: {body!r}"

listing = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
assert any(o["Key"] == "hello.txt" for o in listing), "object not in listing"

s3.delete_object(Bucket=bucket, Key="hello.txt")
print("S3 round-trip OK")
PY
    ok "S3 create_bucket / put / get / list / delete"
else
    fail "S3 round-trip failed"
fi

echo
if [[ $FAIL -eq 0 ]]; then
    log "✅ Filer + S3 + persistence checks all PASS"
    exit 0
else
    log "❌ at least one check failed"
    exit 1
fi
