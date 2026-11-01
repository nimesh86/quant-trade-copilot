/**
 * Quant Studio Webview Chart Controller
 * Coordinates TradingView Lightweight Charts rendering, dual Spot/Option series,
 * dynamic indicator lines, markers, and WebSocket synchronization with debug_runner.py.
 */

(function () {
  const vscode = typeof acquireVsCodeApi === 'function' ? acquireVsCodeApi() : null;

  // DOM Elements
  const spotChartEl = document.getElementById('spot-chart-container');
  const optChartEl = document.getElementById('opt-chart-container');
  const scrubber = document.getElementById('replay-scrubber');
  const badgeEl = document.getElementById('scrubber-badge');
  const playBtn = document.getElementById('btn-play');
  const stepTickBtn = document.getElementById('btn-step-tick');
  const stepBarBtn = document.getElementById('btn-step-bar');
  const speedSelect = document.getElementById('speed-select');
  const sessionStatusEl = document.getElementById('session-status');
  const spotHudEl = document.getElementById('spot-hud');
  const optHudEl = document.getElementById('opt-hud');

  // Date Range & Backtest DOM Elements
  const dateStartInput = document.getElementById('date-start');
  const dateEndInput = document.getElementById('date-end');
  const loadDateBtn = document.getElementById('btn-load-date');
  const runBacktestBtn = document.getElementById('btn-run-backtest');
  const toggleBacktestBtn = document.getElementById('btn-toggle-backtest');
  const closeDrawerBtn = document.getElementById('btn-close-drawer');
  const backtestDrawer = document.getElementById('backtest-drawer');

  // Scorecard Metrics DOM Elements
  const btWinrateEl = document.getElementById('bt-winrate');
  const btTradesEl = document.getElementById('bt-trades');
  const btPnlInrEl = document.getElementById('bt-pnl-inr');
  const btGrossInrEl = document.getElementById('bt-gross-inr');
  const btTotalChargesEl = document.getElementById('bt-total-charges');
  const btPnlPtsEl = document.getElementById('bt-pnl-pts');
  const btPfEl = document.getElementById('bt-pf');
  const btDrawdownEl = document.getElementById('bt-drawdown');
  const btTradesBody = document.getElementById('bt-trades-body');

  // Charges Configuration DOM Elements
  const cfgBrokerPreset = document.getElementById('cfg-broker-preset');
  const cfgBrokerage = document.getElementById('cfg-brokerage');
  const cfgLotSize = document.getElementById('cfg-lotsize');
  const cfgStt = document.getElementById('cfg-stt');
  const cfgExchange = document.getElementById('cfg-exchange');
  const cfgGst = document.getElementById('cfg-gst');
  const btnToggleCharges = document.getElementById('btn-toggle-charges');
  const chargesConfigSection = document.getElementById('charges-config-section');
  const chargesToggleHead = document.getElementById('charges-toggle-head');
  const lblBrokerName = document.getElementById('lbl-broker-name');

  // Bifurcation Summary DOM Elements
  const bifBrokerageEl = document.getElementById('bif-brokerage');
  const bifSttEl = document.getElementById('bif-stt');
  const bifExchangeEl = document.getElementById('bif-exchange');
  const bifGstEl = document.getElementById('bif-gst');
  const bifStampEl = document.getElementById('bif-stamp');
  const bifSebiEl = document.getElementById('bif-sebi');

  // Trade Navigation Indicator Toast
  const tradeNavToast = document.getElementById('trade-nav-toast');
  const toastTitle = document.getElementById('toast-title');
  const toastBody = document.getElementById('toast-body');
  const toastClose = document.getElementById('toast-close');

  let ws = null;
  let isPlaying = false;
  let currentTick = 0;
  let totalTicks = 0;
  let port = 8765;
  let activeSymbol = 'NIFTY';

  function getLotSizeForSymbol(sym) {
    if (!sym) return 65;
    const u = sym.toUpperCase();

    // 1. Prominent F&O stock specifications (checked first to prevent bank/fin stocks colliding with index keywords)
    const fnoStocks = {
      'RELIANCE': 250, 'TCS': 175, 'INFY': 400, 'HDFCBANK': 550,
      'ICICIBANK': 700, 'SBIN': 750, 'BHARTIARTL': 475, 'ITC': 1600,
      'LT': 175, 'AXISBANK': 625, 'KOTAKBANK': 400, 'TATAMOTORS': 575
    };
    for (const [stk, lot] of Object.entries(fnoStocks)) {
      if (u.includes(stk)) return lot;
    }

    // 2. Major Index Specifications (check sub-indices before generic NIFTY)
    if (u.includes('BANKEX')) return 30;
    if (u.includes('BANKNIFTY') || u.includes('CNXBAN') || u.includes('NIFTY BANK') || u.startsWith('BANK')) return 30;
    if (u.includes('SENSEX') || u.includes('BSESEN') || u.includes('BSX')) return 20;
    if (u.includes('FINNIFTY') || u.includes('NIFFIN') || u.includes('FIN SERVICE')) return 60;
    if (u.includes('MIDCPNIFTY') || u.includes('NIFMID') || u.includes('MIDCAP') || u.includes('MID SELECT')) return 120;
    if (u.includes('GIFT')) return 50;
    if (u.includes('NIFTY')) return 65;

    return 1;
  }

  // TradingView Charts instances
  let spotChart = null;
  let spotSeries = null;
  let optChart = null;
  let optSeries = null;
  const spotPlotSeries = {};
  const optPlotSeries = {};

  const CHART_THEME = {
    layout: {
      background: { color: '#0f172a' },
      textColor: '#94a3b8',
      fontSize: 11,
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    },
    localization: {
      timeFormatter: (t) => {
        const d = new Date(t * 1000);
        const h = String(d.getUTCHours()).padStart(2, '0');
        const m = String(d.getUTCMinutes()).padStart(2, '0');
        return `${h}:${m}`;
      },
      dateFormat: 'yyyy-MM-dd',
    },
    grid: {
      vertLines: { color: 'rgba(51, 65, 85, 0.4)' },
      horzLines: { color: 'rgba(51, 65, 85, 0.4)' },
    },
    crosshair: {
      mode: 1,
      vertLine: { color: '#38bdf8', width: 1, style: 3 },
      horzLine: { color: '#38bdf8', width: 1, style: 3 },
    },
    rightPriceScale: {
      borderColor: '#334155',
      autoScale: true,
      scaleMargins: { top: 0.1, bottom: 0.1 },
    },
    timeScale: {
      borderColor: '#334155',
      timeVisible: true,
      secondsVisible: false,
      barSpacing: 9,
      rightOffset: 5,
      tickMarkFormatter: (t) => {
        const d = new Date(t * 1000);
        const h = String(d.getUTCHours()).padStart(2, '0');
        const m = String(d.getUTCMinutes()).padStart(2, '0');
        return `${h}:${m}`;
      },
    },
  };

  function initCharts() {
    if (!window.LightweightCharts) {
      console.error('TradingView Lightweight Charts library not loaded');
      return;
    }

    // Spot Chart
    spotChart = window.LightweightCharts.createChart(spotChartEl, {
      ...CHART_THEME,
      width: spotChartEl.clientWidth || 600,
      height: spotChartEl.clientHeight || 300,
    });
    spotSeries = spotChart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    // Option Chart
    optChart = window.LightweightCharts.createChart(optChartEl, {
      ...CHART_THEME,
      width: optChartEl.clientWidth || 600,
      height: optChartEl.clientHeight || 260,
    });
    optSeries = optChart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    // Synchronize visible logical range between Spot and Option charts in lockstep
    let isSyncingRange = false;
    spotChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (isSyncingRange || !range || !optChart) return;
      isSyncingRange = true;
      try {
        optChart.timeScale().setVisibleLogicalRange(range);
      } catch (e) {}
      isSyncingRange = false;
    });

    optChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (isSyncingRange || !range || !spotChart) return;
      isSyncingRange = true;
      try {
        spotChart.timeScale().setVisibleLogicalRange(range);
      } catch (e) {}
      isSyncingRange = false;
    });

    // Handle Resize
    const resizeObserver = new ResizeObserver(() => {
      if (spotChart && spotChartEl) {
        spotChart.applyOptions({ width: spotChartEl.clientWidth, height: spotChartEl.clientHeight });
      }
      if (optChart && optChartEl) {
        optChart.applyOptions({ width: optChartEl.clientWidth, height: optChartEl.clientHeight });
      }
    });
    resizeObserver.observe(spotChartEl);
    resizeObserver.observe(optChartEl);

    // Continuous Telemetry Sync: sends chart viewport & visible timeframe to debug runner & REST API
    let viewportSyncTimer = null;
    const syncViewportToBackend = () => {
      clearTimeout(viewportSyncTimer);
      viewportSyncTimer = setTimeout(() => {
        if (!optChart || !optChart.timeScale) return;
        const timeRange = optChart.timeScale().getVisibleRange();
        if (!timeRange || !timeRange.from || !timeRange.to) return;

        const formatTs = (ts) => {
          const d = new Date(ts * 1000);
          return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
        };

        const vpPayload = {
          visible_from: timeRange.from,
          visible_to: timeRange.to,
          visible_from_str: formatTs(timeRange.from),
          visible_to_str: formatTs(timeRange.to),
          timeframe: '1m',
          candle_count_in_view: Math.max(1, Math.round((timeRange.to - timeRange.from) / 60)),
          current_tick: currentTick,
        };

        sendCommand({
          action: 'sync_viewport',
          viewport: vpPayload
        });

        // Also post to REST API if FastAPI is listening
        try {
          fetch('http://127.0.0.1:8080/api/quant-studio/viewport', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(vpPayload)
          }).catch(() => {});
        } catch (e) {}
      }, 150);
    };

    optChart.timeScale().subscribeVisibleTimeRangeChange(syncViewportToBackend);
  }

  function getOrCreateLineSeries(chart, seriesStore, plotName, color = '#38bdf8') {
    if (!seriesStore[plotName]) {
      seriesStore[plotName] = chart.addLineSeries({
        color: color,
        lineWidth: 2,
        title: plotName.toUpperCase(),
        priceLineVisible: false,
        crosshairMarkerVisible: true,
      });
    }
    return seriesStore[plotName];
  }

  function parseCandleTime(timeVal) {
    if (typeof timeVal === 'number') {
      return timeVal > 1e11 ? Math.floor(timeVal / 60000) * 60 : Math.floor(timeVal / 60) * 60;
    }
    if (!timeVal) return Math.floor(Date.now() / 1000);

    const str = String(timeVal).trim();
    // Regex parse YYYY-MM-DD HH:MM:SS directly into UTC epoch to preserve exact market clock time (e.g. 09:15)
    const match = str.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):?(\d{2})?/);
    if (match) {
      // Truncate to exact 1-minute candle boundary (seconds = 0)
      const d = new Date(Date.UTC(+match[1], +match[2] - 1, +match[3], +match[4], +match[5], 0));
      return Math.floor(d.getTime() / 1000);
    }

    const iso = str.replace(' ', 'T');
    const parsed = new Date(iso).getTime();
    if (!isNaN(parsed) && parsed > 0) {
      return Math.floor(parsed / 60000) * 60;
    }

    return Math.floor(Date.now() / 1000);
  }

  function isPriceOverlay(plotName, val, isSpot = true) {
    if (typeof val !== 'number' || isNaN(val)) return false;
    const name = String(plotName).toLowerCase();

    // Normalized oscillators or percentage metrics (0-100) MUST NOT be plotted on the price scale
    if (
      name.includes('rsi') ||
      name.includes('buyer_pct') ||
      name.includes('seller_pct') ||
      name.includes('press') ||
      name.includes('atr') ||
      name.includes('delta') ||
      name.includes('pct') ||
      name.includes('volume') ||
      name.includes('vol') ||
      name.includes('oi')
    ) {
      return false;
    }

    // Spot Index indicators (EMA ~24,000, Supertrend ~24,000) belong to Spot price scale
    if (isSpot && val < 1000.0) return false;
    // Option indicators (10 - 2000) belong to Option strike price scale
    if (!isSpot && val >= 1000.0) return false;

    return true;
  }

  function updateIndicatorHud(plots) {
    if (!plots || typeof plots !== 'object') return;
    const hudParts = [];
    if (plots.rsi !== undefined && plots.rsi !== null) {
      hudParts.push(`RSI: ${Number(plots.rsi).toFixed(1)}`);
    }
    if (plots.buyer_pct !== undefined && plots.buyer_pct !== null) {
      hudParts.push(`Buyer: ${Number(plots.buyer_pct).toFixed(0)}%`);
    }
    if (plots.seller_pct !== undefined && plots.seller_pct !== null) {
      hudParts.push(`Seller: ${Number(plots.seller_pct).toFixed(0)}%`);
    }
    if (plots.atr !== undefined && plots.atr !== null) {
      hudParts.push(`ATR: ${Number(plots.atr).toFixed(1)}`);
    }

    if (spotHudEl && hudParts.length > 0) {
      spotHudEl.textContent = hudParts.join(' | ');
      spotHudEl.style.display = 'inline-block';
    }
  }

  function updateBadge(tickIdx, barIdx, timeStr) {
    let timeLabel = '';
    if (timeStr) {
      const parts = String(timeStr).split(' ');
      timeLabel = parts.length > 1 ? parts[1].slice(0, 8) : parts[0];
    }
    badgeEl.textContent = `Tick #${tickIdx} | Bar ${barIdx + 1} (${timeLabel})`;
    scrubber.value = tickIdx;
  }

  function renderHistoryBars(history) {
    if (!history || history.length === 0) return;
    const spotCandles = [];
    const optCandles = [];
    const spotHistoryPlots = {};
    const optHistoryPlots = {};

    history.forEach((h) => {
      const t = parseCandleTime(h.timestamp);
      const sOpen = Number(h.spot_open ?? h.open ?? 100);
      const sHigh = Number(h.spot_high ?? h.high ?? 100);
      const sLow = Number(h.spot_low ?? h.low ?? 100);
      const sClose = Number(h.spot_close ?? h.close ?? 100);

      const oOpen = Number(h.opt_open ?? h.open ?? 100);
      const oHigh = Number(h.opt_high ?? h.high ?? 100);
      const oLow = Number(h.opt_low ?? h.low ?? 100);
      const oClose = Number(h.opt_close ?? h.close ?? 100);

      if (!isNaN(t) && t > 0) {
        spotCandles.push({
          time: t,
          open: sOpen,
          high: Math.max(sHigh, sOpen, sClose),
          low: Math.min(sLow, sOpen, sClose),
          close: sClose,
        });
        optCandles.push({
          time: t,
          open: oOpen,
          high: Math.max(oHigh, oOpen, oClose),
          low: Math.min(oLow, oOpen, oClose),
          close: oClose,
        });
      }

      if (h.indicator_plots && typeof h.indicator_plots === 'object') {
        Object.entries(h.indicator_plots).forEach(([k, val]) => {
          if (typeof val === 'number' && !isNaN(val)) {
            if (isPriceOverlay(k, val, true)) {
              if (!spotHistoryPlots[k]) spotHistoryPlots[k] = [];
              spotHistoryPlots[k].push({ time: t, value: val });
            } else if (isPriceOverlay(k, val, false)) {
              if (!optHistoryPlots[k]) optHistoryPlots[k] = [];
              optHistoryPlots[k].push({ time: t, value: val });
            }
          }
        });
      }
    });

    spotSeries.setData(spotCandles);
    optSeries.setData(optCandles);

    const plotColors = {
      ema_fast: '#38bdf8',
      ema_slow: '#ec4899',
      supertrend: '#10b981',
      res_high: '#ef4444',
      sup_low: '#10b981',
      nearest_res: '#ef4444',
      nearest_sup: '#10b981',
    };

    Object.entries(spotHistoryPlots).forEach(([k, data]) => {
      const line = getOrCreateLineSeries(spotChart, spotPlotSeries, k, plotColors[k] || '#a855f7');
      line.setData(data);
    });
    Object.entries(optHistoryPlots).forEach(([k, data]) => {
      const line = getOrCreateLineSeries(optChart, optPlotSeries, k, plotColors[k] || '#a855f7');
      line.setData(data);
    });

    spotChart.timeScale().fitContent();
    optChart.timeScale().fitContent();
  }

  function handleInitMessage(msg) {
    totalTicks = msg.total_ticks || 1000;
    scrubber.max = totalTicks - 1;
    currentTick = msg.tick_cursor || 0;
    scrubber.value = currentTick;

    if (msg.symbol) {
      activeSymbol = msg.symbol;
    }
    const detectedLot = msg.lot_size || getLotSizeForSymbol(activeSymbol);
    if (cfgLotSize && !cfgLotSize.dataset.userModified) {
      cfgLotSize.value = String(detectedLot);
    }

    const dateDisplay = msg.start_date && msg.end_date && msg.start_date !== msg.end_date
      ? `${msg.start_date} to ${msg.end_date}`
      : (msg.date || 'Active');
    sessionStatusEl.textContent = `🟢 Connected | ${msg.symbol} (${dateDisplay})`;
    sessionStatusEl.classList.add('connected');

    const history = msg.history_bars || [];
    if (history.length > 0) {
      renderHistoryBars(history);
    }

    if (msg.latest_frame) {
      handleFrameMessage({ frame: msg.latest_frame, tick_cursor: msg.tick_cursor, cursor: msg.cursor });
    }
  }

  function handleFrameMessage(msg) {
    const frame = msg.frame;
    if (!frame) return;

    currentTick = msg.tick_cursor !== undefined ? msg.tick_cursor : frame.tick_index;
    const barIdx = msg.cursor !== undefined ? msg.cursor : frame.bar_index;
    const timeStr = frame.timestamp;
    updateBadge(currentTick, barIdx, timeStr);

    // Candle time MUST be truncated to the 1-minute candle open so that all intra-bar
    // ticks update the current candle instead of spawning a new candle on every tick!
    const candleTimeStr = frame.candle_timestamp || (frame.spot_bar && frame.spot_bar.timestamp) || timeStr;
    const candleTime = parseCandleTime(candleTimeStr);

    // Update Spot candle (updates forming candle in-place until minute rolls over)
    if (frame.spot_bar) {
      const sOpen = Number(frame.spot_bar.open ?? frame.spot_bar.spot_open ?? 100);
      const sHigh = Number(frame.spot_bar.high ?? frame.spot_bar.spot_high ?? 100);
      const sLow = Number(frame.spot_bar.low ?? frame.spot_bar.spot_low ?? 100);
      const sClose = Number(frame.spot_bar.close ?? frame.spot_bar.spot_close ?? 100);
      spotSeries.update({
        time: candleTime,
        open: sOpen,
        high: Math.max(sHigh, sOpen, sClose),
        low: Math.min(sLow, sOpen, sClose),
        close: sClose,
      });
    }

    // Update Option candle (updates forming candle in-place until minute rolls over)
    const optCandle = frame.forming_candle || frame.option_bar;
    if (optCandle) {
      const oOpen = Number(optCandle.open ?? optCandle.opt_open ?? 100);
      const oHigh = Number(optCandle.high ?? optCandle.opt_high ?? 100);
      const oLow = Number(optCandle.low ?? optCandle.opt_low ?? 100);
      const oClose = Number(optCandle.close ?? optCandle.opt_close ?? 100);
      optSeries.update({
        time: candleTime,
        open: oOpen,
        high: Math.max(oHigh, oOpen, oClose),
        low: Math.min(oLow, oOpen, oClose),
        close: oClose,
      });
    }

    // Update indicator plots and HUD
    if (frame.indicator_plots) {
      updateIndicatorHud(frame.indicator_plots);

      const plotColors = {
        ema_fast: '#38bdf8',
        ema_slow: '#ec4899',
        supertrend: '#10b981',
        res_high: '#ef4444',
        sup_low: '#10b981',
        nearest_res: '#ef4444',
        nearest_sup: '#10b981',
      };
      Object.entries(frame.indicator_plots).forEach(([k, val]) => {
        if (typeof val === 'number' && !isNaN(val)) {
          // Strictly only plot price overlay indicators onto the price chart
          if (isPriceOverlay(k, val, true)) {
            const color = plotColors[k] || '#a855f7';
            const line = getOrCreateLineSeries(spotChart, spotPlotSeries, k, color);
            line.update({ time: candleTime, value: val });
          } else if (isPriceOverlay(k, val, false)) {
            const color = plotColors[k] || '#a855f7';
            const line = getOrCreateLineSeries(optChart, optPlotSeries, k, color);
            line.update({ time: candleTime, value: val });
          }
        }
      });
    }

    // Signals
    if (frame.active_signal) {
      const isBuy = frame.active_signal.action.includes('BUY');
      const marker = {
        time: candleTime,
        position: isBuy ? 'belowBar' : 'aboveBar',
        color: isBuy ? '#10b981' : '#ef4444',
        shape: isBuy ? 'arrowUp' : 'arrowDown',
        text: `${frame.active_signal.action} @ ${frame.active_signal.trigger_price}`,
      };
      const existing = optSeries.markers ? optSeries.markers() : [];
      optSeries.setMarkers([...existing, marker]);
    }
  }

  function navigateToTrade(tr, idx) {
    const entryTs = parseCandleTime(tr.entry_time);
    const exitTs = parseCandleTime(tr.exit_time);
    if (!entryTs || entryTs <= 0) return;

    // Center chart on trade window with comfortable margin (at least 20 min view)
    const tradeSpan = Math.max((exitTs > entryTs ? exitTs - entryTs : 300), 600);
    const pad = Math.max(300, Math.round(tradeSpan * 0.4));
    const from = entryTs - pad;
    const to = (exitTs && exitTs > entryTs ? exitTs : entryTs) + pad;

    if (optChart && optChart.timeScale) {
      optChart.timeScale().setVisibleRange({ from, to });
    }
    if (spotChart && spotChart.timeScale) {
      spotChart.timeScale().setVisibleRange({ from, to });
    }

    const isWin = (tr.net_pnl_inr !== undefined ? tr.net_pnl_inr : tr.pnl_inr || 0) > 0;
    const netSign = (tr.net_pnl_inr || 0) >= 0 ? '+' : '';
    const grossSign = (tr.gross_pnl_inr || 0) >= 0 ? '+' : '';

    // Clear and set focused markers for this selected trade
    const focusedMarkers = [
      {
        time: entryTs,
        position: tr.action.includes('CE') ? 'belowBar' : 'aboveBar',
        color: tr.action.includes('CE') ? '#10b981' : '#ef4444',
        shape: tr.action.includes('CE') ? 'arrowUp' : 'arrowDown',
        text: `ENTRY ${tr.action} @ ${tr.entry_price.toFixed(1)}`,
      }
    ];

    if (exitTs && exitTs > 0) {
      focusedMarkers.push({
        time: exitTs,
        position: isWin ? 'aboveBar' : 'belowBar',
        color: isWin ? '#38bdf8' : '#eab308',
        shape: 'circle',
        text: `EXIT (${tr.exit_reason}) @ ${tr.exit_price.toFixed(1)} | Net ${netSign}₹${(tr.net_pnl_inr || 0).toFixed(0)}`,
      });
    }

    if (optSeries) optSeries.setMarkers(focusedMarkers);
    if (spotSeries) spotSeries.setMarkers(focusedMarkers);

    // Show floating navigation indicator toast
    if (tradeNavToast) {
      tradeNavToast.style.display = 'flex';
      if (toastTitle) toastTitle.textContent = `Focused Trade #${idx + 1} (${tr.action})`;
      if (toastBody) {
        const entryReasonHtml = tr.entry_reason ? `<div style="margin-top: 5px; padding-top: 4px; border-top: 1px dashed rgba(255,255,255,0.15); font-size: 11px;"><span style="color: #38bdf8; font-weight: 600;">🎯 Entry Diagnostics:</span> <span style="color: #cbd5e1;">${tr.entry_reason}</span></div>` : '';
        const exitReasonHtml = tr.exit_reason ? `<div style="margin-top: 3px; font-size: 11px;"><span style="color: #f59e0b; font-weight: 600;">🛑 Exit Reason:</span> <span style="color: #cbd5e1;">${tr.exit_reason}</span></div>` : '';
        toastBody.innerHTML = `Entry: <b>${tr.entry_time.split(' ')[1] || tr.entry_time}</b> @ ${tr.entry_price.toFixed(1)} &nbsp;➜&nbsp; Exit: <b>${tr.exit_time.split(' ')[1] || tr.exit_time}</b> @ ${tr.exit_price.toFixed(1)} | Gross: <b>${grossSign}₹${(tr.gross_pnl_inr || 0).toFixed(1)}</b> | Charges: <b>-₹${(tr.total_charges_inr || 0).toFixed(1)}</b> | Net: <b style="color: ${isWin ? '#10b981' : '#ef4444'};">${netSign}₹${(tr.net_pnl_inr || 0).toFixed(1)}</b>${entryReasonHtml}${exitReasonHtml}`;
      }
    }

    // Synchronize focused trade with backend telemetry
    sendCommand({
      action: 'focus_trade',
      trade: tr,
      trade_index: idx
    });
  }

  function handleBacktestResult(msg) {
    if (runBacktestBtn) {
      runBacktestBtn.textContent = '📊 Run Backtest';
      runBacktestBtn.disabled = false;
    }
    if (toggleBacktestBtn) toggleBacktestBtn.style.display = 'inline-flex';

    // If multi-day history bars are provided, render the complete multi-day candles on the chart!
    const hist = msg.history_bars || (msg.scorecard && msg.scorecard.history_bars);
    if (hist && hist.length > 0) {
      renderHistoryBars(hist);
    }

    const sc = msg.scorecard || {};

    // 1. Scorecard Summary KPI Cards
    if (btWinrateEl) btWinrateEl.textContent = `${(sc.win_rate_pct || 0).toFixed(1)}%`;
    if (btTradesEl) btTradesEl.textContent = `${sc.total_trades || 0} (${sc.winning_trades || 0}W / ${sc.losing_trades || 0}L)`;

    const netPnlInr = sc.total_pnl_inr !== undefined ? sc.total_pnl_inr : 0;
    if (btPnlInrEl) {
      btPnlInrEl.textContent = `${netPnlInr >= 0 ? '+' : ''}₹${netPnlInr.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
      btPnlInrEl.className = `metric-value ${netPnlInr >= 0 ? 'profit' : 'loss'}`;
    }

    const grossPnlInr = sc.gross_pnl_inr !== undefined ? sc.gross_pnl_inr : 0;
    if (btGrossInrEl) {
      btGrossInrEl.textContent = `${grossPnlInr >= 0 ? '+' : ''}₹${grossPnlInr.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
      btGrossInrEl.className = `metric-value ${grossPnlInr >= 0 ? 'profit' : 'loss'}`;
    }

    const totalChargesInr = sc.total_charges_inr !== undefined ? sc.total_charges_inr : 0;
    if (btTotalChargesEl) {
      btTotalChargesEl.textContent = `-₹${totalChargesInr.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
    }

    const pnlPts = sc.total_pnl_points !== undefined ? sc.total_pnl_points : 0;
    if (btPnlPtsEl) {
      btPnlPtsEl.textContent = `${pnlPts >= 0 ? '+' : ''}${pnlPts.toFixed(1)} pts`;
      btPnlPtsEl.className = `metric-value ${pnlPts >= 0 ? 'profit' : 'loss'}`;
    }

    if (btPfEl) btPfEl.textContent = (sc.profit_factor || 0).toFixed(2);
    if (btDrawdownEl) btDrawdownEl.textContent = `-${(sc.max_drawdown_pct || 0).toFixed(1)}% (₹${Math.abs(sc.max_drawdown_inr || 0).toFixed(0)})`;

    // 2. Charges Bifurcation Breakdown Summary Bar
    const bif = sc.charges_bifurcation || {};
    if (bifBrokerageEl) bifBrokerageEl.textContent = `₹${(bif.brokerage || 0).toFixed(2)}`;
    if (bifSttEl) bifSttEl.textContent = `₹${(bif.stt || 0).toFixed(2)}`;
    if (bifExchangeEl) bifExchangeEl.textContent = `₹${(bif.exchange_txn || 0).toFixed(2)}`;
    if (bifGstEl) bifGstEl.textContent = `₹${(bif.gst || 0).toFixed(2)}`;
    if (bifStampEl) bifStampEl.textContent = `₹${(bif.stamp_duty || 0).toFixed(2)}`;
    if (bifSebiEl) bifSebiEl.textContent = `₹${(bif.sebi || 0).toFixed(2)}`;

    // 3. Populate Trade Rows in Ledger with Bifurcation & Click Navigation
    if (btTradesBody) {
      btTradesBody.innerHTML = '';
      const trades = sc.trades || [];
      if (trades.length === 0) {
        btTradesBody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 20px;">Zero trades generated in this date range.</td></tr>';
      } else {
        const initialMarkers = [];
        trades.forEach((tr, idx) => {
          const row = document.createElement('tr');
          const isWin = (tr.net_pnl_inr !== undefined ? tr.net_pnl_inr : tr.pnl_inr || 0) > 0;
          const badgeClass = isWin ? 'badge-win' : 'badge-loss';
          const netSign = (tr.net_pnl_inr || 0) >= 0 ? '+' : '';
          const grossSign = (tr.gross_pnl_inr || 0) >= 0 ? '+' : '';

          const entryTimeLabel = tr.entry_time.split(' ')[1] || tr.entry_time;
          const exitTimeLabel = tr.exit_time.split(' ')[1] || tr.exit_time;

          const ch = tr.charges || {};
          const bifTooltip = [
            `Brokerage: ₹${(ch.brokerage || 40.0).toFixed(2)}`,
            `STT: ₹${(ch.stt || 0.0).toFixed(2)}`,
            `NSE Txn: ₹${(ch.exchange_txn || 0.0).toFixed(2)}`,
            `GST (18%): ₹${(ch.gst || 0.0).toFixed(2)}`,
            `Stamp Duty: ₹${(ch.stamp_duty || 0.0).toFixed(2)}`,
            `SEBI: ₹${(ch.sebi || 0.0).toFixed(2)}`,
            `------------------------`,
            `Total Charges: ₹${(tr.total_charges_inr || 0.0).toFixed(2)}`
          ].join('\n');

          row.innerHTML = `
            <td style="color: var(--text-muted); font-size: 10px;">${idx + 1}</td>
            <td title="Entry: ${tr.entry_time}\nDiagnostics: ${tr.entry_reason || 'N/A'}">
              ${entryTimeLabel}
              <div style="font-size: 9px; color: #38bdf8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 85px;" title="${tr.entry_reason || ''}">${(tr.entry_reason || '').split('|')[0] || ''}</div>
            </td>
            <td title="Exit: ${tr.exit_time}\nReason: ${tr.exit_reason || 'N/A'}">
              ${exitTimeLabel}
              <div style="font-size: 9px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 85px;" title="${tr.exit_reason || ''}">${tr.exit_reason || ''}</div>
            </td>
            <td><span style="color: ${tr.action.includes('CE') ? '#10b981' : '#ef4444'}; font-weight: 600;">${tr.action}</span></td>
            <td style="color: ${tr.gross_pnl_inr >= 0 ? '#10b981' : '#ef4444'};">${grossSign}₹${(tr.gross_pnl_inr || 0).toFixed(1)}</td>
            <td>
              <span class="charges-pill" title="${bifTooltip}">
                -₹${(tr.total_charges_inr || 0).toFixed(1)} ℹ️
              </span>
            </td>
            <td class="${badgeClass}">${netSign}₹${(tr.net_pnl_inr || 0).toFixed(1)}</td>
          `;

          // When trade row is clicked: Navigate on chart and show entry/exit
          row.addEventListener('click', () => {
            const allRows = btTradesBody.querySelectorAll('tr');
            allRows.forEach(r => r.classList.remove('active-trade-row'));
            row.classList.add('active-trade-row');
            navigateToTrade(tr, idx);
          });

          btTradesBody.appendChild(row);

          // Add default markers to overview map
          const entryT = parseCandleTime(tr.entry_time);
          const exitT = parseCandleTime(tr.exit_time);
          if (entryT > 0) {
            initialMarkers.push({
              time: entryT,
              position: tr.action.includes('CE') ? 'belowBar' : 'aboveBar',
              color: tr.action.includes('CE') ? '#10b981' : '#ef4444',
              shape: tr.action.includes('CE') ? 'arrowUp' : 'arrowDown',
              text: `ENTRY ${tr.action} @ ${tr.entry_price.toFixed(1)}`,
            });
          }
          if (exitT > 0) {
            initialMarkers.push({
              time: exitT,
              position: isWin ? 'aboveBar' : 'belowBar',
              color: isWin ? '#38bdf8' : '#eab308',
              shape: 'circle',
              text: `EXIT (${tr.exit_reason}) ${netSign}${tr.pnl_points || 0} pts`,
            });
          }
        });

        if (initialMarkers.length > 0 && optSeries) {
          optSeries.setMarkers(initialMarkers);
        }
      }
    }

    // Open scorecard drawer automatically
    if (backtestDrawer) backtestDrawer.classList.add('open');
  }

  function handleSeekMessage(msg) {
    handleInitMessage({
      total_ticks: totalTicks,
      tick_cursor: msg.tick_cursor,
      cursor: msg.cursor,
      history_bars: msg.history_bars,
      latest_frame: msg.frame,
    });
  }

  function connectWebSocket(targetPort) {
    port = targetPort || port;
    const url = `ws://127.0.0.1:${port}`;
    sessionStatusEl.textContent = `🟡 Connecting to ${url}...`;
    sessionStatusEl.classList.remove('connected');

    try {
      ws = new WebSocket(url);
    } catch (e) {
      sessionStatusEl.textContent = `🔴 Connection Failed`;
      return;
    }

    ws.onopen = () => {
      sessionStatusEl.textContent = `🟢 Connected | Port ${port}`;
      sessionStatusEl.classList.add('connected');
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'init') {
          handleInitMessage(msg);
        } else if (msg.type === 'frame') {
          handleFrameMessage(msg);
        } else if (msg.type === 'seek') {
          handleSeekMessage(msg);
        } else if (msg.type === 'backtest_result') {
          handleBacktestResult(msg);
        } else if (msg.type === 'status') {
          if (msg.is_playing !== undefined) {
            isPlaying = msg.is_playing;
            playBtn.textContent = isPlaying ? '⏸ Pause' : '▶ Play';
          }
        }
      } catch (e) {
        console.error('Error handling WebSocket message:', e);
      }
    };

    ws.onclose = () => {
      sessionStatusEl.textContent = `🔴 Disconnected. Retrying in 2s...`;
      sessionStatusEl.classList.remove('connected');
      setTimeout(() => connectWebSocket(port), 2000);
    };
  }

  function sendCommand(cmd) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(cmd));
    }
  }

  // Event Listeners
  stepTickBtn.addEventListener('click', () => sendCommand({ action: 'step', ticks: 1 }));
  stepBarBtn.addEventListener('click', () => sendCommand({ action: 'step_bar' }));
  playBtn.addEventListener('click', () => {
    if (isPlaying) {
      sendCommand({ action: 'pause' });
    } else {
      const spd = parseFloat(speedSelect.value) || 5;
      sendCommand({ action: 'play', speed: spd });
    }
  });

  speedSelect.addEventListener('change', () => {
    const spd = parseFloat(speedSelect.value) || 5;
    sendCommand({ action: 'set_speed', speed: spd });
  });

  scrubber.addEventListener('input', () => {
    const val = parseInt(scrubber.value, 10);
    badgeEl.textContent = `Seeking Tick #${val}...`;
  });

  scrubber.addEventListener('change', () => {
    const val = parseInt(scrubber.value, 10);
    sendCommand({ action: 'seek', tick: val });
  });

  // Date Range & Backtest Listeners
  if (loadDateBtn) {
    loadDateBtn.addEventListener('click', () => {
      const dtStart = dateStartInput ? dateStartInput.value : '';
      const dtEnd = dateEndInput ? dateEndInput.value : dtStart;
      if (dtStart) {
        sessionStatusEl.textContent = `⏳ Loading dates ${dtStart} to ${dtEnd}...`;
        sendCommand({ action: 'load_date', start_date: dtStart, end_date: dtEnd, date: dtStart });
      }
    });
  }

  // Broker Preset & Charges Listeners
  if (cfgBrokerPreset) {
    cfgBrokerPreset.addEventListener('change', () => {
      const p = cfgBrokerPreset.value;
      const lot = (cfgLotSize && cfgLotSize.dataset.userModified)
        ? cfgLotSize.value
        : String(getLotSizeForSymbol(activeSymbol));

      if (p === 'icici') {
        if (cfgBrokerage) cfgBrokerage.value = '20';
        if (cfgLotSize && !cfgLotSize.dataset.userModified) cfgLotSize.value = lot;
        if (cfgStt) cfgStt.value = '0.10';
        if (cfgExchange) cfgExchange.value = '0.03503';
        if (cfgGst) cfgGst.value = '18';
        if (lblBrokerName) lblBrokerName.textContent = 'ICICI Direct';
      } else if (p === 'zerodha') {
        if (cfgBrokerage) cfgBrokerage.value = '20';
        if (cfgLotSize && !cfgLotSize.dataset.userModified) cfgLotSize.value = lot;
        if (cfgStt) cfgStt.value = '0.10';
        if (cfgExchange) cfgExchange.value = '0.03503';
        if (cfgGst) cfgGst.value = '18';
        if (lblBrokerName) lblBrokerName.textContent = 'Zerodha';
      } else {
        if (lblBrokerName) lblBrokerName.textContent = 'Custom';
      }
    });
  }

  if (cfgLotSize) {
    cfgLotSize.addEventListener('input', () => {
      cfgLotSize.dataset.userModified = 'true';
    });
  }

  // Toggle charges config panel visibility
  if (btnToggleCharges && chargesConfigSection) {
    btnToggleCharges.addEventListener('click', () => {
      chargesConfigSection.style.display = chargesConfigSection.style.display === 'none' ? 'block' : 'none';
    });
  }

  if (chargesToggleHead) {
    chargesToggleHead.addEventListener('click', () => {
      const grid = document.getElementById('charges-grid-body');
      if (grid) grid.style.display = grid.style.display === 'none' ? 'grid' : 'none';
    });
  }

  // Dismiss trade navigation indicator toast
  if (toastClose && tradeNavToast) {
    toastClose.addEventListener('click', () => {
      tradeNavToast.style.display = 'none';
    });
  }

  if (runBacktestBtn) {
    runBacktestBtn.addEventListener('click', () => {
      const sDt = dateStartInput ? dateStartInput.value : '2026-10-30';
      const eDt = dateEndInput ? dateEndInput.value : '2026-10-30';
      runBacktestBtn.textContent = '⏳ Running...';
      runBacktestBtn.disabled = true;

      const resolvedLot = cfgLotSize
        ? (parseInt(cfgLotSize.value, 10) || getLotSizeForSymbol(activeSymbol))
        : getLotSizeForSymbol(activeSymbol);

      const chargesConfig = {
        broker_name: lblBrokerName ? lblBrokerName.textContent : 'ICICI Direct',
        brokerage_per_order: cfgBrokerage ? parseFloat(cfgBrokerage.value) || 20.0 : 20.0,
        lot_size: resolvedLot,
        stt_rate: cfgStt ? (parseFloat(cfgStt.value) || 0.10) / 100.0 : 0.0010,
        exchange_txn_rate: cfgExchange ? (parseFloat(cfgExchange.value) || 0.03503) / 100.0 : 0.0003503,
        gst_rate: cfgGst ? (parseFloat(cfgGst.value) || 18.0) / 100.0 : 0.18,
      };

      sendCommand({
        action: 'run_backtest',
        start_date: sDt,
        end_date: eDt,
        charges_config: chargesConfig
      });
    });
  }

  if (toggleBacktestBtn) {
    toggleBacktestBtn.addEventListener('click', () => {
      if (backtestDrawer) backtestDrawer.classList.toggle('open');
    });
  }

  if (closeDrawerBtn) {
    closeDrawerBtn.addEventListener('click', () => {
      if (backtestDrawer) backtestDrawer.classList.remove('open');
    });
  }

  // Listen to messages from VS Code Extension Host
  window.addEventListener('message', (event) => {
    const message = event.data;
    if (message.command === 'connect') {
      connectWebSocket(message.port);
    }
  });

  // Boot
  initCharts();
  connectWebSocket(port);
})();
