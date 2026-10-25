/**
 * AI Trader Real-Time Mobile Client Controller
 * ───────────────────────────────────────────
 * Manages:
 *  1. Lightweight Candlestick Chart with Live Entry/Exit Signal Markers & Price Lines
 *  2. Real-Time AI Predictor Gauges & Dynamic Strike Recommendations
 *  3. Live Option Chain Strike Grid (ATM ± 5 Strikes) with Greeks & OI
 *  4. Paper Trading Engine with Statutory Iceberg Order Slicing & Real Costs
 *  5. Bi-directional WebSocket tick and candle stream
 */

// Global Application State
const App = {
    mode: 'predictor', // 'predictor', 'paper', 'simulation', 'backtest', 'live'
    chart: null,
    candleSeries: null,
    smfUpperSeries: null,
    smfLowerSeries: null,
    smfBcSeries: null,
    smfBoSeries: null,
    targetPriceLine: null,
    stopPriceLine: null,
    activeTab: 'chain', // 'chain', 'paper', 'strategy'
    ws: null,
    spotPrice: 22625.20,
    capital: 100000.0,
    activePosition: null,
    lastCandles: [],
    lastCandlesFormatted: [],
    markers: [],
    allMarkers: [],
    simCandles: [],
    simPlaying: false,
    simPaused: false,
    selectedDate: null,
    currentChartDate: null,
    serverDate: null,
    lastStatus: null,
};

// ── DOM Initialization ───────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    initChart();
    initWebSocket();
    initModeSwitcher();
    initSimulationControls();
    initTabToggles();
    initActionButtons();

    // Sync SMF toggle button UI state
    const smfBtn = document.getElementById('btn-toggle-smf');
    if (smfBtn && !SMF.enabled) {
        smfBtn.classList.add('off');
    }

    // Initial Data Fetch
    fetchStatus();
    fetchPrediction();
    fetchCandles();
    fetchOptionChain();
    fetchTrades();

    // Periodic Polling Fallbacks (Runs ONLY when WebSocket is disconnected)
    setInterval(() => {
        if (!App.ws || App.ws.readyState !== WebSocket.OPEN) {
            fetchStatus();
            fetchPrediction();
            fetchOptionChain(App.selectedDate);
        }
    }, 5000);

    // Continuous Live Candle & Trade Sync (every 5 seconds during active session)
    setInterval(() => {
        if (App.mode !== 'simulation') {
            fetchCandles(false, App.selectedDate);
            fetchTrades(null, App.selectedDate);
        }
    }, 5000);
});

// ── TradingView Lightweight Candlestick Chart ────────────────────────────────

function formatCandlesForChart(candles) {
    if (!candles || !Array.isArray(candles)) return [];
    
    const parsedList = [];
    for (const c of candles) {
        let t = c.timestamp !== undefined ? c.timestamp : c.time;
        if (typeof t === 'string') {
            const parsed = new Date(t).getTime();
            t = isNaN(parsed) ? 0 : Math.floor(parsed / 1000);
        }
        t = Number(t);
        if (isNaN(t) || t <= 0) continue;
        parsedList.push({
            time: t,
            open: Number(c.open),
            high: Number(c.high),
            low: Number(c.low),
            close: Number(c.close),
            volume: Number(c.volume || 0),
        });
    }

    parsedList.sort((a, b) => a.time - b.time);

    const formatted = [];
    let lastTime = 0;
    for (const item of parsedList) {
        if (item.time <= lastTime) continue;
        lastTime = item.time;
        formatted.push(item);
    }
    return formatted;
}

function alignMarkersWithCandles(markers, formattedCandles) {
    if (!markers || !markers.length || !formattedCandles || !formattedCandles.length) {
        return [];
    }
    const candleTimes = new Set(formattedCandles.map(c => c.time));
    const minTime = formattedCandles[0].time;
    const maxTime = formattedCandles[formattedCandles.length - 1].time;

    const aligned = [];
    for (const m of markers) {
        let mTime = m.time;
        if (typeof mTime === 'string') {
            const asNum = Number(mTime);
            if (!isNaN(asNum) && asNum > 1000000000) {
                mTime = asNum;
            } else {
                const p = new Date(mTime).getTime();
                mTime = isNaN(p) ? Number(mTime) : Math.floor(p / 1000);
            }
        }
        mTime = Number(mTime);
        if (isNaN(mTime) || mTime <= 0) continue;

        // Skip markers that fall outside candle range
        if (mTime < minTime || mTime > maxTime) {
            continue;
        }

        // Snap to closest candle if not exact match
        if (!candleTimes.has(mTime)) {
            let closest = null;
            let minDiff = Infinity;
            for (const c of formattedCandles) {
                const diff = Math.abs(c.time - mTime);
                if (diff < minDiff && diff <= 120) {
                    minDiff = diff;
                    closest = c.time;
                }
            }
            if (closest !== null) {
                mTime = closest;
            } else {
                continue;
            }
        }

        aligned.push({
            time: mTime,
            position: m.position || 'belowBar',
            color: m.color || '#00f2a9',
            shape: m.shape || 'arrowUp',
            text: m.text,
        });
    }

    // Lightweight Charts strictly requires markers sorted ascending by time
    aligned.sort((a, b) => a.time - b.time);
    return aligned;
}

// Dynamically filters markers to only bars within/near visible chart viewport
// (Prevents Lightweight Charts from clustering out-of-screen markers onto the left canvas border)
function updateVisibleMarkers(timeRange) {
    if (!App.candleSeries) return;
    const all = App.allMarkers || [];
    if (all.length === 0) {
        try { App.candleSeries.setMarkers([]); } catch (e) {}
        return;
    }

    // Determine current visible time bounds from chart
    const range = timeRange || (App.chart && App.chart.timeScale ? App.chart.timeScale().getVisibleRange() : null);
    if (!range || typeof range.from !== 'number' || typeof range.to !== 'number') {
        try { App.candleSeries.setMarkers(all); } catch (e) {}
        return;
    }

    // Filter to markers strictly within visible range with a small buffer
    const visible = all.filter(m => m.time >= range.from - 60 && m.time <= range.to + 60);
    visible.sort((a, b) => a.time - b.time);
    try {
        App.candleSeries.setMarkers(visible.length > 0 ? visible : all);
    } catch (e) {
        console.warn("setMarkers err:", e);
    }
}

function setAllMarkers(markers, formattedCandles) {
    App.allMarkers = alignMarkersWithCandles(markers, formattedCandles);
    updateVisibleMarkers();
}

function fitChartContent() {
    if (App.chart && App.chart.timeScale) {
        App.chart.timeScale().fitContent();
        setTimeout(() => updateVisibleMarkers(), 60);
    }
}

function scrollToLatestCandle() {
    if (App.chart && App.chart.timeScale) {
        App.chart.timeScale().scrollToPosition(0, true);
        setTimeout(() => updateVisibleMarkers(), 60);
    }
}

window.fitChartContent = fitChartContent;
window.scrollToLatestCandle = scrollToLatestCandle;

function jumpToTrade(entryTimeStr, exitTimeStr) {
    if (!App.chart || !App.chart.timeScale) return;
    try {
        let entryTs = 0;
        let exitTs = 0;
        if (typeof entryTimeStr === 'number') {
            entryTs = entryTimeStr;
        } else {
            const parsed = new Date(entryTimeStr).getTime();
            entryTs = isNaN(parsed) ? 0 : Math.floor(parsed / 1000);
        }
        if (exitTimeStr) {
            if (typeof exitTimeStr === 'number') {
                exitTs = exitTimeStr;
            } else {
                const parsed = new Date(exitTimeStr).getTime();
                exitTs = isNaN(parsed) ? entryTs : Math.floor(parsed / 1000);
            }
        } else {
            exitTs = entryTs;
        }

        if (entryTs > 0) {
            // Center chart directly on this trade window (10 minutes before to 12 minutes after)
            App.chart.timeScale().setVisibleRange({
                from: entryTs - 600,
                to: Math.max(exitTs + 720, entryTs + 720)
            });
            setTimeout(() => updateVisibleMarkers(), 60);
        }
    } catch (e) {
        console.warn("jumpToTrade error:", e);
    }
}

// ┌────────────────────────────── BOSWaves ─ SMF Cloud Engine ──────────────────────┐
// └─────────────────────────────────────────────────────────────────────────────────┘

const SMF = {
    enabled: localStorage.getItem('smf_cloud_enabled') !== '0', // default true
    len: 34,
    basisSmooth: 3,
    mfLen: 24,
    mfSmooth: 5,
    mfPower: 1.2,
    atrLen: 14,
    minMult: 0.9,
    maxMult: 2.2,
    dotCooldown: 12,
    bullColor: '#00c8ff',
    bearColor: '#ff005d',
    buyColor: '#00f2a9',   // Vibrant high-contrast green for Buy
    sellColor: '#ff005d',  // Vibrant high-contrast neon red/pink for Sell
    showBands: true,
    showCloud: true,
    showDots: true,
    showSwitch: true,
    lastResult: null,
};

function initSMFSeries(chart) {
    if (!chart) return;
    try {
        const dashedStyle = (LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dashed : 2);
        const dottedStyle = (LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dotted : 1);
        const solidStyle  = (LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Solid : 0);

        const upperOpts = {
            color: 'rgba(255, 255, 255, 0.45)',
            lineWidth: 1,
            lineStyle: dashedStyle,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        };
        const lowerOpts = {
            color: 'rgba(255, 255, 255, 0.45)',
            lineWidth: 1,
            lineStyle: dashedStyle,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        };
        const bcOpts = {
            color: '#00d2ff',
            lineWidth: 2,
            lineStyle: solidStyle,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        };
        const boOpts = {
            color: 'rgba(0, 210, 255, 0.35)',
            lineWidth: 1,
            lineStyle: dottedStyle,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        };

        if (typeof chart.addLineSeries === 'function') {
            App.smfUpperSeries = chart.addLineSeries(upperOpts);
            App.smfLowerSeries = chart.addLineSeries(lowerOpts);
            App.smfBcSeries    = chart.addLineSeries(bcOpts);
            App.smfBoSeries    = chart.addLineSeries(boOpts);
        } else if (typeof chart.addSeries === 'function' && typeof LightweightCharts.LineSeries !== 'undefined') {
            App.smfUpperSeries = chart.addSeries(LightweightCharts.LineSeries, upperOpts);
            App.smfLowerSeries = chart.addSeries(LightweightCharts.LineSeries, lowerOpts);
            App.smfBcSeries    = chart.addSeries(LightweightCharts.LineSeries, bcOpts);
            App.smfBoSeries    = chart.addSeries(LightweightCharts.LineSeries, boOpts);
        }
    } catch (e) {
        console.warn("Failed to init SMF series:", e);
    }
}

