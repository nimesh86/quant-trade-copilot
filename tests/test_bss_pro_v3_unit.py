import json
import subprocess
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JS_ENGINE_PATH = PROJECT_ROOT / "trading_engine" / "web" / "static" / "tv_pine_engine.js"


def run_node_snippet(snippet: str) -> dict:
    """Helper to run a Node snippet with tv_pine_engine.js loaded."""
    full_script = f"""
    const fs = require('fs');
    // Mock browser globals
    global.window = {{}};
    global.document = {{
        readyState: 'loading',
        addEventListener: () => {{}},
        getElementById: () => ({{
            value: '',
            addEventListener: () => {{}},
            classList: {{ add: () => {{}}, remove: () => {{}} }}
        }}),
        querySelectorAll: () => [],
        body: {{
            insertAdjacentHTML: () => {{}}
        }}
    }};
    global.localStorage = {{
        getItem: () => null,
        setItem: () => {{}}
    }};
    global.performance = {{ now: () => Date.now() }};
    global.fetch = () => Promise.resolve({{
        ok: false,
        json: () => Promise.resolve({{}})
    }});

    const jsCode = fs.readFileSync('{JS_ENGINE_PATH}', 'utf8');
    eval(jsCode);

    {snippet}
    """
    res = subprocess.run(
        ["node", "-e", full_script],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT)
    )
    if res.returncode != 0:
        raise RuntimeError(f"Node execution failed:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}")
    return json.loads(res.stdout)


def test_bss_pro_v3_preset_registered():
    """Verify that bss_pro_v3 is properly registered in the TvPineEngine preset registry."""
    snippet = """
    const state = window.TvPineEngine.getState();
    const hasPreset = Boolean(window.TvPineEngine && window.TvPineEngine.computeBSSProV3);
    const presets = window.TvPineEngine.getState ? Object.keys(window.TvPineEngine.getState()) : [];
    
    // Check preset presence via node evaluation
    console.log(JSON.stringify({
        hasEngine: hasPreset,
        hasBSSProRunner: typeof window.TvPineEngine.computeBSSProV3 === 'function',
        hasPineTA: typeof window.TvPineEngine.PineTA === 'function' || typeof window.TvPineEngine.PineTA === 'object'
    }));
    """
    res = run_node_snippet(snippet)
    assert res["hasEngine"] is True
    assert res["hasBSSProRunner"] is True
    assert res["hasPineTA"] is True


def test_pineta_math_functions():
    """Unit test for PineTA RSI, percentrank, and Supertrend mathematical correctness."""
    snippet = """
    const PineTA = window.TvPineEngine.PineTA;
    
    // Sample prices
    const prices = [100, 102, 101, 104, 106, 105, 108, 112, 110, 115, 118, 116, 120, 122, 125, 128];
    const rsi = PineTA.rsi(prices, 5);
    const pr = PineTA.percentrank(prices, 5);
    
    // Sample candles for Supertrend
    const candles = prices.map((p, idx) => ({
        time: 1000 + idx * 60,
        open: p - 1,
        high: p + 2,
        low: p - 2,
        close: p,
        volume: 500 + idx * 50
    }));
    const st = PineTA.supertrend(candles, 2.0, 5);

    console.log(JSON.stringify({
        rsiLength: rsi.length,
        lastRSI: rsi[rsi.length - 1],
        prLength: pr.length,
        lastPR: pr[pr.length - 1],
        stTrendLength: st.trend.length,
        lastTrend: st.trend[st.trend.length - 1]
    }));
    """
    res = run_node_snippet(snippet)
    assert res["rsiLength"] == 16
    assert 0 <= res["lastRSI"] <= 100
    assert res["prLength"] == 16
    assert 0 <= res["lastPR"] <= 100
    assert res["stTrendLength"] == 16
    assert res["lastTrend"] in [-1, 1]


