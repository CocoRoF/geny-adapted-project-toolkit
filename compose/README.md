# compose/ — GAPT deployment stacks

> M0-P1 PR5 — **dev stack only**. Production compose (M1-E4 cycle 4.4 / M2-E3) adds Caddy on-demand TLS + dynamic subdomain registration + SeaweedFS replication knobs.

## Stacks

| File | Purpose |
|---|---|
| `docker-compose.dev.yml` | Dev — Postgres + Redis + SeaweedFS + control plane + Caddy edge on :8080 |
| *(future) `docker-compose.prod.yml`* | M1-E4 — production with on-demand TLS + Sysbox runtime registration |

## Boot the dev stack

```bash
# From repo root:
docker compose -f compose/docker-compose.dev.yml up -d --wait

# Verify
curl http://localhost:8080/health
curl http://localhost:9333/cluster/healthz       # SeaweedFS master

# Tear down (keeps volumes)
docker compose -f compose/docker-compose.dev.yml down

# Tear down with data wipe
docker compose -f compose/docker-compose.dev.yml down -v
```

`--wait` blocks until every service's healthcheck reports `healthy`. If a service fails, `compose ps` shows which one and `compose logs <service>` reveals why.

## Service map

```
:8080  caddy (edge)
       ├─ /_gapt/api/*   → server:8088
       ├─ /_gapt/app/*   → server:8088 (built SPA)
       ├─ /health        → server:8088 (apex; Prometheus / k8s convention)
       ├─ /              → 302 → /_gapt/app/
       └─ everything else → 404

:8088  server (FastAPI; bound to 127.0.0.1 on host for direct hits)
:5432  postgres
:6379  redis
:9333  seaweedfs master
:8888  seaweedfs filer http
:8333  seaweedfs s3
```

All ports bind to `127.0.0.1` on the host — nothing leaks to the network until you put something behind a real domain.

## Volumes

| volume | what's in it |
|---|---|
| `gapt-dev_postgres-data` | Postgres `/var/lib/postgresql/data` |
| `gapt-dev_redis-data` | Redis AOF |
| `gapt-dev_seaweed-data` | **영속 파일 코어** — workspace 데이터, audit cold archive, DB backups |
| `gapt-dev_caddy-data` | Caddy local state |
| `gapt-dev_caddy-config` | Caddy autosaved config |

per project memory: **영속 파일은 무조건 SeaweedFS. host FS는 캐시만**. The `seaweed-data` named volume is host-backed *only because dev runs on one machine* — in M4 multi-node it becomes a multi-volume SeaweedFS cluster.

## Credentials in this file

The `dev` stack pins **dev-only credentials inline** — `gapt_dev_only` for Postgres, `gapt-dev` / `gapt-dev-secret` for SeaweedFS S3, dev placeholder secrets for sessions. Real deployments override every `GAPT_*_SECRET` env var via the host's `SecretBackend` (M1-E1 cycle 1.4) and never reuse these strings.

## Plan ↔ code mapping

- `docs/plan/m0/p1_monorepo_ci.md` cycle 5 (this PR)
- `docs/plan/m0/p2_isolation_seaweedfs.md` (Sysbox sandbox runtime + SeaweedFS mount tested against the same SeaweedFS instance booted here)
- `docs/plan/m1/e4_integration_dogfood_geny.md` cycle 4.4 (Caddy admin API dynamic subdomain)
