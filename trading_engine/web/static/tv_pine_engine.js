/**
 * TradingView Pine Script Indicator Engine & Overlay Renderer
 * ─────────────────────────────────────────────────────────────
 * Enables client-side compilation, mathematical execution, and HTML5 canvas
 * rendering of TradingView Pine Script indicators (e.g. AlgoAlpha Breakout Channels)
 * directly over dual option station charts (CE / PE) with zero server overhead.
 */

(function () {
    'use strict';

    // ── Full Complete AlgoAlpha Script Verbatim ───────────────────────────────────
    const ALGOALPHA_FULL_SCRIPT = `// © AlgoAlpha
// This Source Code is subject to the Mozilla Public License 2.0
// https://mozilla.org/MPL/2.0/
//@version=6
indicator("Smart Money Breakout Channels [AlgoAlpha]", "AlgoAlpha - Breakout Channels",  overlay=true, max_boxes_count = 500)
import TradingView/ta/10

// ============================================================================
// INPUTS
// ============================================================================

overlap = input.bool(false, "Nested Channels", group = "Main Settings", tooltip="When enabled, allows multiple channels to overlap. When disabled, only one channel can exist at a time. Overlapping channels can show multiple breakout levels simultaneously.")
strong = input.bool(true, "Strong Closes Only", "When enabled, breakouts only trigger when more than 50% of the candle body is outside the channel. This reduces false signals from wicks. When disabled, any price movement outside the channel triggers a breakout.", group = "Main Settings")
length_ = input.int(100, title="Normalization Length", minval=1, group = "Main Settings", tooltip="The number of bars used to calculate the highest high and lowest low for price normalization. Higher values create more stable normalization but may be less responsive to recent price changes.")
length = input.int(14, "Box Detection Length", minval=1, group = "Main Settings", tooltip="The number of bars used to detect channel formation patterns. Lower values create more frequent channels but may be more sensitive to noise. Higher values create fewer but potentially more significant channels.")

shw_vol = input.bool(true, "Show Volume Analysis", "When enabled, displays volume analysis as candle-like bars within the channel. This helps identify volume patterns that may precede breakouts.", group = "Volume Analysis")
vol_mode = input.string("Comparison", "Volume Display Mode", options=["Volume", "Comparison", "Delta"], group="Volume Analysis", tooltip="Volume: Shows total volume as symmetrical bars. Comparison: Shows up volume above midline, down volume below. Delta: Shows net volume delta (positive above, negative below midline).")
tf = input.timeframe("1", "Volume Delta Timeframe Source", group = "Volume Analysis", tooltip="The timeframe used to calculate volume delta data. Lower timeframes provide more granular volume analysis but may be noisier.")
vol_scale = input.float(0.5, "Volume Scale", minval=0.1, maxval=2.0, step=0.1, group="Volume Analysis", tooltip="Adjusts the height of volume bars relative to channel size. Higher values make volume bars more prominent, lower values make them more subtle.")

text_size    = input.string("Tiny", "Volume Text Size", options=["Tiny","Small","Medium","Large"], group="Appearance", tooltip="Size of the volume text at the corner of the channels.")
green = input.color(#00ffbb, title = "Bullish Colour", group = "Appearance", tooltip = "Primary colour for bullish visual elements. Adjust for preferred palette – affects bars, fills, and labels when momentum is positive.")
red   = input.color(#ff1100, title = "Bearish Colour", group = "Appearance", tooltip = "Primary colour for bearish visual elements. Adjust for preferred palette – affects bars, fills, and labels when momentum is negative.")

// ============================================================================
// VARIABLES
// ============================================================================

var boxes = array.new_box()
var boxes_u = array.new_box()
var boxes_l = array.new_box()

var line[] gaugeLines = array.new<line>()
var label gaugeLabel = na
var line[] centerLines = array.new<line>()

// ============================================================================
// FUNCTIONS
// ============================================================================

textSize(text_size) =>
    switch text_size
        "Tiny"  => size.tiny
        "Small" => size.small
        "Medium"=> size.normal
        "Large" => size.large

f_can_create(float tNew, float bNew) =>
    ok = true
    if boxes.size() > 0
        for j = 0 to boxes.size()-1
            if (tNew > boxes.get(j).get_bottom()) and (bNew < boxes.get(j).get_top())
                ok := false
                break
    ok

getVolumeTransparency(float vol, float smoothed_vol) =>
    if vol_mode == "Volume"
        float vol_ratio = vol / smoothed_vol
        float transparency = math.max(20, math.min(80, 80 - (vol_ratio - 0.5) * 40))
        transparency
    else
        0

// ============================================================================
// CALCULATIONS
// ============================================================================

lowestLow = ta.lowest(low, length_)
highestHigh = ta.highest(high, length_)
normalizedPrice = (close - lowestLow) / (highestHigh - lowestLow)
vol = ta.stdev(normalizedPrice, 14)

upper = (ta.highestbars(vol, length + 1) + length)/length
lower = (ta.lowestbars(vol, length + 1) + length)/length

upbreak = 0.0
downbreak = 0.0

duration = math.max(nz(ta.barssince(ta.crossover(lower,upper))), 1)
h = ta.highest(duration)
l = ta.lowest(duration)

[uv, dv, vold] = ta.requestUpAndDownVolume(tf)

var hvold = vold
var lvold = vold

if ta.crossover(lower, upper)
    hvold := vold
    lvold := vold

if vold > hvold
    hvold := vold

if vold < lvold
    lvold := vold

smoothedvol = ta.sma(volume, 20)
vola = ta.atr(length)/2

bool newChannelFormed = false
bool bullishBreakout = false
bool bearishBreakout = false

if ta.crossover(upper, lower) and duration > 10
    if overlap or f_can_create(h, l)
        boxes.unshift(box.new(bar_index-duration, h, bar_index, l, bgcolor = color.new(chart.fg_color, 90), border_color = na))
        boxes_u.unshift(box.new(bar_index-duration, h, bar_index, h-vola, bgcolor = color.new(red, 70), border_color = na))
        boxes_l.unshift(box.new(bar_index-duration, l+vola, bar_index, l, bgcolor = color.new(green, 70), border_color = na))
        
        float centerY = (h + l) / 2
        centerLines.unshift(line.new(bar_index-duration, centerY, bar_index, centerY, color = color.new(chart.fg_color, 50), width = 1, style = line.style_dashed))
        newChannelFormed := true

if boxes.size() > 0
    for i = 0 to boxes.size()-1
        if ((strong ? math.avg(close, open) : close) > boxes.get(i).get_top())
            upbreak := boxes.get(i).get_bottom()
            boxes.remove(i)
            boxes_u.remove(i)
            boxes_l.remove(i)
            centerLines.remove(i)
            bullishBreakout := true
            
        else if ((strong ? math.avg(close, open) : close) < boxes.get(i).get_bottom())
            downbreak := boxes.get(i).get_top()
            boxes.remove(i)
            boxes_u.remove(i)
            boxes_l.remove(i)
            centerLines.remove(i)
            bearishBreakout := true
                  
        else
            boxes.get(i).set_right(bar_index)
            boxes_u.get(i).set_right(bar_index)
            boxes_l.get(i).set_right(bar_index)
            centerLines.get(i).set_x2(bar_index)
            
            // Add volume text to appropriate box based on price position
            float boxMidline = (boxes.get(i).get_top() + boxes.get(i).get_bottom()) / 2
            float currentPrice = strong ? math.avg(close, open) : close
            
            string volText = ""
            if vol_mode == "Volume"
                volText := str.tostring(math.round(volume / 1000, 1)) + "K"
            else if vol_mode == "Comparison"
                volText := str.tostring(math.round(uv / 1000, 1)) + "K/" + str.tostring(math.round(dv / 1000, 1)) + "K"
            else if vol_mode == "Delta"
                volText := str.tostring(math.round(vold / 1000, 1)) + "K"
            
            if currentPrice > boxMidline
                // Price is above midline, add text to lower (green) box
                boxes_l.get(i).set_text(volText)
                boxes_l.get(i).set_text_halign(text.align_right)
                boxes_l.get(i).set_text_color(color.new(chart.fg_color, 30))
                boxes_l.get(i).set_text_size(textSize(text_size))
                boxes_u.get(i).set_text("")
            else
                // Price is below midline, add text to upper (red) box
                boxes_u.get(i).set_text(volText)
                boxes_u.get(i).set_text_halign(text.align_right)
                boxes_u.get(i).set_text_color(color.new(chart.fg_color, 30))
                boxes_u.get(i).set_text_size(textSize(text_size))
                boxes_l.get(i).set_text("")

float currentMidline = na
float channelHeight = na

if boxes.size() > 0
    float topBound = boxes.get(0).get_top()
    float bottomBound = boxes.get(0).get_bottom()
    currentMidline := (topBound + bottomBound) / 2
    channelHeight := (topBound - bottomBound) * vol_scale

float vol_upper_open = na
float vol_upper_high = na  
float vol_upper_low = na
float vol_upper_close = na

float vol_lower_open = na
float vol_lower_high = na
float vol_lower_low = na  
float vol_lower_close = na

if not na(currentMidline) and not na(channelHeight) and shw_vol
    if vol_mode == "Volume"
        float vol_height = (volume / smoothedvol) * (channelHeight / 4)
        vol_upper_open := currentMidline
        vol_upper_close := currentMidline + vol_height
        vol_upper_high := currentMidline + vol_height
        vol_upper_low := currentMidline
        
        vol_lower_open := currentMidline
        vol_lower_close := currentMidline - vol_height
        vol_lower_high := currentMidline
        vol_lower_low := currentMidline - vol_height
        
    else if vol_mode == "Comparison"
        float uv_height = na(uv) ? 0 : (uv / smoothedvol) * (channelHeight / 4)
        float dv_height = na(dv) ? 0 : (dv / smoothedvol) * (channelHeight / 4)
        
        vol_upper_open := currentMidline
        vol_upper_close := currentMidline + uv_height  
        vol_upper_high := currentMidline + uv_height
        vol_upper_low := currentMidline
        
        vol_lower_open := currentMidline
        vol_lower_close := currentMidline + dv_height
        vol_lower_high := currentMidline  
        vol_lower_low := currentMidline + dv_height
        
    else if vol_mode == "Delta" 
        float delta_height = na(vold) ? 0 : math.abs(vold / smoothedvol) * (channelHeight / 4)
        
        if vold >= 0
            vol_upper_open := currentMidline
            vol_upper_close := currentMidline + delta_height
            vol_upper_high := currentMidline + delta_height  
            vol_upper_low := currentMidline
            
            vol_lower_open := currentMidline
            vol_lower_close := currentMidline
            vol_lower_high := currentMidline
            vol_lower_low := currentMidline
        else
            vol_upper_open := currentMidline
            vol_upper_close := currentMidline
            vol_upper_high := currentMidline
            vol_upper_low := currentMidline
            
            vol_lower_open := currentMidline
            vol_lower_close := currentMidline - delta_height  
            vol_lower_high := currentMidline
            vol_lower_low := currentMidline - delta_height

color upperColor = na
color lowerColor = na

if vol_mode == "Volume"
    float transparency = getVolumeTransparency(volume, smoothedvol)
    upperColor := color.new(chart.fg_color, transparency)
    lowerColor := color.new(chart.fg_color, transparency)
else
    upperColor := green
    lowerColor := red

// ============================================================================
// VISUALS
// ============================================================================

plotshape(upbreak != 0 ? upbreak : na, "Bullish Breakout Signal", shape.labelup, location.absolute, green, text = "▲", textcolor = chart.fg_color)
plotshape(downbreak != 0 ? downbreak : na, "Bearish Breakout Signal", shape.labeldown, location.absolute, red, text = "▼", textcolor = chart.fg_color)

plotcandle(vol_upper_open, vol_upper_high, vol_upper_low, vol_upper_close, color=upperColor, wickcolor=upperColor, bordercolor=upperColor)
plotcandle(vol_lower_open, vol_lower_high, vol_lower_low, vol_lower_close, color=lowerColor, wickcolor=lowerColor, bordercolor=lowerColor)

volumeAvailable = not na(volume)

if barstate.islast
    bool channelActive = boxes.size() > 0 and upbreak == 0 and downbreak == 0
    
    if array.size(gaugeLines) > 0
        for ln in gaugeLines
            ln.delete()
        gaugeLines.clear()
    
    if not na(gaugeLabel)
        gaugeLabel.delete()
        gaugeLabel := na
    
    if channelActive
        float topBound    = boxes.get(0).get_top()
        float bottomBound = boxes.get(0).get_bottom()
        
        if not na(topBound) and not na(bottomBound) and topBound != bottomBound
            int segments = 21
            float segLen = (topBound - bottomBound) / segments
            
            for i = 0 to segments - 1
                float y1 = topBound   - i * segLen
                float y2 = topBound   - (i + 1) * segLen
                color segCol = color.from_gradient(y1, bottomBound, topBound, red, green)
                line ln = line.new(x1 = bar_index + 2,
                                     y1 = y1,
                                     x2 = bar_index + 2,
                                     y2 = y2,
                                     color = segCol,
                                     width = 4)
                gaugeLines.unshift(ln)
            
            float delvol = -100*2*((vold-lvold)/(hvold-lvold)-0.5)
            delvol := math.max(math.min(delvol, 100), -100)
            float pointerPos = topBound - ((delvol + 100) / 200) * (topBound - bottomBound)
            gaugeLabel := label.new(x = bar_index + 3,
                                     y = pointerPos,
                                     text = "◀",
                                     color = na,
                                     textcolor = chart.fg_color,
                                     size = size.small,
                                     style = label.style_label_left)

if not volumeAvailable
    var gaugeWarnTable = table.new(position = position.top_right, columns = 1, rows = 1, bgcolor = red, border_width = 1, border_color = chart.fg_color, frame_color = chart.fg_color, frame_width = 1)
    table.cell(gaugeWarnTable, 0, 0, "Volume not available\\nGauge may not work as expected", text_color = chart.fg_color, text_halign = text.align_center, text_size = size.small)

// ============================================================================
// ALERTS
// ============================================================================

alertcondition(newChannelFormed, "New Channel Formation", "A new breakout channel has been formed")
alertcondition(bullishBreakout, "Bullish Breakout", "Price has broken out above the channel (bullish signal)")
alertcondition(bearishBreakout, "Bearish Breakout", "Price has broken out below the channel (bearish signal)")`;

    // ── Built-in Preset Registry ────────────────────────────────────────────────
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
                vol_mode: 'Comparison',
                vol_scale: 0.5,
                green: '#00ffbb',
                red: '#ff1100'
            },
            code: ALGOALPHA_FULL_SCRIPT
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
            code: `//@version=6
indicator("Supertrend", overlay=true)

// Inputs
atr_period = input.int(10, "ATR Period", minval=1)
multiplier = input.float(3.0, "Multiplier", step=0.1)
green = input.color(#00ffbb, "Bullish Color")
red = input.color(#ff1100, "Bearish Color")

// Calculations
atr = ta.atr(atr_period)
hl2 = (high + low) / 2
up = hl2 - multiplier * atr
dn = hl2 + multiplier * atr

var float trend = 1.0
var float st = na

if bar_index == 0
    st := dn
else
    if close > st
        st := up
        trend := 1.0
    else if close < st
        st := dn
        trend := -1.0

plot(st, "Supertrend", color = trend == 1.0 ? green : red, linewidth=2)
plotshape(ta.crossover(close, st), "Bullish Entry", shape.labelup, location.belowbar, green, text="▲ BUY")
plotshape(ta.crossunder(close, st), "Bearish Entry", shape.labeldown, location.abovebar, red, text="▼ SELL")`
        },
        ema_ribbon: {
            id: 'ema_ribbon',
            name: 'EMA Ribbon (9 / 21 / 50 / 200)',
            inputs: {
                ema1_len: 9,
                ema2_len: 21,
                ema3_len: 50,
                ema4_len: 200,
                col1: '#38bdf8',
                col2: '#00ffbb',
                col3: '#f59e0b',
                col4: '#ec4899'
            },
            code: `//@version=6
indicator("EMA Ribbon", overlay=true)

len1 = input.int(9, "EMA 1 Length")
len2 = input.int(21, "EMA 2 Length")
len3 = input.int(50, "EMA 3 Length")
len4 = input.int(200, "EMA 4 Length")

col1 = input.color(#38bdf8, "EMA 1 Color")
col2 = input.color(#00ffbb, "EMA 2 Color")
col3 = input.color(#f59e0b, "EMA 3 Color")
col4 = input.color(#ec4899, "EMA 4 Color")

e1 = ta.ema(close, len1)
e2 = ta.ema(close, len2)
e3 = ta.ema(close, len3)
e4 = ta.ema(close, len4)

plot(e1, "EMA 9", color=col1, linewidth=1)
plot(e2, "EMA 21", color=col2, linewidth=1)
plot(e3, "EMA 50", color=col3, linewidth=2)
plot(e4, "EMA 200", color=col4, linewidth=2)

bullishCross = ta.crossover(e1, e2)
bearishCross = ta.crossunder(e1, e2)

plotshape(bullishCross, "Bull Cross", shape.labelup, location.belowbar, col2, text="▲")
plotshape(bearishCross, "Bear Cross", shape.labeldown, location.abovebar, #ff1100, text="▼")`
        }
    };

    // ── State Container ─────────────────────────────────────────────────────────
    const state = {
        enabled: true,
        target: 'both', // 'both', 'CE', 'PE'
        activePresetId: 'algoalpha_breakout_channels',
        customIndicators: {}, // id -> { id, name, code, inputs }
        stationEnabled: {
            CE: { algoalpha_breakout_channels: true },
            PE: { algoalpha_breakout_channels: true }
        },
        currentCode: ALGOALPHA_FULL_SCRIPT,
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
                out[i] = maxIdx - i;
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

        ema(series, len) {
            const n = series.length;
            const out = new Array(n);
            if (n === 0) return out;
            const k = 2 / (len + 1);
            let prev = series[0];
            out[0] = prev;
            for (let i = 1; i < n; i++) {
                prev = series[i] * k + prev * (1 - k);
                out[i] = prev;
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
                const valA = typeof a === 'number' ? a : a[i];
                const prevA = typeof a === 'number' ? a : a[i - 1];
                const valB = typeof b === 'number' ? b : b[i];
                const prevB = typeof b === 'number' ? b : b[i - 1];
                if (valA > valB && prevA <= prevB) out[i] = true;
            }
            return out;
        },

        crossunder(a, b) {
            const out = new Array(a.length).fill(false);
            for (let i = 1; i < a.length; i++) {
                const valA = typeof a === 'number' ? a : a[i];
                const prevA = typeof a === 'number' ? a : a[i - 1];
                const valB = typeof b === 'number' ? b : b[i];
                const prevB = typeof b === 'number' ? b : b[i - 1];
                if (valA < valB && prevA >= prevB) out[i] = true;
            }
            return out;
        }
    };

    // ── Dynamic Pine Script Input Parser ────────────────────────────────────────
    function parsePineInputsFromScript(scriptCode) {
        const parsed = {};
        if (!scriptCode) return parsed;
        const lines = scriptCode.split('\n');

        for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('//') || trimmed.length === 0) continue;

            // Match input.type(def, title, ...) or input(def, title, ...)
            const m = trimmed.match(/^([a-zA-Z0-9_]+)\s*=\s*input(?:\.([a-z]+))?\s*\((.*)\)/);
            if (m) {
                const varName = m[1];
                const type = m[2] || 'float';
                const argsStr = m[3];
                const rawArgs = splitPineArgs(argsStr);
                const rawDef = rawArgs[0] ? rawArgs[0].trim() : '';
                const rawTitle = rawArgs[1] ? rawArgs[1].replace(/["']/g, '').trim() : varName;

                let val = rawDef;
                let inputType = 'text';

                if (type === 'int' || (!m[2] && /^-?\d+$/.test(rawDef))) {
                    val = parseInt(rawDef) || 0;
                    inputType = 'int';
                } else if (type === 'float' || (!m[2] && /^-?\d+\.\d+$/.test(rawDef))) {
                    val = parseFloat(rawDef) || 0.0;
                    inputType = 'float';
                } else if (type === 'bool' || rawDef === 'true' || rawDef === 'false') {
                    val = rawDef === 'true';
                    inputType = 'bool';
                } else if (type === 'color' || rawDef.startsWith('#')) {
                    val = rawDef.startsWith('#') ? rawDef.split(/[\s,)]/)[0] : '#00ffbb';
                    inputType = 'color';
                } else if (type === 'string' || rawDef.startsWith('"') || rawDef.startsWith("'")) {
                    val = rawDef.replace(/["']/g, '');
                    inputType = 'string';
                }

                parsed[varName] = {
                    name: varName,
                    title: rawTitle,
                    type: inputType,
                    val: val
                };
            }
        }
        return parsed;
    }

    function splitPineArgs(argsStr) {
        const args = [];
        let cur = '';
        let inQuotes = false;
        let depth = 0;
        for (let i = 0; i < argsStr.length; i++) {
            const ch = argsStr[i];
            if (ch === '"' || ch === "'") inQuotes = !inQuotes;
            if (!inQuotes) {
                if (ch === '(' || ch === '[') depth++;
                else if (ch === ')' || ch === ']') depth--;
                else if (ch === ',' && depth === 0) {
                    args.push(cur.trim());
                    cur = '';
                    continue;
                }
            }
            cur += ch;
        }
        if (cur.trim()) args.push(cur.trim());
        return args;
    }

    // ── Dedicated AlgoAlpha Breakout Channels Runner ─────────────────────────────
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

        const activeBoxes = [];
        const completedBoxes = [];
        const signals = [];
        const volCandles = [];

        let lastLowerCrossUpper = -999;

        for (let i = 1; i < n; i++) {
            if (lower[i] > upper[i] && lower[i - 1] <= upper[i - 1]) {
                lastLowerCrossUpper = i;
            }

            const duration = Math.max(1, lastLowerCrossUpper >= 0 ? i - lastLowerCrossUpper : i);

            let h = lows[i];
            let l = highs[i];
            const durStart = Math.max(0, i - duration + 1);
            for (let j = durStart; j <= i; j++) {
                if (highs[j] > h) h = highs[j];
                if (lows[j] < l) l = lows[j];
            }

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

            const currentEvalPrice = strong ? (closes[i] + opens[i]) / 2 : closes[i];

            for (let bIdx = activeBoxes.length - 1; bIdx >= 0; bIdx--) {
                const b = activeBoxes[bIdx];

                if (currentEvalPrice > b.top) {
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

    // ── Universal Pine Script Runner & Transpiler ────────────────────────────────
    function computeGenericPineScript(candles, scriptCode, inputs) {
        const n = candles.length;
        if (n < 5) return null;

        const highs = candles.map(c => c.high);
        const lows = candles.map(c => c.low);
        const closes = candles.map(c => c.close);
        const opens = candles.map(c => c.open);
        const volumes = candles.map(c => c.volume || 1);
        const times = candles.map(c => c.time);

        const plots = [];
        const signals = [];

        // Scan plot(...) calls
        const plotMatches = [...scriptCode.matchAll(/plot\s*\(\s*([a-zA-Z0-9_.]+)(?:,\s*["']([^"']*)["'])?(?:,\s*color\s*=\s*([^,\)]+))?(?:,\s*linewidth\s*=\s*(\d+))?/g)];
        
        // Scan plotshape(...) calls
        const shapeMatches = [...scriptCode.matchAll(/plotshape\s*\(\s*([^,\)]+)(?:,\s*["']([^"']*)["'])?(?:,\s*shape\.([a-z0-9_]+))?(?:,\s*location\.([a-z0-9_]+))?(?:,\s*([^,\)]+))?(?:,\s*text\s*=\s*["']([^"']*)["'])?/g)];

        // Compute EMAs / SMAs / ATR referenced
        const seriesVars = {};
        seriesVars['open'] = opens;
        seriesVars['high'] = highs;
        seriesVars['low'] = lows;
        seriesVars['close'] = closes;
        seriesVars['volume'] = volumes;

        const lines = scriptCode.split('\n');
        for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('//') || trimmed.length === 0) continue;

            // EMA assignment: e1 = ta.ema(close, len1)
            const emaM = trimmed.match(/^([a-zA-Z0-9_]+)\s*=\s*ta\.ema\s*\(\s*([a-zA-Z0-9_]+)\s*,\s*([a-zA-Z0-9_]+|\d+)\s*\)/);
            if (emaM) {
                const varName = emaM[1];
                const src = seriesVars[emaM[2]] || closes;
                const len = parseInt(inputs[emaM[3]] !== undefined ? inputs[emaM[3]] : emaM[3]) || 14;
                seriesVars[varName] = PineTA.ema(src, len);
            }

            // SMA assignment: s1 = ta.sma(close, len1)
            const smaM = trimmed.match(/^([a-zA-Z0-9_]+)\s*=\s*ta\.sma\s*\(\s*([a-zA-Z0-9_]+)\s*,\s*([a-zA-Z0-9_]+|\d+)\s*\)/);
            if (smaM) {
                const varName = smaM[1];
                const src = seriesVars[smaM[2]] || closes;
                const len = parseInt(inputs[smaM[3]] !== undefined ? inputs[smaM[3]] : smaM[3]) || 14;
                seriesVars[varName] = PineTA.sma(src, len);
            }

            // ATR assignment: a = ta.atr(len)
            const atrM = trimmed.match(/^([a-zA-Z0-9_]+)\s*=\s*ta\.atr\s*\(\s*([a-zA-Z0-9_]+|\d+)\s*\)/);
            if (atrM) {
                const varName = atrM[1];
                const len = parseInt(inputs[atrM[2]] !== undefined ? inputs[atrM[2]] : atrM[2]) || 14;
                seriesVars[varName] = PineTA.atr(candles, len);
            }

            // Crossover: bull = ta.crossover(a, b)
            const coM = trimmed.match(/^([a-zA-Z0-9_]+)\s*=\s*ta\.crossover\s*\(\s*([a-zA-Z0-9_]+)\s*,\s*([a-zA-Z0-9_]+)\s*\)/);
            if (coM) {
                const varName = coM[1];
                const s1 = seriesVars[coM[2]] || closes;
                const s2 = seriesVars[coM[3]] || closes;
                seriesVars[varName] = PineTA.crossover(s1, s2);
            }

            // Crossunder: bear = ta.crossunder(a, b)
            const cuM = trimmed.match(/^([a-zA-Z0-9_]+)\s*=\s*ta\.crossunder\s*\(\s*([a-zA-Z0-9_]+)\s*,\s*([a-zA-Z0-9_]+)\s*\)/);
            if (cuM) {
                const varName = cuM[1];
                const s1 = seriesVars[cuM[2]] || closes;
                const s2 = seriesVars[cuM[3]] || closes;
                seriesVars[varName] = PineTA.crossunder(s1, s2);
            }
        }

        // Build plot line series
        for (const pm of plotMatches) {
            const seriesName = pm[1];
            const title = pm[2] || seriesName;
            let col = pm[3] || '#38bdf8';
            if (inputs[col.trim()]) col = inputs[col.trim()];
            const lw = parseInt(pm[4]) || 2;

            if (seriesVars[seriesName]) {
                plots.push({
                    title: title,
                    color: col,
                    width: lw,
                    data: seriesVars[seriesName]
                });
            }
        }

        // Build plotshape markers
        for (const sm of shapeMatches) {
            const condName = sm[1].trim();
            const title = sm[2] || condName;
            const isUp = (sm[3] || '').includes('up') || (sm[4] || '').includes('below');
            let col = sm[5] ? sm[5].trim() : (isUp ? '#00ffbb' : '#ff1100');
            if (inputs[col]) col = inputs[col];
            const text = sm[6] || (isUp ? '▲' : '▼');

            const condArr = seriesVars[condName];
            if (condArr && Array.isArray(condArr)) {
                for (let i = 0; i < n; i++) {
                    if (condArr[i]) {
                        signals.push({
                            time: times[i],
                            price: isUp ? lows[i] : highs[i],
                            type: isUp ? 'bullish' : 'bearish',
                            text: text,
                            color: col
                        });
                    }
                }
            }
        }

        return {
            type: 'generic_pine',
            plots,
            signals,
            candles
        };
    }

    // ── Station Computation Dispatcher ──────────────────────────────────────────
    function computeIndicatorsForStation(station) {
        if (!station || !station.rawCandles || station.rawCandles.length < 5) return;
        const st = (station.type || '').toUpperCase();
        if (!state.stationEnabled[st]) {
            state.stationEnabled[st] = { algoalpha_breakout_channels: true };
        }
        const enabledMap = state.stationEnabled[st];

        const t0 = performance.now();
        const results = [];
        const allIndicators = { ...PRESETS, ...state.customIndicators };

        for (const [id, ind] of Object.entries(allIndicators)) {
            const isEnabled = enabledMap[id] === true || (enabledMap[id] === undefined && id === 'algoalpha_breakout_channels');
            if (!isEnabled) continue;

            let res = null;
            if (id === 'algoalpha_breakout_channels') {
                res = computeAlgoAlphaBreakoutChannels(station.rawCandles, state.inputs);
            } else {
                res = computeGenericPineScript(station.rawCandles, ind.code, ind.inputs || state.inputs);
            }
            if (res) results.push(res);
        }

        station.tvIndicatorResults = results;
        const elapsed = (performance.now() - t0).toFixed(1);

        if (results.length > 0 && (!state.compileStats || st === 'CE')) {
            state.compileStats = {
                elapsedMs: elapsed,
                numCandles: station.rawCandles.length,
                activeCount: results.length,
                preset: state.activePresetId
            };
            updateDrawerStatus();
        }
    }

    // ── HTML5 Canvas Overlay Renderer ───────────────────────────────────────────
    function renderStationIndicators(station, ctx, W, H) {
        if (!station || !station.chart || !station.candleSeries) return;
        const results = station.tvIndicatorResults;
        if (!results || results.length === 0) return;

        const timeScale = station.chart.timeScale();
        const candleSeries = station.candleSeries;

        ctx.save();
        for (const res of results) {
            if (res.type === 'algoalpha_breakout_channels') {
                renderAlgoAlphaOverlay(ctx, res, timeScale, candleSeries, W, H);
            } else if (res.type === 'generic_pine') {
                renderGenericPineOverlay(ctx, res, timeScale, candleSeries, W, H);
            }
        }
        ctx.restore();
    }

    function renderAlgoAlphaOverlay(ctx, res, timeScale, candleSeries, W, H) {
        const { activeBoxes, completedBoxes, signals, volCandles, colGreen, colRed } = res;
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

            // A. Main Channel Box Fill
            ctx.fillStyle = box.active ? 'rgba(148, 163, 184, 0.08)' : 'rgba(100, 116, 139, 0.04)';
            ctx.fillRect(drawX1, Math.min(yTop, yBottom), boxWidth, boxHeight);

            // B. Upper Zone (Red Tint)
            if (yTopZone != null) {
                const uH = Math.abs(yTopZone - yTop);
                ctx.fillStyle = box.active ? 'rgba(255, 17, 0, 0.22)' : 'rgba(255, 17, 0, 0.08)';
                ctx.fillRect(drawX1, Math.min(yTop, yTopZone), boxWidth, uH);
                ctx.strokeStyle = box.active ? 'rgba(255, 17, 0, 0.5)' : 'rgba(255, 17, 0, 0.2)';
                ctx.lineWidth = 1;
                ctx.strokeRect(drawX1, Math.min(yTop, yTopZone), boxWidth, uH);
            }

            // C. Lower Zone (Green Tint)
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

            // E. Volume Text
            if (box.active && box.volText) {
                ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                ctx.textAlign = 'right';
                ctx.textBaseline = 'middle';
                if (box.isPriceAboveMid) {
                    ctx.fillStyle = colGreen;
                    ctx.fillText(box.volText, drawX2 - 4, (yBottom + (yBottomZone || yBottom)) / 2);
                } else {
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

                const yUOpen = candleSeries.priceToCoordinate(vc.upperOpen);
                const yUClose = candleSeries.priceToCoordinate(vc.upperClose);
                if (yUOpen != null && yUClose != null) {
                    ctx.fillStyle = colGreen;
                    const barH = Math.max(1, Math.abs(yUClose - yUOpen));
                    ctx.fillRect(x - 1.5, Math.min(yUOpen, yUClose), 3, barH);
                }

                const yLOpen = candleSeries.priceToCoordinate(vc.lowerOpen);
                const yLClose = candleSeries.priceToCoordinate(vc.lowerClose);
                if (yLOpen != null && yLClose != null) {
                    ctx.fillStyle = colRed;
                    const barH = Math.max(1, Math.abs(yLClose - yLOpen));
                    ctx.fillRect(x - 1.5, Math.min(yLOpen, yLClose), 3, barH);
                }
            }
        }

        // 3. Breakout Signal Markers
        if (signals && signals.length > 0) {
            ctx.font = 'bold 12px sans-serif';
            ctx.textAlign = 'center';

            for (const sig of signals) {
                const x = timeScale.timeToCoordinate(sig.time);
                if (x == null || x < -20 || x > W + 20) continue;
                const y = candleSeries.priceToCoordinate(sig.price);
                if (y == null) continue;

                if (sig.type === 'bullish') {
                    ctx.fillStyle = colGreen;
                    ctx.shadowColor = colGreen;
                    ctx.shadowBlur = 8;
                    ctx.fillText('▲', x, y + 16);
                } else {
                    ctx.fillStyle = colRed;
                    ctx.shadowColor = colRed;
                    ctx.shadowBlur = 8;
                    ctx.fillText('▼', x, y - 6);
                }
                ctx.shadowBlur = 0;
            }
        }
    }

    function renderGenericPineOverlay(ctx, res, timeScale, candleSeries, W, H) {
        const { plots, signals, candles } = res;
        const n = candles.length;

        // 1. Draw plot lines
        if (plots && plots.length > 0) {
            for (const p of plots) {
                ctx.lineWidth = p.width || 2;
                ctx.strokeStyle = p.color || '#38bdf8';
                ctx.beginPath();
                let started = false;

                for (let i = 0; i < n; i++) {
                    const val = p.data[i];
                    if (val == null || isNaN(val)) continue;
                    const x = timeScale.timeToCoordinate(candles[i].time);
                    const y = candleSeries.priceToCoordinate(val);
                    if (x == null || y == null) continue;

                    if (!started) {
                        ctx.moveTo(x, y);
                        started = true;
                    } else {
                        ctx.lineTo(x, y);
                    }
                }
                ctx.stroke();
            }
        }

        // 2. Draw plotshape signals
        if (signals && signals.length > 0) {
            ctx.font = 'bold 12px sans-serif';
            ctx.textAlign = 'center';

            for (const sig of signals) {
                const x = timeScale.timeToCoordinate(sig.time);
                if (x == null || x < 0 || x > W) continue;
                const y = candleSeries.priceToCoordinate(sig.price);
                if (y == null) continue;

                ctx.fillStyle = sig.color;
                ctx.shadowColor = sig.color;
                ctx.shadowBlur = 8;
                ctx.fillText(sig.text, x, sig.type === 'bullish' ? y + 14 : y - 6);
                ctx.shadowBlur = 0;
            }
        }
    }

    // ── Persistence & Remote Cloud Sync ─────────────────────────────────────────
    async function loadConfig() {
        try {
            const local = localStorage.getItem('tv_indicators_config');
            if (local) {
                const parsed = JSON.parse(local);
                applyConfig(parsed);
            }
        } catch (e) {
            console.warn('[TvPineEngine] Error loading local config:', e);
        }

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
        if (data.custom_indicators) state.customIndicators = data.custom_indicators;
        if (data.station_enabled) state.stationEnabled = data.station_enabled;

        const presetId = data.active_preset || 'algoalpha_breakout_channels';
        state.activePresetId = presetId;

        if (PRESETS[presetId]) {
            state.currentCode = PRESETS[presetId].code;
            state.inputs = { ...PRESETS[presetId].inputs, ...(data.inputs || {}) };
        } else if (state.customIndicators[presetId]) {
            const ci = state.customIndicators[presetId];
            state.currentCode = ci.code;
            state.inputs = { ...ci.inputs, ...(data.inputs || {}) };
        }

        refreshStationPopovers();
    }

    async function saveConfig() {
        const payload = {
            status: 'ok',
            active_preset: state.activePresetId,
            enabled: state.enabled,
            target: state.target,
            inputs: state.inputs,
            user_code: state.currentCode,
            custom_indicators: state.customIndicators,
            station_enabled: state.stationEnabled,
            updated_at: new Date().toISOString()
        };

        try {
            localStorage.setItem('tv_indicators_config', JSON.stringify(payload));
        } catch (e) {}

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

    function refreshStationPopovers() {
        const stations = ['CE', 'PE'];
        const allIndicators = [];
        for (const [key, p] of Object.entries(PRESETS)) {
            allIndicators.push({ id: key, name: p.name });
        }
        for (const [key, ci] of Object.entries(state.customIndicators)) {
            allIndicators.push({ id: key, name: ci.name });
        }

        for (const st of stations) {
            const container = document.getElementById(`${st.toLowerCase()}-pine-indicators-list`);
            if (!container) continue;

            if (!state.stationEnabled[st]) {
                state.stationEnabled[st] = { algoalpha_breakout_channels: true };
            }

            let html = '';
            for (const ind of allIndicators) {
                const isChecked = state.stationEnabled[st][ind.id] === true || (state.stationEnabled[st][ind.id] === undefined && ind.id === 'algoalpha_breakout_channels');
                html += `
                    <label class="popover-item pine-item" title="${ind.name}">
                        <input type="checkbox" data-station="${st}" data-id="${ind.id}" ${isChecked ? 'checked' : ''} />
                        <span>${ind.name}</span>
                    </label>`;
            }
            container.innerHTML = html;
        }

        // Attach checkbox change event
        document.querySelectorAll('.pine-popover-list input[type="checkbox"]').forEach(chk => {
            chk.onchange = (e) => {
                const st = e.target.getAttribute('data-station');
                const id = e.target.getAttribute('data-id');
                if (!state.stationEnabled[st]) state.stationEnabled[st] = {};
                state.stationEnabled[st][id] = e.target.checked;

                if (window.Stations && window.Stations[st]) {
                    computeIndicatorsForStation(window.Stations[st]);
                    if (typeof window.renderStationSMFCanvas === 'function') {
                        window.renderStationSMFCanvas(window.Stations[st]);
                    }
                }
                saveConfig();
            };
        });
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
                <!-- Preset & Scope Row with "+ New" & "Delete" controls -->
                <div class="tvi-field-row">
                    <div class="tvi-field-group">
                        <div class="tvi-lbl-row">
                            <label class="tvi-lbl">Indicator / Script</label>
                            <div class="tvi-script-actions">
                                <button id="tvi-btn-new-script" class="tvi-mini-btn" title="Create New Pine Indicator">+ New</button>
                                <button id="tvi-btn-del-script" class="tvi-mini-btn danger" title="Delete Custom Script">🗑</button>
                            </div>
                        </div>
                        <select id="tvi-preset-select" class="tvi-select"></select>
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
                    <div id="tvi-inputs-container" class="tvi-inputs-grid"></div>
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
        refreshPresetDropdown();
        renderInputsGrid();
    }

    function refreshPresetDropdown() {
        const sel = document.getElementById('tvi-preset-select');
        if (!sel) return;

        let html = '<optgroup label="Built-in Presets">';
        for (const [key, p] of Object.entries(PRESETS)) {
            html += `<option value="${key}" ${state.activePresetId === key ? 'selected' : ''}>${p.name}</option>`;
        }
        html += '</optgroup>';

        if (Object.keys(state.customIndicators).length > 0) {
            html += '<optgroup label="My Custom Indicators">';
            for (const [key, ci] of Object.entries(state.customIndicators)) {
                html += `<option value="${key}" ${state.activePresetId === key ? 'selected' : ''}>${ci.name}</option>`;
            }
            html += '</optgroup>';
        }

        sel.innerHTML = html;

        // Toggle delete button visibility based on whether it is custom
        const btnDel = document.getElementById('tvi-btn-del-script');
        if (btnDel) {
            btnDel.style.display = state.customIndicators[state.activePresetId] ? 'inline-block' : 'none';
        }
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
            return;
        }

        // Generic dynamic inputs parsing from the script code
        const parsedInputs = parsePineInputsFromScript(state.currentCode);
        const keys = Object.keys(parsedInputs);

        if (keys.length === 0) {
            container.innerHTML = '<div style="color:#64748b; font-size:11px; grid-column:span 2;">No input.* parameters found in script.</div>';
            return;
        }

        let html = '';
        for (const k of keys) {
            const inp = parsedInputs[k];
            const curVal = state.inputs[k] !== undefined ? state.inputs[k] : inp.val;

            if (inp.type === 'bool') {
                html += `
                    <div class="tvi-inp-row">
                        <label class="tvi-inp-lbl">${inp.title}</label>
                        <input type="checkbox" data-var="${inp.name}" class="tvi-dyn-inp" ${curVal ? 'checked' : ''} />
                    </div>`;
            } else if (inp.type === 'color') {
                html += `
                    <div class="tvi-inp-row">
                        <label class="tvi-inp-lbl">${inp.title}</label>
                        <input type="color" data-var="${inp.name}" class="tvi-dyn-inp tvi-color-inp" value="${curVal}" />
                    </div>`;
            } else if (inp.type === 'int' || inp.type === 'float') {
                const step = inp.type === 'float' ? '0.1' : '1';
                html += `
                    <div class="tvi-inp-row">
                        <label class="tvi-inp-lbl">${inp.title}</label>
                        <input type="number" step="${step}" data-var="${inp.name}" class="tvi-dyn-inp tvi-inp" value="${curVal}" />
                    </div>`;
            } else {
                html += `
                    <div class="tvi-inp-row">
                        <label class="tvi-inp-lbl">${inp.title}</label>
                        <input type="text" data-var="${inp.name}" class="tvi-dyn-inp tvi-inp" value="${curVal}" />
                    </div>`;
            }
        }
        container.innerHTML = html;
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
            return;
        }

        const dynInputs = document.querySelectorAll('.tvi-dyn-inp');
        dynInputs.forEach(el => {
            const varName = el.getAttribute('data-var');
            if (!varName) return;
            if (el.type === 'checkbox') {
                state.inputs[varName] = el.checked;
            } else if (el.type === 'number') {
                state.inputs[varName] = parseFloat(el.value);
            } else {
                state.inputs[varName] = el.value;
            }
        });
    }

    function createNewIndicator() {
        const name = prompt('Enter a name for the new Pine Script indicator:', 'My Custom Indicator');
        if (!name || !name.trim()) return;

        const id = 'custom_' + Date.now();
        const starterCode = `//@version=6
indicator("${name.trim()}", overlay=true)

// Inputs
fast_len = input.int(9, "Fast EMA Length")
slow_len = input.int(21, "Slow EMA Length")
green = input.color(#00ffbb, "Bullish Color")
red = input.color(#ff1100, "Bearish Color")

// Calculations
ema_fast = ta.ema(close, fast_len)
ema_slow = ta.ema(close, slow_len)

// Plots
plot(ema_fast, "Fast EMA", color=green, linewidth=2)
plot(ema_slow, "Slow EMA", color=red, linewidth=2)

// Breakout / Signal Triggers
buySig = ta.crossover(ema_fast, ema_slow)
sellSig = ta.crossunder(ema_fast, ema_slow)

plotshape(buySig, "BUY", shape.labelup, location.belowbar, green, text="BUY")
plotshape(sellSig, "SELL", shape.labeldown, location.abovebar, red, text="SELL")`;

        state.customIndicators[id] = {
            id: id,
            name: name.trim(),
            code: starterCode,
            inputs: { fast_len: 9, slow_len: 21, green: '#00ffbb', red: '#ff1100' }
        };

        state.activePresetId = id;
        state.currentCode = starterCode;
        state.inputs = { ...state.customIndicators[id].inputs };

        const editor = document.getElementById('tvi-code-editor');
        if (editor) editor.value = starterCode;

        if (!state.stationEnabled.CE) state.stationEnabled.CE = {};
        if (!state.stationEnabled.PE) state.stationEnabled.PE = {};
        state.stationEnabled.CE[id] = true;
        state.stationEnabled.PE[id] = true;

        refreshPresetDropdown();
        refreshStationPopovers();
        renderInputsGrid();
        triggerRecompute();
        saveConfig();
        showToast(`✓ Created new indicator: ${name}`);
    }

    function deleteCurrentIndicator() {
        if (!state.customIndicators[state.activePresetId]) {
            alert('Built-in presets cannot be deleted.');
            return;
        }
        if (confirm(`Are you sure you want to delete "${state.customIndicators[state.activePresetId].name}"?`)) {
            const delId = state.activePresetId;
            const delName = state.customIndicators[delId].name;
            delete state.customIndicators[delId];
            if (state.stationEnabled.CE) delete state.stationEnabled.CE[delId];
            if (state.stationEnabled.PE) delete state.stationEnabled.PE[delId];

            state.activePresetId = 'algoalpha_breakout_channels';
            state.currentCode = PRESETS.algoalpha_breakout_channels.code;
            state.inputs = { ...PRESETS.algoalpha_breakout_channels.inputs };

            const editor = document.getElementById('tvi-code-editor');
            if (editor) editor.value = state.currentCode;

            refreshPresetDropdown();
            refreshStationPopovers();
            renderInputsGrid();
            triggerRecompute();
            saveConfig();
            showToast(`✓ Deleted indicator: ${delName}`);
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
        const btnNew = document.getElementById('tvi-btn-new-script');
        const btnDel = document.getElementById('tvi-btn-del-script');

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

        if (btnNew) btnNew.addEventListener('click', createNewIndicator);
        if (btnDel) btnDel.addEventListener('click', deleteCurrentIndicator);

        if (presetSelect) {
            presetSelect.addEventListener('change', (e) => {
                const id = e.target.value;
                state.activePresetId = id;

                if (PRESETS[id]) {
                    state.currentCode = PRESETS[id].code;
                    state.inputs = { ...PRESETS[id].inputs };
                } else if (state.customIndicators[id]) {
                    state.currentCode = state.customIndicators[id].code;
                    state.inputs = { ...state.customIndicators[id].inputs };
                }

                const editor = document.getElementById('tvi-code-editor');
                if (editor) editor.value = state.currentCode;

                refreshPresetDropdown();
                renderInputsGrid();
                triggerRecompute();
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
                const editor = document.getElementById('tvi-code-editor');
                if (editor) {
                    state.currentCode = editor.value;
                    if (state.customIndicators[state.activePresetId]) {
                        state.customIndicators[state.activePresetId].code = editor.value;
                    }
                }
                collectInputsFromUI();
                renderInputsGrid();
                triggerRecompute();
                showToast('✓ Compiled and rendered successfully');
            });
        }

        if (btnSave) {
            btnSave.addEventListener('click', () => {
                const editor = document.getElementById('tvi-code-editor');
                if (editor) {
                    state.currentCode = editor.value;
                    if (state.customIndicators[state.activePresetId]) {
                        state.customIndicators[state.activePresetId].code = editor.value;
                    }
                }
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
            const detailStr = s.plotsCount > 0 ? `Lines: <strong>${s.plotsCount}</strong> • Signals: <strong>${s.signalsCount}</strong>` : `Channels: <strong>${s.activeChannels}</strong> • Signals: <strong>${s.signalsCount}</strong>`;
            msgEl.innerHTML = `✓ <strong>${s.preset}</strong> compiled in <strong>${s.elapsedMs}ms</strong> (${s.numCandles} bars) • ${detailStr}`;
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
                refreshStationPopovers();
                triggerRecompute();
            });
        },
        computeIndicatorsForStation,
        renderStationIndicators,
        refreshStationPopovers,
        recompute: triggerRecompute,
        getState: () => state,
        createNewIndicator
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => window.TvPineEngine.init());
    } else {
        window.TvPineEngine.init();
    }
})();
