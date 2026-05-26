"""Best-effort GPU sampling via `nvidia-smi`.

When the binary is missing (no NVIDIA driver / no GPU) `sample()`
returns an empty list and the rest of the dashboard treats GPU as
"not present". Hosts without NVIDIA still see the page render
cleanly — we never block on the call.

This intentionally does NOT depend on the `pynvml` or `nvidia-ml-py`
Python packages: those need a working CUDA install at process start
and break the dashboard on plain CPU boxes. `nvidia-smi` is the
universal lowest common denominator (ships with every NVIDIA driver
since R304).
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class GpuSample:
    index: int
    name: str
    driver_version: str
    utilization_pct: float  # 0..100, current GPU utilisation
    memory_used_bytes: int
    memory_total_bytes: int
    memory_pct: float  # 0..100
    temperature_c: float | None
    power_watts: float | None


_FIELDS = (
    "index,"
    "name,"
    "driver_version,"
    "utilization.gpu,"
    "memory.used,"
    "memory.total,"
    "temperature.gpu,"
    "power.draw"
)


def _have_nvidia_smi() -> bool:
    return shutil.which("nvidia-smi") is not None


async def sample() -> list[GpuSample]:
    """Returns one entry per GPU. `[]` when no NVIDIA driver is
    installed. Failures are logged + swallowed — the dashboard
    should never break because the GPU probe blew up."""
    if not _have_nvidia_smi():
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            f"--query-gpu={_FIELDS}",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
    except (asyncio.TimeoutError, FileNotFoundError, OSError) as exc:
        logger.warning("performance.gpu_probe_failed", error=str(exc))
        return []
    if proc.returncode != 0:
        return []
    out = out_b.decode("utf-8", errors="replace").strip()
    samples: list[GpuSample] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 8:
            continue
        try:
            idx = int(parts[0])
            name = parts[1]
            driver = parts[2]
            util = float(parts[3])
            # nvidia-smi memory.used / memory.total report MiB.
            mem_used = int(float(parts[4]) * 1024 * 1024)
            mem_total = int(float(parts[5]) * 1024 * 1024)
            temp = _maybe_float(parts[6])
            power = _maybe_float(parts[7])
        except (ValueError, IndexError):
            continue
        mem_pct = (mem_used / mem_total * 100.0) if mem_total else 0.0
        samples.append(
            GpuSample(
                index=idx,
                name=name,
                driver_version=driver,
                utilization_pct=util,
                memory_used_bytes=mem_used,
                memory_total_bytes=mem_total,
                memory_pct=mem_pct,
                temperature_c=temp,
                power_watts=power,
            )
        )
    return samples


def _maybe_float(s: str) -> float | None:
    """`nvidia-smi` prints `[Not Supported]` for temp/power on some
    SKUs (datacentre headless cards). Treat any non-numeric as
    "unknown" rather than blowing up the parser."""
    s = (s or "").strip()
    if not s or s.lower().startswith("[not"):
        return None
    try:
        return float(s)
    except ValueError:
        return None