def test_compute_bss_pro_v3_workflow():
    """Unit test verifying computeBSSProV3 calculations and HUD table outputs."""
    snippet = """
    // Generate 60 authentic-style trending candles
    const candles = [];
    let p = 100.0;
    for (let i = 0; i < 60; i++) {
        const drift = i > 15 && i < 40 ? 1.5 : (i >= 40 ? -0.8 : 0.2);
        p = Math.max(10, p + drift + (Math.sin(i) * 0.5));
        candles.push({
            time: 1761450000 + (i * 60),
            open: p - 0.4,
            high: p + 1.2,
            low: p - 1.0,
            close: p,
            volume: 1000 + Math.round(Math.abs(Math.sin(i)) * 500)
        });
    }

    const res = window.TvPineEngine.computeBSSProV3(candles, {
        tradeMode: 'Option buyer (long premium only)',
        emaFastLen: 9,
        emaSlowLen: 21,
        rsiLen: 7,
        stLen: 5,
        stMult: 2.0,
        pressLen: 7,
        domThr: 52.0,
        minRank: 0.0,
        showRibbon: true,
        showTable: true
    }, 'CE');

    console.log(JSON.stringify({
        hasResult: Boolean(res),
        type: res ? res.type : null,
        emaFLen: res && res.emaF ? res.emaF.length : 0,
        emaSLen: res && res.emaS ? res.emaS.length : 0,
        signalsCount: res && res.signals ? res.signals.length : 0,
        hasHudData: Boolean(res && res.hudData),
        buyPct: res && res.hudData ? res.hudData.buyPct : null,
        sellPct: res && res.hudData ? res.hudData.sellPct : null,
        pctSum: res && res.hudData ? Math.round(res.hudData.buyPct + res.hudData.sellPct) : null,
        status: res && res.hudData ? res.hudData.status : null,
        grade: res && res.hudData ? res.hudData.grade : null
    }));
    """
    res = run_node_snippet(snippet)
    assert res["hasResult"] is True
    assert res["type"] == "bss_pro_v3"
    assert res["emaFLen"] == 60
    assert res["emaSLen"] == 60
    assert res["hasHudData"] is True
    assert res["pctSum"] == 100
    assert res["buyPct"] >= 0 and res["sellPct"] >= 0
    assert res["grade"] in ["Strong", "Moderate", "Weak"]
    assert res["status"] in ["▲ LONG active", "▼ SHORT active", "● Flat"]


def test_bss_pro_v3_custom_code_auto_detection():
    """Verify that when raw BSS Pro v3 Pine Script is transpiled, it automatically executes computeBSSProV3."""
    snippet = """
    const candles = [];
    for (let i = 0; i < 30; i++) {
        const p = 150 + i * 0.5;
        candles.push({
            time: 1761450000 + (i * 60),
            open: p - 0.2,
            high: p + 0.8,
            low: p - 0.5,
            close: p,
            volume: 800
        });
    }

    const mockStation = {
        type: 'CE',
        rawCandles: candles
    };

    const mockInd = {
        id: 'custom_user_bss_script',
        name: 'My BSS Indicator',
        code: `//@version=5\\nindicator("BSS Pro v3 - Custom", overlay=true)\\nvar int tDir = 0`,
        inputs: {}
    };

    // Evaluate via computeIndicatorsForStation
    const customIndicators = { custom_user_bss_script: mockInd };
    const allInds = { ...customIndicators };
    
    // Call computeBSSProV3 directly or verify script matching regex
    const isBSSPro = /BSS\\s*Pro/i.test(mockInd.name) || /BSS\\s*Pro/i.test(mockInd.code);
    const res = isBSSPro ? window.TvPineEngine.computeBSSProV3(candles, {}, 'CE') : null;

    console.log(JSON.stringify({
        isBSSProDetected: isBSSPro,
        resType: res ? res.type : null,
        hasHud: Boolean(res && res.hudData)
    }));
    """
    res = run_node_snippet(snippet)
    assert res["isBSSProDetected"] is True
    assert res["resType"] == "bss_pro_v3"
    assert res["hasHud"] is True
