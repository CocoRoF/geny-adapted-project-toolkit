"""Storage domain — D5 (SeaweedFS volume lifecycle).

`VolumeManager` is the protocol the workspace/sandbox layer talks to;
M1-E1 ships:

- ``InMemoryVolumeManager`` — for tests + dev runs without SeaweedFS.
- ``FilerVolumeManager`` — talks to SeaweedFS filer HTTP API (per
  ``poc/sysbox_isolation/decision_volume_driver.md`` Option B).

The runtime image's ``mount_seaweedfs_workspace`` script (see
``runtime/scripts/entrypoint.sh``) does the actual FUSE mount inside
the sandbox; the manager's job is only to allocate / track / purge
the *namespaced path* and ship the connection details into the
sandbox's environment.
"""

from gapt_server.domains.storage.volume import (
    FilerVolumeManager,
    InMemoryVolumeManager,
    VolumeManager,
    VolumeManagerError,
    VolumeRef,
)

__all__ = [
    "FilerVolumeManager",
    "InMemoryVolumeManager",
    "VolumeManager",
    "VolumeManagerError",
    "VolumeRef",
]