function calcEMAArray(values, length) {
    if (!values || values.length === 0) return [];
    const k = 2 / (length + 1);
    const ema = new Array(values.length);
    ema[0] = values[0];
    for (let i = 1; i < values.length; i++) {
        ema[i] = values[i] * k + ema[i - 1] * (1 - k);
    }
    return ema;
}

function calcWilderATR(candles, length) {
    if (!candles || candles.length === 0) return [];
    const tr = new Array(candles.length);
    tr[0] = candles[0].high - candles[0].low;
    for (let i = 1; i < candles.length; i++) {
        const h = candles[i].high;
        const l = candles[i].low;
        const prevC = candles[i - 1].close;
        tr[i] = Math.max(h - l, Math.abs(h - prevC), Math.abs(l - prevC));
    }
    const atr = new Array(candles.length);
    atr[0] = tr[0];
    for (let i = 1; i < candles.length; i++) {
        atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length;
    }
    return atr;
}

function computeSMF(candles) {
    if (!candles || candles.length < 5) return null;
    const n = candles.length;

    // 1. Smart Money Flow Calculation
    const clv = new Array(n);
    const raw = new Array(n);
    for (let i = 0; i < n; i++) {
        const c = candles[i];
        const span = c.high - c.low;
        const cVal = span === 0 ? 0 : ((c.close - c.low) - (c.high - c.close)) / span;
        clv[i] = cVal;
        const vol = (c.volume && c.volume > 0) ? c.volume : Math.max(1, Math.round(Math.abs(c.close - c.open) * 100 + span * 50));
        raw[i] = cVal * vol;
    }

    const mf = new Array(n);
    const mfLen = SMF.mfLen;
    let windowNum = 0;
    let windowDen = 0;
    for (let i = 0; i < n; i++) {
        windowNum += raw[i];
        windowDen += Math.abs(raw[i]);
        if (i >= mfLen) {
            windowNum -= raw[i - mfLen];
            windowDen -= Math.abs(raw[i - mfLen]);
        }
        mf[i] = windowDen === 0 ? 0 : (windowNum / windowDen);
    }

    const mfSm = SMF.mfSmooth > 1 ? calcEMAArray(mf, SMF.mfSmooth) : mf;
    const mult = new Array(n);
    for (let i = 0; i < n; i++) {
        const strength = Math.min(1.0, Math.max(0.0, Math.pow(Math.abs(mfSm[i]), SMF.mfPower)));
        mult[i] = SMF.minMult + (SMF.maxMult - SMF.minMult) * strength;
    }

    // 2. Core Trend Engine (EMA 34 smoothed by EMA 3)
    const opens = candles.map(c => c.open);
    const closes = candles.map(c => c.close);

    const bORaw = calcEMAArray(opens, SMF.len);
    const bCRaw = calcEMAArray(closes, SMF.len);

    const bO = SMF.basisSmooth > 1 ? calcEMAArray(bORaw, SMF.basisSmooth) : bORaw;
    const bC = SMF.basisSmooth > 1 ? calcEMAArray(bCRaw, SMF.basisSmooth) : bCRaw;
    const bMain = bC;

    // 3. Adaptive Bands (bMain ± ATR * mult)
    const atr = calcWilderATR(candles, SMF.atrLen);
    const upper = new Array(n);
    const lower = new Array(n);
    for (let i = 0; i < n; i++) {
        upper[i] = bMain[i] + atr[i] * mult[i];
        lower[i] = bMain[i] - atr[i] * mult[i];
    }

    // 4. Signals & State Transitions
    const lastSignal = new Array(n);
    const coloredCandles = new Array(n);
    const smfMarkers = [];

    let prevLS = candles[0].close >= bMain[0] ? 1 : -1;

    for (let i = 0; i < n; i++) {
        const c = candles[i];
        let longCond = false;
        let shortCond = false;
        if (i > 0) {
            longCond = candles[i - 1].close <= upper[i - 1] && c.close > upper[i];
            shortCond = candles[i - 1].close >= lower[i - 1] && c.close < lower[i];
        }

        const currLS = longCond ? 1 : (shortCond ? -1 : prevLS);
        lastSignal[i] = currLS;
        const prevSignal = (i === 0) ? 0 : lastSignal[i - 1];

        const isSwitchUp = (currLS === 1 && prevSignal === -1);
        const isSwitchDown = (currLS === -1 && prevSignal === 1);
        prevLS = currLS;

        const barCol = currLS === 1 ? SMF.bullColor : SMF.bearColor;
        coloredCandles[i] = {
            time: c.time,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.volume,
            color: barCol,
            borderColor: barCol,
            wickColor: barCol
        };

        if (SMF.showSwitch) {
            if (isSwitchUp) {
                const limP = Math.round(Math.min(c.close, Math.max(bMain[i], c.close - 0.3 * (atr[i] || 2))) * 20) / 20;
                smfMarkers.push({
                    time: c.time,
                    position: 'belowBar',
                    color: SMF.buyColor,
                    shape: 'arrowUp',
                    text: `Buy (Lim: ₹${limP.toFixed(2)})`,
                    limitPrice: limP,
                });
            } else if (isSwitchDown) {
                smfMarkers.push({
                    time: c.time,
                    position: 'aboveBar',
                    color: SMF.sellColor,
                    shape: 'arrowDown',
                    text: 'Sell',
                });
            }
        }
    }

    // 5. Retest Signals (✦) with Cooldown
    let lastBearDotBar = -9999;
    let lastBullDotBar = -9999;
    if (SMF.showDots) {
        for (let i = 0; i < n; i++) {
            const c = candles[i];
            const bearDotCond = (lastSignal[i] === -1 && c.high > bC[i]);
            const bullDotCond = (lastSignal[i] === 1 && c.low < bC[i]);

            const bearOk = bearDotCond && (i - lastBearDotBar >= SMF.dotCooldown);
            const bullOk = bullDotCond && (i - lastBullDotBar >= SMF.dotCooldown);

            if (bearOk) {
                lastBearDotBar = i;
                smfMarkers.push({
                    time: c.time,
                    position: 'aboveBar',
                    color: SMF.sellColor,
                    shape: 'circle',
                    text: '✦',
                });
            }
            if (bullOk) {
                lastBullDotBar = i;
                smfMarkers.push({
                    time: c.time,
                    position: 'belowBar',
                    color: SMF.buyColor,
                    shape: 'circle',
                    text: '✦',
                });
            }
        }
    }

    // 6. Trend Strength Gauge (tanh & EMA 3)
    const tanhVals = new Array(n);
    for (let i = 0; i < n; i++) {
        const basis = bMain[i];
        const upSpan = Math.max(upper[i] - basis, 0.05);
        const dnSpan = Math.max(basis - lower[i], 0.05);
        const rawScore = lastSignal[i] === 1 ? (candles[i].close - basis) / upSpan : - (basis - candles[i].close) / dnSpan;
        tanhVals[i] = Math.tanh(rawScore * 1.5);
    }
    const gaugeEma = calcEMAArray(tanhVals, 3);
    const latestGaugeVal = gaugeEma[n - 1];
    const latestRegime = lastSignal[n - 1];

    // Build line series data
    const upperData = [];
    const lowerData = [];
    const bcData    = [];
    const boData    = [];

    for (let i = 0; i < n; i++) {
        const t = candles[i].time;
        upperData.push({ time: t, value: Number(upper[i].toFixed(2)) });
        lowerData.push({ time: t, value: Number(lower[i].toFixed(2)) });
        bcData.push({ time: t, value: Number(bC[i].toFixed(2)) });
        boData.push({ time: t, value: Number(bO[i].toFixed(2)) });
    }

    const result = {
        coloredCandles,
        upperData,
        lowerData,
        bcData,
        boData,
        smfMarkers,
        latestGaugeVal,
        latestRegime,
    };
    SMF.lastResult = result;
    return result;
}

function updateTrendStrengthGauge(gaugeVal, regime) {
    const card = document.getElementById('smf-bottom-gauge');
    const needle = document.getElementById('smf-bg-needle');
    const txt = document.getElementById('smf-bg-strength-txt');
    if (!card || !needle || !txt) return;

    if (!SMF.enabled) {
        card.style.display = 'none';
        return;
    }
    card.style.display = 'flex';

    const v = Math.max(-1.0, Math.min(1.0, typeof gaugeVal === 'number' ? gaugeVal : 0));
    // 0 is center (50%), -1 is bear (6%), +1 is bull (94%)
    const pct = ((v + 1.0) * 0.5) * 100;
    needle.style.left = `${Math.min(94, Math.max(6, pct))}%`;

    const strengthPct = Math.round(Math.abs(v) * 100);
    txt.innerText = `Strength: ${strengthPct}%`;
}

