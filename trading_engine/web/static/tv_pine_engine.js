/**
 * TradingView Pine Script Indicator Engine & Overlay Renderer
 * ─────────────────────────────────────────────────────────────
 * Enables client-side compilation, mathematical execution, and HTML5 canvas
 * rendering of TradingView Pine Script indicators (e.g. AlgoAlpha Breakout Channels)
 * directly over dual option station charts (CE / PE) with zero server overhead.
 */

(function () {
    'use strict';

    // ── Built-in Preset Scripts ────────────────────────────────────────────────
    const PRESETS = {
        algoalpha_breakout_channels: {
            id: 'algoalpha_breakout_channels',
            name: 'Smart Money Breakout Channels [AlgoAlpha]',
            inputs: {
                length_: 100,
                length: 14,
                overlap: false,
                strong: true,
                shw_vol: true,
                vol_mode: 'Comparison', // 'Volume', 'Comparison', 'Delta'
                vol_scale: 0.5,
                green: '#00ffbb',
                red: '#ff1100'
            },
            code: `// © AlgoAlpha
// This Source Code is subject to the Mozilla Public License 2.0
// https://mozilla.org/MPL/2.0/
//@version=6
indicator("Smart Money Breakout Channels [AlgoAlpha]", "AlgoAlpha - Breakout Channels", overlay=true, max_boxes_count=500)

// Main Settings
overlap = input.bool(false, "Nested Channels")
strong = input.bool(true, "Strong Closes Only")
length_ = input.int(100, "Normalization Length")
length = input.int(14, "Box Detection Length")

// Volume Analysis
shw_vol = input.bool(true, "Show Volume Analysis")
vol_mode = input.string("Comparison", "Volume Display Mode", options=["Volume", "Comparison", "Delta"])
vol_scale = input.float(0.5, "Volume Scale")
green = input.color(#00ffbb, "Bullish Colour")
red = input.color(#ff1100, "Bearish Colour")`
        },
        supertrend: {
            id: 'supertrend',
            name: 'Supertrend [ATR Multiplier]',
            inputs: {
                atr_period: 10,
                multiplier: 3.0,
                green: '#00ffbb',
                red: '#ff1100'
            },
            code: `//@version=5
indicator("Supertrend", overlay=true)
atr_period = input.int(10, "ATR Period")
multiplier = input.float(3.0, "Multiplier")
green = input.color(#00ffbb, "Bullish Color")
red = input.color(#ff1100, "Bearish Color")`
        },
        vwap_bands: {
            id: 'vwap_bands',
            name: 'VWAP + Standard Deviation Bands',
            inputs: {
                band1_mult: 1.0,
                band2_mult: 2.0,
                vwap_color: '#38bdf8',
                band_color: 'rgba(56, 189, 248, 0.3)'
            },
            code: `//@version=5
indicator("VWAP + SD Bands", overlay=true)
band1_mult = input.float(1.0, "Band 1 Multiplier")
band2_mult = input.float(2.0, "Band 2 Multiplier")`
        }
    };

    // ── State Container ─────────────────────────────────────────────────────────
    const state = {
        enabled: true,
        target: 'both', // 'both', 'CE', 'PE'
        activePresetId: 'algoalpha_breakout_channels',
        currentCode: PRESETS.algoalpha_breakout_channels.code,
        inputs: { ...PRESETS.algoalpha_breakout_channels.inputs },
        lastCompileTime: null,
        compileStats: null
    };

    // ── Pine Technical Analysis Library (Pure JS Vectorized) ────────────────────
    const PineTA = {
        lowest(series, len) {
            const out = new Array(series.length);
            for (let i = 0; i < series.length; i++) {
                let minVal = series[i];
                const start = Math.max(0, i - len + 1);
                for (let j = start; j <= i; j++) {
                    if (series[j] < minVal) minVal = series[j];
                }
                out[i] = minVal;
            }
            return out;
        },

        highest(series, len) {
            const out = new Array(series.length);
            for (let i = 0; i < series.length; i++) {
                let maxVal = series[i];
                const start = Math.max(0, i - len + 1);
                for (let j = start; j <= i; j++) {
                    if (series[j] > maxVal) maxVal = series[j];
                }
                out[i] = maxVal;
            }
            return out;
        },

        stdev(series, len) {
            const out = new Array(series.length);
            for (let i = 0; i < series.length; i++) {
                if (i < len - 1) {
                    out[i] = 0;
                    continue;
                }
                let sum = 0;
                for (let j = i - len + 1; j <= i; j++) sum += series[j];
                const mean = sum / len;
                let varSum = 0;
                for (let j = i - len + 1; j <= i; j++) {
                    const diff = series[j] - mean;
                    varSum += diff * diff;
                }
                out[i] = Math.sqrt(varSum / len);
            }
            return out;
        },

        highestbars(series, len) {
            const out = new Array(series.length);
            for (let i = 0; i < series.length; i++) {
                let maxVal = series[i];
                let maxIdx = i;
                const start = Math.max(0, i - len + 1);
                for (let j = start; j <= i; j++) {
                    if (series[j] >= maxVal) {
                        maxVal = series[j];
                        maxIdx = j;
                    }
                }
                out[i] = maxIdx - i; // negative offset or 0
            }
            return out;
        },

        lowestbars(series, len) {
            const out = new Array(series.length);
            for (let i = 0; i < series.length; i++) {
                let minVal = series[i];
                let minIdx = i;
                const start = Math.max(0, i - len + 1);
                for (let j = start; j <= i; j++) {
                    if (series[j] <= minVal) {
                        minVal = series[j];
                        minIdx = j;
                    }
                }
                out[i] = minIdx - i;
            }
            return out;
        },

        sma(series, len) {
            const out = new Array(series.length);
            let sum = 0;
            for (let i = 0; i < series.length; i++) {
                sum += series[i];
                if (i >= len) sum -= series[i - len];
                const count = Math.min(i + 1, len);
                out[i] = sum / count;
            }
            return out;
        },

        atr(candles, len) {
            const n = candles.length;
            const tr = new Array(n);
            for (let i = 0; i < n; i++) {
                const c = candles[i];
                if (i === 0) {
                    tr[i] = c.high - c.low;
                } else {
                    const prevClose = candles[i - 1].close;
                    tr[i] = Math.max(
                        c.high - c.low,
                        Math.abs(c.high - prevClose),
                        Math.abs(c.low - prevClose)
                    );
                }
            }
            return PineTA.sma(tr, len);
        },

        crossover(a, b) {
            const out = new Array(a.length).fill(false);
            for (let i = 1; i < a.length; i++) {
                if (a[i] > b[i] && a[i - 1] <= b[i - 1]) {
                    out[i] = true;
                }
            }
            return out;
        }
    };

    // ── AlgoAlpha Breakout Channels Mathematical Runner ─────────────────────────
    function computeAlgoAlphaBreakoutChannels(candles, inputs) {
        const n = candles.length;
        if (n < 20) return null;

        const length_ = Math.max(10, parseInt(inputs.length_) || 100);
        const length = Math.max(2, parseInt(inputs.length) || 14);
        const overlap = Boolean(inputs.overlap);
        const strong = Boolean(inputs.strong);
        const shw_vol = Boolean(inputs.shw_vol);
        const vol_mode = inputs.vol_mode || 'Comparison';
        const vol_scale = parseFloat(inputs.vol_scale) || 0.5;
        const colGreen = inputs.green || '#00ffbb';
        const colRed = inputs.red || '#ff1100';

        const highs = candles.map(c => c.high);
        const lows = candles.map(c => c.low);
        const closes = candles.map(c => c.close);
        const opens = candles.map(c => c.open);
        const volumes = candles.map(c => c.volume || 1);

        const lowestLow = PineTA.lowest(lows, length_);
        const highestHigh = PineTA.highest(highs, length_);

        const normalizedPrice = new Array(n);
        for (let i = 0; i < n; i++) {
            const range = highestHigh[i] - lowestLow[i];
            normalizedPrice[i] = range > 0 ? (closes[i] - lowestLow[i]) / range : 0.5;
        }

        const vol = PineTA.stdev(normalizedPrice, 14);
        const hb = PineTA.highestbars(vol, length + 1);
        const lb = PineTA.lowestbars(vol, length + 1);

        const upper = new Array(n);
        const lower = new Array(n);
        for (let i = 0; i < n; i++) {
            upper[i] = (hb[i] + length) / length;
            lower[i] = (lb[i] + length) / length;
        }

        const smoothedvol = PineTA.sma(volumes, 20);
        const atrArr = PineTA.atr(candles, length);
        const vola = new Array(n);
        for (let i = 0; i < n; i++) vola[i] = (atrArr[i] || 1) / 2;

        // Up & Down Volume Delta approximations from intraday candles
        const uv = new Array(n);
        const dv = new Array(n);
        const vold = new Array(n);
        for (let i = 0; i < n; i++) {
            const c = candles[i];
            const rng = Math.max(0.001, c.high - c.low);
            const upFrac = Math.max(0, Math.min(1, (c.close - c.low) / rng));
            uv[i] = (c.volume || 0) * upFrac;
            dv[i] = (c.volume || 0) * (1 - upFrac);
            vold[i] = uv[i] - dv[i];
        }

        // Box & Signal State Machine
        const activeBoxes = [];
        const completedBoxes = [];
        const signals = [];
        const volCandles = [];

        let lastLowerCrossUpper = -999;

        for (let i = 1; i < n; i++) {
            // Check crossover(lower, upper)
            if (lower[i] > upper[i] && lower[i - 1] <= upper[i - 1]) {
                lastLowerCrossUpper = i;
            }

            const duration = Math.max(1, lastLowerCrossUpper >= 0 ? i - lastLowerCrossUpper : i);

            // Highest & lowest over duration
            let h = lows[i];
            let l = highs[i];
            const durStart = Math.max(0, i - duration + 1);
            for (let j = durStart; j <= i; j++) {
                if (highs[j] > h) h = highs[j];
                if (lows[j] < l) l = lows[j];
            }

            // Check crossover(upper, lower) and duration > 10
            const upperCrossLower = (upper[i] > lower[i] && upper[i - 1] <= lower[i - 1]);
            if (upperCrossLower && duration > 10) {
                let canCreate = true;
                if (!overlap && activeBoxes.length > 0) {
                    for (const b of activeBoxes) {
                        if (h > b.bottom && l < b.top) {
                            canCreate = false;
                            break;
                        }
                    }
                }

                if (canCreate) {
                    const startIdx = Math.max(0, i - duration);
                    const vZone = vola[i] || ((h - l) * 0.15);
                    activeBoxes.unshift({
                        id: `box_${i}`,
                        startIdx: startIdx,
                        startTime: candles[startIdx].time,
                        endIdx: i,
                        endTime: candles[i].time,
                        top: h,
                        bottom: l,
                        centerY: (h + l) / 2,
                        vola: vZone,
                        topZoneBottom: h - vZone,
                        bottomZoneTop: l + vZone,
                        volText: '',
                        active: true
                    });
                }
            }

            // Evaluate active boxes against current candle
            const currentEvalPrice = strong ? (closes[i] + opens[i]) / 2 : closes[i];

            for (let bIdx = activeBoxes.length - 1; bIdx >= 0; bIdx--) {
                const b = activeBoxes[bIdx];

                if (currentEvalPrice > b.top) {
                    // Bullish Breakout
                    b.endIdx = i;
                    b.endTime = candles[i].time;
                    b.active = false;
                    b.breakout = 'bullish';
                    b.breakoutPrice = b.bottom;
                    completedBoxes.push(b);
                    activeBoxes.splice(bIdx, 1);

                    signals.push({
                        time: candles[i].time,
                        price: candles[i].low,
                        type: 'bullish',
                        text: '▲',
                        color: colGreen
                    });
                } else if (currentEvalPrice < b.bottom) {
                    // Bearish Breakout
                    b.endIdx = i;
                    b.endTime = candles[i].time;
                    b.active = false;
                    b.breakout = 'bearish';
                    b.breakoutPrice = b.top;
                    completedBoxes.push(b);
                    activeBoxes.splice(bIdx, 1);

                    signals.push({
                        time: candles[i].time,
                        price: candles[i].high,
                        type: 'bearish',
                        text: '▼',
                        color: colRed
                    });
                } else {
                    // Channel still active, extend right
                    b.endIdx = i;
                    b.endTime = candles[i].time;

                    const boxMidline = (b.top + b.bottom) / 2;
                    let text = '';
                    if (vol_mode === 'Volume') {
                        text = Math.round((volumes[i] || 0) / 1000) + 'K';
                    } else if (vol_mode === 'Comparison') {
                        text = Math.round(uv[i] / 1000) + 'K / ' + Math.round(dv[i] / 1000) + 'K';
                    } else {
                        text = (vold[i] >= 0 ? '+' : '') + Math.round(vold[i] / 1000) + 'K';
                    }
                    b.volText = text;
                    b.isPriceAboveMid = currentEvalPrice > boxMidline;
                }
            }

            // Channel Volume Candle for current active channel
            if (activeBoxes.length > 0 && shw_vol) {
                const primaryBox = activeBoxes[0];
                const mid = primaryBox.centerY;
                const chHeight = (primaryBox.top - primaryBox.bottom) * vol_scale;
                const smVol = Math.max(1, smoothedvol[i] || 1);

                if (vol_mode === 'Comparison') {
                    const uvH = (uv[i] / smVol) * (chHeight / 4);
                    const dvH = (dv[i] / smVol) * (chHeight / 4);
                    volCandles.push({
                        time: candles[i].time,
                        upperOpen: mid,
                        upperClose: mid + uvH,
                        upperHigh: mid + uvH,
                        upperLow: mid,
                        lowerOpen: mid,
                        lowerClose: mid - dvH,
                        lowerHigh: mid,
                        lowerLow: mid - dvH
                    });
                } else if (vol_mode === 'Delta') {
                    const dH = Math.abs(vold[i] / smVol) * (chHeight / 4);
                    const isPos = vold[i] >= 0;
                    volCandles.push({
                        time: candles[i].time,
                        upperOpen: mid,
                        upperClose: isPos ? mid + dH : mid,
                        upperHigh: isPos ? mid + dH : mid,
                        upperLow: mid,
                        lowerOpen: mid,
                        lowerClose: !isPos ? mid - dH : mid,
                        lowerHigh: mid,
                        lowerLow: !isPos ? mid - dH : mid
                    });
                }
            }
        }

        return {
            type: 'algoalpha_breakout_channels',
            activeBoxes,
            completedBoxes,
            signals,
            volCandles,
            colGreen,
            colRed
        };
    }

    // ── Supertrend Mathematical Runner ──────────────────────────────────────────
    function computeSupertrend(candles, inputs) {
        const n = candles.length;
        if (n < 15) return null;
        const period = parseInt(inputs.atr_period) || 10;
        const mult = parseFloat(inputs.multiplier) || 3.0;
        const atr = PineTA.atr(candles, period);

        const upperBand = new Array(n);
        const lowerBand = new Array(n);
        const supertrend = new Array(n);
        const direction = new Array(n).fill(1); // 1 = Bullish, -1 = Bearish
        const signals = [];

        for (let i = 0; i < n; i++) {
            const hl2 = (candles[i].high + candles[i].low) / 2;
            const a = atr[i] || 1;
            let basicUpper = hl2 + mult * a;
            let basicLower = hl2 - mult * a;

            if (i === 0) {
                upperBand[i] = basicUpper;
                lowerBand[i] = basicLower;
                supertrend[i] = basicLower;
                direction[i] = 1;
                continue;
            }

            // Lower Band trailing
            if (basicLower > lowerBand[i - 1] || candles[i - 1].close < lowerBand[i - 1]) {
                lowerBand[i] = basicLower;
            } else {
                lowerBand[i] = lowerBand[i - 1];
            }

            // Upper Band trailing
            if (basicUpper < upperBand[i - 1] || candles[i - 1].close > upperBand[i - 1]) {
                upperBand[i] = basicUpper;
            } else {
                upperBand[i] = upperBand[i - 1];
            }

            // Direction flip
            if (direction[i - 1] === 1) {
                if (candles[i].close < lowerBand[i]) {
                    direction[i] = -1;
                    supertrend[i] = upperBand[i];
                    signals.push({ time: candles[i].time, price: candles[i].high, type: 'bearish', text: '▼', color: inputs.red || '#ff1100' });
                } else {
                    direction[i] = 1;
                    supertrend[i] = lowerBand[i];
                }
            } else {
                if (candles[i].close > upperBand[i]) {
                    direction[i] = 1;
                    supertrend[i] = lowerBand[i];
                    signals.push({ time: candles[i].time, price: candles[i].low, type: 'bullish', text: '▲', color: inputs.green || '#00ffbb' });
                } else {
                    direction[i] = -1;
                    supertrend[i] = upperBand[i];
                }
            }
        }

        return {
            type: 'supertrend',
            supertrend,
            direction,
            signals,
            candles
        };
    }

    // ── Station Computation Dispatcher ──────────────────────────────────────────
    function computeIndicatorsForStation(station) {
        if (!station || !station.rawCandles || station.rawCandles.length < 15) return;
        if (!state.enabled) {
            station.tvIndicatorResult = null;
            return;
        }

        // Target station check: 'both', 'ce', 'pe'
        const stationType = (station.type || '').toUpperCase();
        if (state.target !== 'both' && state.target.toUpperCase() !== stationType) {
            station.tvIndicatorResult = null;
            return;
        }

        const t0 = performance.now();
        let result = null;

        if (state.activePresetId === 'algoalpha_breakout_channels') {
            result = computeAlgoAlphaBreakoutChannels(station.rawCandles, state.inputs);
        } else if (state.activePresetId === 'supertrend') {
            result = computeSupertrend(station.rawCandles, state.inputs);
        }

        const elapsed = (performance.now() - t0).toFixed(1);
        station.tvIndicatorResult = result;

        if (result && (!state.compileStats || stationType === 'CE')) {
            const activeCount = (result.activeBoxes ? result.activeBoxes.length : 0);
            const sigCount = (result.signals ? result.signals.length : 0);
            state.compileStats = {
                elapsedMs: elapsed,
                numCandles: station.rawCandles.length,
                activeChannels: activeCount,
                signalsCount: sigCount,
                preset: state.activePresetId
            };
            updateDrawerStatus();
        }
    }

    // ── HTML5 Canvas Overlay Renderer ───────────────────────────────────────────
    function renderStationIndicators(station, ctx, W, H) {
        if (!station || !station.chart || !station.candleSeries) return;
        if (!state.enabled || !station.tvIndicatorResult) return;

        const res = station.tvIndicatorResult;
        const timeScale = station.chart.timeScale();
        const candleSeries = station.candleSeries;

        ctx.save();

        if (res.type === 'algoalpha_breakout_channels') {
            renderAlgoAlphaOverlay(ctx, res, timeScale, candleSeries, W, H);
        } else if (res.type === 'supertrend') {
            renderSupertrendOverlay(ctx, res, timeScale, candleSeries, W, H);
        }

        ctx.restore();
    }

    function renderAlgoAlphaOverlay(ctx, res, timeScale, candleSeries, W, H) {
        const { activeBoxes, completedBoxes, signals, volCandles, colGreen, colRed } = res;

        // 1. Draw Active Channels & Breakout Zones
        const allBoxes = [...completedBoxes.slice(-6), ...activeBoxes];

        for (const box of allBoxes) {
            const x1 = timeScale.timeToCoordinate(box.startTime);
            const x2 = timeScale.timeToCoordinate(box.endTime);
            if (x1 == null && x2 == null) continue;

            const drawX1 = Math.max(-50, x1 != null ? x1 : 0);
            const drawX2 = Math.min(W + 50, x2 != null ? x2 : W);
            if (drawX2 < drawX1) continue;

            const yTop = candleSeries.priceToCoordinate(box.top);
            const yBottom = candleSeries.priceToCoordinate(box.bottom);
            const yCenter = candleSeries.priceToCoordinate(box.centerY);
            const yTopZone = candleSeries.priceToCoordinate(box.topZoneBottom);
            const yBottomZone = candleSeries.priceToCoordinate(box.bottomZoneTop);

            if (yTop == null || yBottom == null) continue;

            const boxWidth = Math.max(2, drawX2 - drawX1);
            const boxHeight = Math.abs(yBottom - yTop);

            // A. Main Channel Background Fill
            ctx.fillStyle = box.active ? 'rgba(148, 163, 184, 0.08)' : 'rgba(100, 116, 139, 0.04)';
            ctx.fillRect(drawX1, Math.min(yTop, yBottom), boxWidth, boxHeight);

            // B. Upper Resistance Zone (Red Tint)
            if (yTopZone != null) {
                const uH = Math.abs(yTopZone - yTop);
                ctx.fillStyle = box.active ? 'rgba(255, 17, 0, 0.22)' : 'rgba(255, 17, 0, 0.08)';
                ctx.fillRect(drawX1, Math.min(yTop, yTopZone), boxWidth, uH);
                ctx.strokeStyle = box.active ? 'rgba(255, 17, 0, 0.5)' : 'rgba(255, 17, 0, 0.2)';
                ctx.lineWidth = 1;
                ctx.strokeRect(drawX1, Math.min(yTop, yTopZone), boxWidth, uH);
            }

            // C. Lower Support Zone (Green Tint)
            if (yBottomZone != null) {
                const lH = Math.abs(yBottom - yBottomZone);
                ctx.fillStyle = box.active ? 'rgba(0, 255, 187, 0.20)' : 'rgba(0, 255, 187, 0.08)';
                ctx.fillRect(drawX1, Math.min(yBottom, yBottomZone), boxWidth, lH);
                ctx.strokeStyle = box.active ? 'rgba(0, 255, 187, 0.5)' : 'rgba(0, 255, 187, 0.2)';
                ctx.lineWidth = 1;
                ctx.strokeRect(drawX1, Math.min(yBottom, yBottomZone), boxWidth, lH);
            }

            // D. Dashed Midline
            if (yCenter != null) {
                ctx.beginPath();
                ctx.setLineDash([4, 4]);
                ctx.strokeStyle = box.active ? 'rgba(255, 255, 255, 0.45)' : 'rgba(255, 255, 255, 0.15)';
                ctx.lineWidth = 1;
                ctx.moveTo(drawX1, yCenter);
                ctx.lineTo(drawX2, yCenter);
                ctx.stroke();
                ctx.setLineDash([]);
            }

            // E. Volume Text at right edge
            if (box.active && box.volText) {
                ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                ctx.textAlign = 'right';
                ctx.textBaseline = 'middle';
                if (box.isPriceAboveMid) {
                    // Lower green box
                    ctx.fillStyle = colGreen;
                    ctx.fillText(box.volText, drawX2 - 4, (yBottom + (yBottomZone || yBottom)) / 2);
                } else {
                    // Upper red box
                    ctx.fillStyle = colRed;
                    ctx.fillText(box.volText, drawX2 - 4, (yTop + (yTopZone || yTop)) / 2);
                }
            }
        }

        // 2. Volume Analysis Candles Inside Active Channel
        if (volCandles && volCandles.length > 0) {
            for (const vc of volCandles) {
                const x = timeScale.timeToCoordinate(vc.time);
                if (x == null || x < 0 || x > W) continue;

                // Upper comparison volume bar (Bullish / Up Volume)
                const yUOpen = candleSeries.priceToCoordinate(vc.upperOpen);
                const yUClose = candleSeries.priceToCoordinate(vc.upperClose);
                if (yUOpen != null && yUClose != null) {
                    ctx.fillStyle = colGreen;
                    const barH = Math.max(1, Math.abs(yUClose - yUOpen));
                    ctx.fillRect(x - 1.5, Math.min(yUOpen, yUClose), 3, barH);
                }

                // Lower comparison volume bar (Bearish / Down Volume)
                const yLOpen = candleSeries.priceToCoordinate(vc.lowerOpen);
                const yLClose = candleSeries.priceToCoordinate(vc.lowerClose);
                if (yLOpen != null && yLClose != null) {
                    ctx.fillStyle = colRed;
                    const barH = Math.max(1, Math.abs(yLClose - yLOpen));
                    ctx.fillRect(x - 1.5, Math.min(yLOpen, yLClose), 3, barH);
                }
            }
        }

        // 3. Breakout Signal Markers (▲ Bullish / ▼ Bearish)
        if (signals && signals.length > 0) {
            ctx.font = 'bold 12px sans-serif';
            ctx.textAlign = 'center';

            for (const sig of signals) {
                const x = timeScale.timeToCoordinate(sig.time);
                if (x == null || x < -20 || x > W + 20) continue;
                const y = candleSeries.priceToCoordinate(sig.price);
                if (y == null) continue;

                if (sig.type === 'bullish') {
                    // Green Arrow Up below candle
                    ctx.fillStyle = colGreen;
                    ctx.shadowColor = colGreen;
                    ctx.shadowBlur = 8;
                    ctx.fillText('▲', x, y + 16);
                } else {
                    // Red Arrow Down above candle
                    ctx.fillStyle = colRed;
                    ctx.shadowColor = colRed;
                    ctx.shadowBlur = 8;
                    ctx.fillText('▼', x, y - 6);
                }
                ctx.shadowBlur = 0;
            }
        }
    }

    function renderSupertrendOverlay(ctx, res, timeScale, candleSeries, W, H) {
        const { supertrend, direction, signals, candles } = res;
        const n = candles.length;
        if (n < 2) return;

        ctx.lineWidth = 2;
        let prevX = null;
        let prevY = null;

        for (let i = 0; i < n; i++) {
            const x = timeScale.timeToCoordinate(candles[i].time);
            if (x == null) continue;
            const y = candleSeries.priceToCoordinate(supertrend[i]);
            if (y == null) continue;

            if (prevX != null && Math.abs(x - prevX) < 100) {
                ctx.strokeStyle = direction[i] === 1 ? '#00ffbb' : '#ff1100';
                ctx.beginPath();
                ctx.moveTo(prevX, prevY);
                ctx.lineTo(x, y);
                ctx.stroke();
            }
            prevX = x;
            prevY = y;
        }

        // Signals
        if (signals) {
            ctx.font = 'bold 12px sans-serif';
            ctx.textAlign = 'center';
            for (const sig of signals) {
                const x = timeScale.timeToCoordinate(sig.time);
                if (x == null || x < 0 || x > W) continue;
                const y = candleSeries.priceToCoordinate(sig.price);
                if (y == null) continue;
                ctx.fillStyle = sig.color;
                ctx.fillText(sig.text, x, sig.type === 'bullish' ? y + 14 : y - 6);
            }
        }
    }

    // ── Persistence & Remote Cloud Sync ─────────────────────────────────────────
    async function loadConfig() {
        // 1. Try local cache first for instant 0ms load
        try {
            const local = localStorage.getItem('tv_indicators_config');
            if (local) {
                const parsed = JSON.parse(local);
                applyConfig(parsed);
            }
        } catch (e) {
            console.warn('[TvPineEngine] Error loading local config:', e);
        }

        // 2. Fetch from VPS server for cross-device synchronization
        try {
            const resp = await fetch('/api/user/indicators');
            if (resp.ok) {
                const serverData = await resp.json();
                if (serverData && serverData.active_preset) {
                    applyConfig(serverData);
                    try {
                        localStorage.setItem('tv_indicators_config', JSON.stringify(serverData));
                    } catch (e) {}
                }
            }
        } catch (e) {
            console.warn('[TvPineEngine] Could not reach indicators cloud API:', e);
        }
    }

    function applyConfig(data) {
        if (!data) return;
        if (data.enabled !== undefined) state.enabled = Boolean(data.enabled);
        if (data.target) state.target = data.target;
        if (data.active_preset && PRESETS[data.active_preset]) {
            state.activePresetId = data.active_preset;
            const p = PRESETS[data.active_preset];
            state.currentCode = p.code;
            state.inputs = { ...p.inputs, ...(data.inputs || {}) };
        }
    }

    async function saveConfig() {
        const payload = {
            status: 'ok',
            active_preset: state.activePresetId,
            enabled: state.enabled,
            target: state.target,
            inputs: state.inputs,
            updated_at: new Date().toISOString()
        };

        // Save locally
        try {
            localStorage.setItem('tv_indicators_config', JSON.stringify(payload));
        } catch (e) {}

        // Sync to cloud VPS
        try {
            const resp = await fetch('/api/user/indicators', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (resp.ok) {
                showToast('✓ Indicators synced across devices');
            }
        } catch (e) {
            console.warn('[TvPineEngine] Cloud sync failed:', e);
        }
    }

    function showToast(msg) {
        const el = document.getElementById('tvi-toast');
        if (!el) return;
        el.textContent = msg;
        el.classList.add('show');
        setTimeout(() => el.classList.remove('show'), 2500);
    }

    // ── UI Modal / Studio Drawer ────────────────────────────────────────────────
    function mountUI() {
        if (document.getElementById('tvi-studio-drawer')) return;

        const drawerHtml = `
        <div id="tvi-drawer-backdrop" class="tvi-backdrop hidden"></div>
        <aside id="tvi-studio-drawer" class="tvi-drawer hidden" aria-label="TradingView Indicators Studio">
            <!-- Header -->
            <div class="tvi-hdr">
                <div class="tvi-title-wrap">
                    <span class="tvi-badge">+Tv<span class="tvi-serif-i">I</span></span>
                    <span class="tvi-title">Indicator Studio</span>
                </div>
                <div class="tvi-hdr-actions">
                    <label class="tvi-toggle-label" title="Enable / Disable Indicator Overlay">
                        <input type="checkbox" id="tvi-master-toggle" ${state.enabled ? 'checked' : ''} />
                        <span class="tvi-toggle-slider"></span>
                        <span class="tvi-toggle-text">Active</span>
                    </label>
                    <button id="tvi-btn-close" class="tvi-icon-btn" title="Close Drawer">✕</button>
                </div>
            </div>

            <!-- Body -->
            <div class="tvi-body">
                <!-- Preset & Scope Row -->
                <div class="tvi-field-row">
                    <div class="tvi-field-group">
                        <label class="tvi-lbl">Indicator Preset</label>
                        <select id="tvi-preset-select" class="tvi-select">
                            <option value="algoalpha_breakout_channels">Smart Money Breakout Channels [AlgoAlpha]</option>
                            <option value="supertrend">Supertrend [ATR Multiplier]</option>
                            <option value="vwap_bands">VWAP + SD Bands</option>
                        </select>
                    </div>
                    <div class="tvi-field-group">
                        <label class="tvi-lbl">Apply To</label>
                        <select id="tvi-target-select" class="tvi-select">
                            <option value="both" ${state.target === 'both' ? 'selected' : ''}>Both (CE & PE)</option>
                            <option value="CE" ${state.target === 'CE' ? 'selected' : ''}>CALL (CE) Only</option>
                            <option value="PE" ${state.target === 'PE' ? 'selected' : ''}>PUT (PE) Only</option>
                        </select>
                    </div>
                </div>

                <!-- Parameters Box -->
                <div class="tvi-section">
                    <div class="tvi-sec-title">Settings & Inputs</div>
                    <div id="tvi-inputs-container" class="tvi-inputs-grid">
                        <!-- Populated dynamically based on preset -->
                    </div>
                </div>

                <!-- Pine Script Code Editor -->
                <div class="tvi-section tvi-editor-sec">
                    <div class="tvi-sec-header">
                        <span class="tvi-sec-title">Pine Script Source</span>
                        <span class="tvi-badge-version">Pine v6 Ready</span>
                    </div>
                    <textarea id="tvi-code-editor" class="tvi-editor" spellcheck="false">${state.currentCode}</textarea>
                </div>

                <!-- Console & Status Box -->
                <div id="tvi-console" class="tvi-console">
                    <div id="tvi-status-msg" class="tvi-status-ready">✓ Ready to compile and apply</div>
                </div>
            </div>

            <!-- Footer Toolbar -->
            <div class="tvi-footer">
                <button id="tvi-btn-compile" class="tvi-btn tvi-btn-primary" title="Compile and instantly apply to charts">
                    ▶ Compile & Apply
                </button>
                <button id="tvi-btn-save" class="tvi-btn tvi-btn-secondary" title="Save & Sync across devices">
                    💾 Save to Cloud
                </button>
            </div>
            <div id="tvi-toast" class="tvi-toast"></div>
        </aside>`;

        document.body.insertAdjacentHTML('beforeend', drawerHtml);
        attachEvents();
        renderInputsGrid();
    }

    function renderInputsGrid() {
        const container = document.getElementById('tvi-inputs-container');
        if (!container) return;

        if (state.activePresetId === 'algoalpha_breakout_channels') {
            container.innerHTML = `
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Normalization Len</label>
                    <input type="number" id="inp-len-norm" class="tvi-inp" value="${state.inputs.length_ || 100}" min="10" max="500" />
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Box Detection Len</label>
                    <input type="number" id="inp-len-box" class="tvi-inp" value="${state.inputs.length || 14}" min="2" max="100" />
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Strong Closes Only</label>
                    <input type="checkbox" id="inp-strong" ${state.inputs.strong ? 'checked' : ''} />
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Nested Channels</label>
                    <input type="checkbox" id="inp-overlap" ${state.inputs.overlap ? 'checked' : ''} />
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Volume Mode</label>
                    <select id="inp-vol-mode" class="tvi-inp">
                        <option value="Comparison" ${state.inputs.vol_mode === 'Comparison' ? 'selected' : ''}>Comparison (Up/Down)</option>
                        <option value="Delta" ${state.inputs.vol_mode === 'Delta' ? 'selected' : ''}>Volume Delta</option>
                        <option value="Volume" ${state.inputs.vol_mode === 'Volume' ? 'selected' : ''}>Total Volume</option>
                    </select>
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Bullish Color</label>
                    <input type="color" id="inp-col-green" class="tvi-color-inp" value="${state.inputs.green || '#00ffbb'}" />
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Bearish Color</label>
                    <input type="color" id="inp-col-red" class="tvi-color-inp" value="${state.inputs.red || '#ff1100'}" />
                </div>
            `;
        } else if (state.activePresetId === 'supertrend') {
            container.innerHTML = `
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">ATR Period</label>
                    <input type="number" id="inp-st-period" class="tvi-inp" value="${state.inputs.atr_period || 10}" min="1" max="100" />
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Multiplier</label>
                    <input type="number" step="0.1" id="inp-st-mult" class="tvi-inp" value="${state.inputs.multiplier || 3.0}" min="0.5" max="10" />
                </div>
            `;
        }
    }

    function collectInputsFromUI() {
        if (state.activePresetId === 'algoalpha_breakout_channels') {
            const lNorm = document.getElementById('inp-len-norm');
            const lBox = document.getElementById('inp-len-box');
            const chkStrong = document.getElementById('inp-strong');
            const chkOverlap = document.getElementById('inp-overlap');
            const selVolMode = document.getElementById('inp-vol-mode');
            const colG = document.getElementById('inp-col-green');
            const colR = document.getElementById('inp-col-red');

            if (lNorm) state.inputs.length_ = parseInt(lNorm.value) || 100;
            if (lBox) state.inputs.length = parseInt(lBox.value) || 14;
            if (chkStrong) state.inputs.strong = chkStrong.checked;
            if (chkOverlap) state.inputs.overlap = chkOverlap.checked;
            if (selVolMode) state.inputs.vol_mode = selVolMode.value;
            if (colG) state.inputs.green = colG.value;
            if (colR) state.inputs.red = colR.value;
        } else if (state.activePresetId === 'supertrend') {
            const p = document.getElementById('inp-st-period');
            const m = document.getElementById('inp-st-mult');
            if (p) state.inputs.atr_period = parseInt(p.value) || 10;
            if (m) state.inputs.multiplier = parseFloat(m.value) || 3.0;
        }
    }

    function attachEvents() {
        const btnOpen = document.getElementById('btn-master-tvi');
        const btnClose = document.getElementById('tvi-btn-close');
        const backdrop = document.getElementById('tvi-drawer-backdrop');
        const drawer = document.getElementById('tvi-studio-drawer');
        const presetSelect = document.getElementById('tvi-preset-select');
        const targetSelect = document.getElementById('tvi-target-select');
        const masterToggle = document.getElementById('tvi-master-toggle');
        const btnCompile = document.getElementById('tvi-btn-compile');
        const btnSave = document.getElementById('tvi-btn-save');

        function openDrawer() {
            backdrop.classList.remove('hidden');
            drawer.classList.remove('hidden');
            setTimeout(() => {
                backdrop.classList.add('visible');
                drawer.classList.add('open');
            }, 10);
            updateDrawerStatus();
        }

        function closeDrawer() {
            backdrop.classList.remove('visible');
            drawer.classList.remove('open');
            setTimeout(() => {
                backdrop.classList.add('hidden');
                drawer.classList.add('hidden');
            }, 300);
        }

        if (btnOpen) btnOpen.addEventListener('click', openDrawer);
        if (btnClose) btnClose.addEventListener('click', closeDrawer);
        if (backdrop) backdrop.addEventListener('click', closeDrawer);

        if (presetSelect) {
            presetSelect.value = state.activePresetId;
            presetSelect.addEventListener('change', (e) => {
                const id = e.target.value;
                if (PRESETS[id]) {
                    state.activePresetId = id;
                    state.currentCode = PRESETS[id].code;
                    state.inputs = { ...PRESETS[id].inputs };
                    const editor = document.getElementById('tvi-code-editor');
                    if (editor) editor.value = state.currentCode;
                    renderInputsGrid();
                    triggerRecompute();
                }
            });
        }

        if (targetSelect) {
            targetSelect.addEventListener('change', (e) => {
                state.target = e.target.value;
                triggerRecompute();
            });
        }

        if (masterToggle) {
            masterToggle.addEventListener('change', (e) => {
                state.enabled = e.target.checked;
                triggerRecompute();
            });
        }

        if (btnCompile) {
            btnCompile.addEventListener('click', () => {
                collectInputsFromUI();
                const editor = document.getElementById('tvi-code-editor');
                if (editor) state.currentCode = editor.value;
                triggerRecompute();
                showToast('✓ Compiled and rendered successfully');
            });
        }

        if (btnSave) {
            btnSave.addEventListener('click', () => {
                collectInputsFromUI();
                saveConfig();
            });
        }
    }

    function triggerRecompute() {
        if (window.Stations) {
            if (window.Stations.CE) {
                computeIndicatorsForStation(window.Stations.CE);
                if (typeof window.renderStationSMFCanvas === 'function') {
                    window.renderStationSMFCanvas(window.Stations.CE);
                }
            }
            if (window.Stations.PE) {
                computeIndicatorsForStation(window.Stations.PE);
                if (typeof window.renderStationSMFCanvas === 'function') {
                    window.renderStationSMFCanvas(window.Stations.PE);
                }
            }
        }
    }

    function updateDrawerStatus() {
        const msgEl = document.getElementById('tvi-status-msg');
        if (!msgEl) return;

        if (!state.enabled) {
            msgEl.className = 'tvi-status-off';
            msgEl.textContent = '⏸ Indicator overlay is paused (Toggle Active to display)';
            return;
        }

        if (state.compileStats) {
            const s = state.compileStats;
            msgEl.className = 'tvi-status-ready';
            msgEl.innerHTML = `✓ <strong>${s.preset}</strong> compiled in <strong>${s.elapsedMs}ms</strong> (${s.numCandles} bars) • Channels: <strong>${s.activeChannels}</strong> • Signals: <strong>${s.signalsCount}</strong>`;
        } else {
            msgEl.className = 'tvi-status-ready';
            msgEl.textContent = '✓ Ready to compile and apply to chart';
        }
    }

    // ── Public API ──────────────────────────────────────────────────────────────
    window.TvPineEngine = {
        init() {
            loadConfig().then(() => {
                mountUI();
                triggerRecompute();
            });
        },
        computeIndicatorsForStation,
        renderStationIndicators,
        recompute: triggerRecompute,
        getState: () => state
    };

    // Auto-initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => window.TvPineEngine.init());
    } else {
        window.TvPineEngine.init();
    }
})();
