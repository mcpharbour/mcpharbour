from unittest.mock import patch

from typer.testing import CliRunner

from mcp_harbour.main import app
from mcp_harbour.updater import ReleaseAsset, ReleaseInfo, UpdateError

runner = CliRunner()


def test_version_command():
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "0.1.1" in result.output


def test_update_check_reports_available_update():
    info = ReleaseInfo(
        tag="v0.1.2",
        asset=ReleaseAsset("mcp-harbour-linux-x64.tar.gz", "https://example.com/release.tar.gz"),
        update_available=True,
    )

    with patch("mcp_harbour.main.update_binary", return_value=info) as update:
        result = runner.invoke(app, ["update", "--check"])

    assert result.exit_code == 0
    assert "Update available" in result.output
    update.assert_called_once_with(tag=None, check_only=True, force=False)


def test_update_reports_up_to_date():
    info = ReleaseInfo(
        tag="v0.1.1",
        asset=ReleaseAsset("mcp-harbour-linux-x64.tar.gz", "https://example.com/release.tar.gz"),
        update_available=False,
    )

    with patch("mcp_harbour.main.update_binary", return_value=info) as update:
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert "already up to date" in result.output
    update.assert_called_once_with(tag=None, check_only=True, force=False)


def test_update_installs_after_confirmation():
    info = ReleaseInfo(
        tag="v0.1.2",
        asset=ReleaseAsset("mcp-harbour-linux-x64.tar.gz", "https://example.com/release.tar.gz"),
        update_available=True,
    )

    with patch("mcp_harbour.main.update_binary", return_value=info) as check, \
         patch("mcp_harbour.main.run_update_installer") as installer:
        result = runner.invoke(app, ["update"], input="y\n")

    assert result.exit_code == 0
    assert "Updated Harbour to v0.1.2" in result.output
    check.assert_called_once_with(tag=None, check_only=True, force=False)
    installer.assert_called_once_with("v0.1.2")


def test_update_yes_skips_confirmation():
    info = ReleaseInfo(
        tag="v0.1.2",
        asset=ReleaseAsset("mcp-harbour-linux-x64.tar.gz", "https://example.com/release.tar.gz"),
        update_available=True,
    )

    with patch("mcp_harbour.main.update_binary", return_value=info), \
         patch("mcp_harbour.main.run_update_installer") as installer:
        result = runner.invoke(app, ["update", "--yes"])

    assert result.exit_code == 0
    installer.assert_called_once_with("v0.1.2")


def test_update_reports_errors():
    with patch("mcp_harbour.main.update_binary", side_effect=UpdateError("not a release binary")):
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 1
    assert "not a release binary" in result.output


def test_update_handles_installer_failure():
    info = ReleaseInfo(
        tag="v0.1.2",
        asset=ReleaseAsset("mcp-harbour-linux-x64.tar.gz", "https://example.com/release.tar.gz"),
        update_available=True,
    )

    with patch("mcp_harbour.main.update_binary", return_value=info), \
         patch("mcp_harbour.main.run_update_installer", side_effect=UpdateError("installer exploded")):
        result = runner.invoke(app, ["update", "--yes"])

    assert result.exit_code == 1
    assert "installer exploded" in result.output
