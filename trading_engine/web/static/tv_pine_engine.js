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

    const BSS_PRO_V5_FULL_SCRIPT = `// BSS Pro v5
// The consolidation-channel detection used for breakout levels is adapted from the idea and formulas of
// "Smart Money Breakout Channels" by AlgoAlpha, published under the Mozilla Public License 2.0
// (https://mozilla.org/MPL/2.0/). Keep this notice if you share this script.
//@version=5
indicator("BSS Pro v5 - Buyer/Seller Strength, Targets, SL & Exit", shorttitle="BSS Pro v5", overlay=true, max_labels_count=500, max_lines_count=500, max_boxes_count=500)

// =====================================================================
//  INPUTS
// =====================================================================
g0 = "0. Mode & Signal Source"
tradeMode = input.string("Option buyer (long premium only)", "Trade mode", options=["Option buyer (long premium only)", "Normal (BUY & SELL on this chart)"], group=g0, tooltip="Option buyer: only BUY signals on this chart. Normal: BUY and SELL both, for index / futures / stock charts.")
useSrc    = input.bool(false, "Take signals from another symbol", group=g0, tooltip="Entries, exits and buyer/seller % come from the source symbol; stop loss, targets, support/resistance and prices stay on THIS chart.")
srcSymIn  = input.symbol("NSE:NIFTY1!", "Signal source symbol", group=g0)
optType   = input.string("Auto", "This chart's option type (only used with another source)", options=["Auto", "CALL", "PUT"], group=g0)

g1 = "1. Trend & Signal"
emaFastLen = input.int(21, "Fast EMA", minval=2, group=g1)
emaSlowLen = input.int(50, "Slow EMA", minval=5, group=g1)
rsiLen     = input.int(14, "RSI length", minval=2, group=g1)
stLen      = input.int(10, "Supertrend ATR length", minval=1, group=g1)
stMult     = input.float(3.0, "Supertrend multiplier", minval=0.5, step=0.1, group=g1)

g2 = "2. Buyer / Seller Pressure"
pressLen = input.int(14, "Pressure lookback (bars)", minval=3, group=g2)
domThr   = input.float(58.0, "TREND entries: min dominant side %", minval=50.0, maxval=90.0, step=1.0, group=g2)
useLtf   = input.bool(true, "Use intrabar volume for the buyer/seller split", group=g2, tooltip="Needs the chart timeframe above the intrabar timeframe below (e.g. 5m chart with 1m).")
ltfTf    = input.string("1", "Intrabar timeframe", options=["1", "3", "5"], group=g2)
minRank  = input.float(40.0, "TREND entries: min relative strength rank (0-100)", minval=0.0, maxval=100.0, step=5.0, group=g2)

g3 = "3. TREND entries (pullback in a trend)"
useCandle = input.bool(true, "Signal candle must close in trade direction", group=g3)
maxExtAtr = input.float(1.5, "Max distance from fast EMA (x ATR)", minval=0.3, step=0.1, group=g3)
rsiHi     = input.float(70.0, "BUY blocked if RSI above", minval=55.0, maxval=95.0, group=g3)
rsiLo     = input.float(30.0, "SELL blocked if RSI below", minval=5.0, maxval=45.0, group=g3)
reqPb     = input.bool(true, "Need a recent pullback to the fast EMA", group=g3)
pbBars    = input.int(8, "Pullback look-back (bars)", minval=2, group=g3)
pbAtr     = input.float(0.6, "Pullback touch zone (x ATR)", minval=0.1, step=0.1, group=g3)
useMacd   = input.bool(true, "MACD histogram must agree", group=g3)
maxWick   = input.float(0.4, "Max rejection wick on signal candle (share of range)", minval=0.1, maxval=1.0, step=0.05, group=g3)
minRR     = input.float(0.8, "Min reward:risk to T1", minval=0.0, step=0.1, group=g3)
cooldown  = input.int(5, "Cool-down after a trade closes (bars)", minval=0, group=g3)

g3b = "3b. BREAKOUT entries (first strong close above a resistance level)"
useBrk     = input.bool(true, "Allow BREAKOUT entries", group=g3b, tooltip="Fires on the first strong close above a swing resistance (built from candle BODIES, not wicks) or above a consolidation channel top.")
brkWin     = input.int(3, "Level counts as just-broken for (bars)", minval=1, maxval=10, group=g3b, tooltip="If the first breakout candle fails the checks, the next candles can still trigger within this many bars.")
chaseAtr   = input.float(1.5, "Max distance above the broken level (x ATR)", minval=0.3, step=0.1, group=g3b, tooltip="Late-entry limit. Beyond this the move is treated as already gone.")
minCf      = input.int(3, "Confirmations needed (out of 5)", minval=0, maxval=5, group=g3b, tooltip="The 5 checks: small rejection wick, MACD histogram turning your way, volume at or above average, body size, close beyond the fast EMA. Soft score - one failed check no longer blocks the trade.")
brkDomThr  = input.float(52.0, "Min dominant side % (breakouts)", minval=50.0, maxval=90.0, step=1.0, group=g3b)
brkBody    = input.float(0.4, "Body size check (x ATR)", minval=0.1, step=0.1, group=g3b)
volMult    = input.float(1.0, "Volume check vs 20-bar average (x)", minval=0.0, step=0.1, group=g3b)
chNormLen  = input.int(100, "Channel: normalisation length", minval=10, group=g3b)
chLen      = input.int(14, "Channel: detection length", minval=3, group=g3b)
chMinDur   = input.int(10, "Channel: minimum bars inside", minval=3, group=g3b)
chStrong   = input.bool(true, "Channel: strong close only (body mostly outside)", group=g3b)
chNested   = input.bool(false, "Channel: allow overlapping channels", group=g3b)

g3c = "3c. Support / Resistance (calculated from candle bodies - nothing is drawn)"
srOn       = input.bool(true, "Check support / resistance before entry", group=g3c)
pivL       = input.int(4, "Swing pivot: bars on the left", minval=1, group=g3c)
pivR       = input.int(2, "Swing pivot: bars on the right", minval=1, group=g3c)
srLookback = input.int(150, "Use levels from the last N bars", minval=20, group=g3c)
srTolAtr   = input.float(0.25, "Zone half-width (x ATR)", minval=0.05, step=0.05, group=g3c)
minRoomR   = input.float(2.0, "Min room to the next resistance (x risk)", minval=0.0, step=0.5, group=g3c, tooltip="2.0 = T2 (the 1:2 target) must fit below the next resistance.")

g4 = "4. Stop Loss, Trailing & Costs"
atrLen    = input.int(14, "ATR length", minval=1, group=g4)
swingLen  = input.int(10, "Swing lookback for SL", minval=2, group=g4)
minSlAtr  = input.float(0.6, "Min SL distance (x ATR)", minval=0.2, step=0.1, group=g4)
maxSlAtr  = input.float(2.5, "Max SL distance (x ATR)", minval=0.5, step=0.1, group=g4)
maxSlPct  = input.float(12.0, "Hard cap on SL distance (% of entry, 0 = off)", minval=0.0, step=0.5, group=g4)
trailOn   = input.bool(true, "Trail the stop after T1", group=g4)
trailR    = input.float(1.0, "Trailing distance (x initial risk)", minval=0.3, step=0.1, group=g4)
costPct   = input.float(1.0, "Round-trip cost (% of premium: brokerage, taxes, spread)", minval=0.0, step=0.1, group=g4)

g4b = "4b. Fade Exit (leave early when momentum dies after T1 / T2)"
fadeOn      = input.bool(true, "Exit at candle close when momentum fades", group=g4b)
fadeNeedT1  = input.int(3, "Warning signs needed after T1 (of 7)", minval=1, maxval=7, group=g4b)
fadeNeedT2  = input.int(2, "Warning signs needed after T2 (of 7)", minval=1, maxval=7, group=g4b)

g5 = "5. Targets"
tgtMode  = input.string("R-multiple", "Target mode", options=["R-multiple", "Fibonacci extension"], group=g5)
r1       = input.float(1.0, "T1 (R)", minval=0.2, step=0.1, group=g5)
r2       = input.float(2.0, "T2 (R)", minval=0.2, step=0.1, group=g5)
r3       = input.float(3.0, "Max (R)", minval=0.2, step=0.1, group=g5)
fibLen   = input.int(50, "Fib mode: swing range lookback", minval=10, group=g5)
f1       = input.float(0.382, "Fib mode: T1 ratio", step=0.001, group=g5)
f2       = input.float(0.618, "Fib mode: T2 ratio", step=0.001, group=g5)
f3       = input.float(1.0,   "Fib mode: Max ratio", step=0.001, group=g5)
maxTgtR  = input.float(4.0, "Fib mode: cap targets at (R)", minval=1.0, step=0.5, group=g5)
maxBars  = input.int(60, "Time stop: close trade after N bars", minval=5, group=g5)
minSamples = input.int(20, "Samples needed before showing a chance %", minval=5, group=g5)

g6 = "6. Look & Feel"
markerOff  = input.float(0.45, "Marker distance from candle (x ATR)", minval=0.1, step=0.05, group=g6)
showRibbon = input.bool(true, "EMA ribbon (of this chart)", group=g6)
showZones  = input.bool(true, "Risk / reward zones", group=g6)
zoneToMax  = input.bool(false, "Draw reward zone up to Max (otherwise up to T2)", group=g6)
keepOldLbl = input.bool(false, "Keep level labels on completed trades", group=g6)
showTable  = input.bool(true, "Info panel", group=g6)
tblPosIn   = input.string("Bottom left", "Panel position", options=["Bottom left", "Bottom right", "Top right", "Top left"], group=g6, tooltip="Top left sits under the chart legend and top right usually sits on the candles, so Bottom left is the default. If candles still draw over the panel: indicator menu (three dots) > Visual order > Bring to front.")
colBuy     = input.color(#089981, "BUY marker", group=g6)
colSell    = input.color(#F23645, "SELL marker", group=g6)
colSlBuy   = input.color(#FF9800, "Stop loss - BUY trade", group=g6)
colSlSell  = input.color(#AB47BC, "Stop loss - SELL trade", group=g6)
colTgtBuy  = input.color(#00C853, "Targets - BUY trade", group=g6)
colTgtSell = input.color(#29B6F6, "Targets - SELL trade", group=g6)
colExit    = input.color(#FFD54F, "EXIT marker", group=g6)

colWin  = #00E676
colLoss = #FF5252

tblPos = switch tblPosIn
    "Top left"     => position.top_left
    "Top right"    => position.top_right
    "Bottom right" => position.bottom_right
    => position.bottom_left

// =====================================================================
//  HELPERS
// =====================================================================
f_px(x) => str.tostring(x, format.mintick)

f_gain(x, base, d) =>
    g = d * (x - base) / base * 100.0
    (g >= 0 ? "+" : "") + str.tostring(g, "0.0") + "%"

f_pct(x) => (x >= 0 ? "+" : "") + str.tostring(x, "0.0") + "%"

f_room(r) => r >= 900.0 ? "clear" : str.tostring(r, "0.0") + "R"

f_bar(p, n) =>
    f = math.round(p / 100.0 * n)
    s = ""
    for i = 0 to n - 1
        s += i < f ? "█" : "░"
    s

f_hit(h, n) =>
    n < minSamples ? "hits " + str.tostring(h) + "/" + str.tostring(n) : "chance " + str.tostring(h * 100.0 / n, "0") + "% (" + str.tostring(h) + "/" + str.tostring(n) + ")"

// Signal engine - runs on the SIGNAL SOURCE symbol (this chart unless another symbol is chosen)
f_engine() =>
    a   = ta.atr(atrLen)
    eF  = ta.ema(close, emaFastLen)
    eS  = ta.ema(close, emaSlowLen)
    r   = ta.rsi(close, rsiLen)
    [stL, sd] = ta.supertrend(stMult, stLen)
    [mL, mSg, mH] = ta.macd(close, 12, 26, 9)
    vA  = nz(ta.sma(volume, 20))
    rc  = math.max(high - low, syminfo.mintick)
    up  = sd < 0
    bullC = close > open and close > close[1]
    bearC = close < open and close < close[1]
    pbUp  = ta.lowest(low - eF, pbBars) <= pbAtr * a
    pbDn  = ta.highest(high - eF, pbBars) >= -pbAtr * a
    wkUp  = (high - close) / rc <= maxWick
    wkDn  = (close - low) / rc <= maxWick
    moUp  = (not useMacd) or (mH > 0 and mH > mH[1])
    moDn  = (not useMacd) or (mH < 0 and mH < mH[1])
    mbUp  = (not useMacd) or mH > mH[1]
    mbDn  = (not useMacd) or mH < mH[1]
    vlOk  = nz(volume) <= 0 or vA <= 0 or volume >= volMult * vA
    trUp  = up and eF > eS and close > eF and r > 50 and r < rsiHi
    trDn  = (not up) and eF < eS and close < eF and r < 50 and r > rsiLo
    quUp  = (not useCandle or bullC) and (close - eF) <= maxExtAtr * a and (not reqPb or pbUp) and wkUp and moUp
    quDn  = (not useCandle or bearC) and (eF - close) <= maxExtAtr * a and (not reqPb or pbDn) and wkDn and moDn
    // soft confirmation score for breakouts (0-5)
    cfUp  = (wkUp ? 1 : 0) + (mbUp ? 1 : 0) + (vlOk ? 1 : 0) + ((close - open) >= brkBody * a ? 1 : 0) + (close > eF ? 1 : 0)
    cfDn  = (wkDn ? 1 : 0) + (mbDn ? 1 : 0) + (vlOk ? 1 : 0) + ((open - close) >= brkBody * a ? 1 : 0) + (close < eF ? 1 : 0)
    // warning signs against a rise (c*) and against a fall (d*)
    c1 = r < r[1] and r[1] > 60
    c2 = mH < mH[1] and (mH[1] < mH[2] or mL < mSg)
    c3 = close < eF
    c4 = vA > 0 and close < open and nz(volume) > vA
    c5 = (high - math.max(open, close)) >= 0.5 * rc
    d1 = r > r[1] and r[1] < 40
    d2 = mH > mH[1] and (mH[1] > mH[2] or mL > mSg)
    d3 = close > eF
    d4 = vA > 0 and close > open and nz(volume) > vA
    d5 = (math.min(open, close) - low) >= 0.5 * rc
    fdUpN = (c1 ? 1 : 0) + (c2 ? 1 : 0) + (c3 ? 1 : 0) + (c4 ? 1 : 0) + (c5 ? 1 : 0)
    fdDnN = (d1 ? 1 : 0) + (d2 ? 1 : 0) + (d3 ? 1 : 0) + (d4 ? 1 : 0) + (d5 ? 1 : 0)
    fdUpT = (c1 ? "RSI " : "") + (c2 ? "MACD " : "") + (c3 ? "EMA " : "") + (c4 ? "VOL " : "") + (c5 ? "WICK " : "")
    fdDnT = (d1 ? "RSI " : "") + (d2 ? "MACD " : "") + (d3 ? "EMA " : "") + (d4 ? "VOL " : "") + (d5 ? "WICK " : "")
    [trUp, trDn, quUp, quDn, cfUp, cfDn, up, close < eF, close > eF, fdUpN, fdUpT, fdDnN, fdDnT]

// =====================================================================
//  SIGNAL SOURCE + BUYER / SELLER PRESSURE
// =====================================================================
srcTicker = useSrc ? srcSymIn : syminfo.tickerid

[tUp, tDn, qUp, qDn, cfUp, cfDn, stUp, belowF, aboveF, fdUpN, fdUpT, fdDnN, fdDnT] = request.security(srcTicker, timeframe.period, f_engine())
[sH, sL, sC, sV] = request.security(srcTicker, timeframe.period, [high, low, close, volume])

array<float> upA = request.security_lower_tf(srcTicker, ltfTf, close > open ? nz(volume) : close == open ? nz(volume) * 0.5 : 0.0)
array<float> dnA = request.security_lower_tf(srcTicker, ltfTf, close < open ? nz(volume) : close == open ? nz(volume) * 0.5 : 0.0)
float ltfUp = (useLtf and array.size(upA) > 0) ? array.sum(upA) : 0.0
float ltfDn = (useLtf and array.size(dnA) > 0) ? array.sum(dnA) : 0.0
bool  ltfOk = ltfUp + ltfDn > 0

sRng     = sH - sL
sVol     = nz(sV) > 0 ? sV : 1.0
shapeBuy = sRng > 0 ? sVol * (sC - sL) / sRng : sVol * 0.5
buyVol   = ltfOk ? ltfUp : shapeBuy
sellVol  = ltfOk ? ltfDn : sVol - shapeBuy
buySum   = math.sum(buyVol, pressLen)
sellSum  = math.sum(sellVol, pressLen)
totSum   = buySum + sellSum
buyPct   = totSum > 0 ? buySum / totSum * 100.0 : 50.0
sellPct  = 100.0 - buyPct

domAbs = math.abs(buyPct - 50.0)
rank   = ta.percentrank(domAbs, 100)
rankOK = minRank <= 0 or (not na(rank) and rank >= minRank)
grade  = na(rank) ? "n/a" : rank >= 75 ? "Strong" : rank >= 40 ? "Moderate" : "Weak"

dirOkUp = buyPct >= domThr and rankOK
dirOkDn = sellPct >= domThr and rankOK

// =====================================================================
//  LEVELS (on THIS chart's price)
// =====================================================================
atr    = ta.atr(atrLen)
emaF   = ta.ema(close, emaFastLen)
emaS   = ta.ema(close, emaSlowLen)
hh     = ta.highest(high, fibLen)
ll     = ta.lowest(low, fibLen)
swLow  = ta.lowest(low, swingLen)
swHigh = ta.highest(high, swingLen)
leg    = math.max(hh - ll, 2.0 * atr)
off    = markerOff * atr

slL0 = math.min(math.max(swLow - 0.1 * atr, close - maxSlAtr * atr), close - minSlAtr * atr)
slS0 = math.max(math.min(swHigh + 0.1 * atr, close + maxSlAtr * atr), close + minSlAtr * atr)
slL  = maxSlPct > 0 ? math.max(slL0, close * (1.0 - maxSlPct / 100.0)) : slL0
slS  = maxSlPct > 0 ? math.min(slS0, close * (1.0 + maxSlPct / 100.0)) : slS0
riskL = math.max(close - slL, syminfo.mintick)
riskS = math.max(slS - close, syminfo.mintick)

isR = tgtMode == "R-multiple"
dL1 = isR ? riskL * r1 : math.min(leg * f1, riskL * maxTgtR)
dL2 = isR ? riskL * r2 : math.min(leg * f2, riskL * maxTgtR)
dL3 = isR ? riskL * r3 : math.min(leg * f3, riskL * maxTgtR)
dS1 = isR ? riskS * r1 : math.min(leg * f1, riskS * maxTgtR)
dS2 = isR ? riskS * r2 : math.min(leg * f2, riskS * maxTgtR)
dS3 = isR ? riskS * r3 : math.min(leg * f3, riskS * maxTgtR)

t1L = close + dL1
t2L = close + dL2
tML = close + dL3
t1S = math.max(close - dS1, syminfo.mintick)
t2S = math.max(close - dS2, syminfo.mintick)
tMS = math.max(close - dS3, syminfo.mintick)
rrL = (t1L - close) / riskL
rrS = (close - t1S) / riskS

// =====================================================================
//  SUPPORT / RESISTANCE FROM CANDLE BODIES + CONSOLIDATION CHANNELS (internal only)
// =====================================================================
ph = ta.pivothigh(math.max(open, close), pivL, pivR)
pl = ta.pivotlow(math.min(open, close), pivL, pivR)

var array<float> resLv  = array.new_float()
var array<int>   resBar = array.new_int()
var array<float> supLv  = array.new_float()
var array<int>   supBar = array.new_int()
var array<float> chTop  = array.new_float()
var array<float> chBot  = array.new_float()
var array<int>   chBar  = array.new_int()

var float clrLvlU = na
var int   clrBarU = -1000
var bool  clrChU  = false
var float clrLvlD = na
var int   clrBarD = -1000
var bool  clrChD  = false

if not na(ph)
    array.push(resLv, ph)
    array.push(resBar, bar_index - pivR)
    if array.size(resLv) > 40
        array.shift(resLv)
        array.shift(resBar)
if not na(pl)
    array.push(supLv, pl)
    array.push(supBar, bar_index - pivR)
    if array.size(supLv) > 40
        array.shift(supLv)
        array.shift(supBar)

// consolidation channels: a quiet period (falling volatility) ends when volatility starts expanding
loN   = ta.lowest(low, chNormLen)
hiN   = ta.highest(high, chNormLen)
normP = hiN > loN ? (close - loN) / (hiN - loN) : 0.5
volN  = ta.stdev(normP, 14)
upPos = (ta.highestbars(volN, chLen + 1) + chLen) / chLen
loPos = (ta.lowestbars(volN, chLen + 1) + chLen) / chLen
chDur = math.max(nz(ta.barssince(ta.crossover(loPos, upPos))), 1)
chHi  = ta.highest(high, chDur)
chLo  = ta.lowest(low, chDur)
chForm = ta.crossover(upPos, loPos) and chDur > chMinDur

if chForm
    bool freeSlot = true
    if (not chNested) and array.size(chTop) > 0
        for j = 0 to array.size(chTop) - 1
            if chHi > array.get(chBot, j) and chLo < array.get(chTop, j)
                freeSlot := false
    if freeSlot
        array.push(chTop, chHi)
        array.push(chBot, chLo)
        array.push(chBar, bar_index)
        if array.size(chTop) > 8
            array.shift(chTop)
            array.shift(chBot)
            array.shift(chBar)

tolPx    = srTolAtr * atr
strongPx = chStrong ? math.avg(close, open) : close
float nearRes = na
float nearSup = na

// channels: strong close outside = breakout of that level
if array.size(chTop) > 0
    for k = array.size(chTop) - 1 to 0
        float tp = array.get(chTop, k)
        float bt = array.get(chBot, k)
        int   bb = array.get(chBar, k)
        if strongPx > tp
            clrLvlU := (bar_index == clrBarU and not na(clrLvlU)) ? math.max(clrLvlU, tp) : tp
            clrBarU := bar_index
            clrChU  := true
            array.remove(chTop, k)
            array.remove(chBot, k)
            array.remove(chBar, k)
        else if strongPx < bt
            clrLvlD := (bar_index == clrBarD and not na(clrLvlD)) ? math.min(clrLvlD, bt) : bt
            clrBarD := bar_index
            clrChD  := true
            array.remove(chTop, k)
            array.remove(chBot, k)
            array.remove(chBar, k)
        else if bar_index - bb > 400
            array.remove(chTop, k)
            array.remove(chBot, k)
            array.remove(chBar, k)

// channels still open act as resistance above price and support below it
if array.size(chTop) > 0
    for k2 = 0 to array.size(chTop) - 1
        float tp2 = array.get(chTop, k2)
        float bt2 = array.get(chBot, k2)
        if tp2 > close - tolPx
            nearRes := na(nearRes) ? tp2 : math.min(nearRes, tp2)
        if bt2 < close + tolPx
            nearSup := na(nearSup) ? bt2 : math.max(nearSup, bt2)

// body-based swing levels
if array.size(resLv) > 0
    for i = 0 to array.size(resLv) - 1
        float lv = array.get(resLv, i)
        int   bi = array.get(resBar, i)
        if bar_index - bi <= srLookback
            if lv > close - tolPx
                nearRes := na(nearRes) ? lv : math.min(nearRes, lv)
            if close > lv + tolPx and close[1] <= lv + tolPx and strongPx > lv
                if bar_index != clrBarU or lv > nz(clrLvlU)
                    clrLvlU := lv
                    clrBarU := bar_index
                    clrChU  := false

if array.size(supLv) > 0
    for j2 = 0 to array.size(supLv) - 1
        float lw = array.get(supLv, j2)
        int   bj = array.get(supBar, j2)
        if bar_index - bj <= srLookback
            if lw < close + tolPx
                nearSup := na(nearSup) ? lw : math.max(nearSup, lw)
            if close < lw - tolPx and close[1] >= lw - tolPx and strongPx < lw
                if bar_index != clrBarD or lw < nz(clrLvlD, 1000000000.0)
                    clrLvlD := lw
                    clrBarD := bar_index
                    clrChD  := false

roomRL  = na(nearRes) ? 999.0 : math.max(nearRes - close, 0.0) / riskL
roomRS  = na(nearSup) ? 999.0 : math.max(close - nearSup, 0.0) / riskS
srOKL   = (not srOn) or roomRL >= minRoomR
srOKS   = (not srOn) or roomRS >= minRoomR
atRes   = (not na(nearRes)) and (nearRes - close) <= 0.4 * atr
atSup   = (not na(nearSup)) and (close - nearSup) <= 0.4 * atr

// a level broken in the last few bars, price still above / below it, and not already run away from it
lvlUp   = (bar_index - clrBarU <= brkWin) and (not na(clrLvlU)) and close > clrLvlU
lvlDn   = (bar_index - clrBarD <= brkWin) and (not na(clrLvlD)) and close < clrLvlD
chaseOkL = lvlUp and (close - clrLvlU) <= chaseAtr * atr
chaseOkS = lvlDn and (clrLvlD - close) <= chaseAtr * atr

// =====================================================================
//  MODE: which chart-side trade does a source signal create?
// =====================================================================
buyerMode = tradeMode == "Option buyer (long premium only)"
isPut     = optType == "PUT" or (optType == "Auto" and str.contains(str.upper(syminfo.description), "PUT"))
mapPut    = buyerMode and useSrc and isPut          // PUT premium rises when the source falls

cfLong     = mapPut ? cfDn : cfUp
domLongBrk = (mapPut ? sellPct : buyPct) >= brkDomThr
longTrend  = mapPut ? (tDn and qDn and dirOkDn) : (tUp and qUp and dirOkUp)
longBrk    = useBrk and chaseOkL and close > open and cfLong >= minCf and domLongBrk
shortTrend = (not buyerMode) and (tDn and qDn and dirOkDn)
shortBrk   = (not buyerMode) and useBrk and chaseOkS and close < open and cfDn >= minCf and sellPct >= brkDomThr

sigLong  = (longTrend or longBrk) and srOKL
sigShort = (shortTrend or shortBrk) and srOKS

exitBull = (not stUp) or (belowF and buyPct < 50.0)
exitBear = stUp or (aboveF and buyPct > 50.0)
exitLongTrade  = mapPut ? exitBear : exitBull
exitShortTrade = exitBear

// momentum-fade warning signs for the open trade (source signs + flow + wall)
flowL = mapPut ? sellPct < 50.0 : buyPct < 50.0
fadeLongN = (mapPut ? fdDnN : fdUpN) + (flowL ? 1 : 0) + (atRes ? 1 : 0)
fadeLongT = (mapPut ? fdDnT : fdUpT) + (flowL ? "FLOW " : "") + (atRes ? "RES " : "")
flowS = sellPct < 50.0
fadeShortN = fdDnN + (flowS ? 1 : 0) + (atSup ? 1 : 0)
fadeShortT = fdDnT + (flowS ? "FLOW " : "") + (atSup ? "SUP " : "")

longOK  = sigLong and rrL >= minRR
shortOK = sigShort and rrS >= minRR

// Signals are only confirmed on candle close, so they never repaint
longTrig  = longOK and not longOK[1] and barstate.isconfirmed
shortTrig = shortOK and not shortOK[1] and barstate.isconfirmed

// =====================================================================
//  TRADE STATE + DRAWINGS
// =====================================================================
var int    tDir   = 0
var float  entry  = na
var float  slP    = na
var float  t1P    = na
var float  t2P    = na
var float  tMP    = na
var float  riskPts = na
var float  runExt = na
var bool   h1     = false
var bool   h2     = false
var bool   hM     = false
var bool   trailActive = false
var int    barsIn = 0
var int    lastDir = 0
var int    lastCloseBar = -1000
var color  curSl  = #FF9800
var string lastEvent = "No signal yet"

var int lTot = 0
var int l1   = 0
var int l2   = 0
var int lM   = 0
var int sTot = 0
var int s1   = 0
var int s2   = 0
var int sM   = 0

var int   nTrades = 0
var int   nWin    = 0
var float sumNet  = 0.0

var int lastUpBar = -100
var int slotUp    = 0
var int lastDnBar = -100
var int slotDn    = 0

var line  lnEntry = na
var line  lnSL    = na
var line  lnT1    = na
var line  lnT2    = na
var line  lnTM    = na
var line  lnConn  = na
var label lbEntry = na
var label lbSL    = na
var label lbT1    = na
var label lbT2    = na
var label lbTM    = na
var box   bxRisk  = na
var box   bxRew   = na

bool   evLong    = false
bool   evShort   = false
bool   ev1       = false
bool   ev2       = false
bool   evM       = false
bool   evSL      = false
bool   evExit    = false
bool   evFade    = false
bool   evTime    = false
bool   closedNow = false
string fadeWhy   = ""
float  trailSeries = na

// ---------------------------------------------------------------------
//  Manage an open trade (if SL and a target touch in the same candle, SL counts first)
// ---------------------------------------------------------------------
if tDir != 0
    barsIn += 1
    runExt := tDir == 1 ? math.max(runExt, high) : math.min(runExt, low)

    // keep every drawing in step with the current bar
    if not trailActive
        line.set_x2(lnSL, bar_index)
    line.set_x2(lnEntry, bar_index)
    line.set_x2(lnT1, bar_index)
    line.set_x2(lnT2, bar_index)
    line.set_x2(lnTM, bar_index)
    label.set_x(lbEntry, bar_index + 1)
    label.set_x(lbSL, bar_index + 1)
    label.set_x(lbT1, bar_index + 1)
    label.set_x(lbT2, bar_index + 1)
    label.set_x(lbTM, bar_index + 1)
    if showZones
        box.set_right(bxRisk, bar_index)
        box.set_right(bxRew, bar_index)
    float runPnl = tDir * (close - entry) / entry * 100.0
    line.set_xy2(lnConn, bar_index, close)
    line.set_color(lnConn, runPnl >= 0 ? colWin : colLoss)

    bool slHit = tDir == 1 ? low <= slP : high >= slP
    bool hit1  = tDir == 1 ? high >= t1P : low <= t1P
    bool hit2  = tDir == 1 ? high >= t2P : low <= t2P
    bool hitM  = tDir == 1 ? high >= tMP : low <= tMP

    if slHit
        evSL := true
        closedNow := true
        lastEvent := trailActive ? "Trailing stop hit" : "Stop loss hit"
    else
        if hit1 and not h1
            h1 := true
            ev1 := true
            lastEvent := "T1 hit"
        if hit2 and not h2
            h2 := true
            ev2 := true
            lastEvent := "T2 hit"
        if hitM and not hM
            hM := true
            evM := true
            lastEvent := "Max target hit"

        int  fadeN    = tDir == 1 ? fadeLongN : fadeShortN
        bool fadeExit = fadeOn and barstate.isconfirmed and h1 and fadeN >= (h2 ? fadeNeedT2 : fadeNeedT1)
        bool exitSig  = barstate.isconfirmed and (tDir == 1 ? exitLongTrade : exitShortTrade)

        if hM
            closedNow := true
        else if fadeExit
            evFade := true
            closedNow := true
            fadeWhy := tDir == 1 ? fadeLongT : fadeShortT
            lastEvent := "FADE exit (" + fadeWhy + ")"
        else if exitSig
            evExit := true
            closedNow := true
            lastEvent := "EXIT signal"
        else if barsIn >= maxBars and barstate.isconfirmed
            evTime := true
            closedNow := true
            lastEvent := "Time stop"

        // trailing stop: break-even (+costs) after T1, T1 after T2, and follow the best price
        if (not closedNow) and trailOn and h1
            float beLvl = tDir == 1 ? entry * (1.0 + costPct / 100.0) : entry * (1.0 - costPct / 100.0)
            float cand = slP
            if tDir == 1
                cand := math.max(slP, beLvl)
                if h2
                    cand := math.max(cand, t1P)
                cand := math.max(cand, runExt - trailR * riskPts)
                cand := math.min(cand, close)
                cand := math.max(cand, slP)
            else
                cand := math.min(slP, beLvl)
                if h2
                    cand := math.min(cand, t1P)
                cand := math.min(cand, runExt + trailR * riskPts)
                cand := math.max(cand, close)
                cand := math.min(cand, slP)
            if cand != slP
                slP := cand
                trailActive := true
                label.set_y(lbSL, slP)
                label.set_text(lbSL, "TRAIL SL  " + f_px(slP) + "  (" + f_gain(slP, entry, tDir) + ")")

    if trailActive
        trailSeries := slP

    // level labels: tick off what has happened
    if ev1
        label.set_text(lbT1, "✔ " + label.get_text(lbT1))
    if ev2
        label.set_text(lbT2, "✔ " + label.get_text(lbT2))
    if evM
        label.set_text(lbTM, "✔ " + label.get_text(lbTM))
    if evSL
        label.set_text(lbSL, "✖ " + label.get_text(lbSL))

    // small T1 / T2 markers when the trade continues (stacked so they never overlap)
    if (ev1 or ev2) and not closedNow
        bool upH = tDir == 1
        float hOff = off * 0.6
        if upH
            slotUp := bar_index - lastUpBar <= 4 ? slotUp + 1 : 0
            lastUpBar := bar_index
            hOff := off * 0.6 * (1.0 + 2.8 * slotUp)
        else
            slotDn := bar_index - lastDnBar <= 4 ? slotDn + 1 : 0
            lastDnBar := bar_index
            hOff := off * 0.6 * (1.0 + 2.8 * slotDn)
        string hTxt = (ev1 ? "T1 ✔" : "") + (ev1 and ev2 ? "  " : "") + (ev2 ? "T2 ✔" : "") + "  ₹" + f_px(ev2 ? t2P : t1P)
        label.new(bar_index, upH ? high + hOff : low - hOff, hTxt, style=upH ? label.style_label_down : label.style_label_up, color=upH ? colTgtBuy : colTgtSell, textcolor=color.white, size=size.small)

    if closedNow
        int   cDir   = tDir
        float exitPx = evSL ? slP : hM ? tMP : close
        float pnl    = cDir * (exitPx - entry) / entry * 100.0
        float net    = pnl - costPct

        nTrades += 1
        nWin += net > 0 ? 1 : 0
        sumNet += net
        lastCloseBar := bar_index

        if cDir == 1
            lTot += 1
            l1 += h1 ? 1 : 0
            l2 += h2 ? 1 : 0
            lM += hM ? 1 : 0
        else
            sTot += 1
            s1 += h1 ? 1 : 0
            s2 += h2 ? 1 : 0
            sM += hM ? 1 : 0

        // EXIT / SL / TARGET marker with premium price and gross / net result
        bool   above  = (cDir == 1) != evSL
        bool   locked = trailActive and (cDir == 1 ? slP >= entry : slP <= entry)
        string exTxt  = evSL ? (locked ? "TRAIL STOP" : "SL HIT") : hM ? "MAX TARGET" : evFade ? "FADE EXIT" : evExit ? "EXIT" : "TIME EXIT"
        color  exCol  = evSL ? (cDir == 1 ? colSlBuy : colSlSell) : hM ? (cDir == 1 ? colTgtBuy : colTgtSell) : colExit
        color  exTxtC = (evExit or evTime or evFade) ? color.black : color.white
        string exLbl  = exTxt + "\n₹" + f_px(exitPx) + "\n" + f_pct(pnl) + "  (net " + f_pct(net) + ")" + (evFade ? "\n" + fadeWhy : "")

        float eOff = off
        if above
            slotUp := bar_index - lastUpBar <= 4 ? slotUp + 1 : 0
            lastUpBar := bar_index
            eOff := off * (1.0 + 2.8 * slotUp)
        else
            slotDn := bar_index - lastDnBar <= 4 ? slotDn + 1 : 0
            lastDnBar := bar_index
            eOff := off * (1.0 + 2.8 * slotDn)
        label.new(bar_index, above ? high + eOff : low - eOff, exLbl, style=above ? label.style_label_down : label.style_label_up, color=exCol, textcolor=exTxtC, size=size.small)

        // finish the entry -> exit connector line
        line.set_xy2(lnConn, bar_index, exitPx)
        line.set_color(lnConn, net >= 0 ? colWin : colLoss)

        if not keepOldLbl
            label.delete(lbEntry)
            label.delete(lbSL)
            label.delete(lbT1)
            label.delete(lbT2)
            label.delete(lbTM)

        tDir := 0

// ---------------------------------------------------------------------
//  Open a new trade (only when flat and the cool-down is over)
// ---------------------------------------------------------------------
if tDir == 0 and not closedNow and (longTrig or shortTrig) and (bar_index - lastCloseBar > cooldown)
    tDir := longTrig ? 1 : -1
    bool isL = tDir == 1
    entry := close
    slP := isL ? slL : slS
    t1P := isL ? t1L : t1S
    t2P := isL ? t2L : t2S
    tMP := isL ? tML : tMS
    riskPts := isL ? riskL : riskS
    runExt := close
    h1 := false
    h2 := false
    hM := false
    trailActive := false
    barsIn := 0
    lastDir := tDir
    evLong := isL
    evShort := not isL

    bool   bullSrc = isL ? (not mapPut) : false
    bool   isBrk   = isL ? longBrk : shortBrk
    bool   viaCh   = isL ? clrChU : clrChD
    string eType   = isBrk ? (viaCh ? "CHANNEL BRK" : "LEVEL BRK") : "TREND"
    float  roomNow = isL ? roomRL : roomRS
    lastEvent := (isL ? "BUY (" : "SELL (") + eType + ")"

    color cE = isL ? colBuy : colSell
    color cS = isL ? colSlBuy : colSlSell
    color cT = isL ? colTgtBuy : colTgtSell
    curSl := cS
    int hn  = isL ? lTot : sTot
    int hc1 = isL ? l1 : s1
    int hc2 = isL ? l2 : s2
    int hc3 = isL ? lM : sM

    // BUY / SELL marker with premium price, kept clear of the candle and stacked if crowded
    string domTxt = (bullSrc ? "Buyers " + str.tostring(buyPct, "0") : "Sellers " + str.tostring(sellPct, "0")) + "%" + (useSrc ? " (src)" : "") + " · " + grade
    string entTxt = (isL ? "▲ BUY" : "▼ SELL") + "  ·  " + eType + "\n₹" + f_px(close) + "\n" + domTxt + "\nRoom to wall: " + f_room(roomNow)
    float nOff = off
    if isL
        slotDn := bar_index - lastDnBar <= 4 ? slotDn + 1 : 0
        lastDnBar := bar_index
        nOff := off * (1.0 + 3.2 * slotDn)
    else
        slotUp := bar_index - lastUpBar <= 4 ? slotUp + 1 : 0
        lastUpBar := bar_index
        nOff := off * (1.0 + 3.2 * slotUp)
    label.new(bar_index, isL ? low - nOff : high + nOff, entTxt, style=isL ? label.style_label_up : label.style_label_down, color=cE, textcolor=color.white, size=size.small)

    // risk / reward zones
    if showZones
        float zEnd = zoneToMax ? tMP : t2P
        bxRew := box.new(bar_index, math.max(entry, zEnd), bar_index + 1, math.min(entry, zEnd), border_color=color.new(cT, 100), bgcolor=color.new(cT, 94))
        bxRisk := box.new(bar_index, math.max(entry, slP), bar_index + 1, math.min(entry, slP), border_color=color.new(cS, 100), bgcolor=color.new(cS, 92))

    // level lines
    lnEntry := line.new(bar_index, entry, bar_index + 1, entry, color=color.new(color.gray, 20), width=1, style=line.style_dotted)
    lnSL    := line.new(bar_index, slP, bar_index + 1, slP, color=cS, width=2, style=line.style_solid)
    lnT1    := line.new(bar_index, t1P, bar_index + 1, t1P, color=cT, width=1, style=line.style_dashed)
    lnT2    := line.new(bar_index, t2P, bar_index + 1, t2P, color=cT, width=1, style=line.style_dashed)
    lnTM    := line.new(bar_index, tMP, bar_index + 1, tMP, color=cT, width=2, style=line.style_solid)
    lnConn  := line.new(bar_index, entry, bar_index + 1, entry, color=cE, width=2, style=line.style_dashed)

    // level labels (name, price, % move, past hit record)
    lbEntry := label.new(bar_index + 1, entry, "ENTRY  ₹" + f_px(entry), style=label.style_label_left, color=color.new(color.gray, 25), textcolor=color.white, size=size.small)
    lbSL    := label.new(bar_index + 1, slP, (isL ? "BUY SL  " : "SELL SL  ") + f_px(slP) + "  (" + f_gain(slP, entry, tDir) + ")", style=label.style_label_left, color=cS, textcolor=color.white, size=size.small)
    lbT1    := label.new(bar_index + 1, t1P, "T1  " + f_px(t1P) + "  (" + f_gain(t1P, entry, tDir) + ")  |  " + f_hit(hc1, hn), style=label.style_label_left, color=cT, textcolor=color.white, size=size.small)
    lbT2    := label.new(bar_index + 1, t2P, "T2  " + f_px(t2P) + "  (" + f_gain(t2P, entry, tDir) + ")  |  " + f_hit(hc2, hn), style=label.style_label_left, color=cT, textcolor=color.white, size=size.small)
    lbTM    := label.new(bar_index + 1, tMP, "MAX  " + f_px(tMP) + "  (" + f_gain(tMP, entry, tDir) + ")  |  " + f_hit(hc3, hn), style=label.style_label_left, color=cT, textcolor=color.white, size=size.small)

// =====================================================================
//  PLOTS
// =====================================================================
pF = plot(showRibbon ? emaF : na, "Fast EMA", color=color.new(emaF > emaS ? colBuy : colSell, 15), linewidth=1, display=display.pane)
pS = plot(showRibbon ? emaS : na, "Slow EMA", color=color.new(emaF > emaS ? colBuy : colSell, 55), linewidth=1, display=display.pane)
fill(pF, pS, color=emaF > emaS ? color.new(colBuy, 88) : color.new(colSell, 88))
plot(trailSeries, "Trailing stop", color=curSl, linewidth=2, style=plot.style_linebr, display=display.pane)

// =====================================================================
//  INFO PANEL (compact)
// =====================================================================
int hnT  = lastDir == 1 ? lTot : lastDir == -1 ? sTot : 0
int hT1  = lastDir == 1 ? l1 : lastDir == -1 ? s1 : 0
int hT2  = lastDir == 1 ? l2 : lastDir == -1 ? s2 : 0
int hTM  = lastDir == 1 ? lM : lastDir == -1 ? sM : 0

var table tb = table.new(tblPos, 2, 13, bgcolor=#131722, frame_color=#2A2E39, frame_width=1, border_color=#2A2E39, border_width=1)

f_row(r, k, val, valCol) =>
    table.cell(tb, 0, r, k, text_color=color.new(color.white, 30), bgcolor=#1E222D, text_halign=text.align_left, text_size=size.small)
    table.cell(tb, 1, r, val, text_color=valCol, bgcolor=#131722, text_halign=text.align_left, text_size=size.small)

if barstate.islast and showTable
    haveLv    = lastDir != 0
    statusCol = tDir == 1 ? colBuy : tDir == -1 ? colSell : color.gray
    slCol     = lastDir == 1 ? colSlBuy : lastDir == -1 ? colSlSell : color.gray
    tgCol     = lastDir == 1 ? colTgtBuy : lastDir == -1 ? colTgtSell : color.gray
    gradeCol  = grade == "Strong" ? colWin : grade == "Moderate" ? color.yellow : color.gray
    float pnlNow = tDir != 0 ? tDir * (close - entry) / entry * 100.0 : na
    tradeTxt = tDir == 0 ? "● Flat" : (tDir == 1 ? "▲ LONG @ ₹" : "▼ SHORT @ ₹") + f_px(entry) + "  " + f_pct(pnlNow) + " (net " + f_pct(pnlNow - costPct) + ")"
    int   fN = tDir == 1 ? fadeLongN : tDir == -1 ? fadeShortN : 0
    string fT = tDir == 1 ? fadeLongT : tDir == -1 ? fadeShortT : ""
    roomTxt = "▲ " + f_room(roomRL) + (buyerMode ? "" : "  ▼ " + f_room(roomRS)) + "  (need " + str.tostring(minRoomR, "0.0") + "R)"
    roomCol = roomRL >= minRoomR ? colWin : colLoss
    resTxt  = nTrades == 0 ? "no finished trades yet" : str.tostring(nWin) + "/" + str.tostring(nTrades) + " net wins · " + f_pct(sumNet) + " · avg " + f_pct(sumNet / nTrades)
    resCol  = nTrades == 0 ? color.gray : sumNet >= 0 ? colWin : colLoss
    float lagMin = (timenow - time_close) / 60000.0
    dataTxt = lagMin <= 0 ? "Live" : lagMin < 180 ? (lagMin > 6 ? "⚠ delayed, at least " + str.tostring(lagMin, "0") + " min" : "OK") : "Market closed"
    dataCol = (lagMin > 6 and lagMin < 180) ? color.orange : color.new(color.white, 20)

    table.cell(tb, 0, 0, "BSS PRO v5  ·  Buyer / Seller Strength", text_color=color.white, bgcolor=#2962FF, text_halign=text.align_center, text_size=size.small)
    table.merge_cells(tb, 0, 0, 1, 0)

    f_row(1, "BUYERS", f_bar(buyPct, 10) + " " + str.tostring(buyPct, "0.0") + "%", colBuy)
    f_row(2, "SELLERS", f_bar(sellPct, 10) + " " + str.tostring(sellPct, "0.0") + "%", colSell)
    f_row(3, "STRENGTH", grade + (na(rank) ? "" : "  (" + str.tostring(rank, "0") + "/100)"), gradeCol)
    f_row(4, "TRADE", tradeTxt, statusCol)
    f_row(5, "STOP", haveLv ? "₹" + f_px(slP) + (trailActive and tDir != 0 ? "  (trailing)" : "") : "-", slCol)
    f_row(6, "T1", haveLv ? "₹" + f_px(t1P) + " · " + f_hit(hT1, hnT) : "-", tgCol)
    f_row(7, "T2", haveLv ? "₹" + f_px(t2P) + " · " + f_hit(hT2, hnT) : "-", tgCol)
    f_row(8, "MAX", haveLv ? "₹" + f_px(tMP) + " · " + f_hit(hTM, hnT) : "-", tgCol)
    f_row(9, "ROOM", roomTxt, srOn ? roomCol : color.gray)
    f_row(10, "FADE", tDir != 0 ? str.tostring(fN) + "/7  " + fT : "-", fN >= fadeNeedT1 ? colLoss : color.new(color.white, 20))
    f_row(11, "RESULTS", resTxt, resCol)
    f_row(12, "DATA", dataTxt, dataCol)

// =====================================================================
//  ALERTS
// =====================================================================
alertcondition(evLong,  "BSS BUY",  "BSS: BUY signal @ {{close}}")
alertcondition(evShort, "BSS SELL", "BSS: SELL signal @ {{close}}")
alertcondition(evExit or evFade, "BSS EXIT", "BSS: EXIT / FADE exit @ {{close}}")
alertcondition(evSL,    "BSS Stop hit", "BSS: Stop loss / trailing stop hit")
alertcondition(ev1 or ev2 or evM, "BSS Target hit", "BSS: Target hit")
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
                bss_pro_v5: {
            id: 'bss_pro_v5',
            name: 'BSS Pro v5 [Buyer/Seller Strength, Targets, SL & Exit]',
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
                useMacd: true,
                maxWick: 0.4,
                minRR: 0.8,
                cooldown: 5,
                useBrk: true,
                brkWin: 3,
                chaseAtr: 1.5,
                minCf: 3,
                brkDomThr: 52.0,
                brkBody: 0.4,
                volMult: 1.0,
                chNormLen: 100,
                chLen: 14,
                chMinDur: 10,
                chStrong: true,
                chNested: false,
                srOn: true,
                pivL: 4,
                pivR: 2,
                srLookback: 150,
                srTolAtr: 0.25,
                minRoomR: 2.0,
                atrLen: 14,
                swingLen: 10,
                minSlAtr: 0.6,
                maxSlAtr: 2.5,
                maxSlPct: 12.0,
                trailOn: true,
                trailR: 1.0,
                costPct: 1.0,
                fadeOn: true,
                fadeNeedT1: 3,
                fadeNeedT2: 2,
                tgtMode: 'R-multiple',
                r1: 1.0,
                r2: 2.0,
                r3: 3.0,
                maxBars: 60,
                minSamples: 20,
                markerOff: 0.45,
                showRibbon: true,
                showZones: true,
                zoneToMax: false,
                keepOldLbl: false,
                showTable: true,
                tblPosIn: 'Bottom left',
                colBuy: '#089981',
                colSell: '#F23645',
                colSlBuy: '#FF9800',
                colSlSell: '#AB47BC',
                colTgtBuy: '#00C853',
                colTgtSell: '#29B6F6',
                colExit: '#FFD54F'
            },
            code: BSS_PRO_V5_FULL_SCRIPT
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

        // ── Dedicated BSS Pro v5 Indicator Engine ──────────────────────────────────
    function computeBSSProV5(candles, rawInputs, stationType) {
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
        const useMacd = inputs.useMacd !== false;
        const maxWick = parseFloat(inputs.maxWick) || 0.4;
        const minRR = parseFloat(inputs.minRR) || 0.8;
        const cooldown = Math.max(0, parseInt(inputs.cooldown) || 5);
        const useBrk = inputs.useBrk !== false;
        const brkWin = Math.max(1, parseInt(inputs.brkWin) || 3);
        const chaseAtr = parseFloat(inputs.chaseAtr) || 1.5;
        const minCf = Math.max(0, parseInt(inputs.minCf) || 3);
        const brkDomThr = Math.max(50, parseFloat(inputs.brkDomThr) || 52.0);
        const brkBody = parseFloat(inputs.brkBody) || 0.4;
        const volMult = parseFloat(inputs.volMult) || 1.0;
        const chNormLen = Math.max(10, parseInt(inputs.chNormLen) || 100);
        const chLen = Math.max(3, parseInt(inputs.chLen) || 14);
        const chMinDur = Math.max(3, parseInt(inputs.chMinDur) || 10);
        const chStrong = inputs.chStrong !== false;
        const chNested = inputs.chNested === true;
        const srOn = inputs.srOn !== false;
        const pivL = Math.max(1, parseInt(inputs.pivL) || 4);
        const pivR = Math.max(1, parseInt(inputs.pivR) || 2);
        const srLookback = Math.max(20, parseInt(inputs.srLookback) || 150);
        const srTolAtr = parseFloat(inputs.srTolAtr) || 0.25;
        const minRoomR = parseFloat(inputs.minRoomR) || 2.0;
        const atrLen = Math.max(1, parseInt(inputs.atrLen) || 14);
        const swingLen = Math.max(2, parseInt(inputs.swingLen) || 10);
        const minSlAtr = parseFloat(inputs.minSlAtr) || 0.6;
        const maxSlAtr = parseFloat(inputs.maxSlAtr) || 2.5;
        const maxSlPct = parseFloat(inputs.maxSlPct) || 12.0;
        const trailOn = inputs.trailOn !== false;
        const trailR = parseFloat(inputs.trailR) || 1.0;
        const costPct = parseFloat(inputs.costPct) || 1.0;
        const fadeOn = inputs.fadeOn !== false;
        const fadeNeedT1 = Math.max(1, parseInt(inputs.fadeNeedT1) || 3);
        const fadeNeedT2 = Math.max(1, parseInt(inputs.fadeNeedT2) || 2);
        const tgtMode = inputs.tgtMode || 'R-multiple';
        const r1 = parseFloat(inputs.r1) || 1.0;
        const r2 = parseFloat(inputs.r2) || 2.0;
        const r3 = parseFloat(inputs.r3) || 3.0;
        const fibLen = Math.max(10, parseInt(inputs.fibLen) || 50);
        const f1 = parseFloat(inputs.f1) || 0.382;
        const f2 = parseFloat(inputs.f2) || 0.618;
        const f3 = parseFloat(inputs.f3) || 1.0;
        const maxTgtR = parseFloat(inputs.maxTgtR) || 4.0;
        const maxBars = Math.max(5, parseInt(inputs.maxBars) || 60);
        const minSamples = Math.max(5, parseInt(inputs.minSamples) || 20);
        const markerOff = parseFloat(inputs.markerOff) || 0.45;
        const showRibbon = inputs.showRibbon !== false;
        const showZones = inputs.showZones !== false;
        const zoneToMax = inputs.zoneToMax === true;
        const keepOldLbl = inputs.keepOldLbl === true;
        const showTable = inputs.showTable !== false;
        const tblPosIn = inputs.tblPosIn || 'Bottom left';

        const colBuy = inputs.colBuy || '#089981';
        const colSell = inputs.colSell || '#F23645';
        const colSlBuy = inputs.colSlBuy || '#FF9800';
        const colSlSell = inputs.colSlSell || '#AB47BC';
        const colTgtBuy = inputs.colTgtBuy || '#00C853';
        const colTgtSell = inputs.colTgtSell || '#29B6F6';
        const colExit = inputs.colExit || '#FFD54F';
        const colWin = '#00E676';
        const colLoss = '#FF5252';

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
        const hhArr = PineTA.highest(highs, fibLen);
        const llArr = PineTA.lowest(lows, fibLen);

        // MACD (12, 26, 9)
        const macdFast = PineTA.ema(closes, 12);
        const macdSlow = PineTA.ema(closes, 26);
        const macdLine = new Array(n);
        for (let i = 0; i < n; i++) macdLine[i] = macdFast[i] - macdSlow[i];
        const macdSignal = PineTA.ema(macdLine, 9);
        const macdHist = new Array(n);
        for (let i = 0; i < n; i++) macdHist[i] = macdLine[i] - macdSignal[i];

        // 20-bar SMA of Volume
        const volSMA = PineTA.sma(volumes, 20);

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

        // Candle Body Pivots & Consolidation Channels
        const bodyHighs = new Array(n);
        const bodyLows = new Array(n);
        for (let i = 0; i < n; i++) {
            bodyHighs[i] = Math.max(opens[i], closes[i]);
            bodyLows[i] = Math.min(opens[i], closes[i]);
        }

        const loN = PineTA.lowest(lows, chNormLen);
        const hiN = PineTA.highest(highs, chNormLen);
        const normP = new Array(n);
        for (let i = 0; i < n; i++) {
            const denom = hiN[i] - loN[i];
            normP[i] = denom > 0 ? (closes[i] - loN[i]) / denom : 0.5;
        }
        const volN = PineTA.stdev(normP, 14);
        const hiBars = PineTA.highestbars(volN, chLen + 1);
        const loBars = PineTA.lowestbars(volN, chLen + 1);
        const upPos = new Array(n);
        const loPos = new Array(n);
        for (let i = 0; i < n; i++) {
            upPos[i] = (hiBars[i] + chLen) / chLen;
            loPos[i] = (loBars[i] + chLen) / chLen;
        }

        const resLv = [];
        const resBar = [];
        const supLv = [];
        const supBar = [];
        const chTop = [];
        const chBot = [];
        const chBar = [];

        let clrLvlU = null;
        let clrBarU = -1000;
        let clrChU = false;
        let clrLvlD = null;
        let clrBarD = -1000;
        let clrChD = false;
        let lastLoUpCross = 0;

        // Trade State Variables
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
        let lastCloseBar = -1000;
        let lastEvent = 'No signal yet';
        let tradeStartBar = 0;

        let lastRoomRL = 999.0;
        let lastRoomRS = 999.0;
        let lastFadeLongN = 0;
        let lastFadeLongT = '';
        let lastFadeShortN = 0;
        let lastFadeShortT = '';

        let lTot = 0, l1 = 0, l2 = 0, lM = 0;
        let sTot = 0, s1 = 0, s2 = 0, sM = 0;
        let nTrades = 0, nWin = 0, sumNet = 0.0;

        const cards = [];
        const boxes = [];
        const trailSeries = new Array(n).fill(null);
        let activeLevels = null;

        function f_room(r) {
            if (r == null || r >= 900.0) return 'clear';
            return r.toFixed(1) + 'R';
        }

        function f_hit(h, nTotal) {
            if (nTotal < minSamples) {
                return 'hits ' + h + '/' + nTotal;
            }
            const pct = Math.round((h * 100.0) / nTotal);
            return 'chance ' + pct + '% (' + h + '/' + nTotal + ')';
        }

        for (let i = 1; i < n; i++) {
            const a = atrArr[i] || 1.0;
            const eFast = emaF[i];
            const eSlow = emaS[i];
            const r = rsiArr[i];
            const up = stRes.trend[i] === 1;

            const bullC = closes[i] > opens[i] && closes[i] > closes[i - 1];
            const bearC = closes[i] < opens[i] && closes[i] < closes[i - 1];
            const rc = Math.max(highs[i] - lows[i], 0.05);

            // Pivots from candle bodies
            if (i >= pivL + pivR) {
                const p = i - pivR;
                let isPh = true;
                for (let k = 1; k <= pivL; k++) {
                    if (bodyHighs[p] <= bodyHighs[p - k]) { isPh = false; break; }
                }
                if (isPh) {
                    for (let m = 1; m <= pivR; m++) {
                        if (bodyHighs[p] < bodyHighs[p + m]) { isPh = false; break; }
                    }
                }
                if (isPh) {
                    resLv.push(bodyHighs[p]);
                    resBar.push(p);
                    if (resLv.length > 40) { resLv.shift(); resBar.shift(); }
                }

                let isPl = true;
                for (let k = 1; k <= pivL; k++) {
                    if (bodyLows[p] >= bodyLows[p - k]) { isPl = false; break; }
                }
                if (isPl) {
                    for (let m = 1; m <= pivR; m++) {
                        if (bodyLows[p] > bodyLows[p + m]) { isPl = false; break; }
                    }
                }
                if (isPl) {
                    supLv.push(bodyLows[p]);
                    supBar.push(p);
                    if (supLv.length > 40) { supLv.shift(); supBar.shift(); }
                }
            }

            // Consolidation Channels Formation
            if (i > 0 && loPos[i] > upPos[i] && loPos[i - 1] <= upPos[i - 1]) {
                lastLoUpCross = i;
            }
            const chDur = Math.max(1, i - lastLoUpCross);
            if (i > 0 && upPos[i] > loPos[i] && upPos[i - 1] <= loPos[i - 1] && chDur > chMinDur) {
                let chHi = -999999, chLo = 999999;
                const startB = Math.max(0, i - chDur + 1);
                for (let b = startB; b <= i; b++) {
                    if (highs[b] > chHi) chHi = highs[b];
                    if (lows[b] < chLo) chLo = lows[b];
                }
                let freeSlot = true;
                if (!chNested && chTop.length > 0) {
                    for (let j = 0; j < chTop.length; j++) {
                        if (chHi > chBot[j] && chLo < chTop[j]) { freeSlot = false; break; }
                    }
                }
                if (freeSlot) {
                    chTop.push(chHi);
                    chBot.push(chLo);
                    chBar.push(i);
                    if (chTop.length > 8) { chTop.shift(); chBot.shift(); chBar.shift(); }
                }
            }

            // Channel Breakouts
            const tolPx = srTolAtr * a;
            const strongPx = chStrong ? (closes[i] + opens[i]) / 2 : closes[i];
            for (let k = chTop.length - 1; k >= 0; k--) {
                const tp = chTop[k];
                const bt = chBot[k];
                const bb = chBar[k];
                if (strongPx > tp) {
                    clrLvlU = (i === clrBarU && clrLvlU != null) ? Math.max(clrLvlU, tp) : tp;
                    clrBarU = i;
                    clrChU = true;
                    chTop.splice(k, 1); chBot.splice(k, 1); chBar.splice(k, 1);
                } else if (strongPx < bt) {
                    clrLvlD = (i === clrBarD && clrLvlD != null) ? Math.min(clrLvlD, bt) : bt;
                    clrBarD = i;
                    clrChD = true;
                    chTop.splice(k, 1); chBot.splice(k, 1); chBar.splice(k, 1);
                } else if (i - bb > 400) {
                    chTop.splice(k, 1); chBot.splice(k, 1); chBar.splice(k, 1);
                }
            }

            // Active open channels act as nearest resistance and support
            let nearRes = null;
            let nearSup = null;
            for (let k = 0; k < chTop.length; k++) {
                const tp = chTop[k];
                const bt = chBot[k];
                if (tp > closes[i] - tolPx) nearRes = nearRes == null ? tp : Math.min(nearRes, tp);
                if (bt < closes[i] + tolPx) nearSup = nearSup == null ? bt : Math.max(nearSup, bt);
            }

            // Body Swing Levels
            for (let j = 0; j < resLv.length; j++) {
                const lv = resLv[j];
                const bi = resBar[j];
                if (i - bi <= srLookback) {
                    if (lv > closes[i] - tolPx) nearRes = nearRes == null ? lv : Math.min(nearRes, lv);
                    if (closes[i] > lv + tolPx && closes[i - 1] <= lv + tolPx && strongPx > lv) {
                        if (i !== clrBarU || lv > (clrLvlU || 0)) {
                            clrLvlU = lv;
                            clrBarU = i;
                            clrChU = false;
                        }
                    }
                }
            }
            for (let j = 0; j < supLv.length; j++) {
                const lw = supLv[j];
                const bj = supBar[j];
                if (i - bj <= srLookback) {
                    if (lw < closes[i] + tolPx) nearSup = nearSup == null ? lw : Math.max(nearSup, lw);
                    if (closes[i] < lw - tolPx && closes[i - 1] >= lw - tolPx && strongPx < lw) {
                        if (i !== clrBarD || lw < (clrLvlD || 1e9)) {
                            clrLvlD = lw;
                            clrBarD = i;
                            clrChD = false;
                        }
                    }
                }
            }

            // Technical Criteria
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

            const wkUp = (highs[i] - closes[i]) / rc <= maxWick;
            const wkDn = (closes[i] - lows[i]) / rc <= maxWick;
            const mH = macdHist[i] || 0;
            const mH_1 = macdHist[i - 1] || 0;
            const mH_2 = macdHist[i - 2] || 0;
            const moUp = (!useMacd) || (mH > 0 && mH > mH_1);
            const moDn = (!useMacd) || (mH < 0 && mH < mH_1);
            const mbUp = (!useMacd) || (mH > mH_1);
            const mbDn = (!useMacd) || (mH < mH_1);
            const vlOk = volumes[i] <= 0 || volSMA[i] <= 0 || volumes[i] >= volMult * volSMA[i];

            const trUp = up && eFast > eSlow && closes[i] > eFast && r > 50 && r < rsiHi;
            const trDn = (!up) && eFast < eSlow && closes[i] < eFast && r < 50 && r > rsiLo;

            const quUp = (!useCandle || bullC) && (closes[i] - eFast) <= maxExtAtr * a && (!reqPb || pbUp) && wkUp && moUp;
            const quDn = (!useCandle || bearC) && (eFast - closes[i]) <= maxExtAtr * a && (!reqPb || pbDn) && wkDn && moDn;

            // Confirmations score (0 - 5)
            const cfUp = (wkUp ? 1 : 0) + (mbUp ? 1 : 0) + (vlOk ? 1 : 0) + ((closes[i] - opens[i]) >= brkBody * a ? 1 : 0) + (closes[i] > eFast ? 1 : 0);
            const cfDn = (wkDn ? 1 : 0) + (mbDn ? 1 : 0) + (vlOk ? 1 : 0) + ((opens[i] - closes[i]) >= brkBody * a ? 1 : 0) + (closes[i] < eFast ? 1 : 0);

            // Warning signs
            const c1 = r < rsiArr[i - 1] && rsiArr[i - 1] > 60;
            const c2 = mH < mH_1 && (mH_1 < mH_2 || macdLine[i] < macdSignal[i]);
            const c3 = closes[i] < eFast;
            const c4 = volSMA[i] > 0 && closes[i] < opens[i] && volumes[i] > volSMA[i];
            const c5 = (highs[i] - Math.max(opens[i], closes[i])) >= 0.5 * rc;
            const fdUpN = (c1 ? 1 : 0) + (c2 ? 1 : 0) + (c3 ? 1 : 0) + (c4 ? 1 : 0) + (c5 ? 1 : 0);
            const fdUpT = (c1 ? "RSI " : "") + (c2 ? "MACD " : "") + (c3 ? "EMA " : "") + (c4 ? "VOL " : "") + (c5 ? "WICK " : "");

            const d1 = r > rsiArr[i - 1] && rsiArr[i - 1] < 40;
            const d2 = mH > mH_1 && (mH_1 > mH_2 || macdLine[i] > macdSignal[i]);
            const d3 = closes[i] > eFast;
            const d4 = volSMA[i] > 0 && closes[i] > opens[i] && volumes[i] > volSMA[i];
            const d5 = (Math.min(opens[i], closes[i]) - lows[i]) >= 0.5 * rc;
            const fdDnN = (d1 ? 1 : 0) + (d2 ? 1 : 0) + (d3 ? 1 : 0) + (d4 ? 1 : 0) + (d5 ? 1 : 0);
            const fdDnT = (d1 ? "RSI " : "") + (d2 ? "MACD " : "") + (d3 ? "EMA " : "") + (d4 ? "VOL " : "") + (d5 ? "WICK " : "");

            lastFadeLongN = fdUpN;
            lastFadeLongT = fdUpT;
            lastFadeShortN = fdDnN;
            lastFadeShortT = fdDnT;

            // Dominance & Rank
            const rank = rankArr[i];
            const rankOK = minRank <= 0 || (rank != null && rank >= minRank);
            const grade = rank == null ? "n/a" : (rank >= 75 ? "Strong" : (rank >= 40 ? "Moderate" : "Weak"));
            const dirOkUp = buyPctArr[i] >= domThr && rankOK;
            const dirOkDn = sellPctArr[i] >= domThr && rankOK;

            // SL & Targets on this chart
            const swLow = swLows[i];
            const swHigh = swHighs[i];
            const slL0 = Math.min(Math.max(swLow - 0.1 * a, closes[i] - maxSlAtr * a), closes[i] - minSlAtr * a);
            const slS0 = Math.max(Math.min(swHigh + 0.1 * a, closes[i] + maxSlAtr * a), closes[i] + minSlAtr * a);
            const slL = maxSlPct > 0 ? Math.max(slL0, closes[i] * (1.0 - maxSlPct / 100)) : slL0;
            const slS = maxSlPct > 0 ? Math.min(slS0, closes[i] * (1.0 + maxSlPct / 100)) : slS0;
            const riskL = Math.max(closes[i] - slL, 0.05);
            const riskS = Math.max(slS - closes[i], 0.05);

            const isR = tgtMode === 'R-multiple';
            const leg = Math.max(hhArr[i] - llArr[i], 2.0 * a);
            const dL1 = isR ? riskL * r1 : Math.min(leg * f1, riskL * maxTgtR);
            const dL2 = isR ? riskL * r2 : Math.min(leg * f2, riskL * maxTgtR);
            const dL3 = isR ? riskL * r3 : Math.min(leg * f3, riskL * maxTgtR);
            const dS1 = isR ? riskS * r1 : Math.min(leg * f1, riskS * maxTgtR);
            const dS2 = isR ? riskS * r2 : Math.min(leg * f2, riskS * maxTgtR);
            const dS3 = isR ? riskS * r3 : Math.min(leg * f3, riskS * maxTgtR);

            const t1L = closes[i] + dL1;
            const t2L = closes[i] + dL2;
            const tML = closes[i] + dL3;
            const t1S = Math.max(closes[i] - dS1, 0.05);
            const t2S = Math.max(closes[i] - dS2, 0.05);
            const tMS = Math.max(closes[i] - dS3, 0.05);

            const rrL = (t1L - closes[i]) / riskL;
            const rrS = (closes[i] - t1S) / riskS;

            const roomRL = nearRes == null ? 999.0 : Math.max(nearRes - closes[i], 0.0) / riskL;
            const roomRS = nearSup == null ? 999.0 : Math.max(closes[i] - nearSup, 0.0) / riskS;
            lastRoomRL = roomRL;
            lastRoomRS = roomRS;

            const srOKL = (!srOn) || roomRL >= minRoomR;
            const srOKS = (!srOn) || roomRS >= minRoomR;
            const atRes = nearRes != null && (nearRes - closes[i]) <= 0.4 * a;
            const atSup = nearSup != null && (closes[i] - nearSup) <= 0.4 * a;

            const lvlUp = (i - clrBarU <= brkWin) && clrLvlU != null && closes[i] > clrLvlU;
            const lvlDn = (i - clrBarD <= brkWin) && clrLvlD != null && closes[i] < clrLvlD;
            const chaseOkL = lvlUp && (closes[i] - clrLvlU) <= chaseAtr * a;
            const chaseOkS = lvlDn && (clrLvlD - closes[i]) <= chaseAtr * a;

            // Option buyer mode mapping
            const isPut = String(stationType).toUpperCase() === 'PE';
            const mapPut = buyerMode && isPut;
            const cfLong = mapPut ? cfDn : cfUp;
            const domLongBrk = (mapPut ? sellPctArr[i] : buyPctArr[i]) >= brkDomThr;
            const longTrend = mapPut ? (trDn && quDn && dirOkDn) : (trUp && quUp && dirOkUp);
            const longBrk = useBrk && chaseOkL && closes[i] > opens[i] && cfLong >= minCf && domLongBrk;
            const shortTrend = (!buyerMode) && (trDn && quDn && dirOkDn);
            const shortBrk = (!buyerMode) && useBrk && chaseOkS && closes[i] < opens[i] && cfDn >= minCf && sellPctArr[i] >= brkDomThr;

            const sigLong = (longTrend || longBrk) && srOKL;
            const sigShort = (shortTrend || shortBrk) && srOKS;

            const exitBull = (!up) || (closes[i] < eFast && buyPctArr[i] < 50.0);
            const exitBear = up || (closes[i] > eFast && buyPctArr[i] > 50.0);
            const exitLongTrade = mapPut ? exitBear : exitBull;
            const exitShortTrade = exitBear;

            const flowL = mapPut ? sellPctArr[i] < 50.0 : buyPctArr[i] < 50.0;
            const curFadeLongN = (mapPut ? fdDnN : fdUpN) + (flowL ? 1 : 0) + (atRes ? 1 : 0);
            const curFadeLongT = (mapPut ? fdDnT : fdUpT) + (flowL ? "FLOW " : "") + (atRes ? "RES " : "");
            const flowS = sellPctArr[i] < 50.0;
            const curFadeShortN = fdDnN + (flowS ? 1 : 0) + (atSup ? 1 : 0);
            const curFadeShortT = fdDnT + (flowS ? "FLOW " : "") + (atSup ? "SUP " : "");

            const longOK = sigLong && rrL >= minRR;
            const shortOK = sigShort && rrS >= minRR;

            // Manage Open Position
            let closedNow = false;
            let ev1 = false, ev2 = false, evM = false, evSL = false;
            let evFade = false, evExit = false, evTime = false;
            let fadeWhy = "";

            if (tDir !== 0) {
                barsIn += 1;
                runExt = tDir === 1 ? Math.max(runExt, highs[i]) : Math.min(runExt, lows[i]);

                const slHit = tDir === 1 ? lows[i] <= slP : highs[i] >= slP;
                const hit1 = tDir === 1 ? highs[i] >= t1P : lows[i] <= t1P;
                const hit2 = tDir === 1 ? highs[i] >= t2P : lows[i] <= t2P;
                const hitM = tDir === 1 ? highs[i] >= tMP : lows[i] <= tMP;

                if (slHit) {
                    evSL = true;
                    closedNow = true;
                    lastEvent = trailActive ? 'Trailing stop hit' : 'Stop loss hit';
                } else {
                    if (hit1 && !h1) {
                        h1 = true;
                        ev1 = true;
                        lastEvent = 'T1 hit';
                    }
                    if (hit2 && !h2) {
                        h2 = true;
                        ev2 = true;
                        lastEvent = 'T2 hit';
                    }
                    if (hitM && !hM) {
                        hM = true;
                        evM = true;
                        lastEvent = 'Max target hit';
                    }

                    const fadeN = tDir === 1 ? curFadeLongN : curFadeShortN;
                    const fadeExit = fadeOn && h1 && fadeN >= (h2 ? fadeNeedT2 : fadeNeedT1);
                    const exitSig = tDir === 1 ? exitLongTrade : exitShortTrade;

                    if (hM) {
                        closedNow = true;
                    } else if (fadeExit) {
                        evFade = true;
                        closedNow = true;
                        fadeWhy = tDir === 1 ? curFadeLongT : curFadeShortT;
                        lastEvent = 'FADE exit (' + fadeWhy.trim() + ')';
                    } else if (exitSig) {
                        evExit = true;
                        closedNow = true;
                        lastEvent = 'EXIT signal';
                    } else if (barsIn >= maxBars) {
                        evTime = true;
                        closedNow = true;
                        lastEvent = 'Time stop';
                    }

                    // Trailing Stop Ratchet
                    if (!closedNow && trailOn && h1) {
                        const beLvl = tDir === 1 ? entry * (1.0 + costPct / 100.0) : entry * (1.0 - costPct / 100.0);
                        let cand = slP;
                        if (tDir === 1) {
                            cand = Math.max(slP, beLvl);
                            if (h2) cand = Math.max(cand, t1P);
                            cand = Math.max(cand, runExt - trailR * riskPts);
                            cand = Math.min(cand, closes[i]);
                            cand = Math.max(cand, slP);
                        } else {
                            cand = Math.min(slP, beLvl);
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

                // Target Hit Badges
                if ((ev1 || ev2) && !closedNow) {
                    const hTxt = (ev1 ? 'T1 ✔' : '') + (ev1 && ev2 ? '  ' : '') + (ev2 ? 'T2 ✔' : '') + '  ₹' + (ev2 ? t2P : t1P).toFixed(2);
                    cards.push({
                        barIndex: i,
                        time: times[i],
                        price: ev2 ? t2P : t1P,
                        isBull: tDir === 1,
                        type: 'target_hit',
                        lines: [hTxt],
                        color: tDir === 1 ? colTgtBuy : colTgtSell,
                        textColor: '#ffffff'
                    });
                }

                if (closedNow) {
                    const cDir = tDir;
                    const exitPx = evSL ? slP : (hM ? tMP : closes[i]);
                    const pnl = cDir * (exitPx - entry) / entry * 100.0;
                    const net = pnl - costPct;

                    nTrades += 1;
                    if (net > 0) nWin += 1;
                    sumNet += net;
                    lastCloseBar = i;

                    if (cDir === 1) {
                        lTot += 1;
                        if (h1) l1 += 1;
                        if (h2) l2 += 1;
                        if (hM) lM += 1;
                    } else {
                        sTot += 1;
                        if (h1) s1 += 1;
                        if (h2) s2 += 1;
                        if (hM) sM += 1;
                    }

                    const locked = trailActive && (cDir === 1 ? slP >= entry : slP <= entry);
                    const exTxt = evSL ? (locked ? 'TRAIL STOP' : 'SL HIT') : (hM ? 'MAX TARGET' : (evFade ? 'FADE EXIT' : (evExit ? 'EXIT' : 'TIME EXIT')));
                    const exCol = evSL ? (cDir === 1 ? colSlBuy : colSlSell) : (hM ? (cDir === 1 ? colTgtBuy : colTgtSell) : colExit);
                    const exTxtC = (evExit || evTime || evFade) ? '#000000' : '#ffffff';

                    const exLblLines = [
                        exTxt,
                        '₹' + exitPx.toFixed(2),
                        (pnl >= 0 ? '+' : '') + pnl.toFixed(1) + '%  (net ' + (net >= 0 ? '+' : '') + net.toFixed(1) + '%)'
                    ];
                    if (evFade && fadeWhy) exLblLines.push(fadeWhy.trim());

                    cards.push({
                        barIndex: i,
                        time: times[i],
                        price: exitPx,
                        isAbove: (cDir === 1) !== evSL,
                        type: 'exit',
                        lines: exLblLines,
                        color: exCol,
                        textColor: exTxtC,
                        pnl: pnl,
                        net: net
                    });

                    // Risk / Reward Zone Box
                    if (showZones) {
                        const targetEnd = zoneToMax ? tMP : t2P;
                        boxes.push({
                            entryTime: times[tradeStartBar],
                            exitTime: times[i],
                            topReward: Math.max(entry, targetEnd),
                            botReward: Math.min(entry, targetEnd),
                            topRisk: Math.max(entry, slP),
                            botRisk: Math.min(entry, slP),
                            colTgt: cDir === 1 ? colTgtBuy : colTgtSell,
                            colSl: cDir === 1 ? colSlBuy : colSlSell,
                            entryPrice: entry,
                            exitPrice: exitPx,
                            netPnl: net
                        });
                    }

                    tDir = 0;
                }
            }

            // Open Trade when Flat
            if (tDir === 0 && !closedNow && (longOK || shortOK) && (i - lastCloseBar > cooldown)) {
                tDir = longOK ? 1 : -1;
                const isL = tDir === 1;
                entry = closes[i];
                slP = isL ? slL : slS;
                t1P = isL ? t1L : t1S;
                t2P = isL ? t2L : t2S;
                tMP = isL ? tML : tMS;
                riskPts = isL ? riskL : riskS;
                runExt = entry;
                h1 = false;
                h2 = false;
                hM = false;
                trailActive = false;
                barsIn = 0;
                lastDir = tDir;
                tradeStartBar = i;

                const bullSrc = isL ? (!mapPut) : false;
                const isBrk = isL ? longBrk : shortBrk;
                const viaCh = isL ? clrChU : clrChD;
                const eType = isBrk ? (viaCh ? 'CHANNEL BRK' : 'LEVEL BRK') : 'TREND';
                const roomNow = isL ? roomRL : roomRS;
                lastEvent = (isL ? 'BUY (' : 'SELL (') + eType + ')';

                const domTxt = (bullSrc ? ('Buyers ' + Math.round(buyPctArr[i])) : ('Sellers ' + Math.round(sellPctArr[i]))) + '% · ' + grade;
                const entTxtLines = [
                    (isL ? '▲ BUY' : '▼ SELL') + '  ·  ' + eType,
                    '₹' + entry.toFixed(2),
                    domTxt,
                    'Room to wall: ' + f_room(roomNow)
                ];

                cards.push({
                    barIndex: i,
                    time: times[i],
                    price: entry,
                    isBull: isL,
                    type: 'entry',
                    lines: entTxtLines,
                    color: isL ? colBuy : colSell,
                    entry: entry,
                    sl: slP,
                    t1: t1P,
                    t2: t2P,
                    max: tMP,
                    roomNow: roomNow
                });
            }
        }

        // Active levels for open trade on last bar
        if (tDir !== 0) {
            const isL = tDir === 1;
            const targetEnd = zoneToMax ? tMP : t2P;
            if (showZones) {
                boxes.push({
                    entryTime: times[tradeStartBar],
                    exitTime: times[n - 1],
                    topReward: Math.max(entry, targetEnd),
                    botReward: Math.min(entry, targetEnd),
                    topRisk: Math.max(entry, slP),
                    botRisk: Math.min(entry, slP),
                    colTgt: isL ? colTgtBuy : colTgtSell,
                    colSl: isL ? colSlBuy : colSlSell,
                    entryPrice: entry,
                    exitPrice: closes[n - 1],
                    netPnl: (tDir * (closes[n - 1] - entry) / entry * 100.0) - costPct
                });
            }

            activeLevels = {
                entry: entry,
                sl: slP,
                t1: t1P,
                t2: t2P,
                max: tMP,
                isBull: isL,
                trailActive: trailActive,
                time: times[n - 1]
            };
        }

        // Real-Time HUD Metrics
        const lastIdx = n - 1;
        const curBuyPct = buyPctArr[lastIdx] != null ? buyPctArr[lastIdx] : 50.0;
        const curSellPct = sellPctArr[lastIdx] != null ? sellPctArr[lastIdx] : 50.0;
        const curRank = rankArr[lastIdx] != null ? Math.round(rankArr[lastIdx]) : 50;
        const curGrade = curRank >= 75 ? 'Strong' : (curRank >= 40 ? 'Moderate' : 'Weak');
        const curClose = closes[lastIdx];
        const pnlNow = tDir !== 0 ? (tDir * (curClose - entry) / entry * 100.0) : 0;
        const netNow = pnlNow - costPct;

        const haveLv = tDir !== 0 || lastDir !== 0;
        const tradeTxt = tDir === 0
            ? '● Flat'
            : (tDir === 1 ? '▲ LONG @ ₹' : '▼ SHORT @ ₹') + entry.toFixed(2) + '  ' + (pnlNow >= 0 ? '+' : '') + pnlNow.toFixed(1) + '% (net ' + (netNow >= 0 ? '+' : '') + netNow.toFixed(1) + '%)';

        const roomTxt = '▲ ' + f_room(lastRoomRL) + (buyerMode ? '' : '  ▼ ' + f_room(lastRoomRS)) + '  (need ' + minRoomR.toFixed(1) + 'R)';
        const roomCol = lastRoomRL >= minRoomR ? colWin : colLoss;

        const hnT = lastDir === 1 ? lTot : (lastDir === -1 ? sTot : 0);
        const hT1 = lastDir === 1 ? l1 : (lastDir === -1 ? s1 : 0);
        const hT2 = lastDir === 1 ? l2 : (lastDir === -1 ? s2 : 0);
        const hTM = lastDir === 1 ? lM : (lastDir === -1 ? sM : 0);

        const curFadeN = tDir === 1 ? lastFadeLongN : (tDir === -1 ? lastFadeShortN : 0);
        const curFadeT = tDir === 1 ? lastFadeLongT : (tDir === -1 ? lastFadeShortT : '');
        const fadeTxt = tDir !== 0 ? (curFadeN + '/7  ' + curFadeT) : '-';

        const avgNet = nTrades > 0 ? (sumNet / nTrades) : 0;
        const resTxt = nTrades === 0
            ? 'no finished trades yet'
            : nWin + '/' + nTrades + ' net wins · ' + (sumNet >= 0 ? '+' : '') + sumNet.toFixed(1) + '% · avg ' + (avgNet >= 0 ? '+' : '') + avgNet.toFixed(1) + '%';
        const resCol = nTrades === 0 ? '#94A3B8' : (sumNet >= 0 ? colWin : colLoss);

        const nowMs = Date.now();
        const lastCandleMs = times[lastIdx] > 1e11 ? times[lastIdx] : times[lastIdx] * 1000;
        const lagMin = Math.max(0, (nowMs - lastCandleMs) / 60000);
        const dataTxt = lagMin <= 5 ? 'Live' : (lagMin < 180 ? 'OK (' + Math.round(lagMin) + 'm lag)' : 'Market closed');
        const dataCol = lagMin > 6 && lagMin < 180 ? '#FF9800' : '#E2E8F0';

        const hudData = {
            buyPct: curBuyPct,
            sellPct: curSellPct,
            rank: curRank,
            grade: curGrade,
            status: tDir === 1 ? '▲ LONG active' : (tDir === -1 ? '▼ SHORT active' : '● Flat'),
            tradeTxt: tradeTxt,
            entry: haveLv ? entry : null,
            stop: haveLv ? slP : null,
            stopTxt: haveLv ? ('₹' + slP.toFixed(2) + (trailActive && tDir !== 0 ? '  (trailing)' : '')) : '-',
            t1: haveLv ? t1P : null,
            t1Txt: haveLv ? ('₹' + t1P.toFixed(2) + ' · ' + f_hit(hT1, hnT)) : '-',
            t2: haveLv ? t2P : null,
            t2Txt: haveLv ? ('₹' + t2P.toFixed(2) + ' · ' + f_hit(hT2, hnT)) : '-',
            max: haveLv ? tMP : null,
            maxTxt: haveLv ? ('₹' + tMP.toFixed(2) + ' · ' + f_hit(hTM, hnT)) : '-',
            roomTxt: roomTxt,
            roomCol: roomCol,
            fadeTxt: fadeTxt,
            fadeCol: curFadeN >= fadeNeedT1 ? colLoss : '#94A3B8',
            resTxt: resTxt,
            resCol: resCol,
            dataTxt: dataTxt,
            dataCol: dataCol,
            pnlNow: tDir !== 0 ? pnlNow : null,
            totalTrades: nTrades,
            netWins: nWin,
            sumNet: sumNet,
            lastEvent: lastEvent,
            tblPosIn: tblPosIn,
            showTable: showTable
        };

        // Standardized signals list for backward compatibility
        const signals = cards.map(c => ({
            time: c.time,
            price: c.price,
            type: c.type === 'entry' ? (c.isBull ? 'bullish_entry' : 'bearish_entry') : (c.type === 'exit' ? 'exit_sl' : 'target_hit'),
            text: c.lines[0],
            color: c.color,
            entry: c.entry,
            sl: c.sl,
            t1: c.t1,
            t2: c.t2
        }));

        return {
            type: 'bss_pro_v5',
            emaF: emaF,
            emaS: emaS,
            showRibbon: showRibbon,
            showZones: showZones,
            colBuy: colBuy,
            colSell: colSell,
            colSlBuy: colSlBuy,
            colSlSell: colSlSell,
            colTgtBuy: colTgtBuy,
            colTgtSell: colTgtSell,
            trailSeries: trailSeries,
            cards: cards,
            boxes: boxes,
            activeLevels: activeLevels,
            signals: signals,
            hudData: hudData,
            candles: candles
        };
    }

    function computeBSSProV3(candles, rawInputs, stationType) {
        const res = computeBSSProV5(candles, rawInputs, stationType);
        if (res) res.type = 'bss_pro_v3';
        return res;
    }

    // ── Universal Pine Script Runner & Transpiler ────────────────────────────────
    function computeGenericPineScript(candles, scriptCode, inputs) {
        const n = candles.length;
        if (n < 5) return null;

        // Route BSS Pro v3 indicator script
        const isBSSPro = /BSS\s*Pro/i.test(scriptCode) || /Buyer\/Seller Strength/i.test(scriptCode);
        if (isBSSPro) {
            return computeBSSProV5(candles, inputs);
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
            } else if (id === 'bss_pro_v3' || id === 'bss_pro_v5' || /BSS\s*Pro/i.test(ind.name || '') || /BSS\s*Pro/i.test(ind.code || '')) {
                res = computeBSSProV5(station.rawCandles, ind.inputs || state.inputs, st);
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
            } else if (res.type === 'bss_pro_v3' || res.type === 'bss_pro_v5') {
                renderBSSProV5Overlay(ctx, res, timeScale, candleSeries, W, H);
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

        function renderBSSProV5Overlay(ctx, res, timeScale, candleSeries, W, H) {
        const { emaF, emaS, showRibbon, showZones, colBuy, colSell, colSlBuy, colSlSell, colTgtBuy, colTgtSell, trailSeries, cards, boxes, activeLevels, hudData, candles } = res;
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
            ctx.strokeStyle = colSlBuy || '#FF9800';
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

        // 3. Shaded Risk / Reward Zone Boxes
        if (showZones !== false && boxes && boxes.length > 0) {
            for (const b of boxes) {
                const x1 = timeScale.timeToCoordinate(b.entryTime);
                const x2 = timeScale.timeToCoordinate(b.exitTime);
                if (x1 == null || x2 == null) continue;
                if ((x1 < -50 && x2 < -50) || (x1 > W + 50 && x2 > W + 50)) continue;

                const boxW = Math.max(2, x2 - x1);

                // Reward Zone
                const yRewTop = candleSeries.priceToCoordinate(b.topReward);
                const yRewBot = candleSeries.priceToCoordinate(b.botReward);
                if (yRewTop != null && yRewBot != null) {
                    const rY = Math.min(yRewTop, yRewBot);
                    const rH = Math.max(2, Math.abs(yRewBot - yRewTop));
                    ctx.fillStyle = hexToRgba(b.colTgt || '#00C853', 0.12);
                    ctx.fillRect(x1, rY, boxW, rH);
                    ctx.strokeStyle = hexToRgba(b.colTgt || '#00C853', 0.40);
                    ctx.lineWidth = 1;
                    ctx.strokeRect(x1, rY, boxW, rH);
                }

                // Risk Zone
                const yRiskTop = candleSeries.priceToCoordinate(b.topRisk);
                const yRiskBot = candleSeries.priceToCoordinate(b.botRisk);
                if (yRiskTop != null && yRiskBot != null) {
                    const sY = Math.min(yRiskTop, yRiskBot);
                    const sH = Math.max(2, Math.abs(yRiskBot - yRiskTop));
                    ctx.fillStyle = hexToRgba(b.colSl || '#FF9800', 0.12);
                    ctx.fillRect(x1, sY, boxW, sH);
                    ctx.strokeStyle = hexToRgba(b.colSl || '#FF9800', 0.40);
                    ctx.lineWidth = 1;
                    ctx.strokeRect(x1, sY, boxW, sH);
                }

                // Diagonal entry-to-exit connector line
                const yEntry = candleSeries.priceToCoordinate(b.entryPrice);
                const yExit = candleSeries.priceToCoordinate(b.exitPrice);
                if (yEntry != null && yExit != null) {
                    ctx.save();
                    ctx.setLineDash([3, 3]);
                    ctx.lineWidth = 1.5;
                    ctx.strokeStyle = b.netPnl >= 0 ? '#00E676' : '#FF5252';
                    ctx.beginPath();
                    ctx.moveTo(x1, yEntry);
                    ctx.lineTo(x2, yExit);
                    ctx.stroke();
                    ctx.restore();
                }
            }
        }

        // 4. Trade Entry, Exit & Target Multi-line Signal Cards
        if (cards && cards.length > 0) {
            ctx.save();
            let lastBarIdx = -100;
            let slot = 0;

            for (const card of cards) {
                const x = timeScale.timeToCoordinate(card.time);
                if (x == null || x < -60 || x > W + 60) continue;
                const y = candleSeries.priceToCoordinate(card.price);
                if (y == null) continue;

                // Handle label stacking for crowded candles
                if (Math.abs(card.barIndex - lastBarIdx) <= 3) {
                    slot += 1;
                } else {
                    slot = 0;
                }
                lastBarIdx = card.barIndex;

                const lines = card.lines || [];
                if (lines.length === 0) continue;

                // Measure required card width
                ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                let maxW = 0;
                for (const ln of lines) {
                    const w = ctx.measureText(ln).width;
                    if (w > maxW) maxW = w;
                }
                const cardW = Math.max(76, maxW + 16);
                const lineH = 15;
                const cardH = lines.length * lineH + 10;
                const cardX = x - cardW / 2;

                let cardY = 0;
                let isAbove = false;

                if (card.type === 'entry') {
                    if (card.isBull) {
                        cardY = y + 14 + (slot * 12);
                        isAbove = false;
                    } else {
                        cardY = y - cardH - 14 - (slot * 12);
                        isAbove = true;
                    }
                } else if (card.type === 'exit') {
                    if (card.isAbove) {
                        cardY = y - cardH - 14 - (slot * 12);
                        isAbove = true;
                    } else {
                        cardY = y + 14 + (slot * 12);
                        isAbove = false;
                    }
                } else {
                    // target_hit
                    cardY = card.isBull ? (y - cardH - 10 - slot * 8) : (y + 10 + slot * 8);
                    isAbove = card.isBull;
                }

                // Draw background card with rounded corners and drop shadow
                ctx.shadowColor = 'rgba(0, 0, 0, 0.55)';
                ctx.shadowBlur = 8;
                ctx.fillStyle = 'rgba(19, 23, 34, 0.94)';
                drawRoundedRect(ctx, cardX, cardY, cardW, cardH, 5);
                ctx.fill();
                ctx.shadowBlur = 0;

                // Border matching badge theme
                ctx.strokeStyle = card.color || '#2A2E39';
                ctx.lineWidth = 1;
                drawRoundedRect(ctx, cardX, cardY, cardW, cardH, 5);
                ctx.stroke();

                // Pointer triangle pointing to candle
                ctx.fillStyle = card.color || '#2A2E39';
                ctx.beginPath();
                if (isAbove) {
                    ctx.moveTo(x, cardY + cardH + 5);
                    ctx.lineTo(x - 4, cardY + cardH);
                    ctx.lineTo(x + 4, cardY + cardH);
                } else {
                    ctx.moveTo(x, cardY - 5);
                    ctx.lineTo(x - 4, cardY);
                    ctx.lineTo(x + 4, cardY);
                }
                ctx.closePath();
                ctx.fill();

                // Render lines of text
                let textY = cardY + 14;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';

                for (let k = 0; k < lines.length; k++) {
                    const ln = lines[k];
                    if (k === 0) {
                        // Title Line
                        ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                        ctx.fillStyle = card.color || '#ffffff';
                    } else if (k === 1) {
                        // Price Line
                        ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                        ctx.fillStyle = '#ffffff';
                    } else if (k === 2) {
                        // Metrics / PnL Line
                        ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                        if (ln.includes('+')) ctx.fillStyle = '#00E676';
                        else if (ln.includes('-')) ctx.fillStyle = '#FF5252';
                        else ctx.fillStyle = '#CBD5E1';
                    } else {
                        // Room / Reason Line
                        ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                        ctx.fillStyle = '#94A3B8';
                    }
                    ctx.fillText(ln, x, textY);
                    textY += lineH;
                }
            }
            ctx.restore();
        }

        // 5. Active Trade Level Labels on the right side
        if (activeLevels) {
            ctx.save();
            ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';

            const rightX = Math.min(W - 120, timeScale.timeToCoordinate(activeLevels.time) + 8);
            if (rightX > 100 && rightX < W) {
                const drawSidePill = (price, text, col) => {
                    const y = candleSeries.priceToCoordinate(price);
                    if (y == null) return;
                    const txtW = ctx.measureText(text).width;
                    ctx.fillStyle = 'rgba(19, 23, 34, 0.9)';
                    drawRoundedRect(ctx, rightX, y - 9, txtW + 12, 18, 3);
                    ctx.fill();
                    ctx.strokeStyle = col;
                    ctx.lineWidth = 1;
                    drawRoundedRect(ctx, rightX, y - 9, txtW + 12, 18, 3);
                    ctx.stroke();
                    ctx.fillStyle = col;
                    ctx.fillText(text, rightX + 6, y);
                };

                drawSidePill(activeLevels.entry, 'ENTRY ₹' + activeLevels.entry.toFixed(2), '#94A3B8');
                drawSidePill(activeLevels.sl, (activeLevels.isBull ? 'BUY SL ' : 'SELL SL ') + '₹' + activeLevels.sl.toFixed(2), colSlBuy);
                drawSidePill(activeLevels.t1, 'T1 ₹' + activeLevels.t1.toFixed(2), colTgtBuy);
                drawSidePill(activeLevels.t2, 'T2 ₹' + activeLevels.t2.toFixed(2), colTgtSell);
                drawSidePill(activeLevels.max, 'MAX ₹' + activeLevels.max.toFixed(2), colTgtBuy);
            }
            ctx.restore();
        }

        // 6. BSS Pro v5 12-Row HUD Information Panel
        if (hudData && hudData.showTable !== false) {
            const h = hudData;
            const cardW = 240;
            const cardH = 248;
            let startX = 16;
            let startY = 16;
            const pos = h.tblPosIn || 'Bottom left';
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
            ctx.fillText('BSS PRO v5 · Buyer / Seller Strength', startX + cardW / 2, startY + 11);

            let curY = startY + 31;
            const rowH = 17;
            function drawRow(label, value, valColor) {
                ctx.fillStyle = 'rgba(255, 255, 255, 0.50)';
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
            drawRow('TRADE', h.tradeTxt || h.status, statCol);

            drawRow('STOP', h.stopTxt || '-', colSlBuy);
            drawRow('T1', h.t1Txt || '-', colTgtBuy);
            drawRow('T2', h.t2Txt || '-', colTgtSell);
            drawRow('MAX', h.maxTxt || '-', colTgtBuy);
            drawRow('ROOM', h.roomTxt || '-', h.roomCol || '#94A3B8');
            drawRow('FADE', h.fadeTxt || '-', h.fadeCol || '#94A3B8');
            drawRow('RESULTS', h.resTxt || '-', h.resCol || '#94A3B8');
            drawRow('DATA', h.dataTxt || 'Live', h.dataCol || '#E2E8F0');

            ctx.restore();
        }
    }

    function renderBSSProV3Overlay(ctx, res, timeScale, candleSeries, W, H) {
        return renderBSSProV5Overlay(ctx, res, timeScale, candleSeries, W, H);
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
        computeBSSProV5,
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
