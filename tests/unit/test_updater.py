import hashlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_harbour.updater import (
    ReleaseAsset,
    ReleaseInfo,
    UpdateError,
    fetch_installer_asset,
    fetch_release_info,
    installer_asset_name,
    is_newer,
    normalize_tag,
    parse_checksums,
    platform_asset_name,
    run_installer,
    run_update_installer,
    update_binary,
    verify_checksum,
)


def test_normalize_tag():
    assert normalize_tag("0.1.1") == "v0.1.1"
    assert normalize_tag("v0.1.1") == "v0.1.1"


def test_is_newer():
    assert is_newer("v0.1.2", "0.1.1") is True
    assert is_newer("v0.1.1", "0.1.1") is False
    assert is_newer("v0.1.0", "0.1.1") is False


@pytest.mark.parametrize(
    ("system", "machine", "asset_name"),
    [
        ("Linux", "x86_64", "mcp-harbour-linux-x64.tar.gz"),
        ("Darwin", "arm64", "mcp-harbour-darwin-arm64.tar.gz"),
        ("Darwin", "x86_64", "mcp-harbour-darwin-arm64.tar.gz"),
        ("Windows", "AMD64", "mcp-harbour-windows-x64.zip"),
    ],
)
def test_platform_asset_name(system, machine, asset_name):
    assert platform_asset_name(system, machine) == asset_name


def test_platform_asset_name_unsupported():
    with pytest.raises(UpdateError, match="Unsupported platform"):
        platform_asset_name("Linux", "arm64")


@pytest.mark.parametrize(
    ("system", "asset_name"),
    [("Linux", "install.sh"), ("Darwin", "install.sh"), ("Windows", "install.ps1")],
)
def test_installer_asset_name(system, asset_name):
    assert installer_asset_name(system) == asset_name


def test_fetch_release_info_selects_platform_asset():
    release = {
        "tag_name": "v0.1.2",
        "assets": [
            {"name": "mcp-harbour-linux-x64.tar.gz", "browser_download_url": "https://example.com/linux"},
            {"name": "mcp-harbour-windows-x64.zip", "browser_download_url": "https://example.com/windows"},
        ],
    }

    with patch("mcp_harbour.updater._github_json", return_value=release):
        info = fetch_release_info(current_version="0.1.1", system="Linux", machine="x86_64")

    assert info.tag == "v0.1.2"
    assert info.update_available is True
    assert info.asset == ReleaseAsset("mcp-harbour-linux-x64.tar.gz", "https://example.com/linux")


def test_fetch_release_info_missing_asset():
    release = {"tag_name": "v0.1.2", "assets": []}

    with patch("mcp_harbour.updater._github_json", return_value=release):
        with pytest.raises(UpdateError, match="does not include asset"):
            fetch_release_info(current_version="0.1.1", system="Linux", machine="x86_64")


def test_fetch_installer_asset_selects_platform_installer():
    release = {
        "tag_name": "v0.1.2",
        "assets": [
            {"name": "install.sh", "browser_download_url": "https://example.com/install.sh"},
            {"name": "install.ps1", "browser_download_url": "https://example.com/install.ps1"},
        ],
    }

    with patch("mcp_harbour.updater._github_json", return_value=release):
        asset = fetch_installer_asset("v0.1.2", system="Linux")

    assert asset == ReleaseAsset("install.sh", "https://example.com/install.sh")


def test_parse_checksums_supports_common_formats():
    text = "abc123  mcp-harbour-linux-x64.tar.gz\ndef456 *install.sh\n"

    assert parse_checksums(text) == {
        "mcp-harbour-linux-x64.tar.gz": "abc123",
        "install.sh": "def456",
    }


def test_verify_checksum_success(tmp_path):
    installer = tmp_path / "install.sh"
    installer.write_text("hello")
    checksums = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824  install.sh\n"

    verify_checksum(installer, checksums)


def test_verify_checksum_failure(tmp_path):
    installer = tmp_path / "install.sh"
    installer.write_text("hello")
    checksums = "bad  install.sh\n"

    with pytest.raises(UpdateError, match="Checksum verification failed"):
        verify_checksum(installer, checksums)


