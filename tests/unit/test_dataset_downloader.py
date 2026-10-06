from pathlib import Path
import zipfile

from scripts import download_all_datasets as downloader


def test_inspect_archive_accepts_only_complete_readable_zip(tmp_path: Path):
    target = tmp_path / "sample.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("sample.txt", "ok")
    entry = {
        "dataset": "Test",
        "name": "sample.zip",
        "url": "https://example.invalid/sample.zip",
        "bytes": target.stat().st_size,
    }

    checked = downloader.inspect_archive(entry, target)

    assert checked["status"] == "existing_verified"
    assert checked["zip_check"]["sample_read"] is True


def test_inspect_archive_reports_partial_parts(tmp_path: Path):
    target = tmp_path / "sample.zip"
    parts = tmp_path / "sample.zip.parts"
    parts.mkdir()
    (parts / "part-000000").write_bytes(b"x" * 32)
    (parts / "part-000001.partial").write_bytes(b"y" * 16)
    entry = {
        "dataset": "Test",
        "name": "sample.zip",
        "url": "https://example.invalid/sample.zip",
        "bytes": 1024,
    }

    checked = downloader.inspect_archive(entry, target)

    assert checked["status"] == "partial_parts"
    assert checked["part_count"] == 2
    assert checked["parts_bytes"] == 48


def test_dry_run_never_calls_network_for_missing_resources(tmp_path: Path, monkeypatch):
    item = dict(downloader.PILOT_ITEMS[0])
    monkeypatch.setattr(downloader, "PILOT_ROOT", tmp_path / "pilot")
    monkeypatch.setattr(downloader, "FULL_ROOT", tmp_path / "full")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("dry-run 不应调用网络下载")

    monkeypatch.setattr(downloader, "curl_download", fail_if_called)
    pilot_status = downloader.inspect_pilot(item, downloader.PILOT_ROOT / item["relative_path"])
    pilot_result = downloader.download_pilot(item, pilot_status, refresh=False, dry_run=True)
    archive = dict(downloader.ARCHIVES[0])
    archive_status = downloader.inspect_archive(archive, downloader.FULL_ROOT / archive["name"])
    archive_result = downloader.download_archive(
        archive, archive_status, refresh=False, dry_run=True, workers=1, chunk_mib=16
    )

    assert pilot_result["status"] == "missing"
    assert archive_result["status"] == "missing"


def test_human_size_is_compact():
    assert downloader.human_size(1024 * 1024) == "1.0 MiB"
    assert downloader.human_size(2 * 1024**3) == "2.0 GiB"