function renderSMFCloudCanvas() {
    const canvas = document.getElementById('smf-cloud-canvas');
    if (!canvas || !App.chart || !App.candleSeries) return;

    const container = document.getElementById('tradingview-chart');
    if (!container) return;

    const width = container.clientWidth;
    const height = container.clientHeight;
    if (width <= 0 || height <= 0) return;

    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
        canvas.width = width * dpr;
        canvas.height = height * dpr;
    }

    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    if (!SMF.enabled || !SMF.lastResult) return;

    const { coloredCandles, upperData, lowerData, bcData, boData } = SMF.lastResult;
    const n = coloredCandles.length;
    if (n < 2) return;

    const timeScale = App.chart.timeScale();
    const candleSeries = App.candleSeries;

    // Map all data points to pixel screen coordinates
    const points = [];
    for (let i = 0; i < n; i++) {
        const t = coloredCandles[i].time;
        const x = timeScale.timeToCoordinate(t);
        if (x === null) continue;

        const yUpper = candleSeries.priceToCoordinate(upperData[i].value);
        const yLower = candleSeries.priceToCoordinate(lowerData[i].value);
        const yBc    = candleSeries.priceToCoordinate(bcData[i].value);
        const yBo    = candleSeries.priceToCoordinate(boData[i].value);
        const yClose = candleSeries.priceToCoordinate(coloredCandles[i].close);

        if (yUpper === null || yLower === null || yBc === null) continue;

        points.push({
            i,
            time: t,
            x,
            yUpper,
            yLower,
            yBc,
            yBo: yBo !== null ? yBo : yBc,
            yClose,
            isBull: coloredCandles[i].color === SMF.bullColor,
        });
    }

    if (points.length < 2) return;

    let avgSpacing = 6;
    if (points.length >= 2) {
        avgSpacing = Math.max(2, (points[points.length - 1].x - points[0].x) / (points.length - 1));
    }
    const barWidth = Math.max(1.5, Math.min(12, avgSpacing * 0.75));

    // Group into contiguous bull / bear segments
    const segments = [];
    let currentSegment = [points[0]];

    for (let i = 1; i < points.length; i++) {
        if (points[i].isBull === points[i - 1].isBull) {
            currentSegment.push(points[i]);
        } else {
            currentSegment.push(points[i]);
            segments.push(currentSegment);
            currentSegment = [points[i]];
        }
    }
    if (currentSegment.length > 0) {
        segments.push(currentSegment);
    }

    // 1. Draw Adaptive Volatility Gradient Cloud Fills
    for (const seg of segments) {
        if (seg.length < 2) continue;
        const isBull = seg[0].isBull;
        const topY = Math.min(...seg.map(p => isBull ? p.yBc : p.yUpper));
        const botY = Math.max(...seg.map(p => isBull ? p.yLower : p.yBc));

        // Create linear gradient
        const grad = ctx.createLinearGradient(0, topY, 0, botY);
        if (isBull) {
            grad.addColorStop(0, 'rgba(0, 200, 255, 0.40)');
            grad.addColorStop(0.5, 'rgba(0, 200, 255, 0.20)');
            grad.addColorStop(1, 'rgba(0, 200, 255, 0.03)');
        } else {
            grad.addColorStop(0, 'rgba(255, 0, 93, 0.40)');
            grad.addColorStop(0.5, 'rgba(255, 0, 93, 0.20)');
            grad.addColorStop(1, 'rgba(255, 0, 93, 0.03)');
        }

        // Fill smooth ribbon polygon
        ctx.beginPath();
        for (let i = 0; i < seg.length; i++) {
            const p = seg[i];
            const yTop = isBull ? p.yBc : p.yUpper;
            if (i === 0) ctx.moveTo(p.x, yTop);
            else ctx.lineTo(p.x, yTop);
        }
        for (let i = seg.length - 1; i >= 0; i--) {
            const p = seg[i];
            const yBot = isBull ? p.yLower : p.yBc;
            ctx.lineTo(p.x, yBot);
        }
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();

        // Draw vertical combed ribbons for high-fidelity TradingView look
        ctx.lineWidth = barWidth;
        for (let i = 0; i < seg.length; i++) {
            const p = seg[i];
            const y1 = isBull ? p.yBc : p.yUpper;
            const y2 = isBull ? p.yLower : p.yBc;

            const vGrad = ctx.createLinearGradient(p.x, y1, p.x, y2);
            if (isBull) {
                vGrad.addColorStop(0, 'rgba(0, 200, 255, 0.45)');
                vGrad.addColorStop(1, 'rgba(0, 200, 255, 0.05)');
            } else {
                vGrad.addColorStop(0, 'rgba(255, 0, 93, 0.45)');
                vGrad.addColorStop(1, 'rgba(255, 0, 93, 0.05)');
            }
            ctx.strokeStyle = vGrad;
            ctx.beginPath();
            ctx.moveTo(p.x, y1);
            ctx.lineTo(p.x, y2);
            ctx.stroke();
        }
    }

    // 2. Draw Basis Cloud (between bO and bC)
    for (const seg of segments) {
        if (seg.length < 2) continue;
        const isBull = seg[0].isBull;
        ctx.beginPath();
        for (let i = 0; i < seg.length; i++) {
            if (i === 0) ctx.moveTo(seg[i].x, seg[i].yBc);
            else ctx.lineTo(seg[i].x, seg[i].yBc);
        }
        for (let i = seg.length - 1; i >= 0; i--) {
            ctx.lineTo(seg[i].x, seg[i].yBo);
        }
        ctx.closePath();
        ctx.fillStyle = isBull ? 'rgba(0, 200, 255, 0.28)' : 'rgba(255, 0, 93, 0.28)';
        ctx.fill();
    }

    // 3. Draw Neon Glowing Basis Close Line (bC)
    ctx.save();
    ctx.shadowBlur = 8;
    for (const seg of segments) {
        if (seg.length < 2) continue;
        const isBull = seg[0].isBull;
        ctx.strokeStyle = isBull ? '#00c8ff' : '#ff005d';
        ctx.shadowColor = isBull ? 'rgba(0, 200, 255, 0.8)' : 'rgba(255, 0, 93, 0.8)';
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        for (let i = 0; i < seg.length; i++) {
            if (i === 0) ctx.moveTo(seg[i].x, seg[i].yBc);
            else ctx.lineTo(seg[i].x, seg[i].yBc);
        }
        ctx.stroke();
    }
    ctx.restore();

    // 4. Draw Dashed Outer Adaptive Bands
    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
    ctx.beginPath();
    for (let i = 0; i < points.length; i++) {
        if (i === 0) ctx.moveTo(points[i].x, points[i].yUpper);
        else ctx.lineTo(points[i].x, points[i].yUpper);
    }
    ctx.stroke();

    ctx.beginPath();
    for (let i = 0; i < points.length; i++) {
        if (i === 0) ctx.moveTo(points[i].x, points[i].yLower);
        else ctx.lineTo(points[i].x, points[i].yLower);
    }
    ctx.stroke();
    ctx.restore();
}

function applySMFCloud(formattedCandles, baseMarkers = []) {
    if (!App.candleSeries) return;

    if (!formattedCandles || formattedCandles.length === 0) {
        // Explicitly clear all series and canvas overlays for blank chart
        App.candleSeries.setData([]);
        if (App.smfUpperSeries) App.smfUpperSeries.setData([]);
        if (App.smfLowerSeries) App.smfLowerSeries.setData([]);
        if (App.smfBcSeries) App.smfBcSeries.setData([]);
        if (App.smfBoSeries) App.smfBoSeries.setData([]);

        const canvas = document.getElementById('smf-cloud-canvas');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }

        if (App.targetPriceLine) {
            try { App.candleSeries.removePriceLine(App.targetPriceLine); } catch (e) {}
            App.targetPriceLine = null;
        }
        if (App.stopPriceLine) {
            try { App.candleSeries.removePriceLine(App.stopPriceLine); } catch (e) {}
            App.stopPriceLine = null;
        }

        setAllMarkers([], []);
        updateTrendStrengthGauge(0, 1);
        return;
    }

    if (!SMF.enabled) {
        // Clear SMF Line Series
        if (App.smfUpperSeries) App.smfUpperSeries.setData([]);
        if (App.smfLowerSeries) App.smfLowerSeries.setData([]);
        if (App.smfBcSeries) App.smfBcSeries.setData([]);
        if (App.smfBoSeries) App.smfBoSeries.setData([]);

        // Clear canvas
        const canvas = document.getElementById('smf-cloud-canvas');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }

        // Restore clean default candle colors
        const cleanCandles = formattedCandles.map(c => ({
            time: c.time,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
        }));
        App.candleSeries.setData(cleanCandles);

        // Restore base markers only
        setAllMarkers(baseMarkers, formattedCandles);
        updateTrendStrengthGauge(0, 1);
        return;
    }

    const res = computeSMF(formattedCandles);
    if (!res) {
        App.candleSeries.setData(formattedCandles);
        setAllMarkers(baseMarkers, formattedCandles);
        return;
    }

    // Set line series (hidden or subtle)
    if (App.smfUpperSeries) App.smfUpperSeries.setData([]);
    if (App.smfLowerSeries) App.smfLowerSeries.setData([]);
    if (App.smfBcSeries) App.smfBcSeries.setData([]);
    if (App.smfBoSeries) App.smfBoSeries.setData([]);

    // Apply colored candles (Electric Blue #00c8ff and Hot Pink/Red #ff005d)
    App.candleSeries.setData(res.coloredCandles);

    // Merge base markers with SMF Buy/Sell & Retest signals
    const mergedMarkers = [...(baseMarkers || []), ...res.smfMarkers];
    setAllMarkers(mergedMarkers, formattedCandles);

    // Update gauge
    updateTrendStrengthGauge(res.latestGaugeVal, res.latestRegime);

    // Render Cloud Ribbon on Canvas Overlay
    requestAnimationFrame(renderSMFCloudCanvas);
}

function toggleSMFCloud() {
    SMF.enabled = !SMF.enabled;
    localStorage.setItem('smf_cloud_enabled', SMF.enabled ? '1' : '0');

    const btn = document.getElementById('btn-toggle-smf');
    if (btn) {
        if (SMF.enabled) {
            btn.classList.remove('off');
        } else {
            btn.classList.add('off');
        }
    }

    const candles = (App.mode === 'simulation' && App.simCandles && App.simCandles.length > 0)
        ? App.simCandles
        : (App.lastCandlesFormatted || []);

    const aggregated = aggregateCandles(candles, currentTf);
    applySMFCloud(aggregated, App.markers || []);
}
window.toggleSMFCloud = toggleSMFCloud;

// ── Multi-Timeframe Candle Aggregator (1m, 3m, 5m, 15m) ──────────────────────
let currentTf = 1;

function aggregateCandles(candles, tfMinutes) {
    if (!candles || candles.length === 0 || tfMinutes <= 1) return candles;
    const tfSec = tfMinutes * 60;
    const aggregated = [];
    let cur = null;

    for (const c of candles) {
        const bucketTime = Math.floor(c.time / tfSec) * tfSec;
        if (!cur || cur.time !== bucketTime) {
            if (cur) aggregated.push(cur);
            cur = {
                time: bucketTime,
                open: c.open,
                high: c.high,
                low: c.low,
                close: c.close,
                volume: c.volume || 0,
            };
        } else {
            cur.high = Math.max(cur.high, c.high);
            cur.low = Math.min(cur.low, c.low);
            cur.close = c.close;
            cur.volume += (c.volume || 0);
        }
    }
    if (cur) aggregated.push(cur);
    return aggregated;
}

