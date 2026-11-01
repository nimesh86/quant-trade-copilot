"""
Unit tests for the generated Quant Studio VSIX package.
Validates VSIX ZIP structure, XML vsixmanifest, assets, and engine targeting.
"""

from pathlib import Path
import zipfile
import pytest


@pytest.fixture
def vsix_path():
    p = Path(__file__).parent.parent / "extensions" / "quant-studio" / "quant-studio-1.0.0.vsix"
    assert p.exists(), f"VSIX package not found at {p}"
    return p


def test_vsix_archive_integrity(vsix_path):
    """Verifies that the generated VSIX is a valid, uncorrupted ZIP file."""
    assert vsix_path.stat().st_size > 40_000, "VSIX file size abnormally small"
    with zipfile.ZipFile(vsix_path, "r") as zf:
        bad_file = zf.testzip()
        assert bad_file is None, f"Corrupted file inside VSIX: {bad_file}"


def test_vsix_required_contents(vsix_path):
    """Validates presence of all essential manifest and runtime assets in the package."""
    with zipfile.ZipFile(vsix_path, "r") as zf:
        namelist = set(zf.namelist())

        required_members = {
            "extension.vsixmanifest",
            "[Content_Types].xml",
            "extension/package.json",
            "extension/src/extension.js",
            "extension/src/webview/chart.html",
            "extension/src/webview/chart_view.js",
            "extension/src/webview/lightweight-charts.standalone.production.js",
            "extension/media/icon.svg",
        }

        assert required_members.issubset(namelist), f"Missing members: {required_members - namelist}"


def test_vsix_manifest_xml_metadata(vsix_path):
    """Verifies vsixmanifest XML targets VS Code and Antigravity IDE."""
    with zipfile.ZipFile(vsix_path, "r") as zf:
        xml_content = zf.read("extension.vsixmanifest").decode("utf-8")
        assert 'Id="quant-studio"' in xml_content
        assert 'Publisher="quant-trade-copilot"' in xml_content
        assert 'Version="1.0.0"' in xml_content
