"""Conservative local worker limits, with no dependency on CUDA or a large workstation."""

from __future__ import annotations

import os
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path

_RUNTIME = ContextVar("adhesion_resources", default=None)


@contextmanager
def resource_scope(*, workers=0, memory_mb=2048, progress=None):
    token = _RUNTIME.set({"workers": workers, "memory_mb": memory_mb, "progress": progress})
    try:
        yield
    finally:
        _RUNTIME.reset(token)


def runtime_options():
    return _RUNTIME.get() or {}


def available_memory_mb():
    available = 1024
    with suppress(OSError, ValueError):
        values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        available = int(values["MemAvailable"].split()[0]) // 1024
    # Respect container limits as well as host RAM.
    with suppress(OSError, ValueError):
        limit = int(Path("/sys/fs/cgroup/memory.max").read_text())
        used = int(Path("/sys/fs/cgroup/memory.current").read_text())
        available = min(available, max(0, (limit - used) // (1024 * 1024)))
    return available


def available_cpus():
    cpus = os.cpu_count() or 1
    with suppress(AttributeError, OSError):
        cpus = len(os.sched_getaffinity(0))
    # CPU affinity can expose the host's CPUs while a container has a smaller quota.
    with suppress(OSError, ValueError):
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max" and int(period) > 0:
            cpus = min(cpus, max(1, int(quota) // int(period)))
    with suppress(OSError, ValueError):
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if quota > 0 and period > 0:
            cpus = min(cpus, max(1, quota // period))
    return max(1, cpus)


def worker_limit(requested=0, memory_mb=2048, source_bytes=0):
    cpus = available_cpus()
    budget = min(memory_mb, max(256, available_memory_mb() // 4))
    per_worker = 256 + 2 * (source_bytes // (1024 * 1024))
    memory_limit = max(1, budget // per_worker)
    cpu_limit = max(1, cpus - 1)
    return max(1, min(requested or min(8, max(1, cpus // 2)), cpu_limit, memory_limit))