def test_update_binary_check_only_does_not_run_installer():
    info = ReleaseInfo(
        tag="v0.1.2",
        asset=ReleaseAsset("mcp-harbour-linux-x64.tar.gz", "https://example.com/release.tar.gz"),
        update_available=True,
    )
    release = {"tag_name": "v0.1.2", "assets": []}
    with patch("mcp_harbour.updater._fetch_release", return_value=release), \
         patch("mcp_harbour.updater.fetch_release_info", return_value=info), \
         patch("mcp_harbour.updater.run_update_installer") as installer:
        result = update_binary(check_only=True)

    assert result is info
    installer.assert_not_called()


def test_update_binary_runs_installer_when_update_available():
    info = ReleaseInfo(
        tag="v0.1.2",
        asset=ReleaseAsset("mcp-harbour-linux-x64.tar.gz", "https://example.com/release.tar.gz"),
        update_available=True,
    )
    release = {
        "tag_name": "v0.1.2",
        "assets": [
            {"name": "install.sh", "browser_download_url": "https://example.com/install.sh"},
        ],
    }
    with patch("mcp_harbour.updater._fetch_release", return_value=release), \
         patch("mcp_harbour.updater.fetch_release_info", return_value=info), \
         patch("mcp_harbour.updater.run_update_installer") as installer:
        result = update_binary()

    assert result is info
    installer.assert_called_once_with("v0.1.2", release=release)


def test_run_update_installer_downloads_verifies_and_runs():
    release = {
        "tag_name": "v0.1.2",
        "assets": [
            {"name": "install.sh", "browser_download_url": "https://example.com/install.sh"},
        ],
    }
    installer_bytes = b"echo hi\n"
    expected_sha = hashlib.sha256(installer_bytes).hexdigest()
    checksums_text = f"{expected_sha}  install.sh\n"

    def fake_download(url, dest):
        # write_bytes (not write_text) so the on-disk content matches the hash
        # exactly — write_text translates \n -> \r\n on Windows.
        dest.write_bytes(installer_bytes)

    with patch("mcp_harbour.updater._download", side_effect=fake_download), \
         patch("mcp_harbour.updater.download_text", return_value=checksums_text), \
         patch("mcp_harbour.updater.run_installer") as installer:
        run_update_installer("v0.1.2", system="Linux", release=release)

    installer.assert_called_once()
    args, kwargs = installer.call_args
    assert args[0].name == "install.sh"
    assert args[1] == "Linux"
    assert kwargs["version"] == "v0.1.2"


def test_run_installer_passes_version_env():
    captured = {}

    def fake_run(command, check, env):
        captured["command"] = command
        captured["env"] = env

    with patch("mcp_harbour.updater.subprocess.run", side_effect=fake_run), \
         patch("pathlib.Path.chmod"):
        run_installer(Path("/tmp/install.sh"), system="Linux", version="v0.1.2")

    assert captured["env"]["MCP_HARBOUR_VERSION"] == "v0.1.2"
    assert captured["command"][0] == "bash"


def test_run_installer_without_version_inherits_env():
    captured = {}

    def fake_run(command, check, env):
        captured["env"] = env

    with patch("mcp_harbour.updater.subprocess.run", side_effect=fake_run), \
         patch("pathlib.Path.chmod"):
        run_installer(Path("/tmp/install.sh"), system="Linux")

    assert captured["env"] is None


def test_run_update_installer_aborts_on_bad_checksum():
    release = {
        "tag_name": "v0.1.2",
        "assets": [
            {"name": "install.sh", "browser_download_url": "https://example.com/install.sh"},
        ],
    }

    def fake_download(url, dest):
        dest.write_text("echo hi\n")

    with patch("mcp_harbour.updater._download", side_effect=fake_download), \
         patch("mcp_harbour.updater.download_text", return_value="deadbeef  install.sh\n"), \
         patch("mcp_harbour.updater.run_installer") as installer:
        with pytest.raises(UpdateError, match="Checksum verification failed"):
            run_update_installer("v0.1.2", system="Linux", release=release)

    installer.assert_not_called()


def test_run_installer_translates_subprocess_failure(tmp_path):
    installer_path = tmp_path / "install.sh"
    installer_path.write_text("#!/bin/sh\nexit 7\n")

    with patch(
        "mcp_harbour.updater.subprocess.run",
        side_effect=subprocess.CalledProcessError(7, ["bash"]),
    ):
        with pytest.raises(UpdateError, match="Installer exited with status 7"):
            run_installer(installer_path, system="Linux")
