"""Unit tests for the durable service manifest
(`domains/services/manifest.py`) — the on-disk desired-state that lets
boot reconcile adopt/restart dev services with their full definition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gapt_server.domains.services.manifest import (
    DESIRED_RUNNING,
    DESIRED_STOPPED,
    ServiceManifest,
    delete_manifest,
    list_manifests,
    manifest_path,
    read_manifest,
    update_manifest,
    write_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    m = ServiceManifest(
        label="dev",
        cmd="npm run dev",
        user_port=3000,
        env={"DATABASE_URL": "postgres://x", "PORT": "3000"},
        expose={"host": "gapt.example/preview/ws-dev", "url": "https://..."},
    )
    write_manifest(str(tmp_path), m)
    got = read_manifest(str(tmp_path), "dev")
    assert got is not None
    assert got.cmd == "npm run dev"
    assert got.user_port == 3000
    assert got.env == {"DATABASE_URL": "postgres://x", "PORT": "3000"}
    assert got.expose == {"host": "gapt.example/preview/ws-dev", "url": "https://..."}
    assert got.desired_state == DESIRED_RUNNING


def test_manifest_path_is_under_gapt_services(tmp_path: Path) -> None:
    p = manifest_path(str(tmp_path), "web")
    assert p == tmp_path / ".gapt" / "services" / "web.svc.json"


def test_update_desired_state(tmp_path: Path) -> None:
    write_manifest(str(tmp_path), ServiceManifest(label="dev", cmd="x"))
    update_manifest(str(tmp_path), "dev", desired_state=DESIRED_STOPPED)
    assert read_manifest(str(tmp_path), "dev").desired_state == DESIRED_STOPPED


def test_update_expose_and_clear(tmp_path: Path) -> None:
    write_manifest(str(tmp_path), ServiceManifest(label="dev", cmd="x"))
    update_manifest(str(tmp_path), "dev", expose={"host": "h", "url": "u"})
    assert read_manifest(str(tmp_path), "dev").expose == {"host": "h", "url": "u"}
    update_manifest(str(tmp_path), "dev", clear_expose=True)
    assert read_manifest(str(tmp_path), "dev").expose is None


def test_update_missing_manifest_is_noop(tmp_path: Path) -> None:
    # No manifest written — update must not create one or raise.
    update_manifest(str(tmp_path), "ghost", desired_state=DESIRED_STOPPED)
    assert read_manifest(str(tmp_path), "ghost") is None


def test_delete_then_absent(tmp_path: Path) -> None:
    write_manifest(str(tmp_path), ServiceManifest(label="dev", cmd="x"))
    delete_manifest(str(tmp_path), "dev")
    assert read_manifest(str(tmp_path), "dev") is None
    # Idempotent — deleting again doesn't raise.
    delete_manifest(str(tmp_path), "dev")


def test_list_skips_corrupt_files(tmp_path: Path) -> None:
    write_manifest(str(tmp_path), ServiceManifest(label="good", cmd="run"))
    services_dir = tmp_path / ".gapt" / "services"
    (services_dir / "broken.svc.json").write_text("{ not json", encoding="utf-8")
    # Missing the load-bearing `cmd` field → from_json returns None.
    (services_dir / "partial.svc.json").write_text('{"label":"partial"}', encoding="utf-8")
    labels = sorted(m.label for m in list_manifests(str(tmp_path)))
    assert labels == ["good"]


def test_list_empty_when_no_dir(tmp_path: Path) -> None:
    assert list_manifests(str(tmp_path)) == []


def test_from_json_defaults_unknown_desired_state() -> None:
    m = ServiceManifest.from_json({"label": "x", "cmd": "c", "desired_state": "bogus"})
    assert m is not None
    assert m.desired_state == DESIRED_RUNNING


def test_from_json_rejects_missing_cmd() -> None:
    assert ServiceManifest.from_json({"label": "x"}) is None
    assert ServiceManifest.from_json({"cmd": "c"}) is None