function setTimeframe(tf) {
    currentTf = Number(tf) || 1;
    document.querySelectorAll('.tf-btn').forEach(btn => {
        if (Number(btn.dataset.tf) === currentTf) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });

    const baseCandles = (App.mode === 'simulation' && App.simCandles && App.simCandles.length > 0)
        ? App.simCandles
        : (App.lastCandlesFormatted || []);

    const aggregated = aggregateCandles(baseCandles, currentTf);
    applySMFCloud(aggregated, App.markers || []);
    if (App.chart && aggregated.length > 0) {
        App.chart.timeScale().fitContent();
    }
}
window.setTimeframe = setTimeframe;

function initChart() {
    const container = document.getElementById('tradingview-chart');
    if (!container) return;

    const chartHeight = container.clientHeight || 460;
    const chartWidth = container.clientWidth || 800;

    try {
        App.chart = LightweightCharts.createChart(container, {
            width: chartWidth,
            height: chartHeight,
            layout: {
                background: { color: 'transparent' },
                textColor: '#8492a6',
                fontSize: 11,
                fontFamily: "'JetBrains Mono', monospace",
            },
            grid: {
                vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
                horzLines: { color: 'rgba(255, 255, 255, 0.04)' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: '#00d2ff', width: 1, style: 3 },
                horzLine: { color: '#00d2ff', width: 1, style: 3 },
            },
            rightPriceScale: {
                borderColor: 'rgba(255, 255, 255, 0.12)',
                scaleMargins: { top: 0.12, bottom: 0.15 },
            },
            localization: {
                timeFormatter: (timestamp) => {
                    const d = new Date(timestamp * 1000);
                    const dayMonth = d.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short' });
                    const timeStr = d.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false });
                    return `${dayMonth} ${timeStr}`;
                },
            },
            timeScale: {
                borderColor: 'rgba(255, 255, 255, 0.14)',
                timeVisible: true,
                secondsVisible: false,
                rightOffset: 6,
                barSpacing: 6,
                minBarSpacing: 1.5,
                tickMarkFormatter: (time, tickMarkType, locale) => {
                    const d = new Date(time * 1000);
                    return d.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false });
                },
            },
        });

        const seriesOpts = {
            upColor: '#00c8ff',
            downColor: '#ff005d',
            borderUpColor: '#00c8ff',
            borderDownColor: '#ff005d',
            wickUpColor: '#00c8ff',
            wickDownColor: '#ff005d',
        };

        if (typeof App.chart.addCandlestickSeries === 'function') {
            App.candleSeries = App.chart.addCandlestickSeries(seriesOpts);
        } else if (typeof App.chart.addSeries === 'function' && typeof LightweightCharts.CandlestickSeries !== 'undefined') {
            App.candleSeries = App.chart.addSeries(LightweightCharts.CandlestickSeries, seriesOpts);
        }

        // Initialize BOSWaves SMF Cloud overlay line series
        initSMFSeries(App.chart);

        // Keep markers & canvas cloud synchronized whenever user zooms, pans, or chart auto-scrolls
        if (App.chart && App.chart.timeScale) {
            App.chart.timeScale().subscribeVisibleTimeRangeChange((range) => {
                updateVisibleMarkers(range);
                renderSMFCloudCanvas();
            });
            App.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
                renderSMFCloudCanvas();
            });
        }

        // Bind Chart Control Buttons
        const btnFit = document.getElementById('btn-chart-fit');
        const btnLatest = document.getElementById('btn-chart-latest');
        if (btnFit) btnFit.addEventListener('click', fitChartContent);
        if (btnLatest) btnLatest.addEventListener('click', scrollToLatestCandle);

        // Handle Window Resize
        window.addEventListener('resize', () => {
            if (App.chart && container) {
                const w = container.clientWidth || chartWidth;
                const h = container.clientHeight || chartHeight;
                App.chart.applyOptions({ width: w, height: h });
                setTimeout(renderSMFCloudCanvas, 60);
            }
        });
    } catch (err) {
        console.error('Failed to initialize chart:', err);
    }
}

let isInitialCandleLoad = true;
let lastRenderedCandleCount = 0;
let lastLoadedChartDate = null;

async function fetchCandles(forceFit = false, customDate = null) {
    if (App.mode === 'simulation' && App.simPlaying && !forceFit) {
        return;
    }
    try {
        const dateToFetch = customDate || App.selectedDate;
        const url = '/api/candles' + (dateToFetch ? `?date=${encodeURIComponent(dateToFetch)}` : '');
        const res = await fetch(url);
        const data = await res.json();
        
        const sessionDate = data.date || data.today || dateToFetch;
        const isNewDate = Boolean(customDate || (sessionDate && sessionDate !== lastLoadedChartDate));

        if (data.candles !== undefined) {
            App.lastCandles = data.candles || [];
            const formatted = formatCandlesForChart(data.candles);
            App.lastCandlesFormatted = formatted;
            App.markers = data.markers || [];

            if (App.candleSeries) {
                const aggregated = aggregateCandles(formatted, currentTf);
                const hasCandlesChanged = (formatted.length !== lastRenderedCandleCount);
                const wasEmpty = (lastRenderedCandleCount === 0 && formatted.length > 0);
                if (isNewDate || forceFit || isInitialCandleLoad || hasCandlesChanged || wasEmpty) {
                    applySMFCloud(aggregated, App.markers);
                    lastRenderedCandleCount = formatted.length;
                    lastLoadedChartDate = sessionDate;
                    App.currentChartDate = sessionDate;

                    const cSelect = document.getElementById('chart-session-date-select');
                    if (cSelect && sessionDate && cSelect.value !== sessionDate) {
                        cSelect.value = sessionDate;
                    }
                    const badge = document.getElementById('sim-current-date-badge');
                    if (badge && sessionDate) badge.innerText = sessionDate;

                    if (App.chart && aggregated.length > 0 && (isNewDate || isInitialCandleLoad || forceFit || wasEmpty)) {
                        App.chart.timeScale().fitContent();
                    }
                    isInitialCandleLoad = false;
                    return;
                } else if (formatted.length > 0 && hasCandlesChanged) {
                    applySMFCloud(aggregated, App.markers);
                    lastRenderedCandleCount = formatted.length;
                }

                updateChartPriceLines();
            }
        }
    } catch (err) {
        console.error('Error fetching candles:', err);
    }
}

function updateChartPriceLines() {
    if (!App.candleSeries) return;

    // Remove old lines if existing
    if (App.targetPriceLine) {
        try { App.candleSeries.removePriceLine(App.targetPriceLine); } catch (e) {}
        App.targetPriceLine = null;
    }
    if (App.stopPriceLine) {
        try { App.candleSeries.removePriceLine(App.stopPriceLine); } catch (e) {}
        App.stopPriceLine = null;
    }

    if (App.activePosition) {
        const pos = App.activePosition;
        // Draw Target Line (Green)
        App.targetPriceLine = App.candleSeries.createPriceLine({
            price: pos.target_price,
            color: '#00f2a9',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: `TARGET: ₹${pos.target_price.toFixed(1)}`,
        });

        // Draw Stop Loss Line (Red)
        App.stopPriceLine = App.candleSeries.createPriceLine({
            price: pos.current_sl,
            color: '#ff3860',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: `SL: ₹${pos.current_sl.toFixed(1)}`,
        });
    }
}

// ── Real-Time AI Predictor Panel ─────────────────────────────────────────────

async function fetchPrediction() {
    try {
        const res = await fetch('/api/prediction');
        const pred = await res.json();
        renderPrediction(pred);
    } catch (err) {
        console.error('Error fetching prediction:', err);
    }
}

function renderPrediction(pred) {
    const hero = document.getElementById('prediction-hero-card');
    if (!hero) return;

    if (!pred || pred.active === false || !pred.suggested_strike || pred.status === 'MARKET_CLOSED' || pred.status === 'NO_SETUP' || pred.status === 'INSUFFICIENT_DATA') {
        hero.style.display = 'none';
        return;
    }

    hero.style.display = 'block';

    // Direction Badge
    const dirBadge = document.getElementById('ai-direction-badge');
    const isCall = (pred.direction === 'CALL');
    if (dirBadge) {
        dirBadge.className = `pill-badge ${isCall ? 'call' : 'put'}`;
        dirBadge.innerText = `${isCall ? '🟢 CALL BUY' : '🔴 PUT BUY'} · ${pred.bias}`;
    }

    // Confidence
    const confEl = document.getElementById('ai-confidence-val');
    if (confEl) confEl.innerText = `${pred.confidence_pct}% Conviction`;

    // Suggested Strike & Title
    const strikeEl = document.getElementById('ai-suggested-strike');
    if (strikeEl) strikeEl.innerText = pred.suggested_strike;

    // Targets Grid
    const entryEl = document.getElementById('ai-entry-range');
    if (entryEl) entryEl.innerText = `₹${(pred.entry_premium || 0).toFixed(1)}`;
    const tgtEl = document.getElementById('ai-target-val');
    if (tgtEl) tgtEl.innerText = `₹${(pred.target_premium || 0).toFixed(1)} (+${pred.expected_gain_pct || 0}%)`;
    const slEl = document.getElementById('ai-stop-val');
    if (slEl) slEl.innerText = `₹${(pred.stop_loss_premium || 0).toFixed(1)} (-${pred.max_risk_pct || 0}%)`;

    // Follow AI Trade button
    const tradeBtn = document.getElementById('btn-follow-ai');
    if (tradeBtn) {
        tradeBtn.innerText = `⚡ Follow AI ${isCall ? 'Call' : 'Put'} (${pred.suggested_strike})`;
    }
}

// ── Live Option Chain Grid ───────────────────────────────────────────────────

async function fetchOptionChain(customDate = null) {
    try {
        const dateToFetch = (customDate !== null && customDate !== undefined) ? customDate : App.selectedDate;
        const url = '/api/option_chain' + (dateToFetch ? `?date=${encodeURIComponent(dateToFetch)}` : '');
        const res = await fetch(url);
        const data = await res.json();
        renderOptionChain(data.chain, data.atm_strike);
    } catch (err) {
        console.error('Error fetching option chain:', err);
    }
}

let lastRenderedChainHash = '';

