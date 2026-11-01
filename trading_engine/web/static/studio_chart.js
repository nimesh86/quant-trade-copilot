/**
 * Studio Chart Manager (TradingView Lightweight Charts Integration)
 * Renders dual Spot and Option candlestick charts with dynamic indicator overlays,
 * support/resistance lines, and buy/sell signal markers.
 *
 * Implements multi-scale separation:
 * - Right Price Scale: Candlesticks & Price Overlays (EMAs, Supertrend, S/R, Box)
 * - Left Price Scale: Oscillators & Volume Pressure (RSI, ATR, Buyer %)
 */
class StudioChartManager {
    constructor(containerId, onCrosshairHover) {
        this.container = document.getElementById(containerId);
        this.onCrosshairHover = onCrosshairHover;
        this.chart = null;
        this.candleSeries = null;
        this.plotSeriesMap = {};
        this.plotDataMap = {};
        this.markers = [];
        this.currentMode = "spot"; // "spot", "ce", "pe"
        this.candlesData = [];

        this._initChart();
        this._setupResizeObserver();
    }

    _parseTime(timeVal) {
        if (typeof timeVal === "number") {
            return timeVal > 1e11 ? Math.floor(timeVal / 1000) : timeVal;
        }
        if (!timeVal) return Math.floor(Date.now() / 1000);

        const str = String(timeVal).trim();
        // Regex parse YYYY-MM-DD HH:MM:SS directly into UTC epoch to preserve IST clock time (09:15)
        const match = str.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):?(\d{2})?/);
        if (match) {
            // Truncate to exact 1-minute candle boundary (seconds = 0)
            const d = new Date(Date.UTC(+match[1], +match[2] - 1, +match[3], +match[4], +match[5], 0));
            return Math.floor(d.getTime() / 1000);
        }

        const iso = str.replace(" ", "T");
        const parsed = new Date(iso).getTime();
        if (!isNaN(parsed) && parsed > 0) {
            return Math.floor(parsed / 60000) * 60;
        }

        return Math.floor(Date.now() / 1000);
    }

    _initChart() {
        if (!window.LightweightCharts) {
            console.error("LightweightCharts library not loaded.");
            return;
        }

        const chartOptions = {
            layout: {
                background: { color: "#070b14" },
                textColor: "#94a3b8",
                fontSize: 11,
                fontFamily: "'JetBrains Mono', 'Inter', monospace"
            },
            localization: {
                timeFormatter: (time) => {
                    const d = new Date(time * 1000);
                    const hrs = String(d.getUTCHours()).padStart(2, "0");
                    const mins = String(d.getUTCMinutes()).padStart(2, "0");
                    return `${hrs}:${mins}`;
                },
                dateFormat: "yyyy-MM-dd"
            },
            grid: {
                vertLines: { color: "rgba(255, 255, 255, 0.04)" },
                horzLines: { color: "rgba(255, 255, 255, 0.04)" }
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: "#00f0ff", width: 1, style: 3 },
                horzLine: { color: "#00f0ff", width: 1, style: 3 }
            },
            timeScale: {
                borderColor: "#1a2542",
                timeVisible: true,
                secondsVisible: false,
                barSpacing: 9,
                minBarSpacing: 2,
                rightOffset: 5,
                tickMarkFormatter: (time) => {
                    const d = new Date(time * 1000);
                    const hrs = String(d.getUTCHours()).padStart(2, "0");
                    const mins = String(d.getUTCMinutes()).padStart(2, "0");
                    return `${hrs}:${mins}`;
                }
            },
            rightPriceScale: {
                borderColor: "#1a2542",
                autoScale: true,
                scaleMargins: {
                    top: 0.10,
                    bottom: 0.15
                }
            },
            leftPriceScale: {
                visible: false // Hide duplicate left axis so right price scale cleanly represents candles
            }
        };

        this.chart = LightweightCharts.createChart(this.container, chartOptions);

        // Candlestick Series explicitly bound to right price scale
        this.candleSeries = this.chart.addCandlestickSeries({
            priceScaleId: "right",
            upColor: "#10b981",
            downColor: "#ef4444",
            borderUpColor: "#10b981",
            borderDownColor: "#ef4444",
            wickUpColor: "#10b981",
            wickDownColor: "#ef4444",
            priceFormat: {
                type: "price",
                precision: 2,
                minMove: 0.05
            }
        });

        // Crosshair Hover Sync for Variable Watch Table
        this.chart.subscribeCrosshairMove((param) => {
            if (!param || !param.time || !param.seriesPrices) return;
            const barPrice = param.seriesPrices.get(this.candleSeries);
            if (barPrice && this.onCrosshairHover) {
                this.onCrosshairHover(param.time, barPrice);
            }
        });
    }

    _setupResizeObserver() {
        if (window.ResizeObserver && this.container) {
            const ro = new ResizeObserver((entries) => {
                for (let entry of entries) {
                    const { width, height } = entry.contentRect;
                    if (this.chart && width > 0 && height > 0) {
                        this.chart.applyOptions({ width, height });
                    }
                }
            });
            ro.observe(this.container);
        }
    }

    resize() {
        if (this.chart && this.container) {
            const rect = this.container.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                this.chart.applyOptions({ width: rect.width, height: rect.height });
            }
        }
    }

    clearPlotSeries() {
        if (!this.chart) return;
        for (const [name, series] of Object.entries(this.plotSeriesMap)) {
            try {
                this.chart.removeSeries(series);
            } catch (e) {}
        }
        this.plotSeriesMap = {};
        this.plotDataMap = {};
    }

    truncatePlotsAndMarkers(maxTime) {
        if (maxTime == null || isNaN(maxTime)) return;

        // 1. Truncate signal markers strictly to maxTime
        if (this.markers && this.markers.length > 0) {
            this.markers = this.markers.filter(m => m.time <= maxTime);
            if (this.candleSeries) {
                this.candleSeries.setMarkers(this.markers);
            }
        }

        // 2. Truncate every indicator line series so no line extends past current candle!
        for (const [plotName, series] of Object.entries(this.plotSeriesMap)) {
            let pts = this.plotDataMap[plotName] || [];
            pts = pts.filter(p => p.time <= maxTime);
            this.plotDataMap[plotName] = pts;
            if (pts.length === 0) {
                try {
                    this.chart.removeSeries(series);
                } catch (e) {}
                delete this.plotSeriesMap[plotName];
                delete this.plotDataMap[plotName];
            } else {
                try {
                    series.setData(pts);
                } catch (e) {
                    console.warn(`Error setting series data for ${plotName}:`, e);
                }
            }
        }
    }

    setCandles(bars, mode = "spot") {
        this.clearPlotSeries();
        this.currentMode = mode;
        this.candlesData = bars || [];
        if (!this.candleSeries || this.candlesData.length === 0) return;

        const formatted = [];
        const plotsAccumulator = {}; // plotName -> Array<{ time, value }>

        for (let i = 0; i < this.candlesData.length; i++) {
            const b = this.candlesData[i];
            const isOpt = mode !== "spot";
            const prefix = isOpt ? "opt_" : "spot_";
            const t = this._parseTime(b.timestamp);

            const targetBar = isOpt ? (b.option_bar || b) : (b.spot_bar || b);
            const op = Number(targetBar[prefix + "open"] ?? targetBar.open ?? 100.0);
            const hi = Number(targetBar[prefix + "high"] ?? targetBar.high ?? 100.0);
            const lo = Number(targetBar[prefix + "low"] ?? targetBar.low ?? 100.0);
            const cl = Number(targetBar[prefix + "close"] ?? targetBar.close ?? 100.0);

            // Filter price outliers to prevent scale compression
            if (!isOpt && cl < 1000.0) continue;
            if (isOpt && cl > 5000.0) continue;

            if (!isNaN(t) && !isNaN(op) && !isNaN(hi) && !isNaN(lo) && !isNaN(cl)) {
                formatted.push({
                    time: t,
                    open: op,
                    high: Math.max(hi, op, cl),
                    low: Math.min(lo, op, cl),
                    close: cl
                });

                // Collect any historical indicator plots attached to this bar
                if (b.indicator_plots && typeof b.indicator_plots === "object") {
                    for (const [plotName, val] of Object.entries(b.indicator_plots)) {
                        if (typeof val !== "number" || isNaN(val)) continue;

                        const isOscillator = (
                            plotName.includes("rsi") ||
                            plotName.includes("atr") ||
                            plotName.includes("buyer_pct") ||
                            plotName.includes("seller_pct") ||
                            plotName.includes("delta")
                        );
                        if (isOscillator) continue;
                        if (!isOpt && val < 1000.0) continue;
                        if (isOpt && val > 1000.0) continue;

                        if (!plotsAccumulator[plotName]) {
                            plotsAccumulator[plotName] = [];
                        }
                        plotsAccumulator[plotName].push({ time: t, value: val });
                    }
                }
            }
        }

        // Sort ascending by time (required by Lightweight Charts)
        formatted.sort((a, b) => a.time - b.time);

        // Deduplicate timestamps if any
        const deduped = [];
        let lastT = null;
        for (const bar of formatted) {
            if (bar.time !== lastT) {
                deduped.push(bar);
                lastT = bar.time;
            }
        }

        this.candlesData = [...deduped];
        this.candleSeries.setData(deduped);

        const maxTime = deduped.length > 0 ? deduped[deduped.length - 1].time : null;

        // Truncate markers strictly to maxTime
        if (maxTime != null) {
            this.markers = this.markers.filter(m => m.time <= maxTime);
            this.candleSeries.setMarkers(this.markers);
        }

        // Apply synchronized indicator plot data from history
        for (const [plotName, pts] of Object.entries(plotsAccumulator)) {
            const cleanPts = [];
            let lastPtT = null;
            pts.sort((a, b) => a.time - b.time);
            for (const pt of pts) {
                if (maxTime != null && pt.time > maxTime) continue;
                if (pt.time !== lastPtT) {
                    cleanPts.push(pt);
                    lastPtT = pt.time;
                } else if (cleanPts.length > 0) {
                    cleanPts[cleanPts.length - 1].value = pt.value;
                }
            }

            if (cleanPts.length === 0) continue;

            let color = "#00f0ff";
            if (plotName.includes("slow")) color = "#f59e0b";
            if (plotName.includes("res") || plotName.includes("upper")) color = "#ef4444";
            if (plotName.includes("sup") || plotName.includes("lower")) color = "#10b981";

            this.plotSeriesMap[plotName] = this.chart.addLineSeries({
                color: color,
                lineWidth: 1.5,
                title: plotName,
                priceScaleId: "right",
                priceLineVisible: false
            });

            this.plotDataMap[plotName] = cleanPts;
            try {
                this.plotSeriesMap[plotName].setData(cleanPts);
            } catch (e) {
                console.warn(`Error setting series data for ${plotName}:`, e);
            }
        }

        // Maintain static zoom level and scroll smoothly to the active candle
        this.scrollToCurrent();
    }

    scrollToCurrent() {
        if (this.chart) {
            this.chart.timeScale().scrollToPosition(4, false);
            this.chart.priceScale("right").applyOptions({ autoScale: true });
        }
    }

    addOrUpdateCandle(b, mode = "spot") {
        if (!this.candleSeries || !b) return;
        this.currentMode = mode;
        const isOpt = mode !== "spot";
        const prefix = isOpt ? "opt_" : "spot_";
        const t = this._parseTime(b.timestamp);

        const targetBar = isOpt ? (b.option_bar || b) : (b.spot_bar || b);
        const op = Number(targetBar[prefix + "open"] ?? targetBar.open ?? 100.0);
        const hi = Number(targetBar[prefix + "high"] ?? targetBar.high ?? 100.0);
        const lo = Number(targetBar[prefix + "low"] ?? targetBar.low ?? 100.0);
        const cl = Number(targetBar[prefix + "close"] ?? targetBar.close ?? 100.0);

        // Sanity safeguard: Prevent option prices (~150) contaminating spot chart (~24,000) or vice versa
        if (!isOpt && (cl < 1000.0 || op < 1000.0)) return;
        if (isOpt && (cl > 5000.0 || op > 5000.0)) return;

        if (!isNaN(t) && !isNaN(op) && !isNaN(hi) && !isNaN(lo) && !isNaN(cl)) {
            const candle = {
                time: t,
                open: op,
                high: Math.max(hi, op, cl),
                low: Math.min(lo, op, cl),
                close: cl
            };

            if (!this.candlesData || this.candlesData.length === 0) {
                this.candlesData = [candle];
                this.candleSeries.setData(this.candlesData);
                return;
            }

            const last = this.candlesData[this.candlesData.length - 1];
            if (t > last.time) {
                this.candlesData.push(candle);
                try {
                    this.candleSeries.update(candle);
                } catch (e) {
                    this.candleSeries.setData(this.candlesData);
                }
            } else if (t === last.time) {
                this.candlesData[this.candlesData.length - 1] = candle;
                try {
                    this.candleSeries.update(candle);
                } catch (e) {
                    this.candleSeries.setData(this.candlesData);
                }
            } else {
                // Stepping backward: rewind candle data
                while (this.candlesData.length > 0 && this.candlesData[this.candlesData.length - 1].time > t) {
                    this.candlesData.pop();
                }
                if (this.candlesData.length > 0 && this.candlesData[this.candlesData.length - 1].time === t) {
                    this.candlesData[this.candlesData.length - 1] = candle;
                } else {
                    this.candlesData.push(candle);
                }
                this.candleSeries.setData(this.candlesData);
                this.truncatePlotsAndMarkers(t);
            }
        }
    }

    updateIndicatorPlots(timeVal, plots) {
        if (!plots || !this.chart) return;
        const t = this._parseTime(timeVal);
        if (isNaN(t)) return;

        // Ensure no line extends past current tick time
        this.truncatePlotsAndMarkers(t);

        for (const [plotName, val] of Object.entries(plots)) {
            if (typeof val !== "number" || isNaN(val)) continue;

            const isOscillator = (
                plotName.includes("rsi") ||
                plotName.includes("atr") ||
                plotName.includes("buyer_pct") ||
                plotName.includes("seller_pct") ||
                plotName.includes("delta")
            );

            // Normalized oscillators (0-100) are dynamically displayed in the legend & Variable Watch.
            // Do NOT plot them as lines across the price space to keep candles and price scale clean.
            if (isOscillator) {
                continue;
            }

            // Prevent price scale collision: do not plot Spot ~24,000 indicators on Option chart (~150)
            const isSpotMode = this.currentMode === "spot";
            if (isSpotMode && val < 1000.0) {
                continue; // Skip option prices when viewing Spot Index
            }
            if (!isSpotMode && val > 1000.0) {
                continue; // Skip spot lines (e.g. 24,200 EMA) when viewing Option contract!
            }

            if (!this.plotSeriesMap[plotName]) {
                let color = "#00f0ff";
                if (plotName.includes("slow")) color = "#f59e0b";
                if (plotName.includes("res") || plotName.includes("upper")) color = "#ef4444";
                if (plotName.includes("sup") || plotName.includes("lower")) color = "#10b981";

                this.plotSeriesMap[plotName] = this.chart.addLineSeries({
                    color: color,
                    lineWidth: 1.5,
                    title: plotName,
                    priceScaleId: "right",
                    priceLineVisible: false
                });
                this.plotDataMap[plotName] = [];
            }

            const seriesData = this.plotDataMap[plotName] || [];
            const lastPt = seriesData.length > 0 ? seriesData[seriesData.length - 1] : null;

            if (!lastPt || t > lastPt.time) {
                seriesData.push({ time: t, value: val });
                this.plotDataMap[plotName] = seriesData;
                try {
                    this.plotSeriesMap[plotName].update({ time: t, value: val });
                } catch (e) {
                    this.plotSeriesMap[plotName].setData(seriesData);
                }
            } else if (t === lastPt.time) {
                lastPt.value = val;
                try {
                    this.plotSeriesMap[plotName].update(lastPt);
                } catch (e) {
                    this.plotSeriesMap[plotName].setData(seriesData);
                }
            } else {
                const filtered = seriesData.filter(p => p.time < t);
                filtered.push({ time: t, value: val });
                this.plotDataMap[plotName] = filtered;
                this.plotSeriesMap[plotName].setData(filtered);
            }
        }
    }

    addSignalMarker(timeVal, signal) {
        if (!signal || !this.candleSeries) return;
        const t = this._parseTime(timeVal);
        if (isNaN(t)) return;

        // Prune any existing marker at this exact timestamp or future
        this.markers = this.markers.filter(m => m.time < t);

        const isBuy = String(signal.action).startsWith("BUY");

        const marker = {
            time: t,
            position: isBuy ? "belowBar" : "aboveBar",
            color: isBuy ? "#10b981" : "#ef4444",
            shape: isBuy ? "arrowUp" : "arrowDown",
            text: `${signal.action} @ ${Number(signal.trigger_price).toFixed(2)}`
        };

        this.markers.push(marker);
        this.markers.sort((a, b) => a.time - b.time);
        this.candleSeries.setMarkers(this.markers);
    }

    clearMarkers() {
        this.markers = [];
        if (this.candleSeries) {
            this.candleSeries.setMarkers([]);
        }
    }

    fitContent() {
        if (this.chart) {
            this.chart.timeScale().fitContent();
            this.chart.priceScale("right").applyOptions({ autoScale: true });
        }
    }
}
