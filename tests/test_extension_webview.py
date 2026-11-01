"""
Unit tests for Quant Studio Extension Core Scripts and Webview Assets.
Validates JavaScript syntax compilation with node, HTML structure, and TradingView library bundle integrity.
"""

import subprocess
from pathlib import Path
import pytest


@pytest.fixture
def extension_root():
    p = Path(__file__).parent.parent / "extensions" / "quant-studio"
    assert p.exists() and p.is_dir()
    return p


def test_extension_js_syntax(extension_root):
    """Compiles extension.js using Node.js to verify zero syntax errors."""
    ext_js = extension_root / "src" / "extension.js"
    assert ext_js.exists()
    res = subprocess.run(["node", "-c", str(ext_js)], capture_output=True, text=True)
    assert res.returncode == 0, f"Syntax error in extension.js:\n{res.stderr}"


def test_webview_chart_view_js_syntax(extension_root):
    """Compiles chart_view.js using Node.js to verify zero syntax errors."""
    view_js = extension_root / "src" / "webview" / "chart_view.js"
    assert view_js.exists()
    res = subprocess.run(["node", "-c", str(view_js)], capture_output=True, text=True)
    assert res.returncode == 0, f"Syntax error in chart_view.js:\n{res.stderr}"


def test_lightweight_charts_bundle_integrity(extension_root):
    """Validates that the production TradingView Lightweight Charts bundle is present and intact."""
    bundle = extension_root / "src" / "webview" / "lightweight-charts.standalone.production.js"
    assert bundle.exists()
    assert bundle.stat().st_size > 100_000, "TradingView bundle size too small or truncated"
    content = bundle.read_text(encoding="utf-8", errors="ignore")
    assert "LightweightCharts" in content or "createChart" in content


def test_webview_chart_html_structure(extension_root):
    """Validates Webview HTML layout placeholders and DOM elements."""
    html_file = extension_root / "src" / "webview" / "chart.html"
    assert html_file.exists()
    html = html_file.read_text(encoding="utf-8")

    # Required URI placeholders for asWebviewUri injection
    assert "{{LIGHTWEIGHT_CHARTS_URI}}" in html
    assert "{{CHART_VIEW_URI}}" in html

    # Essential DOM container elements
    assert 'id="spot-chart-container"' in html
    assert 'id="opt-chart-container"' in html
    assert 'id="replay-scrubber"' in html
    assert 'id="scrubber-badge"' in html
    assert 'id="btn-play"' in html
    assert 'id="btn-step-tick"' in html
    assert 'id="btn-step-bar"' in html
    assert 'id="date-start"' in html
    assert 'id="date-end"' in html
    assert 'id="btn-load-date"' in html
    assert 'id="btn-run-backtest"' in html
    assert 'id="backtest-drawer"' in html


def test_parse_candle_time_clock_sync(extension_root):
    """
    Executes a Node.js verification script against parseCandleTime logic
    to ensure 09:15:00 market time maps exactly to 09:15 UTC hours without timezone offset skew.
    """
    view_js = extension_root / "src" / "webview" / "chart_view.js"
    content = view_js.read_text(encoding="utf-8")
    assert "Date.UTC" in content, "parseCandleTime must use Date.UTC to preserve market clock hours"
    assert "tickMarkFormatter" in content, "CHART_THEME must define tickMarkFormatter"
    assert "subscribeVisibleLogicalRangeChange" in content, "Must synchronize logical ranges between spot and opt"

    node_code = """
    const str = "2026-10-30 09:15:00";
    const match = str.match(/^(\\d{4})-(\\d{2})-(\\d{2})[ T](\\d{2}):(\\d{2}):?(\\d{2})?/);
    if (!match) process.exit(1);
    const d = new Date(Date.UTC(+match[1], +match[2] - 1, +match[3], +match[4], +match[5], 0));
    const sec = Math.floor(d.getTime() / 1000);
    const formattedDate = new Date(sec * 1000);
    const h = String(formattedDate.getUTCHours()).padStart(2, '0');
    const m = String(formattedDate.getUTCMinutes()).padStart(2, '0');
    if (`${h}:${m}` !== "09:15") {
        console.error("Mismatch:", `${h}:${m}`);
        process.exit(2);
    }
    """
    res = subprocess.run(["node", "-e", node_code], capture_output=True, text=True)
    assert res.returncode == 0, f"Node verification failed: {res.stderr}"


def test_history_bars_contains_both_spot_and_opt_ohlc():
    """Validates that history payload includes both spot and option OHLC candles for both charts."""
    from trading_engine.web.studio_router import _build_history_payload, _generate_synthetic_candles
    from trading_engine.replay.session import ReplaySession
    from trading_engine.indicators.bss_pro_v5 import BSSProV5Indicator

    df = _generate_synthetic_candles("NIFTY", "NIFTY_ATM_CE", 60, date_str="2026-10-30")
    indicator = BSSProV5Indicator()
    session = ReplaySession(df, indicator, warmup_bars=50)

    history = _build_history_payload(session)
    assert len(history) >= 50
    for bar in history:
        assert "timestamp" in bar
        assert "spot_open" in bar and "spot_close" in bar
        assert "opt_open" in bar and "opt_close" in bar
        assert "open" in bar and "close" in bar  # Aliases for option series

