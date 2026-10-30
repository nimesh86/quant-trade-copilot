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


def test_bss_pro_v5_preset_registration():
    """Verify that bss_pro_v5 is registered with full inputs and runner."""
    snippet = """
    const hasV5Runner = typeof window.TvPineEngine.computeBSSProV5 === 'function';
    const hasV3Runner = typeof window.TvPineEngine.computeBSSProV3 === 'function';
    
    console.log(JSON.stringify({
        hasV5Runner,
        hasV3Runner
    }));
    """
    res = run_node_snippet(snippet)
    assert res["hasV5Runner"] is True
    assert res["hasV3Runner"] is True


def test_bss_pro_v5_dynamic_calculations_no_hardcoding():
    """Verify that entry prices, buyer/seller strength %, PnL %, targets, and room-to-wall are dynamically calculated bar-by-bar."""
    snippet = """
    // Candle series 1: Rising prices starting at 100
    const candlesA = [];
    let pA = 100.0;
    for (let i = 0; i < 80; i++) {
        const isBull = (i % 4 !== 3);
        const delta = isBull ? 1.5 : -1.0;
        pA = Math.max(10, pA + delta);
        candlesA.push({
            time: 1761450000 + (i * 60),
            open: isBull ? pA - 1.2 : pA + 0.8,
            high: isBull ? pA + 0.3 : pA + 1.0,
            low: isBull ? pA - 1.5 : pA - 0.3,
            close: pA,
            volume: 1000
        });
    }

    const resA = window.TvPineEngine.computeBSSProV5(candlesA, {
        tradeMode: 'Option buyer (long premium only)',
        emaFastLen: 9, emaSlowLen: 21, rsiLen: 14, stLen: 10, stMult: 2.0,
        pressLen: 7, domThr: 52.0, minRank: 0.0, rsiHi: 85.0
    }, 'CE');

    // Candle series 2: Different scale (e.g. stock around 450)
    const candlesB = [];
    let pB = 450.0;
    for (let i = 0; i < 80; i++) {
        const isBull = (i % 4 !== 3);
        const delta = isBull ? 4.5 : -3.0;
        pB = Math.max(10, pB + delta);
        candlesB.push({
            time: 1761450000 + (i * 60),
            open: isBull ? pB - 3.6 : pB + 2.4,
            high: isBull ? pB + 1.0 : pB + 3.0,
            low: isBull ? pB - 4.5 : pB - 1.0,
            close: pB,
            volume: 3500
        });
    }

    const resB = window.TvPineEngine.computeBSSProV5(candlesB, {
        tradeMode: 'Option buyer (long premium only)',
        emaFastLen: 9, emaSlowLen: 21, rsiLen: 14, stLen: 10, stMult: 2.0,
        pressLen: 7, domThr: 52.0, minRank: 0.0, rsiHi: 85.0
    }, 'CE');

    console.log(JSON.stringify({
        resA_type: resA.type,
        resA_cardsCount: resA.cards.length,
        resA_boxesCount: resA.boxes.length,
        resA_buyPct: resA.hudData.buyPct,
        resA_sellPct: resA.hudData.sellPct,
        resA_entry: resA.cards.length > 0 ? resA.cards[0].lines[1] : null,

        resB_type: resB.type,
        resB_cardsCount: resB.cards.length,
        resB_boxesCount: resB.boxes.length,
        resB_buyPct: resB.hudData.buyPct,
        resB_sellPct: resB.hudData.sellPct,
        resB_entry: resB.cards.length > 0 ? resB.cards[0].lines[1] : null,

        // Verify multi-line structure of first card in series A
        firstCardLines: resA.cards.length > 0 ? resA.cards[0].lines : [],
        hasExitCard: resA.cards.some(c => c.type === 'exit'),
        resTxt: resA.hudData.resTxt
    }));
    """
    res = run_node_snippet(snippet)
    assert res["resA_type"] == "bss_pro_v5"
    assert res["resB_type"] == "bss_pro_v5"
    assert res["resA_cardsCount"] > 0
    assert res["resB_cardsCount"] > 0
    # Prices must be dynamically calculated based on candle scale, never hardcoded
    assert res["resA_entry"] != res["resB_entry"]
    assert "₹" in res["resA_entry"]
    assert "₹" in res["resB_entry"]
    # Check card multi-line format
    lines = res["firstCardLines"]
    assert len(lines) == 4
    assert "BUY" in lines[0] or "SELL" in lines[0]
    assert "₹" in lines[1]
    assert "Buyers" in lines[2] or "Sellers" in lines[2]
    assert "Room to wall" in lines[3]
    # Boxes must be generated for risk/reward zones
    assert res["resA_boxesCount"] > 0
    assert res["hasExitCard"] is True
    assert "net wins" in res["resTxt"]


def test_bss_pro_v5_pnl_exact_calculation():
    """Verify that gross and net PnL % are calculated using exact formula (net = gross - costPct)."""
    snippet = """
    const candles = [];
    let p = 100.0;
    for (let i = 0; i < 80; i++) {
        const isBull = (i % 4 !== 3);
        const delta = isBull ? 1.5 : -1.0;
        p = Math.max(10, p + delta);
        candles.push({
            time: 1761450000 + (i * 60),
            open: isBull ? p - 1.2 : p + 0.8,
            high: isBull ? p + 0.3 : p + 1.0,
            low: isBull ? p - 1.5 : p - 0.3,
            close: p,
            volume: 1000
        });
    }

    const res = window.TvPineEngine.computeBSSProV5(candles, {
        tradeMode: 'Option buyer (long premium only)',
        emaFastLen: 9, emaSlowLen: 21, rsiLen: 14, stLen: 10, stMult: 2.0,
        pressLen: 7, domThr: 52.0, minRank: 0.0, rsiHi: 85.0,
        costPct: 1.5
    }, 'CE');

    const exitCards = res.cards.filter(c => c.type === 'exit');
    console.log(JSON.stringify({
        hasExit: exitCards.length > 0,
        exitLines: exitCards.length > 0 ? exitCards[0].lines : [],
        pnl: exitCards.length > 0 ? exitCards[0].pnl : null,
        net: exitCards.length > 0 ? exitCards[0].net : null
    }));
    """
    res = run_node_snippet(snippet)
    assert res["hasExit"] is True
    pnl = res["pnl"]
    net = res["net"]
    # net should equal pnl - 1.5
    assert abs(net - (pnl - 1.5)) < 0.001
    assert "net" in res["exitLines"][2]
