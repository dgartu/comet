import io
import stat
import tarfile
import zipfile
from types import SimpleNamespace

import pytest

from deployment import install_par2


def _release_archive(
    *,
    name: str = "par2",
    mode: int = stat.S_IFREG | 0o755,
    extra: bool = False,
) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        member = zipfile.ZipInfo(name)
        member.create_system = 3
        member.external_attr = mode << 16
        archive.writestr(member, b"calculator")
        if extra:
            archive.writestr("unexpected", b"payload")
    return payload.getvalue()


def _source_archive() -> bytes:
    payload = io.BytesIO()
    root = f"par2cmdline-turbo-{install_par2.COMMIT}"
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for name in ("COPYING", "AUTHORS"):
            content = name.encode()
            member = tarfile.TarInfo(f"{root}/{name}")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return payload.getvalue()


def test_release_archive_accepts_only_the_expected_executable():
    assert install_par2.extract_release(_release_archive()) == b"calculator"

    for archive in (
        _release_archive(name="../par2"),
        _release_archive(mode=stat.S_IFREG | 0o644),
        _release_archive(mode=stat.S_IFLNK | 0o755),
        _release_archive(extra=True),
    ):
        with pytest.raises(RuntimeError):
            install_par2.extract_release(archive)


def test_failed_version_check_removes_partial_install(tmp_path, monkeypatch):
    payloads = iter((_release_archive(), _source_archive()))
    monkeypatch.setattr(
        install_par2,
        "download_https",
        lambda *_args: next(payloads),
    )
    monkeypatch.setattr(
        install_par2.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="failed",
        ),
    )
    output = tmp_path / "par2"

    with pytest.raises(RuntimeError, match="version check"):
        install_par2.install("amd64", output)

    assert not output.exists()
