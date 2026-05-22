#!/usr/bin/env bash
# Time five canonical git operations against the same repo cloned into
# three locations inside a Sysbox sandbox:
#
#   (a) /workspace/...      — SeaweedFS FUSE mount  (option B in
#                              decision_volume_driver.md)
#   (b) /var/lib/inner/...  — sandbox-private path (overlayfs on the
#                              sandbox's own root — fast, transient)
#   (c) /host-tmp/...       — host /tmp bind-mounted into the sandbox
#                              (a deliberately-thin control: plain
#                              ext4 with no FUSE in the way)
#
# Five operations, five runs each, mean reported per location. The
# output is appended to perf_seaweed_vs_host.md so the decision in
# decision_volume_driver.md is grounded in numbers, not vibes.

set -uo pipefail

cd "$(dirname "$0")" || exit 1

NAME="gapt-bench-poc"
HOST_BENCH_DIR="${HOST_BENCH_DIR:-/tmp/gapt-bench}"
REPO_URL="${BENCH_REPO_URL:-https://github.com/pre-commit/pre-commit.git}"
REPO_REF_OLD="${BENCH_REPO_REF_OLD:-HEAD~50}"
OUT="$(pwd)/perf_seaweed_vs_host.md"

log() { printf '[bench_git %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# Boot SeaweedFS + sandbox with the host-bind extra.
log "step 1/5: SeaweedFS"
docker compose -f seaweedfs.compose.yml up -d --wait --wait-timeout 60 >/dev/null
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "http://127.0.0.1:18888/?type=json" 2>/dev/null; then break; fi
    sleep 1
done
curl -fsS -X POST \
    "http://127.0.0.1:18888/projects/poc/workspaces/w1/.keep" \
    -F "file=@/dev/null;filename=.keep" -o /dev/null

log "step 2/5: prepare host /tmp/gapt-bench for the (c) bind mount"
mkdir -p "$HOST_BENCH_DIR"
rm -rf "${HOST_BENCH_DIR:?}"/*

log "step 3/5: boot sandbox with --volume $HOST_BENCH_DIR:/host-tmp"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d \
    --name "$NAME" \
    --runtime=sysbox-runc \
    --network gapt-poc-seaweed \
    --device /dev/fuse:/dev/fuse \
    -v "$HOST_BENCH_DIR:/host-tmp" \
    -e "GAPT_SEAWEED_FILER_URL=http://seaweedfs:8888" \
    -e "GAPT_PROJECT_ID=poc" \
    -e "GAPT_WORKSPACE_ID=w1" \
    gapt/runtime:dev \
    /usr/local/bin/gapt-entrypoint sleep infinity >/dev/null

log "  waiting for /workspace mount ..."
for _ in $(seq 1 60); do
    if docker exec "$NAME" mountpoint -q /workspace; then
        log "  /workspace ready"
        break
    fi
    sleep 1
done

log "step 4/5: clone $REPO_URL into three locations"
docker exec "$NAME" bash -lc "
    set -e
    mkdir -p /workspace/bench-a /var/lib/inner-bench /host-tmp/bench-c
    git clone --quiet $REPO_URL /workspace/bench-a
    git clone --quiet $REPO_URL /var/lib/inner-bench/repo
    git clone --quiet $REPO_URL /host-tmp/bench-c
" 2>&1 | tail -3

declare -A PATHS=(
    [a]="/workspace/bench-a"
    [b]="/var/lib/inner-bench/repo"
    [c]="/host-tmp/bench-c"
)
OPS=(
    'status:git status --short'
    'log:git log --oneline -100'
    'diff:git diff HEAD~5'
    'switch_back:git checkout '"$REPO_REF_OLD"' --quiet && git checkout - --quiet'
    'commit_reset:git add -A && git -c user.email=bench@gapt -c user.name=bench commit -m bench --allow-empty --quiet && git reset --soft HEAD~1'
)
RUNS=20

log "step 5/5: measure ($RUNS runs each, bash builtin time @ ms precision)"
declare -A RESULTS
for op_spec in "${OPS[@]}"; do
    op_name="${op_spec%%:*}"
    op_cmd="${op_spec#*:}"
    for loc in a b c; do
        repo="${PATHS[$loc]}"
        # Run each op `$RUNS` times back-to-back inside the same shell
        # so the bash builtin `time` measures the whole batch — gives
        # us millisecond resolution even on ops that finish well under
        # 10ms (the floor of coreutils `time -p`).
        elapsed_s=$(docker exec "$NAME" bash -lc "
            cd $repo
            TIMEFORMAT='%R'
            { time { for _ in \$(seq 1 $RUNS); do $op_cmd >/dev/null 2>&1; done; } ; } 2>&1
        ")
        avg_ms=$(awk -v t="$elapsed_s" -v n="$RUNS" 'BEGIN { printf "%d", (t * 1000 / n) }')
        RESULTS["${op_name}_${loc}"]="$avg_ms"
    done
done

# Emit a fresh table in perf_seaweed_vs_host.md
log "writing $OUT"
{
    echo "# SeaweedFS Mount vs host bind — git operation performance"
    echo
    echo "> Plan: [\`../../docs/plan/m0/p2_isolation_seaweedfs.md\`](../../docs/plan/m0/p2_isolation_seaweedfs.md) §7"
    echo "> Progress: [\`../../docs/progress/m0/p2_isolation_seaweedfs.md\`](../../docs/progress/m0/p2_isolation_seaweedfs.md)"
    echo
    echo "Measured by \`poc/sysbox_isolation/bench_git.sh\`. Repo: \`$REPO_URL\`."
    echo "Each row is mean of $RUNS runs in milliseconds. \`(a)\` is option B from \`decision_volume_driver.md\`; \`(b)\` is the sandbox's own overlayfs root (transient); \`(c)\` is a host bind mount as a control."
    echo
    echo "## Numbers ($(date -u +%Y-%m-%dT%H:%MZ))"
    echo
    echo "| Operation | (a) SeaweedFS FUSE | (b) inner overlayfs | (c) host bind | Ratio (a/c) |"
    echo "|---|---|---|---|---|"
    for op_spec in "${OPS[@]}"; do
        op_name="${op_spec%%:*}"
        a="${RESULTS["${op_name}_a"]}"; b="${RESULTS["${op_name}_b"]}"; c="${RESULTS["${op_name}_c"]}"
        if [ "$c" -gt 0 ]; then
            ratio=$(awk "BEGIN { printf \"%.2fx\", $a/$c }")
        else
            ratio="—"
        fi
        printf '| %s | %s ms | %s ms | %s ms | %s |\n' "$op_name" "$a" "$b" "$c" "$ratio"
    done
    echo
    echo "## Reading"
    echo
    echo "Ratios above ~3x mean SeaweedFS FUSE has a real overhead worth carving out a hybrid layout for; ratios within 2x make a single-mount option-B layout fine. The decision in \`decision_volume_driver.md\` is revised in line with whatever this run says."
} > "$OUT"

head -30 "$OUT"

log "tearing down sandbox + SeaweedFS"
# Sysbox remaps uids inside the sandbox, so files created in /host-tmp
# are owned by an unmapped uid on the host. Sweep them from inside the
# container (where we are uid 0) before tearing the sandbox down.
docker exec "$NAME" rm -rf /host-tmp/bench-c 2>/dev/null || true
docker rm -f "$NAME" >/dev/null
docker compose -f seaweedfs.compose.yml down -v >/dev/null
rmdir "$HOST_BENCH_DIR" 2>/dev/null || true
log "✅ done — results in $OUT"
