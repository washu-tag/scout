from __future__ import annotations

import pytest


PROC_MOUNTS_BASIC = """\
sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
/dev/sda1 /scout/data ext4 rw,relatime 0 0
/dev/sda2 /home_prod ext4 rw,nosuid,nodev,relatime 0 0
beegfs-ctl /shared fuse.beegfs ro,nosuid,nodev,relatime 0 0
beegfs-ctl /rad fuse.beegfs rw,nosuid,nodev,relatime 0 0
"""

PROC_MOUNTS_MINIMAL = """\
sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
"""

PROC_MEMINFO_256GB = """\
MemTotal:       263458816 kB
MemFree:        100000000 kB
MemAvailable:   200000000 kB
"""

PROC_MEMINFO_8GB = """\
MemTotal:       8388608 kB
MemFree:        4000000 kB
MemAvailable:   6000000 kB
"""


@pytest.fixture
def proc_mounts_file(tmp_path):
    """Create a temporary /proc/mounts file with basic mount entries."""
    p = tmp_path / "mounts"
    p.write_text(PROC_MOUNTS_BASIC)
    return str(p)


@pytest.fixture
def proc_mounts_minimal(tmp_path):
    """Create a minimal /proc/mounts with only system mounts."""
    p = tmp_path / "mounts"
    p.write_text(PROC_MOUNTS_MINIMAL)
    return str(p)


@pytest.fixture
def proc_meminfo_256gb(tmp_path):
    """Create /proc/meminfo simulating ~256GB system."""
    p = tmp_path / "meminfo"
    p.write_text(PROC_MEMINFO_256GB)
    return str(p)


@pytest.fixture
def proc_meminfo_8gb(tmp_path):
    """Create /proc/meminfo simulating ~8GB system."""
    p = tmp_path / "meminfo"
    p.write_text(PROC_MEMINFO_8GB)
    return str(p)
