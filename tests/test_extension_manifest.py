"""
Unit tests for Quant Studio VS Code & Antigravity IDE Extension Manifest.
Ensures extension conforms to Visual Studio Code and Google Antigravity extension standards.
"""

import json
from pathlib import Path
import pytest


@pytest.fixture
def extension_dir():
    p = Path(__file__).parent.parent / "extensions" / "quant-studio"
    assert p.exists() and p.is_dir(), f"Extension dir not found at {p}"
    return p


@pytest.fixture
def manifest(extension_dir):
    pkg_file = extension_dir / "package.json"
    assert pkg_file.exists(), f"package.json missing at {pkg_file}"
    with open(pkg_file, "r", encoding="utf-8") as f:
        return json.load(f)


def test_manifest_metadata(manifest):
    """Validates core extension identity and dual-platform engine compatibility."""
    assert manifest["name"] == "quant-studio"
    assert manifest["publisher"] == "quant-trade-copilot"
    assert manifest["version"] == "1.0.0"
    assert "engines" in manifest
    assert "vscode" in manifest["engines"]
    # Ensures compatibility with both VS Code and Antigravity IDE
    assert manifest["engines"]["vscode"].startswith("^1.")


def test_manifest_commands(manifest):
    """Verifies that all required commands are contributed."""
    contributes = manifest.get("contributes", {})
    commands = contributes.get("commands", [])
    cmd_ids = {c["command"] for c in commands}

    required_commands = {
        "quantStudio.openChart",
        "quantStudio.debugIndicator",
        "quantStudio.runReplay",
        "quantStudio.killSession",
    }
    assert required_commands.issubset(cmd_ids), f"Missing commands: {required_commands - cmd_ids}"

    for cmd in commands:
        assert "title" in cmd
        assert "category" in cmd
        assert cmd["category"] == "Quant Studio"


def test_manifest_views_and_activitybar(manifest):
    """Verifies custom activity bar container and tree views."""
    contributes = manifest.get("contributes", {})
    containers = contributes.get("viewsContainers", {}).get("activitybar", [])
    assert len(containers) >= 1
    assert containers[0]["id"] == "quant-studio"
    assert "icon" in containers[0]

    views = contributes.get("views", {}).get("quant-studio", [])
    view_ids = {v["id"] for v in views}
    assert "quantStudio.sessionView" in view_ids
    assert "quantStudio.indicatorsView" in view_ids


def test_manifest_configuration(manifest):
    """Verifies configuration schema for server port, default symbol and auto-open."""
    props = manifest.get("contributes", {}).get("configuration", {}).get("properties", {})
    assert "quantStudio.serverPort" in props
    assert props["quantStudio.serverPort"]["type"] == "integer"
    assert "quantStudio.autoOpenChartOnDebug" in props
    assert props["quantStudio.autoOpenChartOnDebug"]["type"] == "boolean"


def test_assets_exist(extension_dir):
    """Verifies that extension README and icon SVG exist on disk."""
    readme = extension_dir / "README.md"
    icon = extension_dir / "media" / "icon.svg"
    assert readme.exists() and readme.stat().st_size > 100
    assert icon.exists() and icon.stat().st_size > 100
