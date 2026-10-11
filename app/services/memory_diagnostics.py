import logging
from pathlib import Path


def _read_proc_status_kb() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith(("VmRSS:", "VmHWM:", "RssAnon:", "RssFile:")):
                key, value, *_ = line.split()
                values[key.rstrip(":")] = int(value)
    except (OSError, ValueError):
        pass
    return values


def _read_cgroup_bytes(name: str) -> int | None:
    try:
        return int(Path("/sys/fs/cgroup", name).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _read_cgroup_events() -> dict[str, int]:
    events: dict[str, int] = {}
    try:
        for line in Path("/sys/fs/cgroup/memory.events").read_text(encoding="utf-8").splitlines():
            key, value = line.split(maxsplit=1)
            events[key] = int(value)
    except (OSError, ValueError):
        pass
    return events


def _mb(value: int | None) -> float | None:
    return None if value is None else value / (1024 * 1024)


def log_memory(logger: logging.Logger, label: str) -> None:
    status = _read_proc_status_kb()
    rss = status.get("VmRSS")
    hwm = status.get("VmHWM")
    anon = status.get("RssAnon")
    file_ = status.get("RssFile")
    current = _read_cgroup_bytes("memory.current")
    swap = _read_cgroup_bytes("memory.swap.current")
    events = _read_cgroup_events()
    logger.info(
        "MEMDIAG %s | RSS=%.1fMB HWM=%.1fMB anon=%.1fMB file=%.1fMB | "
        "cgroup=%.1fMB swap=%.1fMB | oom=%d oom_kill=%d max=%d",
        label,
        (rss or 0) / 1024,
        (hwm or 0) / 1024,
        (anon or 0) / 1024,
        (file_ or 0) / 1024,
        _mb(current) or 0.0,
        _mb(swap) or 0.0,
        events.get("oom", 0),
        events.get("oom_kill", 0),
        events.get("max", 0),
    )
