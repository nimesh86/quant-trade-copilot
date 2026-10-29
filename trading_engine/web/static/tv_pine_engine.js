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

    const BSS_PRO_V3_FULL_SCRIPT = `//@version=5
indicator("BSS Pro v3 - Buyer/Seller Strength, Targets, SL & Exit", shorttitle="BSS Pro v3", overlay=true, max_labels_count=500, max_lines_count=500, max_boxes_count=500)

// =====================================================================
//  INPUTS
// =====================================================================
g0 = "0. Mode & Signal Source"
tradeMode = input.string("Option buyer (long premium only)", "Trade mode", options=["Option buyer (long premium only)", "Normal (BUY & SELL on this chart)"], group=g0, tooltip="Option buyer: only BUY signals on this chart (a falling premium is just 'no trade'). Normal: BUY and SELL both, for index / futures / stock charts.")
useSrc    = input.bool(false, "Take signals from another symbol", group=g0, tooltip="Example: sit on an option premium chart but generate signals from Nifty futures. Entries, exits and buyer/seller % come from the source symbol; stop loss, targets and prices stay on THIS chart.")
srcSymIn  = input.symbol("NSE:NIFTY1!", "Signal source symbol", group=g0)
optType   = input.string("Auto", "This chart's option type (only used with another source)", options=["Auto", "CALL", "PUT"], group=g0, tooltip="CALL: BUY when the source is bullish. PUT: BUY when the source is bearish. Auto reads CALL / PUT from the symbol description.")

g1 = "1. Trend & Signal"
emaFastLen = input.int(21, "Fast EMA", minval=2, group=g1)
emaSlowLen = input.int(50, "Slow EMA", minval=5, group=g1)
rsiLen     = input.int(14, "RSI length", minval=2, group=g1)
stLen      = input.int(10, "Supertrend ATR length", minval=1, group=g1)
stMult     = input.float(3.0, "Supertrend multiplier", minval=0.5, step=0.1, group=g1)

g2 = "2. Buyer / Seller Pressure"
pressLen = input.int(14, "Pressure lookback (bars)", minval=3, group=g2)
domThr   = input.float(58.0, "Min dominant side % to give signal", minval=50.0, maxval=90.0, step=1.0, group=g2)
useLtf   = input.bool(true, "Use intrabar volume for the buyer/seller split", group=g2, tooltip="Splits each candle's volume using the smaller candles inside it (more accurate). Needs the chart timeframe to be above the intrabar timeframe below, e.g. a 5m chart with 1m. If you use a 1m chart, turn this off AND set the intrabar timeframe to a value below your chart.")
ltfTf    = input.string("1", "Intrabar timeframe", options=["1", "3", "5"], group=g2)
minRank  = input.float(40.0, "Min relative strength rank (0-100)", minval=0.0, maxval=100.0, step=5.0, group=g2, tooltip="How lopsided the buyer/seller split is compared with the last 100 bars. 0 = off. Filters out signals where the % is only barely above the gate.")

g3 = "3. Entry Quality"
useCandle = input.bool(true, "Signal candle must close in trade direction", group=g3)
maxExtAtr = input.float(1.5, "Max distance from fast EMA (x ATR)", minval=0.3, step=0.1, group=g3)
rsiHi     = input.float(70.0, "BUY blocked if RSI above (trend entries)", minval=55.0, maxval=95.0, group=g3)
rsiLo     = input.float(30.0, "SELL blocked if RSI below (trend entries)", minval=5.0, maxval=45.0, group=g3)
reqPb     = input.bool(true, "Trend entries need a recent pullback to the fast EMA", group=g3)
pbBars    = input.int(8, "Pullback look-back (bars)", minval=2, group=g3)
pbAtr     = input.float(0.6, "Pullback touch zone (x ATR)", minval=0.1, step=0.1, group=g3)
useBrk    = input.bool(true, "Also allow BREAKOUT entries (catch early thrusts)", group=g3, tooltip="Strong candle closing above the previous N-bar high (or below the low) with buyer/seller dominance. Bypasses the pullback / RSI / distance filters so fast moves are not missed.")
brkLen    = input.int(20, "Breakout look-back (bars)", minval=5, group=g3)
brkBody   = input.float(0.5, "Breakout candle body (x ATR)", minval=0.1, step=0.1, group=g3)
minRR     = input.float(0.8, "Min reward:risk to T1", minval=0.0, step=0.1, group=g3)

g4 = "4. Stop Loss & Trailing"
atrLen    = input.int(14, "ATR length", minval=1, group=g4)
swingLen  = input.int(10, "Swing lookback for SL", minval=2, group=g4)
minSlAtr  = input.float(0.6, "Min SL distance (x ATR)", minval=0.2, step=0.1, group=g4)
maxSlAtr  = input.float(2.5, "Max SL distance (x ATR)", minval=0.5, step=0.1, group=g4)
maxSlPct  = input.float(12.0, "Hard cap on SL distance (% of entry, 0 = off)", minval=0.0, step=0.5, group=g4, tooltip="Stops an option premium SL from being 18-25% away. Applied after the ATR rules.")
trailOn   = input.bool(true, "Trail the stop after T1", group=g4, tooltip="After T1: stop moves to entry. After T2: stop moves to T1. It also trails the best price by the distance below.")
trailR    = input.float(1.0, "Trailing distance (x initial risk)", minval=0.3, step=0.1, group=g4)

g5 = "5. Targets"
tgtMode  = input.string("R-multiple", "Target mode", options=["R-multiple", "Fibonacci extension"], group=g5, tooltip="R-multiple: targets are multiples of the initial risk. Fibonacci extension: ratios of the recent swing range, capped at 'Max target' R.")
r1       = input.float(1.0, "T1 (R)", minval=0.2, step=0.1, group=g5)
r2       = input.float(2.0, "T2 (R)", minval=0.2, step=0.1, group=g5)
r3       = input.float(3.0, "Max (R)", minval=0.2, step=0.1, group=g5)
fibLen   = input.int(50, "Fib mode: swing range lookback", minval=10, group=g5)
f1       = input.float(0.382, "Fib mode: T1 ratio", step=0.001, group=g5)
f2       = input.float(0.618, "Fib mode: T2 ratio", step=0.001, group=g5)
f3       = input.float(1.0,   "Fib mode: Max ratio", step=0.001, group=g5)
maxTgtR  = input.float(4.0, "Fib mode: cap targets at (R)", minval=1.0, step=0.5, group=g5)
maxBars  = input.int(60, "Time stop: close trade after N bars", minval=5, group=g5)
minSamples = input.int(20, "Samples needed before showing a chance %", minval=5, group=g5, tooltip="Below this many finished trades the script shows raw counts (e.g. 'hits 3/8') instead of a percentage.")

g6 = "6. Look & Feel"
markerOff  = input.float(0.7, "Marker distance from candle (x ATR)", minval=0.1, step=0.1, group=g6)
showRibbon = input.bool(true, "EMA ribbon (of this chart)", group=g6)
showZones  = input.bool(true, "Risk / reward zones", group=g6)
zoneToMax  = input.bool(false, "Draw reward zone up to Max (otherwise up to T2)", group=g6)
keepOldLbl = input.bool(false, "Keep level labels on completed trades", group=g6)
showTable  = input.bool(true, "Info panel", group=g6)
tblPosIn   = input.string("Top left", "Panel position", options=["Top left", "Top right", "Bottom left", "Bottom right"], group=g6)
colBuy     = input.color(#089981, "BUY marker", group=g6)
colSell    = input.color(#F23645, "SELL marker", group=g6)
colSlBuy   = input.color(#FF9800, "Stop loss - BUY trade", group=g6)
colSlSell  = input.color(#AB47BC, "Stop loss - SELL trade", group=g6)
colTgtBuy  = input.color(#00C853, "Targets - BUY trade", group=g6)
colTgtSell = input.color(#29B6F6, "Targets - SELL trade", group=g6)
colExit    = input.color(#FFD54F, "EXIT marker", group=g6)

// =====================================================================
//  PLOTS & ALERTS
// =====================================================================
// Executed via high-precision client Pine Engine
`;

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
                zone_opacity: 55,
                green: '#00ffbb',
                red: '#ff1100',
                vol_up_color: '#38bdf8',
                vol_down_color: '#f59e0b'
            },
            code: ALGOALPHA_FULL_SCRIPT
        },
        bss_pro_v3: {
            id: 'bss_pro_v3',
            name: 'BSS Pro v3 [Buyer/Seller Strength, Targets, SL & Exit]',
            inputs: {
                tradeMode: 'Option buyer (long premium only)',
                useSrc: false,
                srcSymIn: 'NSE:NIFTY1!',
                optType: 'Auto',
                emaFastLen: 21,
                emaSlowLen: 50,
                rsiLen: 14,
                stLen: 10,
                stMult: 3.0,
                pressLen: 14,
                domThr: 58.0,
                useLtf: false,
                ltfTf: '1',
                minRank: 40.0,
                useCandle: true,
                maxExtAtr: 1.5,
                rsiHi: 70.0,
                rsiLo: 30.0,
                reqPb: true,
                pbBars: 8,
                pbAtr: 0.6,
                useBrk: true,
                brkLen: 20,
                brkBody: 0.5,
                minRR: 0.8,
                atrLen: 14,
                swingLen: 10,
                minSlAtr: 0.6,
                maxSlAtr: 2.5,
                maxSlPct: 12.0,
                trailOn: true,
                trailR: 1.0,
                tgtMode: 'R-multiple',
                r1: 1.0,
                r2: 2.0,
                r3: 3.0,
                maxBars: 60,
                minSamples: 20,
                markerOff: 0.7,
                showRibbon: true,
                showZones: true,
                showTable: true,
                tblPosIn: 'Top left',
                colBuy: '#089981',
                colSell: '#F23645',
                colSlBuy: '#FF9800',
                colSlSell: '#AB47BC',
                colTgtBuy: '#00C853',
                colTgtSell: '#29B6F6',
                colExit: '#FFD54F'
            },
            code: BSS_PRO_V3_FULL_SCRIPT
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

    // ── Color Utilities ────────────────────────────────────────────────────────
    function hexToRgba(hex, alpha) {
        if (!hex) return `rgba(0, 255, 187, ${alpha})`;
        if (hex.startsWith('rgba') || hex.startsWith('rgb')) return hex;
        let c = hex.replace('#', '');
        if (c.length === 3) c = c.split('').map(x => x + x).join('');
        const num = parseInt(c, 16);
        if (isNaN(num)) return `rgba(0, 255, 187, ${alpha})`;
        const r = (num >> 16) & 255;
        const g = (num >> 8) & 255;
        const b = num & 255;
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

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

        rma(series, len) {
            const n = series.length;
            const out = new Array(n);
            if (n === 0) return out;
            const alpha = 1 / len;
            let sum = 0;
            for (let i = 0; i < Math.min(len, n); i++) {
                sum += series[i];
            }
            let prev = sum / Math.min(len, n);
            out[0] = series[0];
            for (let i = 1; i < n; i++) {
                if (i < len) {
                    let s = 0;
                    for (let j = 0; j <= i; j++) s += series[j];
                    prev = s / (i + 1);
                } else {
                    prev = alpha * series[i] + (1 - alpha) * prev;
                }
                out[i] = prev;
            }
            return out;
        },

        tr(candles) {
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
            return tr;
        },

        atr(candles, len) {
            const trArr = PineTA.tr(candles);
            return PineTA.rma(trArr, len);
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
        },

        rsi(series, len) {
            const n = series.length;
            const out = new Array(n).fill(50);
            if (n < 2) return out;
            const u = new Array(n).fill(0);
            const d = new Array(n).fill(0);
            for (let i = 1; i < n; i++) {
                const diff = series[i] - series[i - 1];
                u[i] = diff > 0 ? diff : 0;
                d[i] = diff < 0 ? -diff : 0;
            }
            const rmaU = PineTA.rma(u, len);
            const rmaD = PineTA.rma(d, len);
            for (let i = 0; i < n; i++) {
                if (!rmaD[i] || rmaD[i] === 0) {
                    out[i] = 100;
                } else {
                    const rs = rmaU[i] / rmaD[i];
                    out[i] = 100 - (100 / (1 + rs));
                }
            }
            return out;
        },

        percentrank(series, len) {
            const n = series.length;
            const out = new Array(n).fill(50);
            for (let i = 0; i < n; i++) {
                const start = Math.max(0, i - len);
                const countBars = i - start;
                if (countBars <= 0) {
                    out[i] = 50;
                    continue;
                }
                let countLessEqual = 0;
                const cur = series[i];
                for (let j = start; j < i; j++) {
                    if (series[j] <= cur) countLessEqual++;
                }
                out[i] = (countLessEqual / countBars) * 100;
            }
            return out;
        },

        supertrend(candles, multiplier, period) {
            const n = candles.length;
            const tr = PineTA.tr(candles);
            const atr = PineTA.rma(tr, period);
            const src = candles.map(c => (c.high + c.low) / 2);
            const closes = candles.map(c => c.close);
            const up = new Array(n);
            const dn = new Array(n);
            const trend = new Array(n);

            let prevUp = 0;
            let prevDn = 0;
            let prevTrend = 1;

            for (let i = 0; i < n; i++) {
                const curAtr = atr[i] || 0;
                const basicUp = src[i] - (multiplier * curAtr);
                const basicDn = src[i] + (multiplier * curAtr);

                if (i === 0) {
                    up[i] = basicUp;
                    dn[i] = basicDn;
                    trend[i] = 1;
                    prevUp = basicUp;
                    prevDn = basicDn;
                    prevTrend = 1;
                    continue;
                }

                const prevClose = closes[i - 1];
                up[i] = prevClose > prevUp ? Math.max(basicUp, prevUp) : basicUp;
                dn[i] = prevClose < prevDn ? Math.min(basicDn, prevDn) : basicDn;

                let curTrend = prevTrend;
                if (prevTrend === -1 && closes[i] > prevDn) {
                    curTrend = 1;
                } else if (prevTrend === 1 && closes[i] < prevUp) {
                    curTrend = -1;
                }
                trend[i] = curTrend;
                prevUp = up[i];
                prevDn = dn[i];
                prevTrend = curTrend;
            }

            return { up, dn, trend };
        }
    };

    // ── Dynamic Pine Script Input Parser ────────────────────────────────────────
    function stripQuotes(str) {
        if (!str || typeof str !== 'string') return '';
        return str.replace(/^["']|["']$/g, '').trim();
    }

    function formatVarNameToTitle(name) {
        if (!name) return '';
        return name
            .replace(/_/g, ' ')
            .replace(/([a-z])([A-Z])/g, '$1 $2')
            .replace(/\b\w/g, c => c.toUpperCase());
    }

    function splitPineArgs(argsStr) {
        const args = [];
        let cur = '';
        let inQuotes = false;
        let quoteChar = '';
        let depth = 0;
        for (let i = 0; i < argsStr.length; i++) {
            const ch = argsStr[i];
            if (!inQuotes && (ch === '"' || ch === "'")) {
                inQuotes = true;
                quoteChar = ch;
            } else if (inQuotes && ch === quoteChar && argsStr[i - 1] !== '\\') {
                inQuotes = false;
            } else if (!inQuotes) {
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

    function parseSinglePineInput(varName, funcType, argsStr) {
        const rawArgs = splitPineArgs(argsStr);
        let title = '';
        let defval = undefined;
        let type = funcType || '';
        let step = null;
        let minval = null;
        let maxval = null;
        let options = null;

        let positionalIdx = 0;
        for (const raw of rawArgs) {
            const trimmed = raw.trim();
            const kwMatch = trimmed.match(/^([a-zA-Z0-9_]+)\s*=\s*([\s\S]*)$/);
            if (kwMatch) {
                const key = kwMatch[1].toLowerCase();
                const val = kwMatch[2].trim();
                if (key === 'title' || key === 'name') {
                    title = stripQuotes(val);
                } else if (key === 'defval' || key === 'def') {
                    defval = val;
                } else if (key === 'type') {
                    type = val.replace(/^input\./i, '').toLowerCase();
                } else if (key === 'step') {
                    step = parseFloat(val);
                } else if (key === 'minval') {
                    minval = parseFloat(val);
                } else if (key === 'maxval') {
                    maxval = parseFloat(val);
                } else if (key === 'options') {
                    try {
                        options = JSON.parse(val.replace(/'/g, '"'));
                    } catch (e) {
                        options = val.replace(/[\[\]]/g, '').split(',').map(s => stripQuotes(s.trim()));
                    }
                }
            } else {
                if (positionalIdx === 0 && defval === undefined) {
                    defval = trimmed;
                } else if (positionalIdx === 1 && !title) {
                    title = stripQuotes(trimmed);
                } else if (positionalIdx === 2 && !type) {
                    type = trimmed.replace(/^input\./i, '').toLowerCase();
                }
                positionalIdx++;
            }
        }

        if (!title) {
            title = formatVarNameToTitle(varName);
        }

        let inputType = 'text';
        let cleanVal = defval;

        if (type.includes('bool')) {
            inputType = 'bool';
            cleanVal = defval === 'true' || defval === true;
        } else if (type.includes('int')) {
            inputType = 'int';
            cleanVal = parseInt(defval) || 0;
        } else if (type.includes('float') || type.includes('price')) {
            inputType = 'float';
            cleanVal = parseFloat(defval) || 0.0;
        } else if (type.includes('source')) {
            inputType = 'source';
            cleanVal = (defval || 'hl2').toLowerCase();
        } else if (type.includes('color')) {
            inputType = 'color';
            cleanVal = (defval && defval.startsWith('#')) ? defval.split(/[\s,)]/)[0] : '#00ffbb';
        } else if (type.includes('string') || type.includes('resolution') || type.includes('timeframe')) {
            inputType = (options && options.length) ? 'options' : 'string';
            cleanVal = stripQuotes(defval || '');
        } else {
            if (defval === 'true' || defval === 'false' || typeof defval === 'boolean') {
                inputType = 'bool';
                cleanVal = defval === 'true' || defval === true;
            } else if (typeof defval === 'string' && ['hl2', 'close', 'open', 'high', 'low', 'ohlc4', 'hlc3'].includes(defval.toLowerCase())) {
                inputType = 'source';
                cleanVal = defval.toLowerCase();
            } else if (typeof defval === 'string' && /^-?\d+$/.test(defval)) {
                inputType = 'int';
                cleanVal = parseInt(defval);
            } else if (typeof defval === 'string' && /^-?\d*\.\d+$/.test(defval)) {
                inputType = 'float';
                cleanVal = parseFloat(defval);
            } else if (typeof defval === 'string' && defval.startsWith('#')) {
                inputType = 'color';
                cleanVal = defval.split(/[\s,)]/)[0];
            } else if (options && options.length > 0) {
                inputType = 'options';
                cleanVal = stripQuotes(defval || options[0]);
            } else {
                inputType = 'text';
                cleanVal = stripQuotes(defval || '');
            }
        }

        return {
            name: varName,
            title: title,
            type: inputType,
            val: cleanVal,
            step: step,
            minval: minval,
            maxval: maxval,
            options: options
        };
    }

    function parsePineInputsFromScript(scriptCode) {
        const parsed = {};
        if (!scriptCode) return parsed;

        const inputKeywordRegex = /([a-zA-Z0-9_]+)\s*[:=]+\s*input(?:\.([a-zA-Z0-9_]+))?\s*\(/g;
        let match;

        while ((match = inputKeywordRegex.exec(scriptCode)) !== null) {
            const varName = match[1];
            const funcType = match[2] || '';
            const startIndex = inputKeywordRegex.lastIndex;

            let depth = 1;
            let inQuotes = false;
            let quoteChar = '';
            let endIndex = -1;

            for (let i = startIndex; i < scriptCode.length; i++) {
                const ch = scriptCode[i];
                if (!inQuotes && (ch === '"' || ch === "'")) {
                    inQuotes = true;
                    quoteChar = ch;
                } else if (inQuotes && ch === quoteChar && scriptCode[i - 1] !== '\\') {
                    inQuotes = false;
                } else if (!inQuotes) {
                    if (ch === '(') depth++;
                    else if (ch === ')') {
                        depth--;
                        if (depth === 0) {
                            endIndex = i;
                            break;
                        }
                    }
                }
            }

            if (endIndex !== -1) {
                const argsStr = scriptCode.substring(startIndex, endIndex);
                parsed[varName] = parseSinglePineInput(varName, funcType, argsStr);
                inputKeywordRegex.lastIndex = endIndex + 1;
            }
        }

        return parsed;
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

        }

        // Generate volume analysis bars for ALL candles across every channel (active + completed)
        // This guarantees NO candles within any channel are ever missing volume bars!
        if (shw_vol) {
            const allBoxesForVol = [...completedBoxes, ...activeBoxes];
            const processedTimes = new Set();

            for (const b of allBoxesForVol) {
                const mid = b.centerY;
                const chHeight = (b.top - b.bottom) * vol_scale;

                for (let cIdx = b.startIdx; cIdx <= b.endIdx && cIdx < n; cIdx++) {
                    const cTime = candles[cIdx].time;
                    if (processedTimes.has(cTime)) continue;
                    processedTimes.add(cTime);

                    const smVol = Math.max(1, smoothedvol[cIdx] || 1);

                    if (vol_mode === 'Comparison') {
                        const uvH = (uv[cIdx] / smVol) * (chHeight / 4);
                        const dvH = (dv[cIdx] / smVol) * (chHeight / 4);
                        volCandles.push({
                            time: cTime,
                            upperOpen: mid,
                            upperClose: mid + uvH,
                            lowerOpen: mid,
                            lowerClose: mid - dvH
                        });
                    } else if (vol_mode === 'Delta') {
                        const dH = Math.abs(vold[cIdx] / smVol) * (chHeight / 4);
                        const isPos = vold[cIdx] >= 0;
                        volCandles.push({
                            time: cTime,
                            upperOpen: mid,
                            upperClose: isPos ? mid + dH : mid,
                            lowerOpen: mid,
                            lowerClose: !isPos ? mid - dH : mid
                        });
                    } else {
                        const vH = ((volumes[cIdx] || 0) / smVol) * (chHeight / 4);
                        volCandles.push({
                            time: cTime,
                            upperOpen: mid,
                            upperClose: mid + vH,
                            lowerOpen: mid,
                            lowerClose: mid - vH
                        });
                    }
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
            colRed,
            zoneOpacity: inputs.zone_opacity || 55,
            volUpColor: inputs.vol_up_color || '#38bdf8',
            volDownColor: inputs.vol_down_color || '#f59e0b',
            shw_vol: shw_vol
        };
    }

    // ── Dedicated BSS Pro v3 Indicator Runner ──────────────────────────────────
    function computeBSSProV3(candles, rawInputs, stationType) {
        const n = candles.length;
        if (n < 5) return null;

        const inputs = rawInputs || {};
        const tradeMode = inputs.tradeMode || 'Option buyer (long premium only)';
        const buyerMode = tradeMode.includes('Option buyer');
        const emaFastLen = Math.max(2, parseInt(inputs.emaFastLen) || 21);
        const emaSlowLen = Math.max(5, parseInt(inputs.emaSlowLen) || 50);
        const rsiLen = Math.max(2, parseInt(inputs.rsiLen) || 14);
        const stLen = Math.max(1, parseInt(inputs.stLen) || 10);
        const stMult = Math.max(0.5, parseFloat(inputs.stMult) || 3.0);
        const pressLen = Math.max(3, parseInt(inputs.pressLen) || 14);
        const domThr = Math.max(50, Math.min(95, parseFloat(inputs.domThr) || 58.0));
        const minRank = Math.max(0, Math.min(100, parseFloat(inputs.minRank) || 40.0));
        const useCandle = inputs.useCandle !== false;
        const maxExtAtr = parseFloat(inputs.maxExtAtr) || 1.5;
        const rsiHi = parseFloat(inputs.rsiHi) || 70.0;
        const rsiLo = parseFloat(inputs.rsiLo) || 30.0;
        const reqPb = inputs.reqPb !== false;
        const pbBars = Math.max(2, parseInt(inputs.pbBars) || 8);
        const pbAtr = parseFloat(inputs.pbAtr) || 0.6;
        const useBrk = inputs.useBrk !== false;
        const brkLen = Math.max(5, parseInt(inputs.brkLen) || 20);
        const brkBody = parseFloat(inputs.brkBody) || 0.5;
        const minRR = parseFloat(inputs.minRR) || 0.8;
        const atrLen = Math.max(1, parseInt(inputs.atrLen) || 14);
        const swingLen = Math.max(2, parseInt(inputs.swingLen) || 10);
        const minSlAtr = parseFloat(inputs.minSlAtr) || 0.6;
        const maxSlAtr = parseFloat(inputs.maxSlAtr) || 2.5;
        const maxSlPct = parseFloat(inputs.maxSlPct) || 12.0;
        const trailOn = inputs.trailOn !== false;
        const trailR = parseFloat(inputs.trailR) || 1.0;
        const r1 = parseFloat(inputs.r1) || 1.0;
        const r2 = parseFloat(inputs.r2) || 2.0;
        const r3 = parseFloat(inputs.r3) || 3.0;
        const maxBars = Math.max(5, parseInt(inputs.maxBars) || 60);
        const showRibbon = inputs.showRibbon !== false;
        const showTable = inputs.showTable !== false;
        const tblPosIn = inputs.tblPosIn || 'Top left';

        const colBuy = inputs.colBuy || '#089981';
        const colSell = inputs.colSell || '#F23645';
        const colSlBuy = inputs.colSlBuy || '#FF9800';
        const colSlSell = inputs.colSlSell || '#AB47BC';
        const colTgtBuy = inputs.colTgtBuy || '#00C853';
        const colTgtSell = inputs.colTgtSell || '#29B6F6';
        const colExit = inputs.colExit || '#FFD54F';

        const highs = candles.map(c => c.high);
        const lows = candles.map(c => c.low);
        const closes = candles.map(c => c.close);
        const opens = candles.map(c => c.open);
        const volumes = candles.map(c => c.volume || 1);
        const times = candles.map(c => c.time);

        // Core Technical Series
        const atrArr = PineTA.atr(candles, atrLen);
        const emaF = PineTA.ema(closes, emaFastLen);
        const emaS = PineTA.ema(closes, emaSlowLen);
        const rsiArr = PineTA.rsi(closes, rsiLen);
        const stRes = PineTA.supertrend(candles, stMult, stLen);
        const swLows = PineTA.lowest(lows, swingLen);
        const swHighs = PineTA.highest(highs, swingLen);

        // Buyer / Seller Volume Distribution
        const buyVol = new Array(n);
        const sellVol = new Array(n);
        for (let i = 0; i < n; i++) {
            const rng = highs[i] - lows[i];
            const vol = volumes[i] > 0 ? volumes[i] : 1.0;
            const shapeBuy = rng > 0 ? (vol * (closes[i] - lows[i]) / rng) : (vol * 0.5);
            buyVol[i] = shapeBuy;
            sellVol[i] = vol - shapeBuy;
        }

        const buyPctArr = new Array(n);
        const sellPctArr = new Array(n);
        const domAbsArr = new Array(n);

        let curBuySum = 0;
        let curSellSum = 0;
        for (let i = 0; i < n; i++) {
            curBuySum += buyVol[i];
            curSellSum += sellVol[i];
            if (i >= pressLen) {
                curBuySum -= buyVol[i - pressLen];
                curSellSum -= sellVol[i - pressLen];
            }
            const totSum = curBuySum + curSellSum;
            const bPct = totSum > 0 ? (curBuySum / totSum) * 100 : 50.0;
            buyPctArr[i] = bPct;
            sellPctArr[i] = 100.0 - bPct;
            domAbsArr[i] = Math.abs(bPct - 50.0);
        }

        const rankArr = PineTA.percentrank(domAbsArr, 100);

        // Trade State Machine
        let tDir = 0; // 0: flat, 1: long, -1: short
        let entry = 0;
        let slP = 0;
        let t1P = 0;
        let t2P = 0;
        let tMP = 0;
        let riskPts = 0;
        let runExt = 0;
        let h1 = false;
        let h2 = false;
        let hM = false;
        let trailActive = false;
        let barsIn = 0;
        let lastDir = 0;
        let lastEvent = 'No signal yet';

        let lTot = 0, l1 = 0, l2 = 0, lM = 0;
        let sTot = 0, s1 = 0, s2 = 0, sM = 0;

        const signals = [];
        const trailSeries = new Array(n).fill(null);

        for (let i = 1; i < n; i++) {
            const a = atrArr[i] || 1.0;
            const eFast = emaF[i];
            const eSlow = emaS[i];
            const r = rsiArr[i];
            const isUpTrend = stRes.trend[i] === 1;

            const bullC = closes[i] > opens[i] && closes[i] > closes[i - 1];
            const bearC = closes[i] < opens[i] && closes[i] < closes[i - 1];

            // Pullback criteria
            let lowestLowDist = 999999;
            let highestHighDist = -999999;
            const pStart = Math.max(0, i - pbBars + 1);
            for (let j = pStart; j <= i; j++) {
                const lowDist = lows[j] - emaF[j];
                const highDist = highs[j] - emaF[j];
                if (lowDist < lowestLowDist) lowestLowDist = lowDist;
                if (highDist > highestHighDist) highestHighDist = highDist;
            }
            const pbUp = lowestLowDist <= pbAtr * a;
            const pbDn = highestHighDist >= -pbAtr * a;

            // Trend criteria
            const trUp = isUpTrend && eFast > eSlow && closes[i] > eFast && r > 50 && r < rsiHi;
            const trDn = (!isUpTrend) && eFast < eSlow && closes[i] < eFast && r < 50 && r > rsiLo;

            // Quality criteria
            const quUp = (!useCandle || bullC) && (closes[i] - eFast) <= maxExtAtr * a && (!reqPb || pbUp);
            const quDn = (!useCandle || bearC) && (eFast - closes[i]) <= maxExtAtr * a && (!reqPb || pbDn);

            // Breakout criteria
            const brkStart = Math.max(0, i - 1 - brkLen + 1);
            let hiP = -999999;
            let loP = 999999;
            for (let j = brkStart; j < i; j++) {
                if (highs[j] > hiP) hiP = highs[j];
                if (lows[j] < loP) loP = lows[j];
            }
            const brUp = useBrk && closes[i] > hiP && bullC && (closes[i] - opens[i]) >= brkBody * a && closes[i] > eFast;
            const brDn = useBrk && closes[i] < loP && bearC && (opens[i] - closes[i]) >= brkBody * a && closes[i] < eFast;

            const rank = rankArr[i];
            const rankOK = minRank <= 0 || (rank != null && rank >= minRank);
            const dirOkUp = buyPctArr[i] >= domThr && rankOK;
            const dirOkDn = sellPctArr[i] >= domThr && rankOK;

            const upSig = (trUp && quUp && dirOkUp) || (brUp && dirOkUp);
            const dnSig = (trDn && quDn && dirOkDn) || (brDn && dirOkDn);

            // Option buyer mode mapping
            const isPut = String(stationType).toUpperCase() === 'PE';
            const mapPut = buyerMode && isPut;
            const sigLong = mapPut ? dnSig : upSig;
            const sigShort = (!buyerMode) && dnSig;
            const isBreakout = mapPut ? (brDn && dirOkDn) : (brUp && dirOkUp);

            // Stop Loss & Targets
            const swLow = swLows[i];
            const swHigh = swHighs[i];
            const slL0 = Math.min(Math.max(swLow - 0.1 * a, closes[i] - maxSlAtr * a), closes[i] - minSlAtr * a);
            const slS0 = Math.max(Math.min(swHigh + 0.1 * a, closes[i] + maxSlAtr * a), closes[i] + minSlAtr * a);
            const slL = maxSlPct > 0 ? Math.max(slL0, closes[i] * (1.0 - maxSlPct / 100)) : slL0;
            const slS = maxSlPct > 0 ? Math.min(slS0, closes[i] * (1.0 + maxSlPct / 100)) : slS0;
            const riskL = Math.max(closes[i] - slL, 0.05);
            const riskS = Math.max(slS - closes[i], 0.05);

            const t1L = closes[i] + riskL * r1;
            const t2L = closes[i] + riskL * r2;
            const tML = closes[i] + riskL * r3;
            const t1S = Math.max(closes[i] - riskS * r1, 0.05);
            const t2S = Math.max(closes[i] - riskS * r2, 0.05);
            const tMS = Math.max(closes[i] - riskS * r3, 0.05);

            const rrL = (t1L - closes[i]) / riskL;
            const rrS = (closes[i] - t1S) / riskS;

            const longOK = sigLong && rrL >= minRR;
            const shortOK = sigShort && rrS >= minRR;

            // Manage Open Position
            let closedNow = false;
            if (tDir !== 0) {
                barsIn += 1;
                runExt = tDir === 1 ? Math.max(runExt, highs[i]) : Math.min(runExt, lows[i]);

                const slHit = tDir === 1 ? lows[i] <= slP : highs[i] >= slP;
                const hit1 = tDir === 1 ? highs[i] >= t1P : lows[i] <= t1P;
                const hit2 = tDir === 1 ? highs[i] >= t2P : lows[i] <= t2P;
                const hitM = tDir === 1 ? highs[i] >= tMP : lows[i] <= tMP;

                if (slHit) {
                    closedNow = true;
                    lastEvent = trailActive ? 'Trailing stop hit' : 'Stop loss hit';
                    signals.push({
                        time: times[i],
                        price: slP,
                        type: 'exit_sl',
                        text: trailActive ? 'TRAIL STOP' : 'SL HIT',
                        color: tDir === 1 ? colSlBuy : colSlSell
                    });
                } else {
                    if (hit1 && !h1) {
                        h1 = true;
                        lastEvent = 'T1 hit';
                        signals.push({
                            time: times[i],
                            price: t1P,
                            type: 'target_hit',
                            text: '✔ T1',
                            color: colTgtBuy
                        });
                    }
                    if (hit2 && !h2) {
                        h2 = true;
                        lastEvent = 'T2 hit';
                        signals.push({
                            time: times[i],
                            price: t2P,
                            type: 'target_hit',
                            text: '✔ T2',
                            color: colTgtBuy
                        });
                    }
                    if (hitM && !hM) {
                        hM = true;
                        closedNow = true;
                        lastEvent = 'Max target hit';
                        signals.push({
                            time: times[i],
                            price: tMP,
                            type: 'target_hit',
                            text: 'MAX TARGET',
                            color: colTgtBuy
                        });
                    } else if (barsIn >= maxBars) {
                        closedNow = true;
                        lastEvent = 'Time stop';
                        signals.push({
                            time: times[i],
                            price: closes[i],
                            type: 'exit_time',
                            text: 'TIME EXIT',
                            color: colExit
                        });
                    }

                    // Trailing Stop Ratchet
                    if (!closedNow && trailOn && h1) {
                        let cand = slP;
                        if (tDir === 1) {
                            cand = Math.max(slP, entry);
                            if (h2) cand = Math.max(cand, t1P);
                            cand = Math.max(cand, runExt - trailR * riskPts);
                            cand = Math.min(cand, closes[i]);
                            cand = Math.max(cand, slP);
                        } else {
                            cand = Math.min(slP, entry);
                            if (h2) cand = Math.min(cand, t1P);
                            cand = Math.min(cand, runExt + trailR * riskPts);
                            cand = Math.max(cand, closes[i]);
                            cand = Math.min(cand, slP);
                        }
                        if (cand !== slP) {
                            slP = cand;
                            trailActive = true;
                        }
                    }
                }

                if (trailActive) trailSeries[i] = slP;

                if (closedNow) {
                    if (tDir === 1) {
                        lTot++;
                        if (h1) l1++;
                        if (h2) l2++;
                        if (hM) lM++;
                    } else {
                        sTot++;
                        if (h1) s1++;
                        if (h2) s2++;
                        if (hM) sM++;
                    }
                    tDir = 0;
                }
            }

            // Open Trade when Flat
            if (tDir === 0 && !closedNow && (longOK || shortOK)) {
                tDir = longOK ? 1 : -1;
                entry = closes[i];
                slP = tDir === 1 ? slL : slS;
                t1P = tDir === 1 ? t1L : t1S;
                t2P = tDir === 1 ? t2L : t2S;
                tMP = tDir === 1 ? tML : tMS;
                riskPts = tDir === 1 ? riskL : riskS;
                runExt = entry;
                h1 = false;
                h2 = false;
                hM = false;
                trailActive = false;
                barsIn = 0;
                lastDir = tDir;

                const eType = isBreakout ? 'BREAKOUT' : 'TREND';
                lastEvent = (tDir === 1 ? 'BUY (' : 'SELL (') + eType + ')';

                signals.push({
                    time: times[i],
                    price: entry,
                    type: tDir === 1 ? 'bullish_entry' : 'bearish_entry',
                    text: (tDir === 1 ? '▲ BUY' : '▼ SELL') + ' · ' + eType,
                    color: tDir === 1 ? colBuy : colSell,
                    entry: entry,
                    sl: slP,
                    t1: t1P,
                    t2: t2P
                });
            }
        }

        // Real-Time HUD Metrics
        const lastIdx = n - 1;
        const curBuyPct = buyPctArr[lastIdx] || 50.0;
        const curSellPct = sellPctArr[lastIdx] || 50.0;
        const curRank = rankArr[lastIdx] != null ? Math.round(rankArr[lastIdx]) : 50;
        const curGrade = curRank >= 75 ? 'Strong' : curRank >= 40 ? 'Moderate' : 'Weak';
        const curClose = closes[lastIdx];
        const pnlNow = tDir !== 0 ? (tDir * (curClose - entry) / entry * 100.0) : 0;

        const hudData = {
            buyPct: curBuyPct,
            sellPct: curSellPct,
            rank: curRank,
            grade: curGrade,
            status: tDir === 1 ? '▲ LONG active' : tDir === -1 ? '▼ SHORT active' : '● Flat',
            entry: tDir !== 0 ? entry : (lastDir !== 0 ? entry : null),
            stop: tDir !== 0 ? slP : (lastDir !== 0 ? slP : null),
            t1: tDir !== 0 ? t1P : (lastDir !== 0 ? t1P : null),
            t2: tDir !== 0 ? t2P : (lastDir !== 0 ? t2P : null),
            max: tDir !== 0 ? tMP : (lastDir !== 0 ? tMP : null),
            pnlNow: tDir !== 0 ? pnlNow : null,
            totalTrades: lastDir === 1 ? lTot : sTot,
            t1Hits: lastDir === 1 ? l1 : s1,
            lastEvent: lastEvent,
            tblPosIn: tblPosIn,
            showTable: showTable
        };

        return {
            type: 'bss_pro_v3',
            emaF: emaF,
            emaS: emaS,
            showRibbon: showRibbon,
            colBuy: colBuy,
            colSell: colSell,
            colSlBuy: colSlBuy,
            colSlSell: colSlSell,
            colTgtBuy: colTgtBuy,
            colTgtSell: colTgtSell,
            trailSeries: trailSeries,
            signals: signals,
            hudData: hudData,
            candles: candles
        };
    }

    // ── Universal Pine Script Runner & Transpiler ────────────────────────────────
    function computeGenericPineScript(candles, scriptCode, inputs) {
        const n = candles.length;
        if (n < 5) return null;

        // Route BSS Pro v3 indicator script
        const isBSSPro = /BSS\s*Pro/i.test(scriptCode) || /Buyer\/Seller Strength/i.test(scriptCode);
        if (isBSSPro) {
            return computeBSSProV3(candles, inputs);
        }

        const highs = candles.map(c => c.high);
        const lows = candles.map(c => c.low);
        const closes = candles.map(c => c.close);
        const opens = candles.map(c => c.open);
        const volumes = candles.map(c => c.volume || 1);
        const times = candles.map(c => c.time);

        // Check for Supertrend indicator script (Pine v4 or v5/v6)
        const isSupertrend = (!isBSSPro) && (/supertrend/i.test(scriptCode) || 
            (/\bMultiplier\b/i.test(scriptCode) && /\b(?:up1|dn1|trend)\b/i.test(scriptCode)));

        if (isSupertrend) {
            const periods = parseInt(inputs.Periods || inputs.atr_period || inputs.period || 10) || 10;
            const multiplier = parseFloat(inputs.Multiplier || inputs.multiplier || 3.0) || 3.0;
            const srcType = String(inputs.src || 'hl2').toLowerCase();
            const changeATR = inputs.changeATR !== undefined ? Boolean(inputs.changeATR) : true;
            const showsignals = inputs.showsignals !== undefined ? Boolean(inputs.showsignals) : true;

            const src = new Array(n);
            for (let i = 0; i < n; i++) {
                if (srcType === 'close') src[i] = closes[i];
                else if (srcType === 'open') src[i] = opens[i];
                else if (srcType === 'high') src[i] = highs[i];
                else if (srcType === 'low') src[i] = lows[i];
                else if (srcType === 'hlc3') src[i] = (highs[i] + lows[i] + closes[i]) / 3;
                else if (srcType === 'ohlc4') src[i] = (opens[i] + highs[i] + lows[i] + closes[i]) / 4;
                else src[i] = (highs[i] + lows[i]) / 2; // default hl2
            }

            const tr = PineTA.tr(candles);
            const atr = changeATR ? PineTA.rma(tr, periods) : PineTA.sma(tr, periods);

            const up = new Array(n);
            const dn = new Array(n);
            const trend = new Array(n);

            let prevUp = 0;
            let prevDn = 0;
            let prevTrend = 1;

            for (let i = 0; i < n; i++) {
                const curAtr = atr[i] || 0;
                const basicUp = src[i] - (multiplier * curAtr);
                const basicDn = src[i] + (multiplier * curAtr);

                if (i === 0) {
                    up[i] = basicUp;
                    dn[i] = basicDn;
                    trend[i] = 1;
                    prevUp = basicUp;
                    prevDn = basicDn;
                    prevTrend = 1;
                    continue;
                }

                const prevClose = closes[i - 1];
                up[i] = prevClose > prevUp ? Math.max(basicUp, prevUp) : basicUp;
                dn[i] = prevClose < prevDn ? Math.min(basicDn, prevDn) : basicDn;

                let curTrend = prevTrend;
                if (prevTrend === -1 && closes[i] > prevDn) {
                    curTrend = 1;
                } else if (prevTrend === 1 && closes[i] < prevUp) {
                    curTrend = -1;
                }
                trend[i] = curTrend;
                prevUp = up[i];
                prevDn = dn[i];
                prevTrend = curTrend;
            }

            const upPlotData = new Array(n);
            const dnPlotData = new Array(n);
            const signals = [];

            for (let i = 0; i < n; i++) {
                upPlotData[i] = trend[i] === 1 ? up[i] : null;
                dnPlotData[i] = trend[i] === -1 ? dn[i] : null;

                if (i > 0 && showsignals) {
                    if (trend[i] === 1 && trend[i - 1] === -1) {
                        signals.push({
                            time: times[i],
                            price: up[i],
                            type: 'bullish',
                            text: 'BUY',
                            color: '#00ffbb'
                        });
                    } else if (trend[i] === -1 && trend[i - 1] === 1) {
                        signals.push({
                            time: times[i],
                            price: dn[i],
                            type: 'bearish',
                            text: 'SELL',
                            color: '#ff1100'
                        });
                    }
                }
            }

            return {
                type: 'generic_pine',
                plots: [
                    { title: 'Up Trend', color: '#00ffbb', width: 2, data: upPlotData },
                    { title: 'Down Trend', color: '#ff1100', width: 2, data: dnPlotData }
                ],
                signals: signals,
                candles: candles
            };
        }

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
            } else if (id === 'bss_pro_v3' || /BSS\s*Pro/i.test(ind.name || '') || /BSS\s*Pro/i.test(ind.code || '')) {
                res = computeBSSProV3(station.rawCandles, ind.inputs || state.inputs, st);
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
            } else if (res.type === 'bss_pro_v3') {
                renderBSSProV3Overlay(ctx, res, timeScale, candleSeries, W, H);
            } else if (res.type === 'generic_pine') {
                renderGenericPineOverlay(ctx, res, timeScale, candleSeries, W, H);
            }
        }
        ctx.restore();
    }

    function renderAlgoAlphaOverlay(ctx, res, timeScale, candleSeries, W, H) {
        const { activeBoxes, completedBoxes, signals, volCandles, colGreen, colRed } = res;
        const allBoxes = [...completedBoxes.slice(-6), ...activeBoxes];
        const opacityFrac = Math.max(0.1, Math.min(1.0, (parseFloat(res.zoneOpacity) || 55) / 100));
        const activeAlpha = opacityFrac;
        const completedAlpha = opacityFrac * 0.45;
        const borderAlpha = Math.min(1.0, opacityFrac + 0.35);

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

            // B. Upper Zone (Red Tint - Resistance)
            if (yTopZone != null) {
                const uH = Math.abs(yTopZone - yTop);
                ctx.fillStyle = hexToRgba(colRed, box.active ? activeAlpha : completedAlpha);
                ctx.fillRect(drawX1, Math.min(yTop, yTopZone), boxWidth, uH);
                ctx.strokeStyle = hexToRgba(colRed, box.active ? borderAlpha : completedAlpha);
                ctx.lineWidth = 1.5;
                ctx.strokeRect(drawX1, Math.min(yTop, yTopZone), boxWidth, uH);
            }

            // C. Lower Zone (Green Tint - Support)
            if (yBottomZone != null) {
                const lH = Math.abs(yBottom - yBottomZone);
                ctx.fillStyle = hexToRgba(colGreen, box.active ? activeAlpha : completedAlpha);
                ctx.fillRect(drawX1, Math.min(yBottom, yBottomZone), boxWidth, lH);
                ctx.strokeStyle = hexToRgba(colGreen, box.active ? borderAlpha : completedAlpha);
                ctx.lineWidth = 1.5;
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

        // 2. Distinct Volume Analysis Pillars Inside Channels (Sky Blue & Amber Gold)
        if (volCandles && volCandles.length > 0 && res.shw_vol !== false) {
            const volUpCol = hexToRgba(res.volUpColor || '#38bdf8', 0.65);
            const volDnCol = hexToRgba(res.volDownColor || '#f59e0b', 0.65);
            const barW = 5;

            for (const vc of volCandles) {
                const x = timeScale.timeToCoordinate(vc.time);
                if (x == null || x < -10 || x > W + 10) continue;

                // Upper comparison volume bar (Bullish Delta / Up Volume -> Sky Blue)
                const yUOpen = candleSeries.priceToCoordinate(vc.upperOpen);
                const yUClose = candleSeries.priceToCoordinate(vc.upperClose);
                if (yUOpen != null && yUClose != null) {
                    const barH = Math.max(1.5, Math.abs(yUClose - yUOpen));
                    ctx.fillStyle = volUpCol;
                    ctx.fillRect(x - barW / 2, Math.min(yUOpen, yUClose), barW, barH);
                }

                // Lower comparison volume bar (Bearish Delta / Down Volume -> Amber Gold)
                const yLOpen = candleSeries.priceToCoordinate(vc.lowerOpen);
                const yLClose = candleSeries.priceToCoordinate(vc.lowerClose);
                if (yLOpen != null && yLClose != null) {
                    const barH = Math.max(1.5, Math.abs(yLClose - yLOpen));
                    ctx.fillStyle = volDnCol;
                    ctx.fillRect(x - barW / 2, Math.min(yLOpen, yLClose), barW, barH);
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

    function drawRoundedRect(ctx, x, y, width, height, radius) {
        if (ctx.roundRect) {
            ctx.beginPath();
            ctx.roundRect(x, y, width, height, radius);
            return;
        }
        const r = typeof radius === 'number' ? radius : 4;
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + width - r, y);
        ctx.quadraticCurveTo(x + width, y, x + width, y + r);
        ctx.lineTo(x + width, y + height - r);
        ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
        ctx.lineTo(x + r, y + height);
        ctx.quadraticCurveTo(x, y + height, x, y + height - r);
        ctx.lineTo(x, y + r);
        ctx.quadraticCurveTo(x, y, x + r, y);
        ctx.closePath();
    }

    function renderBSSProV3Overlay(ctx, res, timeScale, candleSeries, W, H) {
        const { emaF, emaS, showRibbon, colBuy, colSell, trailSeries, signals, hudData, candles } = res;
        const n = candles.length;

        // 1. EMA Ribbon (21 Fast & 50 Slow) with dynamic cloud fill
        if (showRibbon && emaF && emaS && emaF.length === n && emaS.length === n) {
            for (let i = 1; i < n; i++) {
                if (emaF[i - 1] == null || emaS[i - 1] == null || emaF[i] == null || emaS[i] == null) continue;

                const x0 = timeScale.timeToCoordinate(candles[i - 1].time);
                const x1 = timeScale.timeToCoordinate(candles[i].time);
                if (x0 == null || x1 == null) continue;
                if ((x0 < -50 && x1 < -50) || (x0 > W + 50 && x1 > W + 50)) continue;

                const yF0 = candleSeries.priceToCoordinate(emaF[i - 1]);
                const yS0 = candleSeries.priceToCoordinate(emaS[i - 1]);
                const yF1 = candleSeries.priceToCoordinate(emaF[i]);
                const yS1 = candleSeries.priceToCoordinate(emaS[i]);
                if (yF0 == null || yS0 == null || yF1 == null || yS1 == null) continue;

                const isBull = emaF[i] >= emaS[i];
                ctx.fillStyle = hexToRgba(isBull ? colBuy : colSell, 0.16);
                ctx.beginPath();
                ctx.moveTo(x0, yF0);
                ctx.lineTo(x1, yF1);
                ctx.lineTo(x1, yS1);
                ctx.lineTo(x0, yS0);
                ctx.closePath();
                ctx.fill();
            }

            // Draw Fast EMA stroke
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = colBuy;
            ctx.beginPath();
            let startedF = false;
            for (let i = 0; i < n; i++) {
                if (emaF[i] == null) continue;
                const x = timeScale.timeToCoordinate(candles[i].time);
                const y = candleSeries.priceToCoordinate(emaF[i]);
                if (x == null || y == null) { startedF = false; continue; }
                if (!startedF) { ctx.moveTo(x, y); startedF = true; }
                else ctx.lineTo(x, y);
            }
            ctx.stroke();

            // Draw Slow EMA stroke
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = hexToRgba(colSell, 0.85);
            ctx.beginPath();
            let startedS = false;
            for (let i = 0; i < n; i++) {
                if (emaS[i] == null) continue;
                const x = timeScale.timeToCoordinate(candles[i].time);
                const y = candleSeries.priceToCoordinate(emaS[i]);
                if (x == null || y == null) { startedS = false; continue; }
                if (!startedS) { ctx.moveTo(x, y); startedS = true; }
                else ctx.lineTo(x, y);
            }
            ctx.stroke();
        }

        // 2. Trailing Stop Step Line
        if (trailSeries && trailSeries.length === n) {
            ctx.lineWidth = 2;
            ctx.strokeStyle = res.colSlBuy || '#FF9800';
            ctx.beginPath();
            let startedT = false;
            let lastY = null;
            for (let i = 0; i < n; i++) {
                const val = trailSeries[i];
                if (val == null) { startedT = false; lastY = null; continue; }
                const x = timeScale.timeToCoordinate(candles[i].time);
                const y = candleSeries.priceToCoordinate(val);
                if (x == null || y == null) { startedT = false; lastY = null; continue; }
                if (!startedT) {
                    ctx.moveTo(x, y);
                    startedT = true;
                } else {
                    if (lastY != null) ctx.lineTo(x, lastY);
                    ctx.lineTo(x, y);
                }
                lastY = y;
            }
            ctx.stroke();
        }

        // 3. Trade Entry & Target Signal Badges
        if (signals && signals.length > 0) {
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';

            for (const sig of signals) {
                const x = timeScale.timeToCoordinate(sig.time);
                if (x == null || x < -20 || x > W + 20) continue;
                const y = candleSeries.priceToCoordinate(sig.price);
                if (y == null) continue;

                const text = sig.text || '';
                const textWidth = ctx.measureText(text).width;
                const badgeW = Math.max(48, textWidth + 14);
                const badgeH = 20;

                if (sig.type === 'bullish_entry' || sig.type === 'bearish_entry') {
                    const isBull = sig.type === 'bullish_entry';
                    const badgeY = isBull ? y + 10 : y - badgeH - 10;
                    ctx.fillStyle = sig.color || (isBull ? colBuy : colSell);
                    ctx.shadowColor = sig.color || (isBull ? colBuy : colSell);
                    ctx.shadowBlur = 8;
                    drawRoundedRect(ctx, x - badgeW / 2, badgeY, badgeW, badgeH, 4);
                    ctx.fill();
                    ctx.shadowBlur = 0;

                    ctx.fillStyle = '#ffffff';
                    ctx.fillText(text, x, badgeY + badgeH / 2);
                } else if (sig.type === 'target_hit') {
                    const badgeY = y - badgeH - 6;
                    ctx.fillStyle = '#00C853';
                    ctx.shadowColor = '#00C853';
                    ctx.shadowBlur = 6;
                    drawRoundedRect(ctx, x - badgeW / 2, badgeY, badgeW, badgeH, 4);
                    ctx.fill();
                    ctx.shadowBlur = 0;

                    ctx.fillStyle = '#ffffff';
                    ctx.fillText(text, x, badgeY + badgeH / 2);
                } else if (sig.type === 'exit_sl' || sig.type === 'exit_time') {
                    const isSL = sig.type === 'exit_sl';
                    const badgeY = y + 8;
                    ctx.fillStyle = isSL ? '#FF9800' : '#FFD54F';
                    drawRoundedRect(ctx, x - badgeW / 2, badgeY, badgeW, badgeH, 4);
                    ctx.fill();

                    ctx.fillStyle = isSL ? '#ffffff' : '#000000';
                    ctx.fillText(text, x, badgeY + badgeH / 2);
                }
            }
        }

        // 4. BSS Pro v3 HUD Information Panel (Top Left / Right / Bottom)
        if (hudData && hudData.showTable !== false) {
            const h = hudData;
            const cardW = 210;
            const cardH = 224;
            let startX = 16;
            let startY = 16;
            const pos = h.tblPosIn || 'Top left';
            if (pos === 'Top right') {
                startX = Math.max(16, W - cardW - 16);
                startY = 16;
            } else if (pos === 'Bottom left') {
                startX = 16;
                startY = Math.max(16, H - cardH - 16);
            } else if (pos === 'Bottom right') {
                startX = Math.max(16, W - cardW - 16);
                startY = Math.max(16, H - cardH - 16);
            }

            ctx.save();
            ctx.shadowColor = 'rgba(0, 0, 0, 0.45)';
            ctx.shadowBlur = 10;
            ctx.fillStyle = 'rgba(19, 23, 34, 0.94)';
            drawRoundedRect(ctx, startX, startY, cardW, cardH, 6);
            ctx.fill();
            ctx.shadowBlur = 0;

            ctx.strokeStyle = '#2A2E39';
            ctx.lineWidth = 1;
            drawRoundedRect(ctx, startX, startY, cardW, cardH, 6);
            ctx.stroke();

            // Card Header
            ctx.fillStyle = '#2962FF';
            ctx.beginPath();
            if (ctx.roundRect) {
                ctx.roundRect(startX, startY, cardW, 22, [6, 6, 0, 0]);
            } else {
                ctx.rect(startX, startY, cardW, 22);
            }
            ctx.fill();

            ctx.fillStyle = '#ffffff';
            ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText('BSS PRO v3 · Buyer/Seller Strength', startX + cardW / 2, startY + 11);

            let curY = startY + 31;
            const rowH = 16;
            function drawRow(label, value, valColor) {
                ctx.fillStyle = 'rgba(255, 255, 255, 0.55)';
                ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                ctx.textAlign = 'left';
                ctx.textBaseline = 'middle';
                ctx.fillText(label, startX + 10, curY);

                ctx.fillStyle = valColor || '#ffffff';
                ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                ctx.textAlign = 'right';
                ctx.fillText(value, startX + cardW - 10, curY);
                curY += rowH;
            }

            function getBar(pct, nChars) {
                const f = Math.round((pct / 100.0) * nChars);
                let s = '';
                for (let i = 0; i < nChars; i++) s += (i < f ? '█' : '░');
                return s;
            }

            drawRow('BUYERS', getBar(h.buyPct, 8) + ' ' + h.buyPct.toFixed(1) + '%', '#089981');
            drawRow('SELLERS', getBar(h.sellPct, 8) + ' ' + h.sellPct.toFixed(1) + '%', '#F23645');

            const gradeCol = h.grade === 'Strong' ? '#00E676' : (h.grade === 'Moderate' ? '#FFD54F' : '#94A3B8');
            drawRow('STRENGTH', h.grade + ' (' + h.rank + '/100)', gradeCol);

            const statCol = h.status.includes('LONG') ? '#089981' : (h.status.includes('SHORT') ? '#F23645' : '#94A3B8');
            drawRow('TRADE', h.status, statCol);

            drawRow('ENTRY', h.entry ? '₹' + h.entry.toFixed(2) : '-', '#ffffff');
            drawRow('STOP', h.stop ? '₹' + h.stop.toFixed(2) : '-', '#FF9800');
            drawRow('T1 (1R)', h.t1 ? '₹' + h.t1.toFixed(2) : '-', '#00C853');
            drawRow('T2 (2R)', h.t2 ? '₹' + h.t2.toFixed(2) : '-', '#29B6F6');
            drawRow('MAX (3R)', h.max ? '₹' + h.max.toFixed(2) : '-', '#00C853');

            const pnlTxt = h.pnlNow != null ? (h.pnlNow >= 0 ? '+' : '') + h.pnlNow.toFixed(1) + '%' : '-';
            const pnlCol = (h.pnlNow || 0) >= 0 ? '#00E676' : '#FF5252';
            drawRow('LIVE P/L', pnlTxt, pnlCol);

            drawRow('LAST EVENT', h.lastEvent || 'Flat', 'rgba(255, 255, 255, 0.7)');

            ctx.restore();
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
                    if (val == null || isNaN(val)) {
                        started = false;
                        continue;
                    }
                    const x = timeScale.timeToCoordinate(candles[i].time);
                    const y = candleSeries.priceToCoordinate(val);
                    if (x == null || y == null) {
                        started = false;
                        continue;
                    }

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
                    <label class="tvi-inp-lbl">Zone Opacity %</label>
                    <input type="number" id="inp-zone-opacity" class="tvi-inp" value="${state.inputs.zone_opacity || 55}" min="10" max="100" step="5" />
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Show Volume Bars</label>
                    <input type="checkbox" id="inp-shw-vol" ${state.inputs.shw_vol !== false ? 'checked' : ''} />
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Bullish Zone Color</label>
                    <input type="color" id="inp-col-green" class="tvi-color-inp" value="${state.inputs.green || '#00ffbb'}" />
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Bearish Zone Color</label>
                    <input type="color" id="inp-col-red" class="tvi-color-inp" value="${state.inputs.red || '#ff1100'}" />
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Volume Up Color</label>
                    <input type="color" id="inp-col-vol-up" class="tvi-color-inp" value="${state.inputs.vol_up_color || '#38bdf8'}" />
                </div>
                <div class="tvi-inp-row">
                    <label class="tvi-inp-lbl">Volume Down Color</label>
                    <input type="color" id="inp-col-vol-down" class="tvi-color-inp" value="${state.inputs.vol_down_color || '#f59e0b'}" />
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
            if (state.inputs[k] === undefined) {
                state.inputs[k] = inp.val;
            }
            const curVal = state.inputs[k] !== undefined ? state.inputs[k] : inp.val;

            if (inp.type === 'bool') {
                html += `
                    <div class="tvi-inp-row">
                        <label class="tvi-inp-lbl">${inp.title}</label>
                        <input type="checkbox" data-var="${inp.name}" class="tvi-dyn-inp" ${curVal ? 'checked' : ''} />
                    </div>`;
            } else if (inp.type === 'source') {
                html += `
                    <div class="tvi-inp-row">
                        <label class="tvi-inp-lbl">${inp.title}</label>
                        <select data-var="${inp.name}" class="tvi-dyn-inp tvi-inp">
                            <option value="hl2" ${curVal === 'hl2' ? 'selected' : ''}>hl2 ((H+L)/2)</option>
                            <option value="close" ${curVal === 'close' ? 'selected' : ''}>close</option>
                            <option value="open" ${curVal === 'open' ? 'selected' : ''}>open</option>
                            <option value="high" ${curVal === 'high' ? 'selected' : ''}>high</option>
                            <option value="low" ${curVal === 'low' ? 'selected' : ''}>low</option>
                            <option value="hlc3" ${curVal === 'hlc3' ? 'selected' : ''}>hlc3 ((H+L+C)/3)</option>
                            <option value="ohlc4" ${curVal === 'ohlc4' ? 'selected' : ''}>ohlc4 ((O+H+L+C)/4)</option>
                        </select>
                    </div>`;
            } else if (inp.type === 'options' && inp.options && inp.options.length) {
                html += `
                    <div class="tvi-inp-row">
                        <label class="tvi-inp-lbl">${inp.title}</label>
                        <select data-var="${inp.name}" class="tvi-dyn-inp tvi-inp">
                            ${inp.options.map(opt => `<option value="${opt}" ${curVal === opt ? 'selected' : ''}>${opt}</option>`).join('')}
                        </select>
                    </div>`;
            } else if (inp.type === 'color') {
                html += `
                    <div class="tvi-inp-row">
                        <label class="tvi-inp-lbl">${inp.title}</label>
                        <input type="color" data-var="${inp.name}" class="tvi-dyn-inp tvi-color-inp" value="${curVal}" />
                    </div>`;
            } else if (inp.type === 'int' || inp.type === 'float') {
                const step = inp.step != null ? inp.step : (inp.type === 'float' ? '0.1' : '1');
                const minAttr = inp.minval != null ? `min="${inp.minval}"` : '';
                const maxAttr = inp.maxval != null ? `max="${inp.maxval}"` : '';
                html += `
                    <div class="tvi-inp-row">
                        <label class="tvi-inp-lbl">${inp.title}</label>
                        <input type="number" step="${step}" ${minAttr} ${maxAttr} data-var="${inp.name}" class="tvi-dyn-inp tvi-inp" value="${curVal}" />
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

        // Attach live input & change listeners to dynamic inputs
        container.querySelectorAll('.tvi-dyn-inp').forEach(el => {
            el.addEventListener('change', () => {
                collectInputsFromUI();
                triggerRecompute();
            });
        });
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
            const opEl = document.getElementById('inp-zone-opacity');
            const shwVolEl = document.getElementById('inp-shw-vol');
            const volUpEl = document.getElementById('inp-col-vol-up');
            const volDownEl = document.getElementById('inp-col-vol-down');

            if (lNorm) state.inputs.length_ = parseInt(lNorm.value) || 100;
            if (lBox) state.inputs.length = parseInt(lBox.value) || 14;
            if (chkStrong) state.inputs.strong = chkStrong.checked;
            if (chkOverlap) state.inputs.overlap = chkOverlap.checked;
            if (selVolMode) state.inputs.vol_mode = selVolMode.value;
            if (colG) state.inputs.green = colG.value;
            if (colR) state.inputs.red = colR.value;
            if (opEl) state.inputs.zone_opacity = parseInt(opEl.value) || 55;
            if (shwVolEl) state.inputs.shw_vol = shwVolEl.checked;
            if (volUpEl) state.inputs.vol_up_color = volUpEl.value;
            if (volDownEl) state.inputs.vol_down_color = volDownEl.value;
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

                if (!state.stationEnabled.CE) state.stationEnabled.CE = {};
                if (!state.stationEnabled.PE) state.stationEnabled.PE = {};
                if (state.target === 'both' || state.target === 'CE') state.stationEnabled.CE[id] = true;
                if (state.target === 'both' || state.target === 'PE') state.stationEnabled.PE[id] = true;

                const editor = document.getElementById('tvi-code-editor');
                if (editor) editor.value = state.currentCode;

                refreshPresetDropdown();
                renderInputsGrid();
                refreshStationPopovers();
                triggerRecompute();
            });
        }

        if (targetSelect) {
            targetSelect.addEventListener('change', (e) => {
                state.target = e.target.value;
                const curId = state.activePresetId;
                if (curId) {
                    if (!state.stationEnabled.CE) state.stationEnabled.CE = {};
                    if (!state.stationEnabled.PE) state.stationEnabled.PE = {};
                    if (state.target === 'both' || state.target === 'CE') state.stationEnabled.CE[curId] = true;
                    if (state.target === 'both' || state.target === 'PE') state.stationEnabled.PE[curId] = true;
                }
                refreshStationPopovers();
                triggerRecompute();
            });
        }

        const editor = document.getElementById('tvi-code-editor');
        if (editor) {
            let debounceTimer = null;
            const handleEditorCodeChange = () => {
                clearTimeout(debounceTimer);
                debounceTimer = setTimeout(() => {
                    state.currentCode = editor.value;
                    if (state.customIndicators[state.activePresetId]) {
                        state.customIndicators[state.activePresetId].code = editor.value;
                    }
                    renderInputsGrid();
                }, 250);
            };
            editor.addEventListener('input', handleEditorCodeChange);
            editor.addEventListener('paste', () => setTimeout(handleEditorCodeChange, 50));
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
                const curId = state.activePresetId;
                if (curId) {
                    if (!state.stationEnabled.CE) state.stationEnabled.CE = {};
                    if (!state.stationEnabled.PE) state.stationEnabled.PE = {};
                    if (state.target === 'both' || state.target === 'CE') state.stationEnabled.CE[curId] = true;
                    if (state.target === 'both' || state.target === 'PE') state.stationEnabled.PE[curId] = true;
                }
                collectInputsFromUI();
                renderInputsGrid();
                refreshStationPopovers();
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
        computeBSSProV3,
        computeAlgoAlphaBreakoutChannels,
        PineTA,
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
