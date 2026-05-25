# gapt-workspace image

Per-workspace sandbox image. Every GAPT workspace runs in a container
built from this image (one container per workspace, named
`gapt-ws-<workspace_id>`). The terminal panel, managed services, AND
the agent's `claude` CLI all execute inside that container — never on
the host.

## What's inside

- Ubuntu 24.04 base
- bash + standard userspace (apt, curl, git, ...)
- Node 20 + npm
- Python 3 + pip + venv
- **Claude Code CLI** (`claude`) — installed via `npm i -g
  @anthropic-ai/claude-code`

## Build

```sh
./build.sh           # builds gapt-workspace:latest
```

Re-run after upgrading Claude Code or whenever you want to pick up a
newer base image.

## Override

If your project needs heavier tooling (CUDA, Java, Rust toolchain,
...) layer it on top:

```dockerfile
FROM gapt-workspace:latest
RUN apt-get update && apt-get install -y --no-install-recommends rustc cargo
```

Build with a project-specific tag and point GAPT at it:

```sh
export GAPT_WORKSPACE_SANDBOX_IMAGE=my-project-workspace:latest
```

Restart the server to apply.

## Why an image (vs vanilla `ubuntu:24.04`)

The agent needs `claude` on PATH the moment it runs. If the image
didn't ship it, the first `pipeline.run()` after a workspace boot
would fail with "claude binary not found" — and we'd have to bind-
mount the host's claude binary (host-FS leak) or `apt install` it on
first use (slow + flaky network). Baking it in trades image size for
a deterministic boot.

The image is ~600 MB; small enough that storing one per machine is a
non-issue.