function renderOptionChain(chain, atmStrike) {
    const tbody = document.getElementById('option-chain-tbody');
    if (!tbody || !chain) return;

    const hash = `${atmStrike}_` + chain.map(r => `${r.strike}:${r.call_ltp}:${r.put_ltp}`).join('|');
    if (hash === lastRenderedChainHash) return;
    lastRenderedChainHash = hash;

    tbody.innerHTML = '';
    chain.forEach(row => {
        const tr = document.createElement('tr');
        if (row.is_atm) tr.className = 'atm-row';

        const strikeInt = Math.round(row.strike);
        tr.innerHTML = `
            <td class="val-green" style="cursor: pointer;" onclick="location.href='/contract?symbol=NIFTY26OCT${strikeInt}CE'" title="Open ${strikeInt} CE Chart">${row.call_ltp.toFixed(1)}</td>
            <td>${row.call_iv}%</td>
            <td>${row.call_oi.toLocaleString()}</td>
            <td class="strike-cell" style="cursor: pointer;" onclick="location.href='/contract?symbol=NIFTY26OCT${strikeInt}CE'" title="Chart ${strikeInt} CE/PE Pair">${row.strike} ${row.is_atm ? '★' : ''}</td>
            <td>${row.put_oi.toLocaleString()}</td>
            <td>${row.put_iv}%</td>
            <td class="val-red" style="cursor: pointer;" onclick="location.href='/contract?symbol=NIFTY26OCT${strikeInt}PE'" title="Open ${strikeInt} PE Chart">${row.put_ltp.toFixed(1)}</td>
        `;
        tbody.appendChild(tr);
    });
}

// ── Paper Trading Portfolio & Active Position ────────────────────────────────

async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        const st = await res.json();

        App.spotPrice = st.spot_price;
        App.capital = st.capital;
        App.activePosition = st.active_position;
        App.lastStatus = st;

        // ── Date Change / Rollover Detection ──
        if (st.server_date) {
            if (!App.serverDate) {
                App.serverDate = st.server_date;
            } else if (App.serverDate !== st.server_date) {
                console.log(`[Date Change Detected] Date rolled over: ${App.serverDate} -> ${st.server_date}`);
                App.serverDate = st.server_date;
                // Reload session date dropdowns so new day appears immediately
                loadSimulationDates();
                // If not in historical simulation replay, auto-switch chart to new date!
                if (App.mode !== 'simulation') {
                    App.selectedDate = st.server_date;
                    syncDateInputs(st.server_date);
                    fetchCandles(true, st.server_date);
                }
            }
        }

        // ── Strategy Signals & Suggestions Badge & Rendering ──
        const sBadge = document.getElementById('strategy-count-badge');
        if (sBadge) sBadge.innerText = st.strategy_count || (st.strategy_suggestions ? st.strategy_suggestions.length : 0);
        renderStrategySignals(st.strategy_suggestions);

        // Spot Ticker in Header
        const spotEl = document.getElementById('header-spot-val');
        if (spotEl) spotEl.innerText = `₹${st.spot_price.toFixed(2)}`;

        // Paper Portfolio Stats
        const capEl = document.getElementById('paper-capital-val');
        if (capEl) capEl.innerText = `₹${st.capital.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

        const pnlEl = document.getElementById('paper-floating-pnl');
        if (pnlEl) {
            const pnl = st.unrealized_pnl;
            pnlEl.className = `val ${pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}`;
            pnlEl.innerText = `${pnl >= 0 ? '+' : ''}₹${pnl.toLocaleString('en-IN', { maximumFractionDigits: 1 })}`;
        }

        const wrEl = document.getElementById('paper-winrate-val');
        if (wrEl) wrEl.innerText = `${st.win_rate.toFixed(1)}%`;

        renderActivePosition(st.active_position, st.unrealized_pnl);
        updateChartPriceLines();

        // ── Simulation Progress & Status Sync ──
        if (st.simulation) {
            const sim = st.simulation;
            const curr = sim.current_bar || 0;
            const total = sim.total_bars || 385;
            const pct = Math.min(100, Math.round((curr / Math.max(1, total)) * 100));

            const fillEl = document.getElementById('sim-progress-fill');
            if (fillEl) fillEl.style.width = `${pct}%`;

            const progTxt = document.getElementById('sim-progress-text');
            const metaTime = document.getElementById('sim-meta-time');
            const pill = document.getElementById('sim-status-pill');
            const btnStart = document.getElementById('btn-sim-start');
            const btnPause = document.getElementById('btn-sim-pause');
            const btnStop = document.getElementById('btn-sim-stop');

            if (sim.active) {
                if (progTxt) progTxt.innerText = `Replaying: Bar ${curr} / ${total} (${pct}%) · Speed: ${sim.speed || 10}x`;
                if (metaTime) metaTime.innerText = `Market Time: ${sim.current_time || '09:15 IST'}`;
                if (pill) {
                    if (sim.paused) {
                        pill.style.background = 'rgba(255, 183, 3, 0.15)';
                        pill.style.color = '#ffb703';
                        pill.style.borderColor = 'rgba(255, 183, 3, 0.4)';
                        pill.innerText = '⏸ PAUSED';
                        if (btnPause) btnPause.innerText = '▶ Resume';
                    } else {
                        pill.style.background = 'rgba(0, 242, 169, 0.15)';
                        pill.style.color = '#00f2a9';
                        pill.style.borderColor = 'rgba(0, 242, 169, 0.4)';
                        pill.innerText = '▶ REPLAYING LIVE';
                        if (btnPause) btnPause.innerText = '⏸ Pause';
                    }
                }
                if (btnStart) btnStart.disabled = true;
                if (btnPause) btnPause.disabled = false;
                if (btnStop) btnStop.disabled = false;
            } else if (App.simPlaying) {
                // Completed session
                App.simPlaying = false;
                if (progTxt) progTxt.innerText = `Replay Completed: ${total} bars replayed (${sim.date || ''})`;
                if (metaTime) metaTime.innerText = `Market Time: 15:30 IST`;
                if (pill) {
                    pill.style.background = 'rgba(0, 210, 255, 0.15)';
                    pill.style.color = '#00d2ff';
                    pill.style.borderColor = 'rgba(0, 210, 255, 0.3)';
                    pill.innerText = '⚡ REPLAY COMPLETED';
                }
                if (btnStart) btnStart.disabled = false;
                if (btnPause) btnPause.disabled = true;
                if (btnStop) btnStop.disabled = true;
                fetchTrades();
            }
        }
    } catch (err) {
        console.error('Error fetching status:', err);
    }
}

function renderActivePosition(pos, unrlPnl) {
    const container = document.getElementById('active-position-container');
    if (!container) return;

    if (!pos) {
        container.innerHTML = `
            <div style="text-align: center; padding: 24px 10px; color: var(--text-dim);">
                <div style="font-size: 1.8rem; margin-bottom: 6px;">💤</div>
                <div style="font-family: var(--font-display); font-size: 0.95rem; font-weight: 600;">No Open Position</div>
                <div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 4px;">Engine is scanning live ticks for high-conviction breakout setup...</div>
            </div>
        `;
        return;
    }

    const nSlices = (pos.slices && pos.slices.length) ? pos.slices.length : 1;
    container.innerHTML = `
        <div class="active-pos-card glass-panel">
            <div class="pos-header">
                <div class="pos-title">
                    <span class="pill-badge ${pos.side === 'CE' ? 'call' : 'put'}">${pos.side} LONG</span>
                    <span>${pos.symbol}</span>
                    <span class="iceberg-legs-badge">${pos.lots} Lots · ${nSlices} Iceberg Legs</span>
                </div>
                <div class="floating-pnl-live ${unrlPnl >= 0 ? 'val-green' : 'val-red'}">
                    ${unrlPnl >= 0 ? '+' : ''}₹${unrlPnl.toLocaleString('en-IN', { maximumFractionDigits: 1 })}
                </div>
            </div>

            <div class="pos-meta-grid">
                <div>
                    <span style="color: var(--text-dim);">ENTRY:</span>
                    <strong style="color: #fff;">₹${pos.entry_price.toFixed(1)}</strong>
                </div>
                <div>
                    <span style="color: var(--text-dim);">TARGET:</span>
                    <strong class="val-green">₹${pos.target_price.toFixed(1)}</strong>
                </div>
                <div>
                    <span style="color: var(--text-dim);">STOP LOSS:</span>
                    <strong class="val-red">₹${pos.current_sl.toFixed(1)}</strong>
                </div>
            </div>

            <div style="display: flex; gap: 8px; margin-top: 4px;">
                <button id="btn-squareoff-pos" class="btn-squareoff" style="flex: 1;" onclick="closePosition()">
                    🛑 Square Off Active Position
                </button>
            </div>
        </div>
    `;
}

async function fetchTrades(mode, customDate = null) {
    try {
        const dateToFetch = (customDate !== null && customDate !== undefined) ? customDate : App.selectedDate;
        const queryMode = mode || (App.mode === 'simulation' ? 'simulation' : 'paper');
        let url = `/api/paper/trades?mode=${encodeURIComponent(queryMode)}`;
        if (dateToFetch) {
            url += `&date=${encodeURIComponent(dateToFetch)}`;
        }
        const res = await fetch(url);
        const data = await res.json();
        renderTradesList(data.trades, dateToFetch);
        if (data.capital) {
            const capEl = document.getElementById('paper-capital-val');
            if (capEl) capEl.innerText = `₹${Math.round(data.capital).toLocaleString('en-IN')}`;
        }
        const winEl = document.getElementById('paper-winrate-val');
        if (winEl) {
            if (data.date_trades_count > 0) {
                winEl.innerText = `${data.date_win_rate}%`;
            } else {
                winEl.innerText = `--`;
            }
        }
        const pnlEl = document.getElementById('paper-floating-pnl');
        if (pnlEl && data.date_pnl !== undefined) {
            const isPos = data.date_pnl >= 0;
            pnlEl.className = `val ${isPos ? 'val-green' : 'val-red'}`;
            pnlEl.innerText = `${isPos ? '+' : ''}₹${data.date_pnl.toLocaleString('en-IN')}`;
        }
    } catch (err) {
        console.error('Error fetching trades:', err);
    }
}

let lastRenderedTradesHash = '';

function renderTradesList(trades, dateLabel = null) {
    const container = document.getElementById('trades-history-list');
    if (!container) return;

    const hash = (trades || []).map(t => `${t.entry_time}:${t.exit_time}:${t.net_pnl_inr}`).join('|') + `_${dateLabel}`;
    if (hash === lastRenderedTradesHash) return;
    lastRenderedTradesHash = hash;

    if (!trades || trades.length === 0) {
        const label = dateLabel ? `for ${dateLabel}` : 'yet';
        container.innerHTML = `<div style="text-align: center; color: var(--text-dim); padding: 16px 12px; font-size: 0.8rem;">No executed trades ${label}.</div>`;
        return;
    }

    container.innerHTML = trades.slice(0, 15).map(t => {
        const isWin = t.is_win;
        const entryDate = new Date(t.entry_time);
        const exitDate = new Date(t.exit_time);
        const entryTime = entryDate.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit' });
        const exitTime = exitDate.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit' });
        return `
            <div class="history-item ${isWin ? 'win' : 'loss'}" style="cursor: pointer; transition: background 0.15s ease;" onclick="jumpToTrade('${t.entry_time}', '${t.exit_time}')" title="Click to view trade candles & markers on chart">
                <div>
                    <strong style="color: #fff;">${t.side}</strong>
                    <span style="color: var(--text-muted); font-size: 0.72rem;"> (${t.lots} Lots · ${t.n_slices} Legs)</span>
                    <div style="font-size: 0.68rem; color: var(--text-dim);">${entryTime} → ${exitTime} · ${t.exit_reason} 🔍</div>
                </div>
                <div style="text-align: right;">
                    <div class="${isWin ? 'val-green' : 'val-red'}" style="font-weight: 700;">
                        ${t.net_pnl_inr >= 0 ? '+' : ''}₹${t.net_pnl_inr.toLocaleString('en-IN', { maximumFractionDigits: 1 })}
                    </div>
                    <div style="font-size: 0.65rem; color: var(--text-dim);">Brokerage: ₹${t.brokerage}</div>
                </div>
            </div>
        `;
    }).join('');
}

// ── Manual Execution Controls ────────────────────────────────────────────────

async function enterAITrade() {
    try {
        const res = await fetch('/api/paper/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'ENTER' }),
        });
        const data = await res.json();
        if (data.status === 'success') {
            fetchStatus();
            fetchTrades();
        } else {
            alert(data.detail || 'Could not place order');
        }
    } catch (e) {
        console.error(e);
    }
}

async function closePosition() {
    try {
        const res = await fetch('/api/paper/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'CLOSE' }),
        });
        const data = await res.json();
        if (data.status === 'success') {
            fetchStatus();
            fetchTrades();
        }
    } catch (e) {
        console.error(e);
    }
}

// ── Mode Switcher & Tabs Logic ───────────────────────────────────────────────

async function selectMode(targetMode) {
    App.mode = targetMode;

    // Highlight button in mode-bar
    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.mode === targetMode) {
            btn.classList.add('active');
        }
    });

    const heroCard = document.getElementById('prediction-hero-card');
    const simCard = document.getElementById('simulation-control-card');
    const backtestCard = document.getElementById('panel-backtest-summary');

    if (targetMode === 'simulation') {
        if (heroCard) heroCard.style.display = 'none';
        if (backtestCard) backtestCard.style.display = 'none';
        if (simCard) simCard.style.display = 'flex';
        openSimulationModal();
        const btnPaper = document.getElementById('tab-btn-paper');
        if (btnPaper) btnPaper.click();
    } else if (targetMode === 'backtest') {
        if (heroCard) heroCard.style.display = 'none';
        if (simCard) simCard.style.display = 'none';
        if (backtestCard) backtestCard.style.display = 'block';
        closeSimulationModal();
        if (simPollerId) {
            clearInterval(simPollerId);
            simPollerId = null;
        }
        App.simPlaying = false;
        App.simPaused = false;
        try { await fetch('/api/simulation/stop', { method: 'POST' }); } catch (e) {}
    } else {
        // predictor, paper, live
        if (heroCard) heroCard.style.display = 'block';
        if (simCard) simCard.style.display = 'none';
        if (backtestCard) backtestCard.style.display = 'none';
        closeSimulationModal();
        if (simPollerId) {
            clearInterval(simPollerId);
            simPollerId = null;
        }
        App.simPlaying = false;
        App.simPaused = false;
        try { await fetch('/api/simulation/stop', { method: 'POST' }); } catch (e) {}
    }

    try {
        await fetch('/api/mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: targetMode }),
        });
    } catch (e) {
        console.error(e);
    }

    updateChartPriceLines();
    await fetchCandles(true);
    await fetchTrades();
    await fetchStatus();
}

function initModeSwitcher() {
    const buttons = document.querySelectorAll('.mode-btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetMode = btn.dataset.mode;
            if (targetMode) selectMode(targetMode);
        });
    });
}

// ── Simulation Modal & Sync Helpers ──────────────────────────────────────────

function openSimulationModal() {
    const overlay = document.getElementById('simulation-modal-overlay');
    if (overlay) overlay.style.display = 'flex';
}

function closeSimulationModal() {
    const overlay = document.getElementById('simulation-modal-overlay');
    if (overlay) overlay.style.display = 'none';
}

function closeSimulationModalOnBackdrop(e) {
    if (e.target && e.target.id === 'simulation-modal-overlay') {
        closeSimulationModal();
    }
}

function syncDateInputs(val, triggerFetch = true) {
    if (!val) return;
    const mSelect = document.getElementById('sim-modal-date-select');
    const pSelect = document.getElementById('sim-date-select');
    const cSelect = document.getElementById('chart-session-date-select');
    const badge = document.getElementById('sim-current-date-badge');
    if (mSelect && mSelect.value !== val) mSelect.value = val;
    if (pSelect && pSelect.value !== val) pSelect.value = val;
    if (cSelect && cSelect.value !== val) cSelect.value = val;
    if (badge) badge.innerText = val;

    App.selectedDate = val;
    if (triggerFetch && (!App.simPlaying || App.mode !== 'simulation')) {
        fetchCandles(true, val);
        fetchTrades(null, val);
        fetchOptionChain(val);
    }
}

window.syncDateInputs = syncDateInputs;
window.selectMode = selectMode;

function syncSpeedInputs(val) {
    const mSelect = document.getElementById('sim-modal-speed-select');
    const pSelect = document.getElementById('sim-speed-select');
    if (mSelect && mSelect.value !== val) mSelect.value = val;
    if (pSelect && pSelect.value !== val) pSelect.value = val;
}

function startSimulationFromModal() {
    const mDate = document.getElementById('sim-modal-date-select');
    const mSpeed = document.getElementById('sim-modal-speed-select');
    if (mDate) syncDateInputs(mDate.value);
    if (mSpeed) syncSpeedInputs(mSpeed.value);

    closeSimulationModal();
    startSimulation();
}

// ── Simulation Engine Controls ──────────────────────────────────────────────

function initSimulationControls() {
    loadSimulationDates();

    const btnStart = document.getElementById('btn-sim-start');
    const btnPause = document.getElementById('btn-sim-pause');
    const btnStop = document.getElementById('btn-sim-stop');

    if (btnStart) btnStart.addEventListener('click', () => startSimulation());
    if (btnPause) btnPause.addEventListener('click', togglePauseSimulation);
    if (btnStop) btnStop.addEventListener('click', stopSimulation);

    const cSelect = document.getElementById('chart-session-date-select');
    if (cSelect) cSelect.addEventListener('change', (e) => syncDateInputs(e.target.value));

    const pSelect = document.getElementById('sim-date-select');
    if (pSelect) pSelect.addEventListener('change', (e) => syncDateInputs(e.target.value));

    const mSelect = document.getElementById('sim-modal-date-select');
    if (mSelect) mSelect.addEventListener('change', (e) => syncDateInputs(e.target.value));
}

async function loadSimulationDates() {
    try {
        const res = await fetch('/api/simulation/dates');
        const data = await res.json();
        if (data.dates && data.dates.length > 0) {
            const cappedDates = data.dates.slice(0, 30);
            const todayStr = data.today || new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
            const html = cappedDates.map((d, idx) => {
                let label = `${d} (Historical)`;
                if (d === todayStr) {
                    label = `${d} (Today · Live Session)`;
                } else if (idx === 0) {
                    label = `${d} (Latest Session)`;
                }
                return `<option value="${d}">${label}</option>`;
            }).join('');
            const pSelect = document.getElementById('sim-date-select');
            const mSelect = document.getElementById('sim-modal-date-select');
            const cSelect = document.getElementById('chart-session-date-select');
            if (pSelect) pSelect.innerHTML = html;
            if (mSelect) mSelect.innerHTML = html;
            if (cSelect) cSelect.innerHTML = html;

            const targetDate = App.selectedDate || (cappedDates.includes(todayStr) ? todayStr : cappedDates[0]);
            syncDateInputs(targetDate);
        }
    } catch (e) {
        console.error("Error loading simulation dates:", e);
    }
}

let simPollerId = null;

function startSimulationPoller() {
    if (simPollerId) clearInterval(simPollerId);
    simPollerId = setInterval(async () => {
        if (!App.simPlaying) {
            clearInterval(simPollerId);
            return;
        }
        try {
            const res = await fetch('/api/candles');
            const data = await res.json();
            if (data.candles && data.candles.length > 0) {
                const formatted = formatCandlesForChart(data.candles);
                App.simCandles = formatted;
                if (App.candleSeries && formatted.length > 0) {
                    if (data.markers && data.markers.length > 0) {
                        App.markers = data.markers;
                    }
                    applySMFCloud(formatted, App.markers || []);
                }
            }
            fetchStatus();
        } catch (err) {
            console.warn("Simulation poller tick:", err);
        }
    }, 1200);
}

async function startSimulation(optDate, optSpeed) {
    try {
        // If an event object or non-string was passed, ignore it
        if (optDate && (typeof optDate !== 'string' || optDate.target || optDate.isTrusted)) {
            optDate = null;
        }
        if (optSpeed && typeof optSpeed !== 'number' && typeof optSpeed !== 'string') {
            optSpeed = null;
        }

        const mDateSelect = document.getElementById('sim-modal-date-select');
        const pDateSelect = document.getElementById('sim-date-select');
        const mSpeedSelect = document.getElementById('sim-modal-speed-select');
        const pSpeedSelect = document.getElementById('sim-speed-select');

        let simDate = (typeof optDate === 'string' && optDate.length === 10) ? optDate : null;
        if (!simDate) {
            simDate = (pDateSelect && pDateSelect.value) || (mDateSelect && mDateSelect.value) || "2026-10-09";
        }
        let speed = parseInt(optSpeed || (pSpeedSelect ? pSpeedSelect.value : null) || (mSpeedSelect ? mSpeedSelect.value : null) || "10", 10);
        if (isNaN(speed) || speed < 1) speed = 10;

        syncDateInputs(simDate, false);
        syncSpeedInputs(speed);

        // Reset state
        App.markers = [];
        App.allMarkers = [];
        App.simCandles = [];
        App.simPlaying = true;
        App.simPaused = false;
        if (App.candleSeries) {
            try { App.candleSeries.setMarkers([]); } catch (e) {}
        }
        renderStrategySignals([]);
        const sBadge = document.getElementById('strategy-count-badge');
        if (sBadge) sBadge.innerText = '0';

        const btnStart = document.getElementById('btn-sim-start');
        const btnPause = document.getElementById('btn-sim-pause');
        const btnStop = document.getElementById('btn-sim-stop');
        if (btnStart) btnStart.disabled = true;
        if (btnPause) { btnPause.disabled = false; btnPause.innerText = '⏸ Pause'; }
        if (btnStop) btnStop.disabled = false;

        const pill = document.getElementById('sim-status-pill');
        if (pill) {
            pill.style.background = 'rgba(0, 242, 169, 0.15)';
            pill.style.color = '#00f2a9';
            pill.style.borderColor = 'rgba(0, 242, 169, 0.4)';
            pill.innerText = '▶ REPLAYING LIVE';
        }

        const progTxt = document.getElementById('sim-progress-text');
        if (progTxt) progTxt.innerText = `Starting replay for ${simDate} at ${speed}x speed...`;

        // Switch to Paper Trading tab so user sees live positions
        const btnPaper = document.getElementById('tab-btn-paper');
        if (btnPaper) btnPaper.click();

        // Close modal if open
        closeSimulationModal();

        // POST to start simulation
        const res = await fetch('/api/simulation/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: String(simDate), speed: Number(speed) })
        });
        if (!res.ok) {
            const errText = await res.text();
            throw new Error(`Server returned status ${res.status}: ${errText}`);
        }
        const data = await res.json();
        console.log("Simulation session launched:", data);

        // If server provided opening candles, populate chart immediately
        if (data.initial_candles && data.initial_candles.length > 0) {
            const formatted = formatCandlesForChart(data.initial_candles);
            App.simCandles = formatted;
            if (App.candleSeries && formatted.length > 0) {
                applySMFCloud(formatted, App.markers || []);
                if (App.chart) App.chart.timeScale().fitContent();
            }
        }

        // Start fallback poller
        startSimulationPoller();

    } catch (e) {
        console.error("Error starting simulation:", e);
        const btnStart = document.getElementById('btn-sim-start');
        if (btnStart) btnStart.disabled = false;
        alert("Could not start simulation: " + (e.message || e));
    }
}

async function togglePauseSimulation() {
    try {
        const res = await fetch('/api/simulation/pause', { method: 'POST' });
        const data = await res.json();
        const btnPause = document.getElementById('btn-sim-pause');
        if (btnPause) {
            btnPause.innerText = data.paused ? '▶ Resume' : '⏸ Pause';
        }
    } catch (e) {
        console.error("Error toggling pause:", e);
    }
}

async function stopSimulation() {
    try {
        if (simPollerId) {
            clearInterval(simPollerId);
            simPollerId = null;
        }

        await fetch('/api/simulation/stop', { method: 'POST' });
        const btnStart = document.getElementById('btn-sim-start');
        const btnPause = document.getElementById('btn-sim-pause');
        const btnStop = document.getElementById('btn-sim-stop');
        if (btnStart) btnStart.disabled = false;
        if (btnPause) { btnPause.disabled = true; btnPause.innerText = '⏸ Pause'; }
        if (btnStop) btnStop.disabled = true;

        App.simPlaying = false;
        App.simPaused = false;

        const pill = document.getElementById('sim-status-pill');
        if (pill) {
            pill.style.background = 'rgba(0, 210, 255, 0.15)';
            pill.style.color = '#00d2ff';
            pill.style.borderColor = 'rgba(0, 210, 255, 0.3)';
            pill.innerText = '⚡ REPLAY READY';
        }

        // Restore default candles
        fetchCandles();
    } catch (e) {
        console.error("Error stopping simulation:", e);
    }
}

function initTabToggles() {
    const btnChain = document.getElementById('tab-btn-chain');
    const btnPaper = document.getElementById('tab-btn-paper');
    const btnStrategy = document.getElementById('tab-btn-strategy');
    const viewChain = document.getElementById('panel-option-chain');
    const viewPaper = document.getElementById('panel-paper-trading');
    const viewStrategy = document.getElementById('panel-strategy-signals');

    function switchTab(tab) {
        App.activeTab = tab;
        if (btnChain) btnChain.classList.toggle('active', tab === 'chain');
        if (btnPaper) btnPaper.classList.toggle('active', tab === 'paper');
        if (btnStrategy) btnStrategy.classList.toggle('active', tab === 'strategy');

        if (viewChain) viewChain.style.display = (tab === 'chain') ? 'flex' : 'none';
        if (viewPaper) viewPaper.style.display = (tab === 'paper') ? 'flex' : 'none';
        if (viewStrategy) viewStrategy.style.display = (tab === 'strategy') ? 'block' : 'none';

        if (tab === 'paper') {
            fetchTrades();
        } else if (tab === 'chain') {
            fetchOptionChain();
        } else if (tab === 'strategy') {
            fetchStatus();
        }
    }

    if (btnChain) btnChain.addEventListener('click', () => switchTab('chain'));
    if (btnPaper) btnPaper.addEventListener('click', () => switchTab('paper'));
    if (btnStrategy) btnStrategy.addEventListener('click', () => switchTab('strategy'));

    window.switchTab = switchTab;
}

// ── Strategy Signals & GTT Order Recommendations ─────────────────────────────

function renderStrategySignals(suggestions) {
    const container = document.getElementById('strategy-signals-container');
    if (!container) return;

    if (!suggestions || suggestions.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; padding: 24px 10px; color: var(--text-dim);">
                <div style="font-size: 1.8rem; margin-bottom: 6px;">🎯</div>
                <div style="font-family: var(--font-display); font-size: 0.95rem; font-weight: 600; color: #fff;">Scanning for Wyckoff Sweeps & AMD Setups...</div>
                <div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 4px;">The modular strategy engine runs parallel TPO Volume Profile (VAL / POC / VAH) scans on every 1M candle.</div>
            </div>
        `;
        return;
    }

    container.innerHTML = suggestions.map((sig, idx) => {
        const isCall = sig.action === 'BUY_CE';
        const badgeColor = isCall ? '#00f2a9' : '#ff3860';
        const bgGlow = isCall ? 'rgba(0, 242, 169, 0.08)' : 'rgba(255, 56, 96, 0.08)';
        const borderGlow = isCall ? 'rgba(0, 242, 169, 0.3)' : 'rgba(255, 56, 96, 0.3)';

        return `
            <div class="strategy-card glass-panel" style="background: ${bgGlow}; border: 1px solid ${borderGlow}; border-radius: 8px; padding: 12px; margin-bottom: 10px;">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">
                    <div>
                        <span class="pill-badge" style="background: ${isCall ? 'rgba(0, 242, 169, 0.2)' : 'rgba(255, 56, 96, 0.2)'}; color: ${badgeColor}; font-weight: 700; font-size: 0.75rem;">
                            ${sig.signal_type || (isCall ? 'BULLISH SPRING' : 'BEARISH UPTHRUST')}
                        </span>
                        <div style="font-family: var(--font-display); font-size: 0.92rem; font-weight: 700; color: #fff; margin-top: 4px;">
                            ${sig.strategy_name}
                        </div>
                    </div>
                    <div style="text-align: right;">
                        <span style="font-family: var(--font-mono); font-size: 0.85rem; font-weight: 700; color: ${badgeColor};">
                            ${sig.strike}
                        </span>
                        <div style="font-size: 0.72rem; color: var(--text-muted);">${sig.gtt_type}</div>
                    </div>
                </div>

                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; background: rgba(0,0,0,0.25); border-radius: 6px; padding: 8px; margin-bottom: 10px; font-family: var(--font-mono); font-size: 0.78rem;">
                    <div>
                        <div style="font-size: 0.68rem; color: var(--text-muted);">Trigger Px</div>
                        <div style="font-weight: 600; color: #fff;">₹${Number(sig.trigger_price).toFixed(1)}</div>
                    </div>
                    <div>
                        <div style="font-size: 0.68rem; color: #00f2a9;">Target Px</div>
                        <div style="font-weight: 600; color: #00f2a9;">₹${Number(sig.target_price).toFixed(1)}</div>
                    </div>
                    <div>
                        <div style="font-size: 0.68rem; color: #ff3860;">Stop Loss</div>
                        <div style="font-weight: 600; color: #ff3860;">₹${Number(sig.stop_loss).toFixed(1)}</div>
                    </div>
                </div>

                <div style="display: flex; gap: 8px; align-items: center;">
                    <button class="btn-trade-ai" style="flex: 1; padding: 8px 12px; font-size: 0.8rem; justify-content: center;" onclick="placeStrategyOrder(${idx})">
                        ⚡ Place Paper GTT Order (${sig.lots} Lots)
                    </button>
                    <button class="mode-btn" style="padding: 8px 12px; font-size: 0.78rem;" onclick="copyGTTOrder(${idx})">
                        📋 Copy GTT
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

async function placeStrategyOrder(idx) {
    try {
        const res = await fetch('/api/strategy/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index: Number(idx) })
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "Failed to execute strategy order");
        }
        const data = await res.json();
        alert(`⚡ Institutional Strategy Order Executed! Filled ${data.position ? data.position.lots : 1} lots @ ₹${data.position ? data.position.entry_price.toFixed(1) : ''}`);
        fetchStatus();
    } catch (e) {
        alert("Error executing strategy order: " + e.message);
    }
}

function copyGTTOrder(idx) {
    if (App.lastStatus && App.lastStatus.strategy_suggestions && App.lastStatus.strategy_suggestions[idx]) {
        const payload = JSON.stringify(App.lastStatus.strategy_suggestions[idx], null, 2);
        navigator.clipboard.writeText(payload).then(() => {
            alert("Institutional GTT Order copied to clipboard!");
        }).catch(() => {
            prompt("Institutional GTT Order details:", payload);
        });
    }
}

function initActionButtons() {
    const followBtn = document.getElementById('btn-follow-ai');
    if (followBtn) {
        followBtn.addEventListener('click', enterAITrade);
    }
}

// ── WebSocket Live Tick & Simulation Stream ──────────────────────────────────

function initWebSocket() {
    const proto = (window.location.protocol === 'https:') ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${window.location.host}/ws`;

    App.ws = new WebSocket(wsUrl);

    App.ws.onopen = () => {
        console.log("WebSocket connected to trading engine stream");
    };

    if (App.wsPingInterval) clearInterval(App.wsPingInterval);
    App.wsPingInterval = setInterval(() => {
        if (App.ws && App.ws.readyState === WebSocket.OPEN) {
            App.ws.send(JSON.stringify({ type: 'PING' }));
        }
    }, 4000);

    App.ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);

            // 1. Standard Live Mode Tick Update
            if (data.type === 'TICK_UPDATE') {
                App.spotPrice = data.spot;

                // Auto-fetch candles if chart is currently empty (e.g. loaded pre-market)
                if (App.mode !== 'simulation' && (!App.lastCandles || App.lastCandles.length === 0)) {
                    fetchCandles(true, App.selectedDate);
                }

                // Update spot price on header
                const spotEl = document.getElementById('header-spot-val');
                if (spotEl) spotEl.innerText = `₹${data.spot.toFixed(2)}`;

                // Update dynamic AI prediction & strike suggestions
                if (data.prediction) {
                    renderPrediction(data.prediction);
                }

                // Update dynamic option chain
                if (data.option_chain && data.option_chain.chain) {
                    renderOptionChain(data.option_chain.chain, data.option_chain.atm_strike);
                }

                // Update active strategy signals and badge count
                if (data.strategy_suggestions !== undefined) {
                    const sBadge = document.getElementById('strategy-count-badge');
                    if (sBadge) sBadge.innerText = data.strategy_count || data.strategy_suggestions.length;
                    renderStrategySignals(data.strategy_suggestions);
                }

                // Update account balance
                if (data.capital) {
                    const capEl = document.getElementById('paper-capital-val');
                    if (capEl) capEl.innerText = `₹${Math.round(data.capital).toLocaleString('en-IN')}`;
                }

                // Update floating PnL
                if (data.unrealized_pnl !== undefined) {
                    const pnlEl = document.getElementById('paper-floating-pnl');
                    if (pnlEl) {
                        const pnl = data.unrealized_pnl;
                        pnlEl.className = `val ${pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}`;
                        pnlEl.innerText = `${pnl >= 0 ? '+' : ''}₹${pnl.toLocaleString('en-IN', { maximumFractionDigits: 1 })}`;
                    }
                }

                // Update active position or clear if closed
                if (data.active_position) {
                    App.activePosition = data.active_position;
                    renderActivePosition(data.active_position, data.unrealized_pnl);
                    updateChartPriceLines();
                } else if (App.activePosition) {
                    App.activePosition = null;
                    renderActivePosition(null, 0);
                    updateChartPriceLines();
                    fetchTrades();
                }

                // Push forming candle or tick update to chart in real time
                if (App.candleSeries) {
                    if (data.forming_candle) {
                        const fc = {
                            time: Number(data.forming_candle.time),
                            open: Number(data.forming_candle.open),
                            high: Number(data.forming_candle.high),
                            low: Number(data.forming_candle.low),
                            close: Number(data.forming_candle.close),
                        };
                        if (SMF.enabled && SMF.lastResult) {
                            const col = SMF.lastResult.latestRegime === 1 ? SMF.bullColor : SMF.bearColor;
                            fc.color = col;
                            fc.borderColor = col;
                            fc.wickColor = col;
                        }
                        try { App.candleSeries.update(fc); } catch (e) {}
                    } else if (App.lastCandles && App.lastCandles.length > 0) {
                        const last = App.lastCandles[App.lastCandles.length - 1];
                        const updatedCandle = {
                            time: Number(last.timestamp || last.time),
                            open: Number(last.open),
                            high: Math.max(Number(last.high), Number(data.spot)),
                            low: Math.min(Number(last.low), Number(data.spot)),
                            close: Number(data.spot),
                        };
                        if (SMF.enabled && SMF.lastResult) {
                            const col = SMF.lastResult.latestRegime === 1 ? SMF.bullColor : SMF.bearColor;
                            updatedCandle.color = col;
                            updatedCandle.borderColor = col;
                            updatedCandle.wickColor = col;
                        }
                        try { App.candleSeries.update(updatedCandle); } catch (e) {}
                    }
                }

                // If sub-second execution hit Target or SL, display marker immediately & update trades
                if (data.marker && App.candleSeries) {
                    App.markers.push({
                        time: Number(data.marker.time),
                        position: data.marker.position || 'aboveBar',
                        color: data.marker.color || '#00d2ff',
                        shape: data.marker.shape || 'flag',
                        text: data.marker.text,
                    });
                    if (SMF.enabled && App.lastCandlesFormatted && App.lastCandlesFormatted.length > 0) {
                        applySMFCloud(App.lastCandlesFormatted, App.markers);
                    } else {
                        setAllMarkers(App.markers, App.lastCandles || []);
                    }
                    fetchTrades();
                    fetchStatus();
                }
            }

            // 2. Simulation Replay Live Bar Stream
            else if (data.type === 'SIMULATION_TICK') {
                App.spotPrice = data.spot;

                // Update spot price on header
                const spotEl = document.getElementById('header-spot-val');
                if (spotEl) spotEl.innerText = `₹${data.spot.toFixed(2)}`;

                // Safely update or push candle on chart
                if (App.candleSeries && data.candle) {
                    const c = {
                        time: Number(data.candle.timestamp || data.candle.time),
                        open: Number(data.candle.open),
                        high: Number(data.candle.high),
                        low: Number(data.candle.low),
                        close: Number(data.candle.close),
                        volume: Number(data.candle.volume || 0),
                    };
                    if (!App.simCandles || App.simCandles.length === 0) {
                        App.simCandles = [c];
                    } else {
                        const lastTime = App.simCandles[App.simCandles.length - 1].time;
                        if (c.time > lastTime) {
                            App.simCandles.push(c);
                        } else if (c.time === lastTime) {
                            App.simCandles[App.simCandles.length - 1] = c;
                        }
                    }

                    if (SMF.enabled && App.simCandles.length >= 5) {
                        applySMFCloud(App.simCandles, App.markers || []);
                    } else {
                        try {
                            App.candleSeries.update(c);
                        } catch (e) {
                            App.candleSeries.setData(App.simCandles);
                        }
                    }
                }

                // Append and display AI Entry / Target / SL Markers directly on the chart
                if (data.marker && App.candleSeries) {
                    App.markers.push({
                        time: Number(data.marker.time),
                        position: data.marker.position || 'belowBar',
                        color: data.marker.color || '#00f2a9',
                        shape: data.marker.shape || 'arrowUp',
                        text: data.marker.text,
                    });
                    if (SMF.enabled && App.simCandles && App.simCandles.length > 0) {
                        applySMFCloud(App.simCandles, App.markers);
                    } else {
                        setAllMarkers(App.markers, App.simCandles);
                    }

                    // If an exit occurred, refresh closed trade history list immediately
                    if (data.marker.type && data.marker.type.startsWith('EXIT')) {
                        fetchTrades();
                    }
                }

                // Update Real-Time AI Prediction Card
                if (data.prediction) {
                    renderPrediction(data.prediction);
                }

                // Update Dynamic Option Chain Matrix
                if (data.option_chain && data.option_chain.chain) {
                    renderOptionChain(data.option_chain.chain, data.option_chain.atm_strike);
                }

                // Update Paper Account Balance
                if (data.capital) {
                    const capEl = document.getElementById('paper-capital-val');
                    if (capEl) capEl.innerText = `₹${Math.round(data.capital).toLocaleString('en-IN')}`;
                }

                // Update Active Position and Floating PnL
                App.activePosition = data.active_position;
                let unrlPnl = 0.0;
                if (data.active_position) {
                    const pos = data.active_position;
                    const pts = (pos.side === 'CE') ? (data.spot - pos.entry_price) : (pos.entry_price - data.spot);
                    unrlPnl = pts * 0.65 * pos.units;
                }
                renderActivePosition(data.active_position, unrlPnl);
                updateChartPriceLines();

                // Update Strategy Suggestions & Badge during Simulation Replay
                if (data.strategy_suggestions !== undefined) {
                    const sBadge = document.getElementById('strategy-count-badge');
                    if (sBadge) sBadge.innerText = data.strategy_count || data.strategy_suggestions.length;
                    renderStrategySignals(data.strategy_suggestions);
                }

                // Update Replay Progress Tracker
                if (data.progress) {
                    const p = data.progress;
                    const curr = p.current_bar || 0;
                    const total = p.total_bars || 375;
                    const pct = Math.min(100, Math.round((curr / Math.max(1, total)) * 100));

                    const fillEl = document.getElementById('sim-progress-fill');
                    if (fillEl) fillEl.style.width = `${pct}%`;

                    const progTxt = document.getElementById('sim-progress-text');
                    if (progTxt) progTxt.innerText = `Replaying: Bar ${curr} / ${total} (${pct}%) · Speed: ${p.speed || 10}x`;

                    const metaTime = document.getElementById('sim-meta-time');
                    if (metaTime) metaTime.innerText = `Market Time: ${p.current_time || '--:-- IST'}`;

                    const pill = document.getElementById('sim-status-pill');
                    if (pill) {
                        if (p.paused) {
                            pill.style.background = 'rgba(255, 183, 3, 0.15)';
                            pill.style.color = '#ffb703';
                            pill.style.borderColor = 'rgba(255, 183, 3, 0.4)';
                            pill.innerText = '⏸ PAUSED';
                        } else if (p.active) {
                            pill.style.background = 'rgba(0, 242, 169, 0.15)';
                            pill.style.color = '#00f2a9';
                            pill.style.borderColor = 'rgba(0, 242, 169, 0.4)';
                            pill.innerText = '▶ REPLAYING LIVE';
                        } else {
                            pill.style.background = 'rgba(0, 210, 255, 0.15)';
                            pill.style.color = '#00d2ff';
                            pill.style.borderColor = 'rgba(0, 210, 255, 0.3)';
                            pill.innerText = '⚡ REPLAY COMPLETED';
                            const btnStart = document.getElementById('btn-sim-start');
                            const btnPause = document.getElementById('btn-sim-pause');
                            const btnStop = document.getElementById('btn-sim-stop');
                            if (btnStart) btnStart.disabled = false;
                            if (btnPause) btnPause.disabled = true;
                            if (btnStop) btnStop.disabled = true;
                        }
                    }
                }
            }
        } catch (e) {
            console.error('WS parse error:', e);
        }
    };

    App.ws.onclose = () => {
        if (App.wsPingInterval) clearInterval(App.wsPingInterval);
        setTimeout(initWebSocket, 2000);
    };
}
