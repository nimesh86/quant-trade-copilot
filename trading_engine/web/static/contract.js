/**
 * Contract Analysis Page Controller
 * ──────────────────────────────────
 * Handles:
 *   1. Symbol search with autocomplete (debounced, keyboard navigable)
 *   2. TradingView Lightweight Chart initialization
 *   3. SMF Cloud canvas overlay (same algorithm as main dashboard)
 *   4. Mid-session candle catch-up via /api/contracts/candles
 *   5. Live WebSocket subscription via /ws/contracts?symbol=...
 *   6. Multi-timeframe client-side candle aggregation (1m, 3m, 5m, 15m)
 *   7. Real-time forming candle updates
 */

// ── App State ─────────────────────────────────────────────────────────────────
const C = {
    symbol: null,
    displayName: null,
    underlying: 'NIFTY',
    selectedExpiry: '',
    optionChainOpen: false,
    optionChainData: null,
    optionChainFullRange: false,
    chart: null,
    candleSeries: null,
    smfUpperSeries: null,
    smfLowerSeries: null,
    smfBcSeries: null,
    smfBoSeries: null,
    smfTargetUpperSeries: null,
    smfTargetLowerSeries: null,
    targetsEnabled: localStorage.getItem('smf_targets_enabled') !== '0', // ON by default
    targetPriceLines: [],
    ws: null,
    wsPingInterval: null,
    currentTf: 1,        // active timeframe in minutes
    smfEnabled: true,
    rawCandles: [],       // 1-min candle array (formatted for chart)
    sessionOpen: null,
    sessionHigh: null,
    sessionLow: null,
    sessionClose: null,
    smfLastResult: null,
    liveQuote: null,
    quotePollInterval: null,
    dynamicExitEnabled: true,
    dynamicExitVariant: 'V5',
    dynamicTrailSeries: null,
    dynamicLadderSeries: null,
    tradingLots: 1,
    lotUnits: 65,
    tradingState: null,
    tradingPollTimer: null,
    selectedSessionDate: 'today',
    availableSessionDates: [],
    limitTool: {
        active: false,
        limitPrice: 0,
        targetPrice: 0,
        slPrice: 0,
        dragging: null
    },
    limitToolHitBoxes: [],
    _tradingMouseInitialized: false,
    _canvasRenderPending: false,
    _executedTargets: new Set(),
    dualMode: false,
    secondaryChart: null,
    secondaryCandleSeries: null,
    secondaryRawCandles: [],
    secondarySmfLastResult: null,
    secondaryContract: null,
    secondaryWs: null,
    secondaryWsPingInterval: null,
};

// ── SMF Cloud Config (identical to main dashboard) ────────────────────────────
const SMF = {
    fastLen: 9,
    slowLen: 21,
    atrLen: 14,
    bullColor: '#00c8ff',
    bearColor: '#ff005d',
    buyColor: '#00f2a9',   // Vibrant high-contrast green for Buy
    sellColor: '#ff005d',  // Vibrant high-contrast neon red/pink for Sell
    cloudBull: 'rgba(0, 200, 255, 0.12)',
    cloudBear: 'rgba(255, 0, 93, 0.12)',
    ribbonBull: 'rgba(0, 200, 255, 0.22)',
    ribbonBear: 'rgba(255, 0, 93, 0.22)',
    showSwitch: true,
    showPrice: localStorage.getItem('smf_show_price') === '1',
    showDots: false, // Default off to keep chart clean and uncluttered
    dotCooldown: 15,
    atrMult: 0.5,           // ATR Volatility Buffer (0.5 * ATR) to filter out 1m noise
    confirmBars: 2,         // 2-bar consecutive close confirmation to prevent 1-bar shakeouts
    target1Mult: 1.5,       // Target 1 (1.5x Risk/ATR)
    target2Mult: 2.5,       // Target 2 (2.5x Risk/ATR)
    maxTargetMult: 3.5,     // Maximum Trend Target (3.5x ATR dynamic channel, reaches ~150 from 114)
    targetColor: '#ffd166', // Golden amber for max target
};

// ─────────────────────────────────────────────────────────────────────────────
// ── INIT ─────────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    await initParallelStations();
    initSearch();
    await initSessionDateSelector();
    await initExpirySelector();
    initOptionChain();
    initKeyboardShortcuts();
    initReplayController();
    initTradingTerminal();
    checkMarketStatus();
    setInterval(checkMarketStatus, 30_000);

    // Check URL parameters for symbol, or load ATM contract from option chain
    const urlParams = new URLSearchParams(window.location.search);
    const paramSym = urlParams.get('symbol');
    if (paramSym) {
        selectContract({ symbol: paramSym, display_name: paramSym });
    } else {
        await fetchOptionChain();
    }
});

// ── Chart Init ────────────────────────────────────────────────────────────────

function initChart() {
    const container = document.getElementById('contract-chart-container');
    const canvas = document.getElementById('contract-smf-canvas');

    C.chart = LightweightCharts.createChart(container, {
        layout: {
            background: { type: 'solid', color: '#070b14' },
            textColor: '#6a8fac',
            fontFamily: "'Inter', sans-serif",
            fontSize: 11,
        },
        grid: {
            vertLines: { color: 'rgba(0, 195, 255, 0.05)' },
            horzLines: { color: 'rgba(0, 195, 255, 0.05)' },
        },
        crosshair: {
            vertLine: { color: 'rgba(0, 195, 255, 0.5)', width: 1, style: 2 },
            horzLine: { color: 'rgba(0, 195, 255, 0.5)', width: 1, style: 2 },
        },
        rightPriceScale: {
            borderColor: 'rgba(0, 195, 255, 0.15)',
            textColor: '#6a8fac',
            autoScale: true,
            scaleMargins: {
                top: 0.10,
                bottom: 0.05,
            },
        },
        timeScale: {
            borderColor: 'rgba(0, 195, 255, 0.15)',
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 25,
            barSpacing: 8,
            minBarSpacing: 4,
            tickMarkFormatter: (t) => {
                const d = new Date(t * 1000);
                const h = d.getHours().toString().padStart(2, '0');
                const m = d.getMinutes().toString().padStart(2, '0');
                return `${h}:${m}`;
            },
        },
        localization: {
            timeFormatter: (t) => {
                const d = new Date(t * 1000);
                const day = d.getDate().toString().padStart(2, '0');
                const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                const mon = months[d.getMonth()];
                const yr = d.getFullYear().toString().slice(-2);
                const h = d.getHours().toString().padStart(2, '0');
                const m = d.getMinutes().toString().padStart(2, '0');
                return `${day} ${mon} '${yr}  ${h}:${m}`;
            },
            dateFormat: "dd MMM ''yy",
        },
        handleScale: {
            axisPressedMouseMove: { time: true, price: true },
            axisDoubleClickReset: { time: true, price: true },
            mouseWheel: true,
            pinch: true,
        },
        handleScroll: {
            mouseWheel: true,
            pressedMouseMove: true,
            horzTouchDrag: true,
            vertTouchDrag: true,
        },
    });

    C.candleSeries = C.chart.addCandlestickSeries({
        upColor: SMF.bullColor,
        downColor: SMF.bearColor,
        borderUpColor: SMF.bullColor,
        borderDownColor: SMF.bearColor,
        wickUpColor: SMF.bullColor,
        wickDownColor: SMF.bearColor,
        autoscaleInfoProvider: (original) => {
            const res = original();
            if (res && res.priceRange) {
                return {
                    priceRange: {
                        minValue: Math.max(0, res.priceRange.minValue),
                        maxValue: Math.max(1, res.priceRange.maxValue),
                    },
                };
            }
            return res;
        },
    });

    // SMF Line series (hidden, used for band reference)
    C.smfUpperSeries = C.chart.addLineSeries({
        color: 'rgba(0,0,0,0)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });
    C.smfLowerSeries = C.chart.addLineSeries({
        color: 'rgba(0,0,0,0)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });
    C.smfBcSeries = C.chart.addLineSeries({
        color: 'rgba(0,200,255,0.45)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });
    C.smfBoSeries = C.chart.addLineSeries({
        color: 'rgba(255,0,93,0.45)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });
    // Maximum Target Dual Volatility Bands (Upper & Lower continuous dashed boundary lines)
    C.smfTargetUpperSeries = C.chart.addLineSeries({
        color: 'rgba(255, 209, 102, 0.35)', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false,
        title: '',
    });
    C.smfTargetLowerSeries = C.chart.addLineSeries({
        color: 'rgba(255, 209, 102, 0.25)', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false,
        title: '',
        autoscaleInfoProvider: () => null,
    });


    // SMF Dynamic Exit Trailing Stop & Ladder Floor Series
    C.dynamicTrailSeries = C.chart.addLineSeries({
        color: '#f59e0b', // Vibrant golden amber
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: '',
    });
    C.dynamicLadderSeries = C.chart.addLineSeries({
        color: '#06b6d4', // Cyan dashed
        lineWidth: 1.5,
        lineStyle: 2, // Dashed
        priceLineVisible: false,
        lastValueVisible: false,
        title: '',
    });

    // Canvas & Chart sync with chart viewport (responsive for mobile & orientation change)
    const chartArea = document.getElementById('chart-area') || container;
    const syncCanvas = () => {
        const w = container.clientWidth || chartArea.clientWidth;
        const h = container.clientHeight || chartArea.clientHeight;
        if (C.chart && w > 0 && h > 0) {
            C.chart.applyOptions({ width: w, height: h });
        }
        canvas.width = w;
        canvas.height = h;
        if (C.rawCandles.length >= 5) {
            requestAnimationFrame(renderSMFCanvas);
        }
    };
    new ResizeObserver(syncCanvas).observe(container);
    new ResizeObserver(syncCanvas).observe(chartArea);
    window.addEventListener('resize', syncCanvas);
    syncCanvas();

    // Sync canvas when time range changes (pan/zoom along X axis)
    C.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
        requestAnimationFrame(renderSMFCanvas);
    });
    C.chart.timeScale().subscribeVisibleTimeRangeChange(() => {
        requestAnimationFrame(renderSMFCanvas);
    });
    C.chart.subscribeCrosshairMove(() => {
        requestAnimationFrame(renderSMFCanvas);
    });

    // Real-time synchronization during Y-axis price scale zoom & pan
    let isChartInteracting = false;
    let syncRafId = null;

    const renderSyncFrame = () => {
        renderSMFCanvas();
        if (isChartInteracting) {
            syncRafId = requestAnimationFrame(renderSyncFrame);
        }
    };

    const startInteractionSync = () => {
        if (isChartInteracting) return;
        isChartInteracting = true;
        cancelAnimationFrame(syncRafId);
        syncRafId = requestAnimationFrame(renderSyncFrame);
    };

    const stopInteractionSync = () => {
        isChartInteracting = false;
        cancelAnimationFrame(syncRafId);
        let trailingFrames = 12;
        const trail = () => {
            renderSMFCanvas();
            if (--trailingFrames > 0) {
                requestAnimationFrame(trail);
            }
        };
        requestAnimationFrame(trail);
    };

    // Pointer events on chart area & window (captures dragging the right price scale or chart)
    chartArea.addEventListener('pointerdown', () => {
        startInteractionSync();
        const onPointerUp = () => {
            window.removeEventListener('pointerup', onPointerUp);
            window.removeEventListener('pointercancel', onPointerUp);
            stopInteractionSync();
        };
        window.addEventListener('pointerup', onPointerUp);
        window.addEventListener('pointercancel', onPointerUp);
    });

    // Mouse wheel zoom (both over chart body and over right price scale)
    let wheelTimer = null;
    chartArea.addEventListener('wheel', () => {
        startInteractionSync();
        clearTimeout(wheelTimer);
        wheelTimer = setTimeout(stopInteractionSync, 200);
    }, { passive: true });

    // Touch events for mobile pinch-zoom and drag
    chartArea.addEventListener('touchstart', startInteractionSync, { passive: true });
    chartArea.addEventListener('touchend', stopInteractionSync, { passive: true });
    chartArea.addEventListener('touchcancel', stopInteractionSync, { passive: true });
}

// ─────────────────────────────────────────────────────────────────────────────
// ── SEARCH ────────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

let searchDebounceTimer = null;
let searchHighlightIdx = -1;
let searchResults = [];

function initSearch() {
    const input = document.getElementById('search-input');
    const dropdown = document.getElementById('search-dropdown');

    input.addEventListener('input', () => {
        clearTimeout(searchDebounceTimer);
        searchDebounceTimer = setTimeout(() => runSearch(input.value), 150);
    });

    input.addEventListener('keydown', (e) => {
        if (!dropdown.classList.contains('open')) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            searchHighlightIdx = Math.min(searchHighlightIdx + 1, searchResults.length - 1);
            updateSearchHighlight();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            searchHighlightIdx = Math.max(searchHighlightIdx - 1, -1);
            updateSearchHighlight();
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (searchHighlightIdx >= 0 && searchResults[searchHighlightIdx]) {
                selectContract(searchResults[searchHighlightIdx]);
                closeSearch();
            }
        } else if (e.key === 'Escape') {
            closeSearch();
        }
    });

    input.addEventListener('blur', () => {
        setTimeout(closeSearch, 200);
    });
}

async function runSearch(q) {
    const dropdown = document.getElementById('search-dropdown');
    if (!q || q.trim().length < 1) {
        closeSearch();
        return;
    }

    try {
        const expParam = C.selectedExpiry ? `&expiry=${encodeURIComponent(C.selectedExpiry)}` : '';
        const res = await fetch(`/api/contracts/search?q=${encodeURIComponent(q)}${expParam}&limit=20`);
        const data = await res.json();
        searchResults = data.results || [];
        renderSearchDropdown(searchResults);
    } catch (e) {
        dropdown.innerHTML = '<div class="search-no-result">Search unavailable</div>';
        dropdown.classList.add('open');
    }
}

function renderSearchDropdown(results) {
    const dropdown = document.getElementById('search-dropdown');
    searchHighlightIdx = -1;

    if (!results.length) {
        dropdown.innerHTML = '<div class="search-no-result">No contracts found</div>';
        dropdown.classList.add('open');
        return;
    }

    // Group by category
    const groups = { 'Indices': [], 'Futures': [], 'Options (CE)': [], 'Options (PE)': [] };
    for (const r of results) {
        if (r.segment === 'NSE-INDEX') groups['Indices'].push(r);
        else if (r.segment === 'NFO-FUT' || r.option_type === '') groups['Futures'].push(r);
        else if (r.option_type === 'CE') groups['Options (CE)'].push(r);
        else if (r.option_type === 'PE') groups['Options (PE)'].push(r);
        else groups['Indices'].push(r);
    }

    let html = '';
    let flatIdx = 0;
    const ordered = ['Indices', 'Futures', 'Options (CE)', 'Options (PE)'];
    for (const grp of ordered) {
        const items = groups[grp];
        if (!items.length) continue;
        html += `<div class="search-group-label">${grp}</div>`;
        for (const item of items) {
            const ot = item.option_type;
            let badge = '';
            if (ot === 'CE') badge = `<span class="result-badge badge-ce">CE</span>`;
            else if (ot === 'PE') badge = `<span class="result-badge badge-pe">PE</span>`;
            else if (item.segment === 'NFO-FUT') badge = `<span class="result-badge badge-fut">FUT</span>`;
            else badge = `<span class="result-badge badge-idx">IDX</span>`;

            const sub = item.expiry ? item.expiry : (item.segment || '');

            html += `<div class="search-result-item" data-idx="${flatIdx}" onclick="selectContract(searchResults[${flatIdx}]); closeSearch();">
                <span class="result-name">${escHtml(item.display_name)}</span>
                <span class="result-sub">${escHtml(sub)}</span>
                ${badge}
            </div>`;
            flatIdx++;
        }
    }

    dropdown.innerHTML = html;
    dropdown.classList.add('open');
}

function updateSearchHighlight() {
    const items = document.querySelectorAll('.search-result-item');
    items.forEach((el, i) => {
        el.classList.toggle('highlighted', i === searchHighlightIdx);
    });
}

function closeSearch() {
    const dropdown = document.getElementById('search-dropdown');
    dropdown.classList.remove('open');
    searchHighlightIdx = -1;
}

// ─────────────────────────────────────────────────────────────────────────────
// ── SESSION DATE (DAY) & EXPIRY DATES CONTROLLER ─────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

function formatSessionDateDisplay(dateStr, tag) {
    if (!dateStr || dateStr === 'today') return `Today (${tag})`;
    try {
        const parts = dateStr.split('-');
        if (parts.length === 3) {
            const yy = parts[0].slice(-2);
            const mm = parts[1];
            const dd = parts[2];
            return `${dd}/${mm}/${yy} (${tag})`;
        }
    } catch {}
    return `${dateStr} (${tag})`;
}

function formatExpiryDisplay(exp) {
    if (!exp) return '';
    let base = '';
    try {
        const parts = (exp.date || '').split('-');
        if (parts.length === 3) {
            const d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
            const mon = d.toLocaleString('en-US', { month: 'short' }).toUpperCase();
            base = `${parts[2]} ${mon}`;
        } else {
            base = exp.date || '';
        }
    } catch {
        base = exp.date || '';
    }

    const disp = (exp.display || '').toLowerCase();
    let tag = '';
    if (disp.includes('weekly')) {
        tag = ' (W)';
    } else if (disp.includes('monthly')) {
        tag = ' (M)';
    } else if (exp.days_to_expiry !== undefined) {
        tag = exp.days_to_expiry <= 7 ? ' (W)' : ' (M)';
    }
    return `${base}${tag}`;
}

async function initSessionDateSelector() {
    const sel = document.getElementById('master-session-date-select') || document.getElementById('session-date-select');
    if (!sel) return;

    try {
        const res = await fetch('/api/simulation/dates');
        const data = await res.json();
        const dates = data.dates || [];
        const todayStr = data.today || new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
        C.availableSessionDates = dates;

        sel.innerHTML = '';

        // Check if today is weekend or outside market hours
        const istNow = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
        const day = istNow.getDay(); // 0=Sun, 6=Sat
        const mins = istNow.getHours() * 60 + istNow.getMinutes();
        const isWeekend = (day === 0 || day === 6);
        const isMarketHours = !isWeekend && (mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30);

        // 1. Today option with compact format: dd/MM/yy (C) or (L)
        const todayOpt = document.createElement('option');
        todayOpt.value = 'today';
        if (isWeekend || !isMarketHours) {
            todayOpt.textContent = formatSessionDateDisplay(todayStr, 'C');
        } else {
            todayOpt.textContent = formatSessionDateDisplay(todayStr, 'L');
        }
        sel.appendChild(todayOpt);

        // 2. Historical dates for Replay: dd/MM/yy (R)
        dates.forEach((d) => {
            if (d === todayStr) return;
            const opt = document.createElement('option');
            opt.value = d;
            opt.textContent = formatSessionDateDisplay(d, 'R');
            sel.appendChild(opt);
        });

        // If today is a weekend or market is closed and we have past dates, default to most recent trading date
        let initialDate = 'today';
        if ((isWeekend || !isMarketHours) && dates.length > 0) {
            initialDate = dates[0];
        }

        sel.value = initialDate;
        C.selectedSessionDate = initialDate;
        MasterState.selectedSessionDate = initialDate;

        sel.addEventListener('change', async (e) => {
            const chosen = e.target.value;
            C.selectedSessionDate = chosen;
            MasterState.selectedSessionDate = chosen;

            if (chosen === 'today') {
                if (Replay.active) exitReplay();
                showLoading(true);
                await loadStrikePair(MasterState.currentStrike, MasterState.selectedExpiry, 'today');
                showLoading(false);
            } else {
                showLoading(true);
                await loadStrikePair(MasterState.currentStrike, MasterState.selectedExpiry, chosen);
                showLoading(false);
            }
        });
    } catch (err) {
        console.warn('initSessionDateSelector error:', err);
    }
}

async function initExpirySelector() {
    const headerSel = document.getElementById('master-expiry-select') || document.getElementById('expiry-select');
    const ocSel = document.getElementById('oc-expiry-select');
    if (!headerSel && !ocSel) return;

    try {
        const und = MasterState.currentUnderlying || C.underlying || 'NIFTY';
        const res = await fetch(`/api/contracts/expiries?underlying=${encodeURIComponent(und)}`);
        const data = await res.json();
        const expiries = data.expiries || [];

        if (headerSel) headerSel.innerHTML = '<option value="">ALL EXP</option>';
        if (ocSel) ocSel.innerHTML = '';

        expiries.forEach((exp) => {
            const compactText = formatExpiryDisplay(exp);
            if (headerSel) {
                const opt1 = document.createElement('option');
                opt1.value = exp.date;
                opt1.textContent = compactText;
                headerSel.appendChild(opt1);
            }

            if (ocSel) {
                const opt2 = document.createElement('option');
                opt2.value = exp.date;
                opt2.textContent = compactText;
                ocSel.appendChild(opt2);
            }
        });

        // Set default to first active upcoming expiry (e.g. 27 Oct 2026)
        if (expiries.length > 0) {
            const activeExp = expiries.find(e => e.days_to_expiry >= 0) || expiries[0];
            C.selectedExpiry = activeExp.date;
            MasterState.selectedExpiry = activeExp.date;
            if (headerSel) headerSel.value = activeExp.date;
            if (ocSel) ocSel.value = activeExp.date;
            updateExpiryBadge(activeExp.date);
        }

        // Listener for master header select if present
        if (headerSel) {
            headerSel.addEventListener('change', () => {
                C.selectedExpiry = headerSel.value;
                MasterState.selectedExpiry = headerSel.value;
                if (ocSel) ocSel.value = headerSel.value;
                updateExpiryBadge(headerSel.value);
                loadStrikePair(MasterState.currentStrike, MasterState.selectedExpiry, MasterState.selectedSessionDate);
                if (C.optionChainOpen || MasterState.optionChainOpen) {
                    fetchOptionChain();
                }
            });
        }

        // Listener for option chain modal select
        if (ocSel) {
            ocSel.addEventListener('change', () => {
                C.selectedExpiry = ocSel.value;
                MasterState.selectedExpiry = ocSel.value;
                if (headerSel) headerSel.value = ocSel.value;
                updateExpiryBadge(ocSel.value);
                fetchOptionChain();
                loadStrikePair(MasterState.currentStrike, MasterState.selectedExpiry, MasterState.selectedSessionDate);
            });
        }

    } catch (e) {
        console.error('Failed to load expiries:', e);
    }
}

function updateExpiryBadge(expStr) {
    const badge = document.getElementById('oc-btn-badge');
    if (!badge) return;
    if (!expStr) {
        badge.textContent = 'ALL';
        return;
    }
    try {
        const parts = expStr.split('-');
        const d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
        const m = d.toLocaleString('en-US', { month: 'short' }).toUpperCase();
        badge.textContent = `${parts[2]} ${m}`;
    } catch {
        badge.textContent = expStr;
    }
}

function toggleOptionChain() {
    const panel = document.getElementById('option-chain-panel');
    const toggleBtn = document.getElementById('btn-master-chain') || document.getElementById('btn-toggle-oc');
    if (!panel) return;
    MasterState.optionChainOpen = !MasterState.optionChainOpen;
    C.optionChainOpen = MasterState.optionChainOpen;
    if (toggleBtn) toggleBtn.classList.toggle('active', MasterState.optionChainOpen);
    panel.classList.toggle('collapsed', !MasterState.optionChainOpen);
    if (MasterState.optionChainOpen) {
        fetchOptionChain();
        if (window.innerWidth <= 900) {
            setTimeout(() => {
                panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 50);
        }
    }
}
window.toggleOptionChain = toggleOptionChain;

function initOptionChain() {
    const toggleBtn = document.getElementById('btn-master-chain') || document.getElementById('btn-toggle-oc');
    const closeBtn = document.getElementById('oc-close-btn');
    const rangeBtn = document.getElementById('oc-range-toggle');
    const refreshBtn = document.getElementById('oc-refresh-btn');
    const panel = document.getElementById('option-chain-panel');

    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleOptionChain);
    }

    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            MasterState.optionChainOpen = false;
            C.optionChainOpen = false;
            if (toggleBtn) toggleBtn.classList.remove('active');
            if (panel) panel.classList.add('collapsed');
        });
    }

    if (rangeBtn) {
        rangeBtn.addEventListener('click', () => {
            C.optionChainFullRange = !C.optionChainFullRange;
            rangeBtn.textContent = C.optionChainFullRange ? 'All Strikes' : '±15 Strikes';
            rangeBtn.style.color = C.optionChainFullRange ? 'var(--cyan)' : 'var(--text-secondary)';
            if (C.optionChainData) {
                renderOptionChainTable(C.optionChainData);
            }
        });
    }

    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            fetchOptionChain();
        });
    }

    const undSel = document.getElementById('oc-underlying-select');
    if (undSel) {
        undSel.addEventListener('change', async (e) => {
            C.underlying = e.target.value;
            C.selectedExpiry = null;
            await initExpirySelector();
            await fetchOptionChain();
        });
    }
}

async function fetchOptionChain() {
    const tbody = document.getElementById('oc-tbody');
    const summary = document.getElementById('oc-summary-text');
    if (!tbody) return;

    if (summary) summary.textContent = 'Updating live prices…';

    try {
        const expiryParam = C.selectedExpiry ? `&expiry=${encodeURIComponent(C.selectedExpiry)}` : '';
        const res = await fetch(`/api/contracts/option_chain?underlying=${encodeURIComponent(C.underlying)}${expiryParam}`);
        const data = await res.json();
        C.optionChainData = data;

        // Update spot display
        const spotVal = document.getElementById('oc-spot-val');
        const spotNum = (data.spot !== undefined && data.spot !== null) ? data.spot : data.spot_price;
        if (spotVal && spotNum) {
            spotVal.textContent = `₹${Number(spotNum).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
        }

        MasterState.optionChainData = data;
        C.optionChainData = data;
        if (data.atm_strike) {
            MasterState.atmStrike = Number(data.atm_strike);
        }

        renderOptionChainTable(data);

        // If no contract is charted yet, automatically chart the ATM strike pair
        if (!Stations.CE.symbol && data.chain && data.chain.length > 0) {
            const atmRow = data.chain.find(i => i.is_atm) || data.chain[Math.floor(data.chain.length / 2)];
            if (atmRow) {
                const strike = Number(atmRow.strike);
                MasterState.currentStrike = strike;
                loadStrikePair(strike, data.expiry);
            }
        }

        if (summary) {
            const count = data.chain ? data.chain.length : 0;
            summary.textContent = `${count} strikes around ATM (${data.atm_strike || MasterState.atmStrike})`;
        }
    } catch (e) {
        console.error('Failed to fetch option chain:', e);
        if (summary) summary.textContent = 'Error loading option chain';
    }
}

function renderOptionChainTable(data) {
    const tbody = document.getElementById('oc-tbody');
    if (!tbody || !data || !data.chain) return;

    const rows = data.chain;
    const atmStrike = data.atm_strike;

    let html = '';
    for (const item of rows) {
        const strike = item.strike;
        const isAtm = item.is_atm;
        const call = item.call || {};
        const put = item.put || {};

        const callActive = C.symbol === call.symbol ? ' active-contract' : '';
        const putActive = C.symbol === put.symbol ? ' active-contract' : '';
        const rowAtm = isAtm ? ' atm-row' : '';

        const callLtp = (call.ltp !== null && call.ltp !== undefined) ? `₹${Number(call.ltp).toFixed(2)}` : '——';
        const putLtp  = (put.ltp !== null && put.ltp !== undefined) ? `₹${Number(put.ltp).toFixed(2)}` : '——';

        const callSym = call.symbol || '';
        const putSym  = put.symbol || '';

        const callDisp = call.display_name || callSym;
        const putDisp  = put.display_name || putSym;

        html += `
        <tr class="oc-row${rowAtm}">
            <!-- CALLS SIDE -->
            <td class="oc-call-cell col-ltp call-ltp${callActive}" onclick="selectFromChain('${callSym}', '${callDisp}')" title="Chart ${callDisp}">
                ${callLtp}
            </td>
            <td class="oc-call-cell col-sym oc-sym-text${callActive}" onclick="selectFromChain('${callSym}', '${callDisp}')" title="${callDisp}">
                ${intOrRound(strike)} CE
            </td>
            <td class="col-action">
                <button class="oc-action-btn call-action-btn" onclick="selectFromChain('${callSym}', '${callDisp}')" title="Chart ${callDisp}">📈</button>
            </td>

            <!-- STRIKE CENTER -->
            <td class="oc-strike-cell col-strike">
                ${strike}
                ${isAtm ? '<span class="atm-badge">ATM</span>' : ''}
            </td>

            <!-- PUTS SIDE -->
            <td class="col-action">
                <button class="oc-action-btn put-action-btn" onclick="selectFromChain('${putSym}', '${putDisp}')" title="Chart ${putDisp}">📉</button>
            </td>
            <td class="oc-put-cell col-sym oc-sym-text${putActive}" onclick="selectFromChain('${putSym}', '${putDisp}')" title="${putDisp}">
                ${intOrRound(strike)} PE
            </td>
            <td class="oc-put-cell col-ltp put-ltp${putActive}" onclick="selectFromChain('${putSym}', '${putDisp}')" title="Chart ${putDisp}">
                ${putLtp}
            </td>
        </tr>`;
    }

    tbody.innerHTML = html;
}

function intOrRound(val) {
    return Math.round(Number(val));
}

function selectFromChain(sym, disp) {
    if (!sym) return;
    selectContract({ symbol: sym, display_name: disp });
    if (C.optionChainData) {
        renderOptionChainTable(C.optionChainData);
    }
    const sInput = document.getElementById('search-input');
    if (sInput && document.activeElement === sInput) {
        sInput.blur();
    }
    // On mobile, smoothly scroll up to the chart to view the live SMF cloud immediately
    if (window.innerWidth <= 900) {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
}

function initKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // / or Cmd+K or Ctrl+K → focus search
        if (e.key === '/' && document.activeElement?.id !== 'search-input') {
            e.preventDefault();
            document.getElementById('search-input').focus();
            document.getElementById('search-input').select();
        }
        if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            document.getElementById('search-input').focus();
            document.getElementById('search-input').select();
        }
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// ── CONTRACT SELECTION ────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

function selectContract(contract) {
    const symbol = contract.symbol;
    if (!symbol) return;

    C.symbol = symbol;
    C.displayName = contract.display_name || symbol;

    const sInput = document.getElementById('search-input');
    if (sInput) sInput.value = C.displayName;
    const badgeSym = document.getElementById('badge-symbol');
    if (badgeSym) badgeSym.textContent = C.displayName;

    if (contract.expiry) {
        MasterState.selectedExpiry = contract.expiry;
        C.selectedExpiry = contract.expiry;
        const hSel = document.getElementById('master-expiry-select') || document.getElementById('expiry-select');
        const ocSel = document.getElementById('oc-expiry-select');
        if (hSel) hSel.value = contract.expiry;
        if (ocSel) ocSel.value = contract.expiry;
    }

    // Auto-close Option Chain drawer on contract select so user gets full chart view
    const ocPanel = document.getElementById('option-chain-panel');
    const toggleBtn = document.getElementById('btn-master-chain') || document.getElementById('btn-toggle-oc');
    if (ocPanel && MasterState.optionChainOpen) {
        MasterState.optionChainOpen = false;
        if (toggleBtn) toggleBtn.classList.remove('active');
        ocPanel.classList.add('collapsed');
    }

    // Extract strike number
    const match = symbol.match(/(\d{4,6})(CE|PE)/i) || (contract.display_name && contract.display_name.match(/(\d{4,6})/));
    let strike = MasterState.currentStrike;
    if (match) {
        strike = Number(match[1]);
    }

    loadStrikePair(strike, MasterState.selectedExpiry, MasterState.selectedSessionDate);
}

// ─────────────────────────────────────────────────────────────────────────────
// ── CANDLE LOADING ────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

async function loadContractCandles(symbol, optDate) {
    try {
        const targetDate = optDate || C.selectedSessionDate || 'today';
        const dateParam = (targetDate && targetDate !== 'today') ? `&date=${encodeURIComponent(targetDate)}` : (targetDate === 'today' ? '&date=today' : '');
        const res = await fetch(`/api/contracts/candles?symbol=${encodeURIComponent(symbol)}${dateParam}`);
        const data = await res.json();

        const candles = data.candles || [];
        if (!candles.length) {
            if (data.market_status === 'PRE_MARKET' || targetDate === 'today') {
                showOverlay('⏰', 'Today\'s Session: Live Feed Ready', 'Waiting for live ticks from the broker feed. Chart will auto-populate as live bars stream in.');
            } else {
                showOverlay('📉', 'No Data Available', `No trading candles found for ${symbol} on ${data.date || targetDate}.`);
            }
            return;
        }

        hideOverlay();
        const formatted = formatCandles(candles);
        C.rawCandles = formatted;

        if (data.quote) {
            C.liveQuote = data.quote;
        }

        // Inform user if showing previous trading day data on weekend / market closed
        const statusLabel = document.getElementById('market-status-label');
        if (statusLabel) {
            if (data.is_last_day_data && data.date) {
                statusLabel.textContent = `Historical (${data.date})`;
            } else if (targetDate === 'today') {
                statusLabel.textContent = `Live Session (${data.date || 'Today'})`;
            }
        }

        updateSessionStats(formatted);
        applyFullChart(formatted);

        // Sync session-date-select if it has loaded
        const dateSel = document.getElementById('session-date-select');
        if (dateSel && data.date) {
            if (targetDate === 'today') {
                dateSel.value = 'today';
            } else if ([...dateSel.options].some(o => o.value === data.date)) {
                dateSel.value = data.date;
            }
        }
        const rpDatePicker = document.getElementById('rp-date-picker');
        if (rpDatePicker && data.date) {
            rpDatePicker.value = data.date;
        }

        // Start periodic quote polling (5s) for live Bid/Offer depth with error backoff
        if (C.quotePollInterval) clearInterval(C.quotePollInterval);
        let quoteErrorCount = 0;
        C.quotePollInterval = setInterval(async () => {
            if (!C.symbol || quoteErrorCount > 3) return;
            try {
                const qRes = await fetch(`/api/contracts/quote?symbol=${encodeURIComponent(C.symbol)}`);
                if (!qRes.ok) { quoteErrorCount++; return; }
                const qData = await qRes.json();
                if (qData && !qData.error) {
                    quoteErrorCount = 0;
                    C.liveQuote = qData;
                    if (C.rawCandles.length > 0 && C.smfLastResult) {
                        updateAdvancedStats(C.rawCandles, C.smfLastResult.vwap, C.smfLastResult.rsi, C.liveQuote);
                    }
                } else {
                    quoteErrorCount++;
                }
            } catch (err) {
                quoteErrorCount++;
            }
        }, 5000);

    } catch (e) {
        console.error('loadContractCandles error:', e);
        showOverlay('⚠️', 'Failed to Load Data', 'Could not fetch candle data. Check your connection and try again.');
    }
}

function formatCandles(rawList) {
    const parsed = [];
    for (const c of rawList) {
        let t = c.timestamp !== undefined ? c.timestamp : c.time;
        if (typeof t === 'string') {
            const p = new Date(t).getTime();
            t = isNaN(p) ? 0 : Math.floor(p / 1000);
        }
        t = Number(t);
        if (isNaN(t) || t <= 0) continue;
        parsed.push({
            time: t,
            open: Number(c.open),
            high: Number(c.high),
            low: Number(c.low),
            close: Number(c.close),
            volume: Number(c.volume || 0),
        });
    }
    parsed.sort((a, b) => a.time - b.time);
    const out = [];
    let last = 0;
    for (const c of parsed) {
        if (c.time <= last) continue;
        last = c.time;
        out.push(c);
    }
    return out;
}

function aggregateCandles(candles, tfMin) {
    if (!candles || candles.length === 0 || tfMin <= 1) return candles;
    const tfSec = tfMin * 60;
    const agg = [];
    let cur = null;
    for (const c of candles) {
        const bucket = Math.floor(c.time / tfSec) * tfSec;
        if (!cur || cur.time !== bucket) {
            if (cur) agg.push(cur);
            cur = { time: bucket, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume || 0 };
        } else {
            cur.high = Math.max(cur.high, c.high);
            cur.low = Math.min(cur.low, c.low);
            cur.close = c.close;
            cur.volume = (cur.volume || 0) + (c.volume || 0);
        }
    }
    if (cur) agg.push(cur);
    return agg;
}

function applyFullChart(candles1m, shouldFit = true) {
    const agg = aggregateCandles(candles1m, C.currentTf);
    applySMFCloud(agg);
    const countEl = document.getElementById('stat-candles');
    if (countEl) countEl.textContent = agg.length;
    if (candles1m && candles1m.length > 0) {
        updateMarketBuyButton(candles1m[candles1m.length - 1].close);
    }
    if (shouldFit && !C.replayMode && C.chart && agg.length > 0) {
        setTimeout(() => {
            if (!C.replayMode && C.chart) {
                const total = agg.length;
                const visibleBars = Math.min(total, 85);
                C.chart.timeScale().setVisibleLogicalRange({
                    from: Math.max(0, total - visibleBars),
                    to: total + 25
                });
            }
        }, 50);
    }
}

function clearChart() {
    if (C.quotePollInterval) {
        clearInterval(C.quotePollInterval);
        C.quotePollInterval = null;
    }
    C.liveQuote = null;
    C.rawCandles = [];
    C.smfLastResult = null;
    C.sessionOpen = null;
    C.sessionHigh = null;
    C.sessionLow = null;
    C.sessionClose = null;
    if (C.candleSeries) C.candleSeries.setData([]);
    if (C.smfUpperSeries) C.smfUpperSeries.setData([]);
    if (C.smfLowerSeries) C.smfLowerSeries.setData([]);
    if (C.smfBcSeries) C.smfBcSeries.setData([]);
    if (C.smfBoSeries) C.smfBoSeries.setData([]);
    clearAllPriceLines();
    const canvas = document.getElementById('contract-smf-canvas');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
    updateSessionStatsDisplay(null, null, null, null);
    document.getElementById('stat-candles').textContent = '0';
    document.getElementById('gauge-fill').style.width = '0%';
    document.getElementById('gauge-pct').textContent = '—';
}

function updateSessionStats(candles) {
    if (!candles || !candles.length) return;
    C.sessionOpen = candles[0].open;
    C.sessionHigh = Math.max(...candles.map(c => c.high));
    C.sessionLow  = Math.min(...candles.map(c => c.low));
    C.sessionClose = candles[candles.length - 1].close;
    updateSessionStatsDisplay(C.sessionOpen, C.sessionHigh, C.sessionLow, C.sessionClose);
}

function updateSessionStatsDisplay(open, high, low, ltp) {
    const fmt = v => v != null ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : '——';
    const elOpen = document.getElementById('stat-open');
    if (elOpen) elOpen.textContent = fmt(open);
    const elHigh = document.getElementById('stat-high');
    if (elHigh) elHigh.textContent = fmt(high);
    const elLow = document.getElementById('stat-low');
    if (elLow) elLow.textContent  = fmt(low);
    const elLtp = document.getElementById('stat-ltp');
    if (elLtp) elLtp.textContent  = fmt(ltp);
}

// ─────────────────────────────────────────────────────────────────────────────
// ── WEBSOCKET ─────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

function connectContractWS(symbol) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}/ws/contracts?symbol=${encodeURIComponent(symbol)}`;

    C.ws = new WebSocket(url);

    C.ws.onopen = () => {
        setConnStatus('live', 'Live');
    };

    if (C.wsPingInterval) clearInterval(C.wsPingInterval);
    C.wsPingInterval = setInterval(() => {
        if (C.ws && C.ws.readyState === WebSocket.OPEN) {
            C.ws.send(JSON.stringify({ type: 'PING' }));
        }
    }, 5000);

    C.ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'CONTRACT_TICK') {
                handleContractTick(data);
            }
        } catch (e) {
            console.error('WS parse error:', e);
        }
    };

    C.ws.onerror = () => {
        setConnStatus('disconnected', 'Error');
    };

    C.ws.onclose = () => {
        setConnStatus('disconnected', 'Reconnecting…');
        if (C.symbol === symbol) {
            setTimeout(() => connectContractWS(symbol), 3000);
        }
    };
}

function disconnectContractWS() {
    if (C.wsPingInterval) clearInterval(C.wsPingInterval);
    if (C.ws) {
        C.ws.onclose = null; // prevent auto-reconnect
        try { C.ws.close(); } catch(e) {}
        C.ws = null;
    }
    setConnStatus('waiting', 'Waiting…');
}

// ── ZERO-LATENCY LIVE TICK ORDER & TARGET EXECUTION ENGINE ─────────────────

function flashExecutionAlert(title, message, type = 'success') {
    let container = document.getElementById('execution-toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'execution-toast-container';
        container.style.cssText = 'position:fixed;top:56px;right:20px;z-index:99999;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    const color = type === 'success' ? '#00f2a9' : (type === 'danger' ? '#ff4d6d' : (type === 'warning' ? '#ffd166' : '#00e5ff'));
    const bg = type === 'success' ? 'rgba(0, 242, 169, 0.18)' : (type === 'danger' ? 'rgba(255, 77, 109, 0.18)' : (type === 'warning' ? 'rgba(255, 209, 102, 0.18)' : 'rgba(0, 229, 255, 0.18)'));
    toast.style.cssText = `background:${bg};backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border:1px solid ${color};color:#fff;padding:10px 16px;border-radius:8px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,0.5), 0 0 12px ${color}44;pointer-events:auto;transition:opacity 0.3s ease, transform 0.3s ease;`;
    toast.innerHTML = `<div style="font-weight:700;color:${color};margin-bottom:2px;">${title}</div><div style="font-size:12px;color:rgba(255,255,255,0.85);">${message}</div>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-10px)';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function dispatchOrderExecutionEvent(payload) {
    if (!payload || !payload.symbol) return;
    try {
        fetch('/api/events/order_execution', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            keepalive: true
        }).catch(err => console.debug('Order execution dispatch error:', err));
    } catch (e) {
        console.debug('dispatchOrderExecutionEvent error:', e);
    }
}

function checkImmediateTargetExecution(ltp, timestamp) {
    if (ltp == null || ltp <= 0) return;

    // 1. Check SMF Dynamic Exit Active Trade Stops & Targets
    if (C.dynamicExitEnabled && C.dynamicExitLastResult && C.dynamicExitLastResult.activeTrade) {
        const t = C.dynamicExitLastResult.activeTrade;

        // A. Catastrophic Stop Breach (Sub-second emergency exit)
        if (t.catastrophicStop && ltp <= t.catastrophicStop && !t._catastrophic_executed) {
            t._catastrophic_executed = true;
            dispatchOrderExecutionEvent({
                symbol: C.symbol,
                action: 'CATASTROPHIC_STOP',
                target_level: 'CATASTROPHIC_STOP',
                target_price: t.catastrophicStop,
                execution_price: ltp,
                timestamp: timestamp || new Date().toISOString(),
                details: { entryPrice: t.entryPrice, R: t.R, hh: t.hh, mfeR: t.mfeR }
            });
            flashExecutionAlert('🛑 CATASTROPHIC STOP EXECUTED', `Executed exit @ ₹${ltp.toFixed(2)}`, 'danger');
            return;
        }

        // B. Hard Stop / Ladder Floor Breach
        const hardStop = Math.max(t.initialStop || 0, t.ladderFloor || 0);
        if (hardStop > 0 && ltp <= hardStop && !t._stop_executed) {
            t._stop_executed = true;
            const isLadder = (t.ladderFloor || 0) > (t.initialStop || 0);
            dispatchOrderExecutionEvent({
                symbol: C.symbol,
                action: isLadder ? 'LADDER_FLOOR_STOP' : 'INITIAL_STOP',
                target_level: isLadder ? 'LADDER_FLOOR' : 'INITIAL_STOP',
                target_price: hardStop,
                execution_price: ltp,
                timestamp: timestamp || new Date().toISOString(),
                details: { entryPrice: t.entryPrice, R: t.R, hh: t.hh, mfeR: t.mfeR }
            });
            flashExecutionAlert(isLadder ? '🛡️ LADDER FLOOR STOP EXECUTED' : '🛑 STOP LOSS EXECUTED', `Exited @ ₹${ltp.toFixed(2)}`, 'warning');
            return;
        }

        // C. Partial Profit Lock (P1 at +1.5R)
        if (C.dynamicExitVariant === 'V5_P1' && !t.p1Booked && (ltp - t.entryPrice) >= (1.5 * t.R)) {
            t.p1Booked = true;
            const partialTarget = t.entryPrice + 1.5 * t.R;
            dispatchOrderExecutionEvent({
                symbol: C.symbol,
                action: 'PARTIAL_PROFIT_P1',
                target_level: 'P1_PARTIAL_1.5R',
                target_price: partialTarget,
                execution_price: ltp,
                timestamp: timestamp || new Date().toISOString(),
                details: { entryPrice: t.entryPrice, lockedQty: 0.25, remainingQty: 0.75 }
            });
            flashExecutionAlert('💰 PARTIAL PROFIT LOCKED (25%)', `Locked +1.5R @ ₹${ltp.toFixed(2)}`, 'success');
        }
    }

    // 2. Check SMF Cloud Strategy Targets (Target 1, Target 2, Max Target)
    if (C.smfLastResult && C.smfLastResult.latestTradeSignal) {
        const sig = C.smfLastResult.latestTradeSignal;
        if (sig.direction === 'BUY') {
            // Target 1 (Scalp Target 50%)
            if (sig.target1 && ltp >= sig.target1 && !sig._t1_executed) {
                sig._t1_executed = true;
                dispatchOrderExecutionEvent({
                    symbol: C.symbol,
                    action: 'TARGET_1_HIT',
                    target_level: 'TARGET_1',
                    target_price: sig.target1,
                    execution_price: ltp,
                    timestamp: timestamp || new Date().toISOString(),
                    details: { entryPrice: sig.entryPrice, target1: sig.target1, maxTarget: sig.maxTarget }
                });
                flashExecutionAlert('🎯 TARGET 1 HIT!', `Executed scalp exit @ ₹${ltp.toFixed(2)}`, 'success');
            }

            // Max Runner Target
            if (sig.maxTarget && ltp >= sig.maxTarget && !sig._max_executed) {
                sig._max_executed = true;
                dispatchOrderExecutionEvent({
                    symbol: C.symbol,
                    action: 'MAX_RUNNER_TARGET_HIT',
                    target_level: 'MAX_TARGET',
                    target_price: sig.maxTarget,
                    execution_price: ltp,
                    timestamp: timestamp || new Date().toISOString(),
                    details: { entryPrice: sig.entryPrice, maxTarget: sig.maxTarget }
                });
                flashExecutionAlert('⚡ RUNNER TARGET HIT!', `Executed runner exit @ ₹${ltp.toFixed(2)}`, 'highlight');
            }

            // Stop Loss
            if (sig.sl && ltp <= sig.sl && !sig._sl_executed) {
                sig._sl_executed = true;
                dispatchOrderExecutionEvent({
                    symbol: C.symbol,
                    action: 'STRATEGY_STOP_LOSS',
                    target_level: 'STOP_LOSS',
                    target_price: sig.sl,
                    execution_price: ltp,
                    timestamp: timestamp || new Date().toISOString(),
                    details: { entryPrice: sig.entryPrice, sl: sig.sl }
                });
                flashExecutionAlert('🛑 STOP LOSS HIT!', `Executed stop exit @ ₹${ltp.toFixed(2)}`, 'danger');
            }
        }
    }
}

function updateDynamicExitLiveTick(ltp) {
    if (!C.dynamicExitEnabled || !C.dynamicExitLastResult) return;
    const t = C.dynamicExitLastResult.activeTrade;
    if (!t) return;

    // Instantly track high-water mark on live tick
    if (ltp > t.hh) {
        t.hh = ltp;
        t.mfeR = (t.hh - t.entryPrice) / t.R;
        if (t.mfeR >= 2.5) t.phase = 'RUNNER';
        else if (t.mfeR >= 1.0) t.phase = 'SECURED';
        else t.phase = 'PROTECT';
    }

    // Update Live PnL in footer HUD
    const livePnl = document.getElementById('de-live-pnl');
    const liveMfe = document.getElementById('de-live-mfe');
    const statusPill = document.getElementById('de-status-pill');

    if (liveMfe) liveMfe.textContent = `+${t.mfeR.toFixed(1)}R (₹${t.hh.toFixed(1)})`;
    if (statusPill) {
        statusPill.className = `de-pill ${t.phase.toLowerCase()}`;
        statusPill.textContent = `${t.phase} (${t.state})`;
    }
    if (livePnl) {
        const curPts = (ltp - t.entryPrice) * t.remQty + t.realizedPts - 0.40;
        const curR = curPts / t.R;
        const sign = curR >= 0 ? '+' : '';
        livePnl.textContent = `${sign}${curR.toFixed(2)}R`;
        livePnl.className = `de-stat-val ${curR >= 0 ? 'success' : 'danger'}`;
    }
}

function handleContractTick(data) {
    if (C.replayMode) return; // Suppress live ticks while in Bar Replay
    const ltp = data.ltp;
    if (ltp == null) return;

    // ── 1. ZERO-LATENCY TARGET & STOP-LOSS IMMEDIATE EXECUTION ──────────────
    // Every tick matters: Evaluates instantly in O(1) time (< 1 microsecond)
    checkImmediateTargetExecution(ltp, data.timestamp);
    updateDynamicExitLiveTick(ltp);

    // Hide any "No Data Yet" overlay once real live tick arrives
    hideOverlay();

    // Update badge & buy button live premium
    document.getElementById('badge-ltp').textContent = `₹${ltp.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
    updateMarketBuyButton(ltp);
    updateAtmPairPillLiveLtp(ltp);

    if (C.tradingState && C.tradingState.active_position && C.tradingState.active_position.symbol === C.symbol) {
        updateTradingUI(C.tradingState);
        requestAnimationFrame(renderSMFCanvas);
    }
    const chgEl = document.getElementById('badge-chg');
    if (C.sessionOpen != null) {
        const chgPct = ((ltp - C.sessionOpen) / C.sessionOpen * 100);
        const chgAbs = ltp - C.sessionOpen;
        chgEl.textContent = `${chgAbs >= 0 ? '+' : ''}${chgAbs.toFixed(2)} (${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(2)}%)`;
        chgEl.className = chgAbs >= 0 ? 'pos' : 'neg';
    }

    // Update session stats
    if (C.sessionOpen == null) C.sessionOpen = ltp;
    if (C.sessionHigh == null || ltp > C.sessionHigh) C.sessionHigh = ltp;
    if (C.sessionLow  == null || ltp < C.sessionLow)  C.sessionLow  = ltp;
    C.sessionClose = ltp;
    updateSessionStatsDisplay(C.sessionOpen, C.sessionHigh, C.sessionLow, ltp);

    // Push forming candle to chart
    if (C.candleSeries && data.forming_candle) {
        const fc = data.forming_candle;
        const oneMinCandle = {
            time: Number(fc.time),
            open: Number(fc.open),
            high: Number(fc.high),
            low: Number(fc.low),
            close: Number(fc.close),
        };

        // Keep raw 1-min candles in sync
        _upsertFormingInRaw(oneMinCandle);

        // Calculate the candle to push to the series based on current timeframe
        let candleToPush;
        if (C.currentTf <= 1) {
            candleToPush = { ...oneMinCandle };
        } else {
            const tfSec = C.currentTf * 60;
            const bucket = Math.floor(oneMinCandle.time / tfSec) * tfSec;
            const bucketBars = C.rawCandles.filter(c => Math.floor(c.time / tfSec) * tfSec === bucket);
            if (bucketBars.length > 0) {
                candleToPush = {
                    time: bucket,
                    open: bucketBars[0].open,
                    high: Math.max(...bucketBars.map(c => c.high), oneMinCandle.high),
                    low: Math.min(...bucketBars.map(c => c.low), oneMinCandle.low),
                    close: oneMinCandle.close,
                };
            } else {
                candleToPush = { ...oneMinCandle, time: bucket };
            }
        }

        // ── Real-time SMF Cloud recomputation on every incoming tick ─────────
        const agg = aggregateCandles(C.rawCandles, C.currentTf);
        if (agg && agg.length >= 5) {
            const smf = computeSMF(agg);
            if (smf) {
                C.smfLastResult = smf;
                const lastIdx = agg.length - 1;
                const latestRegime = smf.lastSignal[lastIdx];
                let col = latestRegime === 1 ? SMF.bullColor : SMF.bearColor;
                if (!C.smfEnabled) {
                    col = candleToPush.close >= candleToPush.open ? '#00f2a9' : '#ff4d6d';
                }

                candleToPush.color = col;
                candleToPush.borderColor = col;
                candleToPush.wickColor = col;

                // Update indicator line series so cloud advances with forming candle
                if (C.smfEnabled) {
                    if (C.smfBcSeries && smf.bMain[lastIdx] != null) {
                        try { C.smfBcSeries.update({ time: candleToPush.time, value: smf.bMain[lastIdx] }); } catch(e) {}
                    }
                    if (C.smfUpperSeries && smf.upper[lastIdx] != null) {
                        try { C.smfUpperSeries.update({ time: candleToPush.time, value: smf.upper[lastIdx] }); } catch(e) {}
                    }
                    if (C.smfLowerSeries && smf.lower[lastIdx] != null) {
                        try { C.smfLowerSeries.update({ time: candleToPush.time, value: smf.lower[lastIdx] }); } catch(e) {}
                    }
                }

                // Update live maximum target lines
                if (C.targetsEnabled) {
                    if (C.smfTargetUpperSeries && smf.targetMaxUpperPoints && smf.targetMaxUpperPoints[lastIdx]) {
                        try { C.smfTargetUpperSeries.update(smf.targetMaxUpperPoints[lastIdx]); } catch(e) {}
                    }
                    if (C.smfTargetLowerSeries && smf.targetMaxLowerPoints && smf.targetMaxLowerPoints[lastIdx]) {
                        try { C.smfTargetLowerSeries.update(smf.targetMaxLowerPoints[lastIdx]); } catch(e) {}
                    }
                }

                // Update live horizontal targets & badges
                if (smf.latestTradeSignal) {
                    updateTargetPriceLines(smf.latestTradeSignal, candleToPush.close);
                }

                // Update trend gauge immediately
                updateGauge(smf.latestGaugeVal, smf.latestRegime);

                // Update Buy/Sell markers if signals changed
                const liveMarkers = [];
                if (C.smfEnabled && smf.smfMarkers && smf.smfMarkers.length > 0) {
                    liveMarkers.push(...smf.smfMarkers);
                }
                if (liveMarkers.length > 0) {
                    const aligned = alignContractMarkers(liveMarkers, agg);
                    try { C.candleSeries.setMarkers(aligned); } catch(e) {}
                }

                // Trigger zero-latency event notification when a new Buy/Sell signal occurs (exclude retest dots)
                const latestMarker = smf.smfMarkers && smf.smfMarkers.length > 0 ? smf.smfMarkers[smf.smfMarkers.length - 1] : null;
                const isBuy = latestMarker && latestMarker.signalType === 'BUY';
                const isSell = latestMarker && (latestMarker.signalType === 'SELL' || latestMarker.signalType === 'EXIT');
                if (latestMarker && (isBuy || isSell) && latestMarker.time === candleToPush.time && C._lastDispatchedSignalTime !== latestMarker.time) {
                    C._lastDispatchedSignalTime = latestMarker.time;
                    dispatchTrendSignalEvent({
                        symbol: C.symbol,
                        direction: isBuy ? 'BUY' : 'SELL',
                        price: candleToPush.close,
                        strategy: 'SMF_CLOUD',
                        timeframe: `${C.currentTf}m`,
                        market_regime: latestRegime === 1 ? 'BULLISH' : 'BEARISH',
                        details: {
                            bMain: smf.bMain[lastIdx],
                            upper: smf.upper[lastIdx],
                            lower: smf.lower[lastIdx],
                            target1: smf.latestTradeSignal?.target1,
                            max_target: smf.latestTradeSignal?.maxTarget,
                            limit_price: latestMarker?.limitPrice || smf.latestTradeSignal?.limitPrice,
                            max_limit: latestMarker?.maxLimitCeiling || smf.latestTradeSignal?.maxLimitCeiling,
                            sl: latestMarker?.stopLoss || smf.latestTradeSignal?.sl,
                            runway: latestMarker.runway,
                            extension: latestMarker.extension,
                            gauge: smf.latestGaugeVal,
                            forming_bar: candleToPush,
                        }
                    });
                }

                // Smoothly repaint canvas ribbon synchronized to monitor refresh rate (prevents CPU saturation)
                if (!C._canvasRenderPending) {
                    C._canvasRenderPending = true;
                    requestAnimationFrame(() => {
                        C._canvasRenderPending = false;
                        renderSMFCanvas();
                    });
                }
            }
        } else if (C.smfLastResult) {
            const d = new Date(candleToPush.time * 1000);
            const istH = (d.getUTCHours() + 5 + Math.floor((d.getUTCMinutes() + 30) / 60)) % 24;
            const istM = (d.getUTCMinutes() + 30) % 60;
            const cTimeStr = `${String(istH).padStart(2, '0')}:${String(istM).padStart(2, '0')}`;
            const isWarmup = (cTimeStr >= '09:15' && cTimeStr < '09:25');
            const isBull = C.smfLastResult.latestRegime === 1;

            let col = isBull ? SMF.bullColor : SMF.bearColor;
            let borderCol = col;
            let wickCol = col;
            if (isWarmup) {
                // Light pastel translucent warmup styling (09:15 - 09:25 IST)
                col = isBull ? 'rgba(0, 242, 169, 0.38)' : 'rgba(255, 77, 109, 0.38)';
                borderCol = isBull ? 'rgba(0, 242, 169, 0.55)' : 'rgba(255, 77, 109, 0.55)';
                wickCol = isBull ? 'rgba(0, 242, 169, 0.38)' : 'rgba(255, 77, 109, 0.38)';
            }
            candleToPush.color = col;
            candleToPush.borderColor = borderCol;
            candleToPush.wickColor = wickCol;
        }

        try {
            C.candleSeries.update(candleToPush);
        } catch (e) {
            console.warn('candleSeries.update error:', e);
        }
    }
}

function _upsertFormingInRaw(candleData) {
    const t = candleData.time;
    const idx = C.rawCandles.findIndex(c => c.time === t);
    if (idx >= 0) {
        C.rawCandles[idx] = { ...C.rawCandles[idx], ...candleData };
    } else {
        C.rawCandles.push({ ...candleData, volume: 0 });
    }
    // Update stat
    document.getElementById('stat-candles').textContent = C.rawCandles.length;
}

// ─────────────────────────────────────────────────────────────────────────────
// ── SMF CLOUD (identical algorithm to main dashboard) ─────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

function calcEMA(src, len) {
    const ema = new Array(src.length).fill(0);
    const k = 2 / (len + 1);
    ema[0] = src[0];
    for (let i = 1; i < src.length; i++) {
        ema[i] = src[i] * k + ema[i - 1] * (1 - k);
    }
    return ema;
}

function calcWilderATR(candles, length) {
    if (!candles || candles.length === 0) return [];
    const n = candles.length;
    const tr = new Array(n);
    tr[0] = candles[0].high - candles[0].low;
    for (let i = 1; i < n; i++) {
        const h = candles[i].high, l = candles[i].low, pc = candles[i - 1].close;
        tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
    }
    const atr = new Array(n);
    let sum = 0;
    const init = Math.min(length, n);
    for (let i = 0; i < init; i++) sum += tr[i];
    atr[init - 1] = sum / init;
    for (let i = init; i < n; i++) {
        atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length;
    }
    for (let i = 0; i < init - 1; i++) atr[i] = atr[init - 1];
    return atr;
}

// ── Multi-Factor Institutional Technical Indicators ──────────────────────────

function calcRSI(closes, period = 14) {
    const n = closes.length;
    const rsi = new Array(n).fill(50);
    if (n <= period) return rsi;

    let gains = 0, losses = 0;
    for (let i = 1; i <= period; i++) {
        const diff = closes[i] - closes[i - 1];
        if (diff >= 0) gains += diff;
        else losses -= diff;
    }
    let avgGain = gains / period;
    let avgLoss = losses / period;
    rsi[period] = avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss));

    for (let i = period + 1; i < n; i++) {
        const diff = closes[i] - closes[i - 1];
        const gain = diff >= 0 ? diff : 0;
        const loss = diff < 0 ? -diff : 0;

        avgGain = (avgGain * (period - 1) + gain) / period;
        avgLoss = (avgLoss * (period - 1) + loss) / period;

        if (avgLoss === 0) {
            rsi[i] = 100;
        } else {
            const rs = avgGain / avgLoss;
            rsi[i] = 100 - (100 / (1 + rs));
        }
    }
    for (let i = 0; i < period; i++) rsi[i] = rsi[period];
    return rsi;
}

function calcVWAP(candles) {
    const n = candles.length;
    const vwap = new Array(n);
    let cumVol = 0;
    let cumVolPrice = 0;

    for (let i = 0; i < n; i++) {
        const c = candles[i];
        const typical = (c.high + c.low + c.close) / 3;
        const vol = Math.max(c.volume || 0, 1);
        cumVol += vol;
        cumVolPrice += typical * vol;
        vwap[i] = cumVolPrice / cumVol;
    }
    return vwap;
}

function calcVolumeSMA(candles, period = 20) {
    const n = candles.length;
    const vsma = new Array(n).fill(0);
    let sum = 0;
    for (let i = 0; i < n; i++) {
        const v = candles[i].volume || 0;
        sum += v;
        if (i >= period) {
            sum -= (candles[i - period].volume || 0);
            vsma[i] = sum / period;
        } else {
            vsma[i] = sum / (i + 1);
        }
    }
    return vsma;
}

function alignContractMarkers(markers, candles) {
    if (!markers || !markers.length || !candles || !candles.length) return [];
    const candleTimes = new Set(candles.map(c => c.time));
    const minTime = candles[0].time;
    const maxTime = candles[candles.length - 1].time;

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
        if (mTime < minTime || mTime > maxTime) continue;

        if (!candleTimes.has(mTime)) {
            let closest = null, minDiff = Infinity;
            for (const c of candles) {
                const diff = Math.abs(c.time - mTime);
                if (diff < minDiff && diff <= 120) {
                    minDiff = diff;
                    closest = c.time;
                }
            }
            if (closest !== null) mTime = closest;
            else continue;
        }

        aligned.push({
            time: mTime,
            position: m.position || 'belowBar',
            color: m.color || '#00f2a9',
            shape: m.shape || 'square',
            text: m.text,
            signalType: m.signalType,
            runway: m.runway,
            score: m.score,
            extension: m.extension,
            limitPrice: m.limitPrice,
            maxLimitCeiling: m.maxLimitCeiling,
            targetPrice: m.targetPrice,
            target2: m.target2,
            maxTarget: m.maxTarget,
            stopLoss: m.stopLoss,
            candleLow: m.candleLow,
            candleClose: m.candleClose,
            dailyTradeNum: m.dailyTradeNum,
            details: m.details,
        });
    }
    aligned.sort((a, b) => a.time - b.time);
    return aligned;
}

// ── Institutional Limit & Target Price Predictor ─────────────────────────────
// Multi-window swing analysis (9, 21, 50 bars), volume profile, and active sellers
function calculateSMFLimitAndTarget(candles, i, atr, bMain, vsma, rsi) {
    const c = candles[i];
    const curClose = c.close;
    const curOpen = c.open;
    const curHigh = c.high;
    const curLow = c.low;
    const curAtr = atr[i] || 2.0;
    const curBm = bMain[i];

    // Multi-period lowest low & highest high
    const getLowest = (len) => {
        const start = Math.max(0, i - len + 1);
        let minL = Infinity;
        for (let k = start; k <= i; k++) {
            if (candles[k].low < minL) minL = candles[k].low;
        }
        return minL === Infinity ? curLow : minL;
    };
    const getHighest = (len) => {
        const start = Math.max(0, i - len + 1);
        let maxH = -Infinity;
        for (let k = start; k <= i; k++) {
            if (candles[k].high > maxH) maxH = candles[k].high;
        }
        return maxH === -Infinity ? curHigh : maxH;
    };

    const low9 = getLowest(9);
    const low21 = getLowest(21);
    const low50 = getLowest(50);
    const high10 = getHighest(10);
    const low10 = getLowest(10);
    const recentDipLow = getLowest(5);

    // Volume & active seller/buyer pressure analysis
    const vol = c.volume || 1;
    const avgVol = (vsma && vsma[i]) ? vsma[i] : 1;
    const rvol = vol / Math.max(avgVol, 1);

    const candleSpan = Math.max(curHigh - curLow, 0.05);
    const upperWick = curHigh - Math.max(curOpen, curClose);
    const lowerWick = Math.min(curOpen, curClose) - curLow;
    const sellerPressure = upperWick / candleSpan; // 0.0 to 1.0 (overhead rejection)
    const buyerPressure = lowerWick / candleSpan;   // 0.0 to 1.0 (dip absorption)

    // Momentum check across recent 3 candles
    const prev3Close = i >= 3 ? candles[i - 3].close : curOpen;
    const isDroppingRecently = curClose < prev3Close || sellerPressure > 0.42;

    // Calculate suggested limit price based on multi-window swing logic
    let suggestedLimit;
    if (!isDroppingRecently && rvol >= 1.15 && buyerPressure >= sellerPressure) {
        // Strong bullish thrust with shallow pullback (e.g. 34-36 on 36.30 high)
        const shallowDip = curClose - (0.20 * curAtr);
        suggestedLimit = Math.max(shallowDip, recentDipLow, curClose * 0.96);
    } else if (isDroppingRecently) {
        // Price was dropping recently / sellers active: expect small drop towards 9-period support
        const dropSupport = Math.max(low9, curBm, curClose - (0.55 * curAtr));
        suggestedLimit = (curClose + dropSupport) / 2;
    } else {
        // Balanced regime: mid-point between recent dip support and trigger close
        const clusterSupport = Math.max(recentDipLow, curBm, curClose - (0.35 * curAtr));
        suggestedLimit = Math.max(clusterSupport, curClose - (0.30 * curAtr));
    }

    // Safety rules:
    suggestedLimit = Math.min(suggestedLimit, curClose);
    if (curBm && suggestedLimit < curBm) {
        suggestedLimit = Math.max(curBm, curClose - curAtr);
    }
    // Round to standard Indian option tick size (0.05)
    suggestedLimit = Math.round(suggestedLimit * 20) / 20;
    if (suggestedLimit <= 0) suggestedLimit = curClose;

    const maxLimitCeiling = Math.round(Math.min(curClose + 0.30 * curAtr, curClose * 1.02) * 20) / 20;

    // 1. Capped Risk Stop Loss: Capped between ₹4.0 and ₹10.0 (or 1.2 * ATR)
    const slRisk = Math.max(3.0, Math.min(10.0, curAtr * 1.2));
    const stopLoss = Math.round(Math.max(suggestedLimit - slRisk, curBm - 0.75 * curAtr) * 20) / 20;
    const risk = Math.max(3.0, suggestedLimit - stopLoss);

    // 2. Scalp Target 1 (50% Qty Lock): 1.2x Risk or at least 7.5 pts
    // Ensures net profit is well above statutory taxes (~1.0 pt on NIFTY)
    const minTarget1Pts = Math.max(7.5, curAtr * 1.2);
    const scalpPts = Math.max(minTarget1Pts, risk * 1.2);
    const target1 = Math.round((suggestedLimit + scalpPts) * 20) / 20;

    // 3. Break-Even Lock Trigger Level (Entry + costs)
    const beLockTrigger = Math.round((suggestedLimit + Math.max(4.5, curAtr * 0.8)) * 20) / 20;

    // 4. Runner Target 2: 2.2x Risk or 16.0 pts
    const target2 = Math.round((suggestedLimit + Math.max(16.0, risk * 2.2, curAtr * 2.2)) * 20) / 20;
    
    // 5. Maximum Gamma Target: 3.2x Risk or 26.0 pts
    const maxTarget = Math.round((suggestedLimit + Math.max(26.0, risk * 3.2, curAtr * 3.2)) * 20) / 20;

    // 5. Session Momentum Window Check (IST):
    // Morning Ignition: 09:30 - 10:45 | Afternoon Momentum: 13:15 - 14:30 | Midday Chop: 11:00 - 13:15
    const cDate = new Date(c.time * 1000);
    const hrs = cDate.getHours();
    const mins = cDate.getMinutes();
    const isMorningIgnition = (hrs === 9 && mins >= 30) || (hrs === 10 && mins <= 45);
    const isAfternoonMomentum = (hrs === 13 && mins >= 15) || (hrs === 14 && mins <= 30);
    const isMiddayChop = (hrs === 11) || (hrs === 12) || (hrs === 13 && mins < 15);
    const isOptimalWindow = isMorningIgnition || isAfternoonMomentum;

    // 6. Velocity & Order Type Classification:
    // Rocket Momentum = high volume explosion + strong marubozu body + minimal overhead wick rejection
    // -> Requires IMMEDIATE MARKET ORDER so massive multi-bagger runners are never left behind!
    // Normal / Pullback = lower volume or overhead wick
    // -> Requires PASSIVE LIMIT ORDER to avoid buying at the top of range-bound chop!
    const isRocketMomentum = rvol >= 1.35 && (curClose - curOpen) >= (0.65 * candleSpan) && sellerPressure <= 0.25;
    const recommendedOrder = isRocketMomentum ? "MARKET" : "LIMIT";

    return {
        limitPrice: suggestedLimit,
        maxLimitCeiling,
        limitRange: [suggestedLimit, curClose],
        stopLoss,
        risk,
        target1,
        beLockTrigger,
        target2,
        maxTarget,
        isOptimalWindow,
        isMiddayChop,
        isRocketMomentum,
        recommendedOrder,
        sessionLabel: isOptimalWindow ? (isMorningIgnition ? "MORNING IGNITION" : "AFTERNOON RUNNER") : (isMiddayChop ? "MIDDAY CHOP (AVOID)" : "SESSION BOUNDARY"),
        rvol,
        sellerPressure,
        buyerPressure,
        low9,
        low21,
        low50,
    };
}

function computeSMF(candles) {
    if (!candles || candles.length < 5) return null;
    const n = candles.length;

    const opens = candles.map(c => c.open);
    const closes = candles.map(c => c.close);
    const highs = candles.map(c => c.high);
    const lows = candles.map(c => c.low);

    const fEMA = calcEMA(closes, SMF.fastLen);
    const sEMA = calcEMA(closes, SMF.slowLen);
    const bMain = fEMA.map((f, i) => (f + sEMA[i]) / 2);
    const atr = calcWilderATR(candles, SMF.atrLen);
    const rsi = calcRSI(closes, 14);
    const vwap = calcVWAP(candles);
    const vsma = calcVolumeSMA(candles, 20);

    // Confirmation requirements:
    // 2 consecutive closed bars on 1m (eliminates 1-bar flash drops/shakeouts),
    // 1 bar on higher timeframes (3m/5m/15m are already multi-minute aggregated)
    const reqBars = (C.currentTf && C.currentTf > 1) ? 1 : (SMF.confirmBars || 2);
    // Volatility buffer multiplier: 0.5x on 1m to filter out whipsaws; 0.35x on higher timeframes
    const mult = (C.currentTf && C.currentTf > 1) ? 0.35 : (SMF.atrMult || 0.5);
    const upper = bMain.map((b, i) => b + atr[i]);
    const lower = bMain.map((b, i) => b - atr[i]);

    const smfMarkers = [];
    const lastSignal = new Array(n);

    // Initial regime based on first candle relative to baseline
    let prevRegime = closes[0] >= bMain[0] ? 1 : -1;
    lastSignal[0] = prevRegime;
    let belowCount = 0;
    let aboveCount = 0;
    let cloudBullStreak = 0;

    // Daily trade tracking for strict 5-trade limit & active signal targets
    let currentDayStr = null;
    let dailyTradesCount = 0;
    let inLongPosition = false;
    let activeTradeSignal = null;
    let target1Achieved = false;
    let continuationCount = 0;
    let lastBuyBar = -9999;

    for (let i = 0; i < n; i++) {
        const c = candles[i];
        const buf = atr[i] * mult;

        const cloudTop = Math.max(fEMA[i], sEMA[i]);
        const cloudBottom = Math.min(fEMA[i], sEMA[i]);
        const cloudBull = fEMA[i] >= sEMA[i];

        if (cloudBull) cloudBullStreak++;
        else cloudBullStreak = 0;

        const isBelow = c.close < cloudBottom - buf;
        const isAbove = c.close > cloudTop + buf;

        if (isBelow) {
            belowCount++;
            aboveCount = 0;
        } else if (isAbove) {
            aboveCount++;
            belowCount = 0;
        } else {
            belowCount = 0;
            aboveCount = 0;
        }

        // Multi-Bar Confirmed Trend Reversal with Strict Support & Resistance Validation:
        // Requires consecutive closes beyond the buffer plus structural resistance clearance.
        let currLS = prevRegime;
        if (prevRegime === 1 && belowCount >= reqBars) {
            currLS = -1;
            belowCount = 0; // reset streak after confirmed flip
        } else if (prevRegime === -1 && aboveCount >= reqBars) {
            // High-Conviction Bullish Filter:
            // 1. Block entries during midday dead chop (11:15 - 13:15 IST)
            // 2. Cloud Ribbon must be confirmed Bullish for >= 2 consecutive bars (prevents single-bar traps)
            // 3. Candle body must be solid bullish (bodyRatio >= 0.50, no shooting star / upper wick rejections)
            // 4. RSI >= 48 (healthy expanding momentum)
            // 5. Structure Break (CHoCH): must clear 8-bar swing high (clearing immediate resistance ceiling)
            const d = new Date(c.time * 1000);
            const istH = (d.getUTCHours() + 5 + Math.floor((d.getUTCMinutes() + 30) / 60)) % 24;
            const istM = (d.getUTCMinutes() + 30) % 60;
            const cTimeStr = `${String(istH).padStart(2, '0')}:${String(istM).padStart(2, '0')}`;
            const isWarmup = (cTimeStr >= '09:15' && cTimeStr < '09:25');

            const span = Math.max(c.high - c.low, 0.05);
            const bodyRatio = (c.close - c.low) / span;

            const startIdx = Math.max(0, i - 8);
            let prevSwingHigh = -Infinity;
            for (let k = startIdx; k < i; k++) {
                if (highs[k] > prevSwingHigh) prevSwingHigh = highs[k];
            }
            const brokeResistance = (c.close >= prevSwingHigh - 0.10 * atr[i]);

            if (!isWarmup && cloudBullStreak >= 2 && bodyRatio >= 0.50 && rsi[i] >= 48 && brokeResistance) {
                currLS = 1;
                aboveCount = 0; // reset streak after confirmed flip
            }
        }

        lastSignal[i] = currLS;
        const prevSignal = (i === 0) ? currLS : lastSignal[i - 1];

        // Option Buyer State & Strict 5-Trade Daily Limit
        const cDate = new Date(c.time * 1000).toDateString();
        if (cDate !== currentDayStr) {
            currentDayStr = cDate;
            dailyTradesCount = 0;
            inLongPosition = false;
            activeTradeSignal = null;
            target1Achieved = false;
            continuationCount = 0;
            lastBuyBar = -9999;
        }

        // ── Support Reversal Reclaim (SRR) Early Reversal Detection ──────────
        // When price tests base support and has wide runway (>= 22 pts) to prior session resistance,
        // trigger an early high-reward SRR BUY entry!
        let isSRR = false;
        let srrDipLow = c.low;
        if (i >= 20 && !inLongPosition) {
            const d = new Date(c.time * 1000);
            const istH = (d.getUTCHours() + 5 + Math.floor((d.getUTCMinutes() + 30) / 60)) % 24;
            const istM = (d.getUTCMinutes() + 30) % 60;
            const cTimeStr = `${String(istH).padStart(2, '0')}:${String(istM).padStart(2, '0')}`;
            const inSrrWindow = (cTimeStr >= '09:35' && cTimeStr <= '11:00') || (cTimeStr >= '13:15' && cTimeStr <= '14:30');

            if (inSrrWindow) {
                let morningBase = Infinity;
                const baseEnd = Math.min(26, i);
                for (let k = 5; k < baseEnd; k++) {
                    if (lows[k] < morningBase) morningBase = lows[k];
                }

                const startDip = Math.max(0, i - 4);
                let dip5 = Infinity;
                for (let k = startDip; k <= i; k++) {
                    if (lows[k] < dip5) dip5 = lows[k];
                }
                srrDipLow = dip5;

                const supCeiling = morningBase + 1.2 * atr[i];
                const inSupportZone = (dip5 <= supCeiling) && (dip5 >= morningBase - 0.5 * atr[i]);

                let resCeiling = -Infinity;
                for (let k = 0; k < i; k++) {
                    if (highs[k] > resCeiling) resCeiling = highs[k];
                }
                const runway = resCeiling - c.close;
                const hasWideRunway = (runway >= Math.max(20.0, 2.0 * atr[i]));

                const prev2High = Math.max(highs[Math.max(0, i - 2)], highs[Math.max(0, i - 1)]);
                const isReversal = (c.close > c.open) && (c.close > prev2High) && (rsi[i] >= 45.0);

                if (inSupportZone && hasWideRunway && isReversal) {
                    isSRR = true;
                }
            }
        }

        // Confirmed trend switch: fires on confirmed regime transition OR Support Reversal Reclaim (SRR)
        const isSwitchUp = (currLS === 1 && prevSignal === -1);
        const isSwitchDown = (currLS === -1 && prevSignal === 1);
        prevRegime = currLS;

        if (SMF.showSwitch) {
            const ext = Number((Math.abs(c.close - bMain[i]) / (atr[i] || 1.0)).toFixed(2));
            const priceStr = c.close.toFixed(2);
            if (isSwitchUp || (isSRR && !inLongPosition)) {
                // Strict 5 trades per day cap
                if (dailyTradesCount < 5) {
                    dailyTradesCount++;
                    inLongPosition = true;
                    target1Achieved = false;
                    continuationCount = 0;
                    lastBuyBar = i;

                    const tradeMetrics = calculateSMFLimitAndTarget(candles, i, atr, bMain, vsma, rsi);
                    if (isSRR && !isSwitchUp) {
                        const srrStop = Math.round(Math.max(srrDipLow - 1.5, c.close - 16.0) * 20) / 20;
                        tradeMetrics.stopLoss = srrStop;
                        tradeMetrics.risk = Math.max(4.0, c.close - srrStop);
                        tradeMetrics.target1 = Math.round((c.close + Math.max(7.5, atr[i] * 1.1)) * 20) / 20;
                        tradeMetrics.target2 = Math.round((c.close + Math.max(16.0, tradeMetrics.risk * 2.2, atr[i] * 2.2)) * 20) / 20;
                        tradeMetrics.maxTarget = Math.round((c.close + Math.max(26.0, tradeMetrics.risk * 3.2, atr[i] * 3.2)) * 20) / 20;
                    }
                    activeTradeSignal = tradeMetrics;
                    const limStr = tradeMetrics.limitPrice.toFixed(2);
                    const label = (isSRR && !isSwitchUp) ? '▲ SRR BUY' : '▲ BUY';

                    smfMarkers.push({
                        time: c.time,
                        position: 'belowBar',
                        color: SMF.buyColor, // Opaque vibrant green
                        shape: 'arrowUp',    // Clean directional arrow
                        text: label,
                        signalType: (isSRR && !isSwitchUp) ? 'SRR_BUY' : 'BUY',
                        limitPrice: tradeMetrics.limitPrice,
                        maxLimitCeiling: tradeMetrics.maxLimitCeiling,
                        targetPrice: tradeMetrics.target1,
                        target2: tradeMetrics.target2,
                        maxTarget: tradeMetrics.maxTarget,
                        beLockTrigger: tradeMetrics.beLockTrigger,
                        stopLoss: tradeMetrics.stopLoss,
                        isMiddayChop: tradeMetrics.isMiddayChop,
                        sessionLabel: (isSRR && !isSwitchUp) ? 'SUPPORT REVERSAL RECLAIM' : tradeMetrics.sessionLabel,
                        candleLow: c.low,
                        candleClose: c.close,
                        extension: ext,
                        dailyTradeNum: dailyTradesCount,
                    });
                }
            } else if (isSwitchDown) {
                // Option Buyer EXIT (Square-Off Long Position) - never naked short sell
                if (inLongPosition) {
                    inLongPosition = false;
                    activeTradeSignal = null;
                    target1Achieved = false;
                    const label = '▼ EXIT';
                    smfMarkers.push({
                        time: c.time,
                        position: 'aboveBar',
                        color: '#ff9f43',   // Distinct warm coral for EXIT (square-off)
                        shape: 'arrowDown',  // Clean exit arrow
                        text: label,
                        signalType: 'EXIT',
                        extension: ext,
                    });
                }
            }
        }

        // ── Trend Continuation / Re-Buy Trigger ──────────────────────────────
        // If Target 1 was achieved and momentum/volume remain strong,
        // show a continuation indicator (🚀) with updated limit and target
        if (inLongPosition && currLS === 1 && activeTradeSignal && i > lastBuyBar) {
            if (c.high >= activeTradeSignal.target1) {
                target1Achieved = true;
            }
            if (target1Achieved && (i - lastBuyBar >= 4) && continuationCount < 2) {
                const contMetrics = calculateSMFLimitAndTarget(candles, i, atr, bMain, vsma, rsi);
                const isHealthyVol = (c.volume || 1) >= (vsma[i] || 1) * 0.8;
                const isHealthyRsi = rsi[i] >= 50 && rsi[i] <= 82;
                const holdsAboveCloud = c.close >= bMain[i];
                const lowSellers = contMetrics.sellerPressure < 0.40;

                if (isHealthyVol && isHealthyRsi && holdsAboveCloud && lowSellers) {
                    lastBuyBar = i;
                    continuationCount++;
                    const contLimStr = contMetrics.limitPrice.toFixed(2);
                    const contTgt = contMetrics.target2 || contMetrics.maxTarget;
                    const contLabel = '▲ CONT';

                    smfMarkers.push({
                        time: c.time,
                        position: 'belowBar',
                        color: '#00e5ff', // Vibrant cyan for continuation
                        shape: 'arrowUp',
                        text: contLabel,
                        signalType: 'CONT_BUY',
                        limitPrice: contMetrics.limitPrice,
                        maxLimitCeiling: contMetrics.maxLimitCeiling,
                        targetPrice: contTgt,
                        target2: contMetrics.target2,
                        maxTarget: contMetrics.maxTarget,
                        stopLoss: contMetrics.stopLoss,
                        candleLow: c.low,
                        candleClose: c.close,
                        extension: Number((Math.abs(c.close - bMain[i]) / (atr[i] || 1.0)).toFixed(2)),
                        dailyTradeNum: dailyTradesCount,
                    });

                    activeTradeSignal.limitPrice = contMetrics.limitPrice;
                    activeTradeSignal.target1 = contTgt;
                }
            }
        }
    }

    // Retest signals (✦) with cooldown:
    // In Bullish (+1): Price dips to touch baseline (low <= bMain) but stays above breakdown buffer
    // In Bearish (-1): Price rallies to touch baseline (high >= bMain) but stays below breakout buffer
    let lastBearDotBar = -9999;
    let lastBullDotBar = -9999;
    if (SMF.showDots) {
        for (let i = 0; i < n; i++) {
            const c = candles[i];
            const buf = atr[i] * mult;
            const bearDotCond = (lastSignal[i] === -1 && c.high >= bMain[i] && c.close <= bMain[i] + buf);
            const bullDotCond = (lastSignal[i] === 1 && c.low <= bMain[i] && c.close >= bMain[i] - buf);

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

    // Dynamic Maximum Target & Exhaustion Bands (Options prices cannot be negative)
    const targetMaxUpper = bMain.map((b, i) => Math.max(0.10, b + (SMF.maxTargetMult * atr[i])));
    const targetMaxLower = bMain.map((b, i) => Math.max(0.10, b - (SMF.maxTargetMult * atr[i])));

    // Exhaustion Markers: flags when price reaches the Maximum Target (Option Buyer upward spikes)
    let lastExhaustionBar = -9999;
    for (let i = 0; i < n; i++) {
        const c = candles[i];
        if (lastSignal[i] === 1 && c.high >= targetMaxUpper[i]) {
            if (i - lastExhaustionBar >= 10) {
                lastExhaustionBar = i;
                smfMarkers.push({
                    time: c.time,
                    position: 'aboveBar',
                    color: SMF.targetColor || '#ffd166',
                    shape: 'circle',
                    text: `🎯 Max ₹${c.high.toFixed(1)}`,
                });
            }
        }
    }

    const bcPoints = [], boPoints = [], upPoints = [], loPoints = [];
    const targetMaxUpperPoints = [], targetMaxLowerPoints = [];
    for (let i = 0; i < n; i++) {
        const t = candles[i].time;
        bcPoints.push({ time: t, value: bMain[i] });
        boPoints.push({ time: t, value: null });
        upPoints.push({ time: t, value: upper[i] });
        loPoints.push({ time: t, value: lower[i] });
        targetMaxUpperPoints.push({ time: t, value: targetMaxUpper[i] });
        targetMaxLowerPoints.push({ time: t, value: Math.max(0.10, targetMaxLower[i]) });
    }

    // Find latest confirmed Buy or Exit signal to project static horizontal targets
    let latestTradeSignal = null;
    for (let i = n - 1; i >= 0; i--) {
        const isSwitchUp = (lastSignal[i] === 1 && (i === 0 || lastSignal[i - 1] === -1));
        const isSwitchDown = (lastSignal[i] === -1 && (i === 0 || lastSignal[i - 1] === 1));
        if (isSwitchUp || isSwitchDown) {
            const entryPrice = candles[i].close;
            const entryAtr = atr[i];
            const dir = isSwitchUp ? 'BUY' : 'EXIT';
            const tradeMetrics = dir === 'BUY' ? calculateSMFLimitAndTarget(candles, i, atr, bMain, vsma, rsi) : null;
            const sl = isSwitchUp ? (tradeMetrics ? tradeMetrics.stopLoss : (bMain[i] - 0.5 * entryAtr)) : bMain[i];
            const t1 = dir === 'BUY' ? (tradeMetrics ? tradeMetrics.target1 : (entryPrice + 1.5 * 2.0)) : entryPrice;
            const t2 = dir === 'BUY' ? (tradeMetrics ? tradeMetrics.target2 : (entryPrice + 3.0 * 2.0)) : entryPrice;
            const maxT = dir === 'BUY' ? (tradeMetrics ? tradeMetrics.maxTarget : (entryPrice + 4.5 * 2.0)) : entryPrice;
            const limP = tradeMetrics ? tradeMetrics.limitPrice : entryPrice;
            const maxLim = tradeMetrics ? tradeMetrics.maxLimitCeiling : entryPrice;
            
            latestTradeSignal = {
                index: i,
                time: candles[i].time,
                direction: dir,
                entryPrice,
                limitPrice: limP,
                maxLimitCeiling: maxLim,
                entryAtr,
                sl,
                target1: t1,
                target2: t2,
                maxTarget: maxT,
            };
            break;
        }
    }

    // Trend gauge
    const gaugeVals = [], smfRegimes = [];
    for (let i = 0; i < n; i++) {
        const basis = bMain[i];
        const upSpan = upper[i] - basis;
        const dnSpan = basis - lower[i];
        const raw = lastSignal[i] === 1
            ? (candles[i].close - basis) / (upSpan || 1)
            : -(basis - candles[i].close) / (dnSpan || 1);
        gaugeVals.push(Math.max(-1, Math.min(1, raw)));
        smfRegimes.push(lastSignal[i]);
    }

    return {
        n, upper, lower, bMain, lastSignal,
        atr, rsi, vsma,
        bcPoints, boPoints, upPoints, loPoints,
        targetMaxUpperPoints, targetMaxLowerPoints,
        targetMaxUpper, targetMaxLower, latestTradeSignal,
        smfMarkers,
        gaugeVals, smfRegimes,
        latestGaugeVal: gaugeVals[n - 1] || 0,
        latestRegime: smfRegimes[n - 1] || 1,
        candles,
    };
}

function applySMFCloud(agg) {
    if (!C.candleSeries) return;

    if (!agg || agg.length === 0) {
        C.candleSeries.setData([]);
        if (C.smfUpperSeries) C.smfUpperSeries.setData([]);
        if (C.smfLowerSeries) C.smfLowerSeries.setData([]);
        if (C.smfBcSeries) C.smfBcSeries.setData([]);
        if (C.smfBoSeries) C.smfBoSeries.setData([]);
        if (C.smfTargetUpperSeries) C.smfTargetUpperSeries.setData([]);
        if (C.smfTargetLowerSeries) C.smfTargetLowerSeries.setData([]);
        if (C.dynamicTrailSeries) C.dynamicTrailSeries.setData([]);
        if (C.dynamicLadderSeries) C.dynamicLadderSeries.setData([]);
        clearTargetPriceLines();
        const canvas = document.getElementById('contract-smf-canvas');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }
        updateGauge(0, 1);
        updateDynamicExitHUD(null, []);
        return;
    }

    const smf = computeSMF(agg);
    C.smfLastResult = smf;

    // Color candles (with soft light translucent styling for 09:15 - 09:25 warmup period)
    const coloredCandles = agg.map((c, i) => {
        const d = new Date(c.time * 1000);
        const istH = (d.getUTCHours() + 5 + Math.floor((d.getUTCMinutes() + 30) / 60)) % 24;
        const istM = (d.getUTCMinutes() + 30) % 60;
        const cTimeStr = `${String(istH).padStart(2, '0')}:${String(istM).padStart(2, '0')}`;
        const isWarmup = (cTimeStr >= '09:15' && cTimeStr < '09:25');
        const isBull = smf.lastSignal[i] === 1;

        let col, borderCol, wickCol;
        if (isWarmup) {
            // Light translucent pastel styling for indicator warmup / stabilization
            col = isBull ? 'rgba(0, 242, 169, 0.38)' : 'rgba(255, 77, 109, 0.38)';
            borderCol = isBull ? 'rgba(0, 242, 169, 0.55)' : 'rgba(255, 77, 109, 0.55)';
            wickCol = isBull ? 'rgba(0, 242, 169, 0.38)' : 'rgba(255, 77, 109, 0.38)';
        } else {
            col = isBull ? SMF.bullColor : SMF.bearColor;
            if (!C.smfEnabled) {
                col = c.close >= c.open ? '#00f2a9' : '#ff4d6d';
            }
            borderCol = col;
            wickCol = col;
        }

        return {
            ...c,
            color: col,
            borderColor: borderCol,
            wickColor: wickCol,
        };
    });
    C.candleSeries.setData(coloredCandles);

    if (C.smfEnabled) {
        if (C.smfBcSeries) C.smfBcSeries.setData(smf.bcPoints);
        if (C.smfUpperSeries) C.smfUpperSeries.setData(smf.upPoints);
        if (C.smfLowerSeries) C.smfLowerSeries.setData(smf.loPoints);
        if (C.smfBoSeries) C.smfBoSeries.setData(smf.boPoints);
    } else {
        if (C.smfBcSeries) C.smfBcSeries.setData([]);
        if (C.smfUpperSeries) C.smfUpperSeries.setData([]);
        if (C.smfLowerSeries) C.smfLowerSeries.setData([]);
        if (C.smfBoSeries) C.smfBoSeries.setData([]);
    }

    // Maximum Target Dynamic Volatility Bands (Upper & Lower)
    if (C.targetsEnabled && C.smfTargetUpperSeries && smf.targetMaxUpperPoints) {
        C.smfTargetUpperSeries.setData(smf.targetMaxUpperPoints);
    } else if (C.smfTargetUpperSeries) {
        C.smfTargetUpperSeries.setData([]);
    }

    if (C.targetsEnabled && C.smfTargetLowerSeries && smf.targetMaxLowerPoints) {
        C.smfTargetLowerSeries.setData(smf.targetMaxLowerPoints);
    } else if (C.smfTargetLowerSeries) {
        C.smfTargetLowerSeries.setData([]);
    }

    // Update horizontal target projections & header target badge
    updateTargetPriceLines(smf.latestTradeSignal, agg[agg.length - 1]?.close);

    // ── SMF Dynamic Exit & Chandelier Trailing Engine Execution ──────────────
    let deResult = null;
    if (C.dynamicExitEnabled) {
        deResult = computeSMFDynamicExit(agg, smf, C.dynamicExitVariant);
        C.dynamicExitLastResult = deResult;
        if (deResult) {
            if (C.dynamicTrailSeries && deResult.trailPoints) {
                C.dynamicTrailSeries.setData(deResult.trailPoints);
            }
            if (C.dynamicLadderSeries && deResult.ladderPoints) {
                C.dynamicLadderSeries.setData(deResult.ladderPoints);
            }
        }
    } else {
        if (C.dynamicTrailSeries) C.dynamicTrailSeries.setData([]);
        if (C.dynamicLadderSeries) C.dynamicLadderSeries.setData([]);
    }
    updateDynamicExitHUD(deResult, agg);

    // Combine markers: When Dynamic Exit is enabled, deResult.markers is the SINGLE SOURCE OF TRUTH
    // for trade entries, targets, and exits. This prevents duplicate BUY markers and prevents ghost EXIT
    // markers from appearing after a trade was already stopped out!
    const allMarkers = [];
    if (C.dynamicExitEnabled && deResult && deResult.markers && deResult.markers.length > 0) {
        allMarkers.push(...deResult.markers);
    } else if (C.smfEnabled && smf.smfMarkers && smf.smfMarkers.length > 0) {
        allMarkers.push(...smf.smfMarkers);
    }

    C.allRawSignalMarkers = allMarkers;
    try {
        // Clear default unstyled transparent markers from LightweightCharts to prevent overlaps
        C.candleSeries.setMarkers([]);
    } catch (e) {}

    updateGauge(smf.latestGaugeVal, smf.latestRegime);
    requestAnimationFrame(renderSMFCanvas);
}

// ─────────────────────────────────────────────────────────────────────────────
// ── SMF DYNAMIC EXIT & RE-ENTRY ENGINE (QUANTITATIVE 4-PILLAR LOGIC) ─────────
// ─────────────────────────────────────────────────────────────────────────────

function computeSMFDynamicExit(candles, smf, variant = 'V5') {
    if (!candles || candles.length < 5 || !smf) return null;
    const n = candles.length;
    const variantUpper = (variant || 'V5').toUpperCase();
    const enableP1 = variantUpper.includes('P1');

    const NSE_TICK = 0.05;
    const COSTS = 0.40;
    const SLIP_PCT = 0.003;
    const roundTick = (p) => Math.round(Number(p) * 20.0) / 20.0;
    const slip = (p) => Math.max(NSE_TICK, Number(p) * SLIP_PCT);

    const bMain = smf.bMain;
    const lastSignal = smf.lastSignal;
    const atr = smf.atr || calcWilderATR(candles, 14);
    const rsi = smf.rsi || calcRSI(candles.map(c => c.close), 14);
    const vsma = smf.vsma || calcVolumeSMA(candles, 20);

    const rvol = new Float64Array(n);
    const ext = new Float64Array(n);
    const sellerPressure = new Float64Array(n);
    const buyerPressure = new Float64Array(n);

    for (let i = 0; i < n; i++) {
        const c = candles[i];
        const v = c.volume || 1000;
        const vRef = (vsma && vsma[i] > 0) ? vsma[i] : 1000;
        rvol[i] = v / vRef;

        const a = (atr && atr[i] > 0) ? atr[i] : 2.0;
        ext[i] = Math.abs(c.close - bMain[i]) / a;

        const span = Math.max(c.high - c.low, 0.05);
        const upWick = c.high - Math.max(c.open, c.close);
        const dnWick = Math.min(c.open, c.close) - c.low;
        sellerPressure[i] = upWick / span;
        buyerPressure[i] = dnWick / span;
    }

    const getTimeStr = (t) => {
        let ms = 0;
        if (typeof t === 'number') {
            ms = t > 1e11 ? t : t * 1000;
        } else if (typeof t === 'string') {
            let str = t.trim();
            if (!str.endsWith('Z') && !str.includes('+')) {
                str = str.replace(' ', 'T') + 'Z';
            }
            const parsed = Date.parse(str);
            if (!isNaN(parsed)) {
                ms = parsed;
            } else {
                const parts = t.split(' ');
                return parts[parts.length - 1].slice(0, 5);
            }
        }
        if (ms <= 0) return '09:30';
        // UTC ms + 5:30 (19,800,000 ms) = IST
        const istDate = new Date(ms + 19800000);
        const h = String(istDate.getUTCHours()).padStart(2, '0');
        const m = String(istDate.getUTCMinutes()).padStart(2, '0');
        return `${h}:${m}`;
    };

    const isGammaIgnition = (i) => {
        const c = candles[i];
        const span = Math.max(c.high - c.low, 0.05);
        const bodyRatio = (c.close - c.low) / span;
        if (bodyRatio < 0.50) return false;
        const r = rsi[i];
        if (r < 48 || r > 75) return false;
        if (c.close < (bMain[i] + 0.20 * atr[i])) return false;
        return true;
    };

    const calcTHS = (i) => {
        let score = 10;
        const c = candles[i].close;
        const o = candles[i].open;
        const bm = bMain[i];
        const a = atr[i];
        if (c >= bm + 0.50 * a) score += 20;
        else if (c >= bm) score += 10;

        const bmPrev3 = i >= 3 ? bMain[i - 3] : bm;
        if (bm > bmPrev3) score += 15;

        if (i >= 5) {
            const recent3Low = Math.min(candles[i - 2].low, candles[i - 1].low, candles[i].low);
            const prior3Low = Math.min(candles[i - 5].low, candles[i - 4].low, candles[i - 3].low);
            if (recent3Low > prior3Low) score += 15;
        } else if (i >= 2) {
            score += 10;
        }

        const r = rsi[i];
        if (r >= 50 && r <= 80) score += 10;
        else if ((r >= 45 && r < 50) || r > 80) score += 5;

        const span = Math.max(candles[i].high - candles[i].low, 0.05);
        const isRedBlowout = (c < o) && (Math.abs(c - o) >= 0.60 * span) && (rvol[i] >= 1.5);
        if (isRedBlowout) score += 0;
        else if (rvol[i] >= 0.8) score += 10;
        else score += 5;

        const sStart = Math.max(0, i - 2);
        let sSum = 0;
        for (let k = sStart; k <= i; k++) sSum += sellerPressure[k];
        if ((sSum / (i - sStart + 1)) < 0.40) score += 10;

        if (ext[i] < 2.5) score += 10;
        else if (ext[i] <= 3.5) score += 5;

        return Math.min(100, Math.max(0, score));
    };

    const getHealthState = (ths, curState, improvingBars) => {
        let target = 'NORMAL';
        if (ths >= 70) target = 'STRONG';
        else if (ths >= 45) target = 'NORMAL';
        else target = 'WEAK';

        const rank = { WEAK: 1, NORMAL: 2, STRONG: 3, CLIMAX: 0 };
        const curRank = rank[curState] || 2;
        const targetRank = rank[target] || 2;

        if (targetRank < curRank) {
            return { state: target, improvingBars: 0 };
        } else if (targetRank > curRank) {
            const nextImp = improvingBars + 1;
            if (nextImp >= 2) return { state: target, improvingBars: 0 };
            return { state: curState, improvingBars: nextImp };
        }
        return { state: curState, improvingBars: 0 };
    };

    const calcRelaxedLadder = (t) => {
        if (t.mfeR >= 3.2) return roundTick(t.entryPrice + 0.60 * (t.hh - t.entryPrice));
        if (t.mfeR >= 2.2) return roundTick(t.entryPrice + 1.0 * t.R);
        if (t.mfeR >= 1.5) return roundTick(t.entryPrice + 0.50 * t.R);
        if (t.mfeR >= 1.0) return roundTick(t.entryPrice + COSTS + NSE_TICK); // True Break-Even!
        if (t.mfeR >= 0.80) return roundTick(t.entryPrice - 0.40 * t.R); // Cut risk by 60%
        return t.initialStop;
    };

    let activeTrade = null;
    let reentryWatcher = null;
    let dailyTradeCount = 0;
    let dailyRealizedR = 0.0;
    let consecutiveLosses = 0;
    let dayHalted = false;

    const trades = [];
    const trailPoints = [];
    const ladderPoints = [];
    const markers = [];

    const K_BY_STATE = { STRONG: 3.0, NORMAL: 2.2, WEAK: 1.4, CLIMAX: 0.8 };

    for (let i = 0; i < n; i++) {
        const c = candles[i];
        const cTime = getTimeStr(c.time);
        const isSwitchUp = (lastSignal[i] === 1 && (i === 0 || lastSignal[i - 1] === -1));
        const isSwitchDown = (lastSignal[i] === -1 && (i === 0 || lastSignal[i - 1] === 1));

        // 1. Process active trade
        if (activeTrade) {
            const t = activeTrade;
            const isEntryBar = (i === t.entryBar);

            // Baseline: Fixed 1.6R Target + Initial Stop
            if (variantUpper === 'BASELINE') {
                const target1 = roundTick(t.entryPrice + 1.6 * t.R);
                let exitReason = null;
                let exitPrice = c.close;
                let isLimit = false;

                if (c.close < t.initialStop || c.low <= t.catastrophicStop) {
                    exitReason = 'INITIAL_STOP';
                    exitPrice = Math.min(t.initialStop, c.open);
                } else if (!isEntryBar) {
                    t.hh = Math.max(t.hh, c.high);
                    if (c.high >= target1) {
                        exitReason = 'TARGET_1';
                        exitPrice = target1;
                        isLimit = true;
                    }
                }
                t.mfeR = (t.hh - t.entryPrice) / t.R;
                if (!exitReason && isSwitchDown) {
                    exitReason = 'REGIME_FLIP';
                    exitPrice = c.close;
                }
                if (!exitReason && cTime >= '15:15') {
                    exitReason = 'INTRADAY_TIME_EXIT';
                    exitPrice = c.close;
                }

                if (exitReason) {
                    executeExit(i, cTime, exitPrice, exitReason, isLimit);
                }
            } else {
                // Dynamic Exit Variants (V1 through V5_P1)
                let exitReason = null;
                let exitPrice = c.close;
                let isLimit = false;

                // Stop checks against PREVIOUS bar's hard stop (prevents same-bar BE-lock trap!)
                if (c.low <= t.catastrophicStop) {
                    exitReason = 'CATASTROPHIC_STOP';
                    exitPrice = Math.min(t.catastrophicStop, c.open);
                } else {
                    const hardStop = Math.max(t.initialStop, t.ladderFloor);
                    if (c.low <= hardStop) {
                        exitReason = (hardStop > t.initialStop) ? 'LADDER_FLOOR' : 'INITIAL_STOP';
                        // Stop loss order triggers at the stop price (or gap open if opened below stop)
                        exitPrice = (c.open < hardStop) ? roundTick(c.open - slip(c.open)) : roundTick(hardStop - slip(hardStop));
                    }
                }

                // If not stopped out, update HH & MFE
                if (!exitReason) {
                    if (!isEntryBar && c.high > t.hh) {
                        t.hh = c.high;
                        t.lastHighBar = i;
                    }
                    t.mfeR = (t.hh - t.entryPrice) / t.R;

                    // Update Phase
                    if (t.mfeR >= 2.2) t.phase = 'RUNNER';
                    else if (t.mfeR >= 1.0) t.phase = 'SECURED';
                    else t.phase = 'PROTECT';

                    // Update Ladder Floor
                    t.ladderFloor = Math.max(t.ladderFloor, calcRelaxedLadder(t));

                    // Dynamic swing-low trail after T1:
                    // Protect profit / reduce loss by trailing to previous bar low (minus 1.5 pt buffer)
                    if (t.t1Booked) {
                        const prevLow = candles[Math.max(0, i - 1)].low;
                        t.ladderFloor = Math.max(t.ladderFloor, roundTick(prevLow - 1.5));
                    }
                    if (!exitReason && t.target2 && c.high >= t.target2) {
                        // Once T2 is reached, guarantee exit floor is at least Target 1
                        t.ladderFloor = Math.max(t.ladderFloor, t.target1);
                    }

                    // ── SMART TARGET 1 PARTIAL PROFIT SCALING (50% at T1 or >= +1.2R) ──
                    if (!t.t1Booked && (c.high >= (t.target1 || (t.entryPrice + 1.2 * t.R)) || t.mfeR >= 1.2)) {
                        t.t1Booked = true;
                        const targetPx = t.target1 || roundTick(t.entryPrice + 1.2 * t.R);
                        const partialP = Math.max(targetPx, roundTick(t.entryPrice + 1.2 * t.R));
                        t.realizedPts += 0.50 * (partialP - t.entryPrice);
                        t.remQty = 0.50;
                        // Trail stop to previous candle low minus buffer (reducing risk while letting runner breathe)
                        const prevLow = candles[Math.max(0, i - 1)].low;
                        t.ladderFloor = Math.max(t.ladderFloor, roundTick(prevLow - 1.5));
                        markers.push({
                            time: c.time,
                            position: 'aboveBar',
                            color: '#fbbf24',
                            shape: 'circle',
                            text: `🎯 T1 BOOKED 50% (+${(partialP - t.entryPrice).toFixed(1)} pts)`,
                        });

                        // ── TARGET 1 RE-EVALUATION: CONTINUATION VS REVERSAL CHECK ──
                        const span = Math.max(c.high - c.low, 0.05);
                        const upperWick = c.high - Math.max(c.open, c.close);
                        const sellP = upperWick / span;
                        const isBlowoff = (sellP >= 0.50) && (rvol[i] >= 1.3 || c.close < targetPx);
                        if (isBlowoff) {
                            // Sellers dumped into Target 1! Tighten stop immediately to candle low!
                            t.ladderFloor = Math.max(t.ladderFloor, roundTick(c.low - NSE_TICK));
                            if (c.close <= c.low + 0.25 * span) {
                                // Closed right at bottom of the rejection candle -> exit remaining qty immediately!
                                exitReason = 'TARGET_1_REVERSAL';
                                exitPrice = c.close;
                            }
                        }
                    }

                    // ── MAXIMUM TARGET HIT CHECK ──
                    if (!exitReason && t.maxTarget && c.high >= t.maxTarget) {
                        exitReason = 'MAX_TARGET_HIT';
                        exitPrice = t.maxTarget;
                        isLimit = true;
                    }

                    // Structural exits
                    if (!exitReason && isSwitchDown) {
                        exitReason = 'REGIME_FLIP';
                        exitPrice = c.close;
                    } else if (!exitReason && cTime >= '15:15') {
                        exitReason = 'INTRADAY_TIME_EXIT';
                        exitPrice = c.close;
                    } else if (!exitReason && variantUpper !== 'V1') {
                        // V2+ Trailing Stop & Health Checks
                        let ths = null;
                        if (['V3', 'V4', 'V5', 'V5_P1'].includes(variantUpper)) {
                            ths = calcTHS(i);
                            const hs = getHealthState(ths, t.state, t.improvingBars);
                            t.state = hs.state;
                            t.improvingBars = hs.improvingBars;
                            if (ths < 25) {
                                t.lowThsBars++;
                                if (t.lowThsBars >= 2) {
                                    exitReason = 'HEALTH_COLLAPSE';
                                    exitPrice = c.close;
                                }
                            } else {
                                t.lowThsBars = 0;
                            }
                        }

                        if (!exitReason) {
                            // Adaptive Chandelier Trail
                            let k = (t.mfeR >= 2.0) ? 1.0 : (t.mfeR >= 1.0 ? 1.4 : 1.8);
                            if (['V4', 'V5', 'V5_P1'].includes(variantUpper)) {
                                const span = c.high - c.low;
                                const isClimax = (rvol[i] >= 1.5) && (sellerPressure[i] >= 0.55);
                                if (isClimax) {
                                    t.climaxUntilBar = i + 2;
                                    k = Math.min(k, 0.8);
                                    t.ladderFloor = Math.max(t.ladderFloor, roundTick(c.low - NSE_TICK));
                                }
                                if (ext[i] > 3.2 || cTime >= '14:30') k = Math.min(k, 1.2);
                            }

                            const rawTrail = roundTick(t.hh - k * atr[i]);
                            t.trail = Math.max(t.trail, rawTrail);

                            // Trail break (evaluated when MFE >= +1.0R and close < trail)
                            if (t.mfeR >= 1.0 && c.close < t.trail) {
                                exitReason = (i <= t.climaxUntilBar) ? 'CLIMAX_TIGHTEN' : 'TRAIL_BREAK';
                                exitPrice = c.close;
                            } else if (['V4', 'V5', 'V5_P1'].includes(variantUpper)) {
                                // Time Stops
                                if ((i - t.entryBar >= 14) && t.mfeR < 0.3 && c.close < (t.entryPrice - 0.5 * t.R)) {
                                    exitReason = 'FAIL_FAST';
                                    exitPrice = c.close;
                                } else if ((i - t.lastHighBar >= 12) && ths !== null && ths < 60) {
                                    exitReason = 'STALL';
                                    exitPrice = c.close;
                                }
                            }
                        }
                    }
                }

                if (exitReason) {
                    executeExit(i, cTime, exitPrice, exitReason, isLimit);
                }
            }
        }

        function executeExit(barIdx, timeStr, rawExitPrice, reason, isLimit = false) {
            const t = activeTrade;
            const fill = isLimit ? rawExitPrice : rawExitPrice - slip(rawExitPrice);
            const exitP = roundTick(Math.max(fill, NSE_TICK));
            const pnlPts = (exitP - t.entryPrice) * t.remQty - COSTS + t.realizedPts;
            const pnlR = pnlPts / t.R;

            const peakPts = t.hh - t.entryPrice;
            const captureRatio = peakPts > NSE_TICK ? Math.max(0, pnlPts / peakPts) : 0;
            const giveBack = peakPts > NSE_TICK ? Math.max(0, (t.hh - exitP) / peakPts) : 0;

            const rec = {
                tradeNum: trades.length + 1,
                entryBar: t.entryBar,
                entryTime: t.entryTime,
                entryPrice: t.entryPrice,
                exitBar: barIdx,
                exitTime: timeStr,
                exitPrice: exitP,
                initialStop: t.initialStop,
                R: t.R,
                hh: t.hh,
                mfeR: t.mfeR,
                pnlPts,
                pnlR,
                captureRatio,
                giveBack,
                reason,
                exitReason: reason,
                isReentry: t.isReentry,
                reentryType: t.reentryType,
                target1: t.target1,
                maxTarget: t.maxTarget,
                t1Booked: t.t1Booked,
                p1Booked: t.p1Booked,
            };
            trades.push(rec);

            dailyRealizedR += pnlR;
            if (pnlR < 0) consecutiveLosses++;
            else consecutiveLosses = 0;
            if (consecutiveLosses >= 3) dayHalted = true;

            const isWin = pnlR >= 0;
            const pnlSign = pnlR >= 0 ? '+' : '';
            markers.push({
                time: candles[barIdx].time,
                position: 'aboveBar',
                color: isWin ? (pnlR >= 1.0 ? '#34d399' : '#00c8ff') : '#ff005d',
                shape: 'arrowDown',
                text: `🛑 ${reason.replace('_', ' ')} (${pnlSign}${pnlR.toFixed(1)}R)`,
                signalType: 'DE_EXIT',
                exitReason: reason,
                pnlR: pnlR,
            });

            // If stopped out on hard stop, target reversal, or max target completion, do NOT prime a re-entry attempt
            if (['INITIAL_STOP', 'CATASTROPHIC_STOP', 'TARGET_1_REVERSAL', 'MAX_TARGET_HIT'].includes(reason)) {
                reentryWatcher = null;
            } else {
                const pauseBars = (consecutiveLosses >= 2) ? 20 : 0;
                reentryWatcher = {
                    regime: 1,
                    exitBar: barIdx,
                    exitPrice: exitP,
                    exitReason: reason,
                    lastHh: t.hh,
                    lastHigherLow: t.ladderFloor,
                    pullbackLow: Infinity,
                    reentryCount: t.legReentries,
                    pausedUntilBar: barIdx + pauseBars,
                };
            }

            activeTrade = null;
        }

        // 2. Check Re-Entry if no active trade (V5, V5_P1)
        if (!activeTrade && ['V5', 'V5_P1'].includes(variantUpper) && reentryWatcher && !dayHalted) {
            const w = reentryWatcher;
            const inTimeWindow = (cTime >= '09:25' && cTime <= '15:15');
            if (inTimeWindow && lastSignal[i] === w.regime && w.reentryCount < 2 && i > w.pausedUntilBar && dailyTradeCount < 4) {
                const ths = calcTHS(i);
                w.pullbackLow = Math.min(w.pullbackLow, c.low);

                const isPullback = (w.pullbackLow <= bMain[i] + 0.35 * atr[i]) || (w.pullbackLow <= w.lastHh - 0.50 * atr[i]);
                const isBullTurn = (c.close > c.open) && (c.close >= bMain[i] + 0.10 * atr[i]);
                const isBreakout = (c.close > w.lastHh + 0.10 * atr[i]);

                let reType = null;
                if (isPullback && isBullTurn && ths >= 60) {
                    reType = 'PULLBACK';
                } else if (isBreakout && ths >= 60) {
                    reType = 'BREAKOUT';
                }

                if (reType) {
                    const entryP = roundTick(c.close + slip(c.close));
                    const rawStop = bMain[i] - 0.35 * atr[i];
                    const rawR = entryP - rawStop;
                    const clampedR = Math.min(Math.max(rawR, 1.5), 14.0, 0.10 * entryP);
                    const initStop = roundTick(entryP - clampedR);
                    const R = roundTick(entryP - initStop);

                    const minT1Pts = Math.max(6.5, atr[i] * 1.1);
                    const t1 = roundTick(entryP + minT1Pts);
                    const t2 = roundTick(entryP + Math.max(16.0, 2.2 * R, 2.2 * atr[i]));
                    const maxT = roundTick(entryP + Math.max(26.0, 3.2 * R, 3.2 * atr[i]));

                    activeTrade = {
                        entryPrice: entryP,
                        entryBar: i,
                        entryTime: cTime,
                        initialStop: initStop,
                        target1: t1,
                        target2: t2,
                        maxTarget: maxT,
                        R,
                        catastrophicStop: roundTick(entryP - 2.0 * R),
                        baseQty: 1.0,
                        remQty: 1.0,
                        hh: entryP,
                        lastHighBar: i,
                        mfeR: 0.0,
                        phase: 'PROTECT',
                        state: 'NORMAL',
                        trail: initStop,
                        ladderFloor: initStop,
                        climaxUntilBar: -1,
                        lowThsBars: 0,
                        improvingBars: 0,
                        t1Booked: false,
                        p1Booked: false,
                        realizedPts: 0.0,
                        isReentry: true,
                        reentryType: reType,
                        legReentries: w.reentryCount + 1,
                    };
                    dailyTradeCount++;
                    reentryWatcher = null;

                    const reLabel = reType === 'PULLBACK' ? `🔄 RE-PB ₹${entryP.toFixed(1)} [T1: ${t1.toFixed(1)}]` : `🔄 RE-BO ₹${entryP.toFixed(1)} [T1: ${t1.toFixed(1)}]`;
                    markers.push({
                        time: c.time,
                        position: 'belowBar',
                        color: '#00f2a9',
                        shape: 'arrowUp',
                        text: reLabel,
                        signalType: 'REENTRY',
                        reType: reType,
                        entryPrice: entryP,
                    });
                }
            }
        }

        // 3. Check New Entry on SMF Switch Up OR Support Reversal Reclaim (SRR)
        let isSRRBar = false;
        let srrDipLowVal = c.low;
        if (!activeTrade && i >= 20 && !dayHalted && dailyTradeCount < 4) {
            const inSrrWindow = (cTime >= '09:35' && cTime <= '11:00') || (cTime >= '13:15' && cTime <= '14:30');
            if (inSrrWindow) {
                let morningBase = Infinity;
                const baseEnd = Math.min(26, i);
                for (let k = 5; k < baseEnd; k++) {
                    if (candles[k].low < morningBase) morningBase = candles[k].low;
                }
                const startDip = Math.max(0, i - 4);
                let dip5 = Infinity;
                for (let k = startDip; k <= i; k++) {
                    if (candles[k].low < dip5) dip5 = candles[k].low;
                }
                srrDipLowVal = dip5;
                const supCeiling = morningBase + 1.2 * atr[i];
                const inSupportZone = (dip5 <= supCeiling) && (dip5 >= morningBase - 0.5 * atr[i]);

                let resCeiling = -Infinity;
                for (let k = 0; k < i; k++) {
                    if (candles[k].high > resCeiling) resCeiling = candles[k].high;
                }
                const runway = resCeiling - c.close;
                const hasWideRunway = (runway >= Math.max(20.0, 2.0 * atr[i]));

                const prev2High = Math.max(candles[Math.max(0, i - 2)].high, candles[Math.max(0, i - 1)].high);
                const isReversal = (c.close > c.open) && (c.close > prev2High) && (rsi[i] >= 45.0);

                if (inSupportZone && hasWideRunway && isReversal) {
                    isSRRBar = true;
                }
            }
        }

        if (!activeTrade && (isSwitchUp || isSRRBar) && !dayHalted && dailyTradeCount < 4) {
            const inTimeWindow = (cTime >= '09:25' && cTime <= '15:15');
            const needsIgnition = ['V4', 'V5', 'V5_P1'].includes(variantUpper);
            if (inTimeWindow && (isSRRBar || !needsIgnition || isGammaIgnition(i))) {
                const entryP = roundTick(c.close + slip(c.close));
                let initStop, R;
                if (isSRRBar && !isSwitchUp) {
                    const rawStop = roundTick(srrDipLowVal - 1.5);
                    const rawR = entryP - rawStop;
                    const clampedR = Math.min(Math.max(rawR, 4.0), 16.0);
                    initStop = roundTick(entryP - clampedR);
                    R = roundTick(entryP - initStop);
                } else {
                    const rawStop = bMain[i] - 0.35 * atr[i];
                    const rawR = entryP - rawStop;
                    const clampedR = Math.min(Math.max(rawR, 1.5), 14.0, 0.10 * entryP);
                    initStop = roundTick(entryP - clampedR);
                    R = roundTick(entryP - initStop);
                }

                const minT1Pts = Math.max(6.5, atr[i] * 1.1);
                const t1 = roundTick(entryP + minT1Pts);
                const t2 = roundTick(entryP + Math.max(16.0, 2.2 * R, 2.2 * atr[i]));
                const maxT = roundTick(entryP + Math.max(26.0, 3.2 * R, 3.2 * atr[i]));

                activeTrade = {
                    entryPrice: entryP,
                    entryBar: i,
                    entryTime: cTime,
                    initialStop: initStop,
                    target1: t1,
                    target2: t2,
                    maxTarget: maxT,
                    R,
                    catastrophicStop: roundTick(entryP - 2.0 * R),
                    baseQty: 1.0,
                    remQty: 1.0,
                    hh: entryP,
                    lastHighBar: i,
                    mfeR: 0.0,
                    phase: 'PROTECT',
                    state: 'NORMAL',
                    trail: initStop,
                    ladderFloor: initStop,
                    climaxUntilBar: -1,
                    lowThsBars: 0,
                    improvingBars: 0,
                    t1Booked: false,
                    p1Booked: false,
                    realizedPts: 0.0,
                    isReentry: false,
                    reentryType: isSRRBar && !isSwitchUp ? 'SRR_REVERSAL' : 'NONE',
                    legReentries: 0,
                };
                dailyTradeCount++;
                reentryWatcher = null;

                const entryLabel = (isSRRBar && !isSwitchUp)
                    ? `🔄 SRR BUY ₹${entryP.toFixed(1)} [T1: ${t1.toFixed(1)}]`
                    : `▲ BUY ₹${entryP.toFixed(1)} [T1: ${t1.toFixed(1)}]`;

                markers.push({
                    time: c.time,
                    position: 'belowBar',
                    color: '#00f2a9',
                    shape: 'arrowUp',
                    text: entryLabel,
                    signalType: isSRRBar && !isSwitchUp ? 'SRR_BUY' : 'DE_BUY',
                    entryPrice: entryP,
                    R: R,
                });
            }
        }

        // Record visual line series points
        if (activeTrade) {
            const hardStop = Math.max(activeTrade.initialStop, activeTrade.ladderFloor);
            ladderPoints.push({ time: c.time, value: hardStop });
            if (activeTrade.mfeR >= 1.0 && variantUpper !== 'BASELINE' && variantUpper !== 'V1') {
                trailPoints.push({ time: c.time, value: activeTrade.trail });
            } else {
                trailPoints.push({ time: c.time, value: hardStop });
            }
        }
    }

    const totalTrades = trades.length;
    const winTrades = trades.filter(t => t.pnlR > 0).length;
    const lossTrades = trades.filter(t => t.pnlR < 0).length;
    const winRate = totalTrades > 0 ? (winTrades / totalTrades) * 100 : 0;
    const netPnlR = trades.reduce((acc, t) => acc + t.pnlR, 0);
    const netPnlPts = trades.reduce((acc, t) => acc + t.pnlPts, 0);

    const winningTrades = trades.filter(t => t.pnlR > 0);
    const avgCaptureRatio = winningTrades.length > 0
        ? (winningTrades.reduce((acc, t) => acc + t.captureRatio, 0) / winningTrades.length) * 100
        : 0;
    const avgGiveBack = winningTrades.length > 0
        ? (winningTrades.reduce((acc, t) => acc + t.giveBack, 0) / winningTrades.length) * 100
        : 0;

    return {
        variant: variantUpper,
        trailPoints,
        ladderPoints,
        markers,
        activeTrade,
        reentryWatcher,
        trades,
        summary: {
            totalTrades,
            winTrades,
            lossTrades,
            winRate,
            netPnlR,
            netPnlPts,
            captureRatio: avgCaptureRatio,
            giveBack: avgGiveBack,
            dailyRealizedR,
            dayHalted,
        },
    };
}

// ── SMF Dynamic Exit HUD Updater ─────────────────────────────────────────────

function updateDynamicExitHUD(deResult, candles) {
    const card = document.getElementById('dynamic-exit-card');
    if (!card) return;

    if (!C.dynamicExitEnabled) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');

    if (!deResult) {
        const statusPill = document.getElementById('de-status-pill');
        if (statusPill) {
            statusPill.className = 'de-pill flat';
            statusPill.textContent = 'STANDBY';
        }
        return;
    }

    const statusPill = document.getElementById('de-status-pill');
    const liveEntry = document.getElementById('de-live-entry');
    const liveStop = document.getElementById('de-live-stop');
    const liveTrail = document.getElementById('de-live-trail');
    const liveMfe = document.getElementById('de-live-mfe');
    const liveThs = document.getElementById('de-live-ths');
    const livePnl = document.getElementById('de-live-pnl');

    const sumTrades = document.getElementById('de-sum-trades');
    const sumWinrate = document.getElementById('de-sum-winrate');
    const sumPnlR = document.getElementById('de-sum-pnl-r');
    const sumPts = document.getElementById('de-sum-pts');
    const sumCapture = document.getElementById('de-sum-capture');
    const sumGiveback = document.getElementById('de-sum-giveback');
    const tradesCount = document.getElementById('de-trades-count');
    const tradesList = document.getElementById('de-trades-list');

    // 1. Status Pill & Live Position
    const t = deResult.activeTrade;
    if (t) {
        if (statusPill) {
            statusPill.className = `de-pill ${t.phase.toLowerCase()}`;
            statusPill.textContent = `${t.phase} (${t.state})`;
        }
        if (liveEntry) liveEntry.textContent = `₹${t.entryPrice.toFixed(1)}`;
        if (liveStop) {
            const curStop = Math.max(t.initialStop, t.ladderFloor);
            liveStop.textContent = `₹${curStop.toFixed(1)}`;
        }
        if (liveTrail) liveTrail.textContent = `₹${t.trail.toFixed(1)}`;
        if (liveMfe) liveMfe.textContent = `+${t.mfeR.toFixed(1)}R (₹${t.hh.toFixed(1)})`;
        if (liveThs) liveThs.textContent = `${t.state}`;

        if (livePnl && candles && candles.length > 0) {
            const curClose = candles[candles.length - 1].close;
            const curPts = (curClose - t.entryPrice) * t.remQty + t.realizedPts - 0.40;
            const curR = curPts / t.R;
            const sign = curR >= 0 ? '+' : '';
            livePnl.textContent = `${sign}${curR.toFixed(2)}R`;
            livePnl.className = `de-stat-val ${curR >= 0 ? 'success' : 'danger'}`;
        }
    } else {
        if (statusPill) {
            if (deResult.summary.dayHalted) {
                statusPill.className = 'de-pill flat';
                statusPill.textContent = 'DAY HALTED (3L)';
            } else if (deResult.reentryWatcher) {
                statusPill.className = 'de-pill armed';
                statusPill.textContent = 'ARMED RE-ENTRY';
            } else {
                statusPill.className = 'de-pill flat';
                statusPill.textContent = 'FLAT';
            }
        }
        if (liveEntry) liveEntry.textContent = '₹--';
        if (liveStop) liveStop.textContent = '₹--';
        if (liveTrail) liveTrail.textContent = '₹--';
        if (liveMfe) liveMfe.textContent = '+0.0R';
        if (liveThs) liveThs.textContent = '--';
        if (livePnl) {
            livePnl.textContent = '+0.0R';
            livePnl.className = 'de-stat-val highlight';
        }
    }

    // 2. Summary Grid
    const s = deResult.summary;
    if (sumTrades) sumTrades.textContent = `${s.totalTrades} (${s.winTrades}W / ${s.lossTrades}L)`;
    if (sumWinrate) sumWinrate.textContent = `${s.winRate.toFixed(0)}%`;

    if (sumPnlR) {
        const signR = s.netPnlR >= 0 ? '+' : '';
        sumPnlR.textContent = `${signR}${s.netPnlR.toFixed(2)}R`;
        sumPnlR.className = `de-sum-val ${s.netPnlR >= 0 ? 'win' : 'loss'}`;
    }

    if (sumPts) {
        const signPts = s.netPnlPts >= 0 ? '+' : '';
        sumPts.textContent = `${signPts}${s.netPnlPts.toFixed(1)} pts`;
        sumPts.className = `de-sum-val ${s.netPnlPts >= 0 ? 'win' : 'loss'}`;
    }

    if (sumCapture) sumCapture.textContent = `${s.captureRatio.toFixed(1)}%`;
    if (sumGiveback) sumGiveback.textContent = `${s.giveBack.toFixed(1)}%`;
    if (tradesCount) tradesCount.textContent = `${s.totalTrades} executed`;

    // 3. Trade History List & Ticker
    const ticker = document.getElementById('de-trades-ticker');
    if (ticker) {
        if (deResult.trades.length === 0) {
            ticker.innerHTML = '<span class="de-ticker-empty">No trades on this session</span>';
        } else {
            ticker.innerHTML = deResult.trades.slice(-3).map(tr => {
                const isWin = tr.pnlR >= 0;
                const sign = isWin ? '+' : '';
                const action = tr.isReentry ? `RE-ENTRY` : 'BUY';
                return `<span class="de-trade-chip ${isWin ? 'win' : 'loss'}" title="${tr.entryTime} ➔ ${tr.exitTime} | ${tr.reason}">#${tr.tradeNum} ${action} ➔ ${tr.reason.replace('_', ' ')} (${sign}${tr.pnlR.toFixed(1)}R)</span>`;
            }).join('');
        }
    }

    if (tradesList) {
        if (deResult.trades.length === 0) {
            tradesList.innerHTML = '<div class="de-no-trades">No trades triggered on this session</div>';
        } else {
            tradesList.innerHTML = deResult.trades.map(tr => {
                const isWin = tr.pnlR >= 0;
                const sign = isWin ? '+' : '';
                const typeLabel = tr.isReentry ? `RE-ENTRY (${tr.reentryType})` : 'BUY';
                return `
                    <div class="de-trade-row ${isWin ? 'win' : 'loss'}">
                        <div class="de-trade-info">
                            <span class="de-trade-action">#${tr.tradeNum} ${typeLabel} @ ₹${tr.entryPrice.toFixed(1)}</span>
                            <span class="de-trade-times">${tr.entryTime} ➔ ${tr.exitTime}</span>
                        </div>
                        <div class="de-trade-result">
                            <span class="de-trade-pnl ${isWin ? 'win' : 'loss'}">${sign}${tr.pnlR.toFixed(1)}R (${sign}${tr.pnlPts.toFixed(1)} pts)</span>
                            <span class="de-trade-reason">${tr.reason.replace('_', ' ')}</span>
                        </div>
                    </div>
                `;
            }).join('');
        }
    }
}

// ── SMF Dynamic Exit Controller Initialization ───────────────────────────────

function initDynamicExitController() {
    const btn = document.getElementById('dynamic-exit-btn');
    const card = document.getElementById('dynamic-exit-card');
    const varSelect = document.getElementById('de-variant-select');
    const closeBtn = document.getElementById('de-card-close');
    const toggleLogBtn = document.getElementById('de-btn-toggle-log');
    const popover = document.getElementById('de-trades-popover');
    const popoverClose = document.getElementById('de-popover-close');

    if (btn) {
        btn.classList.toggle('active', !!C.dynamicExitEnabled);
        btn.addEventListener('click', () => {
            C.dynamicExitEnabled = !C.dynamicExitEnabled;
            btn.classList.toggle('active', C.dynamicExitEnabled);
            if (card) {
                if (C.dynamicExitEnabled) card.classList.remove('hidden');
                else {
                    card.classList.add('hidden');
                    if (popover) popover.classList.add('hidden');
                }
            }
            if (C.rawCandles.length > 0) {
                applyFullChart(C.rawCandles, false);
            }
        });
    }

    if (varSelect) {
        varSelect.value = C.dynamicExitVariant || 'V5';
        varSelect.addEventListener('change', (e) => {
            C.dynamicExitVariant = e.target.value;
            if (C.rawCandles.length > 0) {
                applyFullChart(C.rawCandles, false);
            }
        });
    }

    if (toggleLogBtn && popover) {
        toggleLogBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            popover.classList.toggle('hidden');
        });
    }

    if (popoverClose && popover) {
        popoverClose.addEventListener('click', () => {
            popover.classList.add('hidden');
        });
    }

    // Close popover when clicking outside
    document.addEventListener('click', (e) => {
        if (popover && !popover.classList.contains('hidden')) {
            if (!popover.contains(e.target) && e.target !== toggleLogBtn) {
                popover.classList.add('hidden');
            }
        }
    });

    if (closeBtn && card) {
        closeBtn.addEventListener('click', () => {
            card.classList.add('hidden');
            if (popover) popover.classList.add('hidden');
            if (btn) btn.classList.remove('active');
            C.dynamicExitEnabled = false;
            if (C.dynamicTrailSeries) C.dynamicTrailSeries.setData([]);
            if (C.dynamicLadderSeries) C.dynamicLadderSeries.setData([]);
            if (C.rawCandles.length > 0) {
                applyFullChart(C.rawCandles, false);
            }
        });
    }
}

function updateGauge(val, regime) {
    const pct = Math.min(100, Math.round(Math.abs(val) * 100));
    const isBull = regime === 1;
    const fill = document.getElementById('gauge-fill');
    const pctEl = document.getElementById('gauge-pct');
    if (fill) {
        fill.style.width = `${pct}%`;
        fill.style.background = isBull
            ? 'linear-gradient(90deg, #00c8ff, #00f2a9)'
            : 'linear-gradient(90deg, #ff005d, #ff6b9d)';
    }
    if (pctEl) {
        pctEl.textContent = `${pct}% ${isBull ? '▲' : '▼'}`;
        pctEl.style.color = isBull ? '#00c8ff' : '#ff005d';
    }
}

// ── SMF Canvas Ribbon Render ─────────────────────────────────────────────────

function renderSMFCanvas() {
    const canvas = document.getElementById('contract-smf-canvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!C.smfLastResult || !C.smfEnabled) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        return;
    }

    const smf = C.smfLastResult;
    const { n, upper, lower, bMain, lastSignal, candles } = smf;
    if (!n || n < 2) return;

    const chartArea = document.getElementById('chart-area') || document.getElementById('contract-chart-container');
    const W = chartArea ? chartArea.clientWidth : canvas.width;
    const H = chartArea ? chartArea.clientHeight : canvas.height;

    if (canvas.width !== W || canvas.height !== H) {
        canvas.width = W;
        canvas.height = H;
    }

    ctx.clearRect(0, 0, W, H);

    // Accurate boundary where candle plot ends and right price scale starts
    let priceScaleWidth = 65;
    try {
        if (C.chart && typeof C.chart.priceScale === 'function') {
            const ps = C.chart.priceScale('right');
            if (ps && typeof ps.width === 'function') {
                const psw = ps.width();
                if (psw > 30) priceScaleWidth = psw;
            }
        }
    } catch (e) {}
    const chartPlotRight = Math.round(W - priceScaleWidth);

    // Build pixel points matching chart coordinate system 1:1 (culled to visible viewport)
    const points = [];
    for (let i = 0; i < n; i++) {
        const t = candles[i].time;
        const coord = C.chart.timeScale().timeToCoordinate(t);
        if (coord == null || coord < -80 || coord > W + 80) continue;
        const yU = C.candleSeries.priceToCoordinate(upper[i]);
        const yL = C.candleSeries.priceToCoordinate(lower[i]);
        const yB = C.candleSeries.priceToCoordinate(bMain[i]);
        if (yU == null || yL == null || yB == null) continue;
        points.push({ x: coord, yUpper: yU, yLower: yL, yBc: yB, regime: lastSignal[i] });
    }
    if (points.length < 2) return;

    // Split into segments by regime
    const segs = [];
    let seg = [points[0]];
    for (let i = 1; i < points.length; i++) {
        if (points[i].regime !== points[i - 1].regime) {
            segs.push({ regime: points[i - 1].regime, pts: seg });
            seg = [points[i - 1], points[i]];
        } else {
            seg.push(points[i]);
        }
    }
    segs.push({ regime: points[points.length - 1].regime, pts: seg });

    // Draw filled cloud ribbons
    for (const { regime, pts } of segs) {
        if (pts.length < 2) continue;
        const isBull = regime === 1;

        ctx.save();
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].yUpper);
        for (const p of pts) ctx.lineTo(p.x, p.yUpper);
        for (let j = pts.length - 1; j >= 0; j--) ctx.lineTo(pts[j].x, pts[j].yLower);
        ctx.closePath();

        const grad = ctx.createLinearGradient(0, 0, 0, H);
        if (isBull) {
            grad.addColorStop(0, 'rgba(0, 200, 255, 0.22)');
            grad.addColorStop(1, 'rgba(0, 200, 255, 0.04)');
        } else {
            grad.addColorStop(0, 'rgba(255, 0, 93, 0.04)');
            grad.addColorStop(1, 'rgba(255, 0, 93, 0.22)');
        }
        ctx.fillStyle = grad;
        ctx.fill();
        ctx.restore();
    }

    // Draw basis center line
    ctx.save();
    ctx.lineWidth = 1.5;
    for (const { regime, pts } of segs) {
        ctx.strokeStyle = regime === 1 ? 'rgba(0, 200, 255, 0.55)' : 'rgba(255, 0, 93, 0.55)';
        ctx.beginPath();
        for (let i = 0; i < pts.length; i++) {
            if (i === 0) ctx.moveTo(pts[i].x, pts[i].yBc);
            else ctx.lineTo(pts[i].x, pts[i].yBc);
        }
        ctx.stroke();
    }
    ctx.restore();

    // Draw outer dashed bands
    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.25)';
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

    // ── 1. Partial Horizontal Target & SL Lines with Right Column Badges (Requests 2 & 3)
    if (C.targetsEnabled && C.activeTargetSignal && C.candleSeries) {
        const sig = C.activeTargetSignal;
        const curCandles = candles || C.rawCandles || [];
        const lastCandle = curCandles.length > 0 ? curCandles[curCandles.length - 1] : null;

        // Accurate boundary where candle plot ends and right price scale starts
        let priceScaleWidth = 65;
        try {
            if (C.chart && typeof C.chart.priceScale === 'function') {
                const ps = C.chart.priceScale('right');
                if (ps && typeof ps.width === 'function') {
                    const psw = ps.width();
                    if (psw > 30) priceScaleWidth = psw;
                }
            }
        } catch (e) {}
        const chartPlotRight = Math.round(W - priceScaleWidth);

        // Transition X: The current candle (last candle) coordinate
        const curCandleX = lastCandle
            ? Math.max(0, Math.min(chartPlotRight - 30, Math.round(C.chart.timeScale().timeToCoordinate(lastCandle.time) || 0)))
            : Math.max(0, chartPlotRight - 150);

        const isMkt = sig.recommendedOrder === 'MARKET';
        const isMobile = W <= 768 || window.innerWidth <= 768;
        const levels = [
            { key: 't2', price: sig.target2, label: `T2 ₹${sig.target2?.toFixed(1)}`, color: '#ffbe0b', lightColor: 'rgba(255, 190, 11, 0.40)', dash: [] },
            { key: 't1', price: sig.target1, label: `T1 ₹${sig.target1?.toFixed(1)}`, color: '#ffd166', lightColor: 'rgba(255, 209, 102, 0.40)', dash: [5, 3] },
            { key: 'lt', price: sig.limitPrice, label: (isMkt ? `MKT ₹${sig.limitPrice?.toFixed(1)}` : `LT ₹${sig.limitPrice?.toFixed(1)}`), color: (isMkt ? '#00f2a9' : '#00e5ff'), lightColor: (isMkt ? 'rgba(0, 242, 169, 0.40)' : 'rgba(0, 229, 255, 0.40)'), dash: [] },
            { key: 'sl', price: sig.sl, label: `SL ₹${sig.sl?.toFixed(1)}`, color: '#ff4d6d', lightColor: 'rgba(255, 77, 109, 0.40)', dash: [5, 3] },
        ];

        let sigX = 0;
        if (sig.time) {
            const rawSigX = C.chart.timeScale().timeToCoordinate(sig.time);
            if (rawSigX != null) sigX = Math.max(0, Math.round(rawSigX));
        }

        ctx.save();
        for (const lvl of levels) {
            if (!lvl.price) continue;
            const y = Math.round(C.candleSeries.priceToCoordinate(lvl.price));
            if (y == null || y < -20 || y > H + 20) continue;

            // A. Past timeframe: Clean visible light line from signal candle up to current candle
            if (curCandleX > sigX) {
                ctx.beginPath();
                ctx.strokeStyle = lvl.lightColor;
                ctx.lineWidth = 1;
                ctx.setLineDash([4, 4]);
                ctx.moveTo(sigX, y);
                ctx.lineTo(curCandleX, y);
                ctx.stroke();
            }

            // B. Active timeframe: Solid line from current candle into blank area
            ctx.beginPath();
            ctx.strokeStyle = lvl.color;
            ctx.lineWidth = 1.3;
            ctx.setLineDash(lvl.dash);
            ctx.moveTo(curCandleX, y);
            ctx.lineTo(chartPlotRight, y);
            ctx.stroke();

            // C. Dedicated Right Column Badge in preserved margin before price scale
            ctx.setLineDash([]);
            ctx.font = isMobile ? 'bold 9px Inter, -apple-system, sans-serif' : 'bold 10px Inter, -apple-system, sans-serif';
            const textW = Math.ceil(ctx.measureText(lvl.label).width);
            const badgeW = textW + (isMobile ? 8 : 12);
            const badgeH = isMobile ? 16 : 18;
            const badgeX = Math.round(chartPlotRight - badgeW - 4);
            const badgeY = Math.round(y - badgeH / 2);

            // Badge Background
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(badgeX, badgeY, badgeW, badgeH, 3);
            } else {
                ctx.rect(badgeX, badgeY, badgeW, badgeH);
            }
            ctx.fillStyle = 'rgba(8, 14, 26, 0.95)';
            ctx.fill();
            ctx.strokeStyle = lvl.color;
            ctx.lineWidth = 1.1;
            ctx.stroke();

            // Badge Text
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            ctx.fillStyle = lvl.color;
            ctx.fillText(lvl.label, badgeX + (isMobile ? 4 : 6), badgeY + badgeH / 2);
        }
        ctx.restore();
    }

    // ── 2. Visual Projections & Bottom Summary Pill (MKT, T1, T2) ───────────
    if (C.targetsEnabled && C.activeTargetSignal) {
        const sig = C.activeTargetSignal;
        const isMkt = sig.recommendedOrder === 'MARKET';
        const isMobile = W <= 768 || window.innerWidth <= 768;
        const limText = isMkt ? `MKT ₹${sig.limitPrice.toFixed(1)}` : `LT ₹${sig.limitPrice.toFixed(1)}`;
        const tgtText = `T1 ₹${sig.target1.toFixed(1)}`;
        const t2Text = sig.target2 ? `T2 ₹${sig.target2.toFixed(1)}` : '';
        const chopText = sig.isMiddayChop ? `CHOP` : '';

        ctx.save();
        ctx.font = isMobile ? 'bold 9px Inter, -apple-system, sans-serif' : 'bold 10px Inter, -apple-system, sans-serif';
        const limW = Math.ceil(ctx.measureText(limText).width);
        const tgtW = Math.ceil(ctx.measureText(tgtText).width);
        const t2W = t2Text ? Math.ceil(ctx.measureText(t2Text).width) + 8 : 0;
        const chopW = chopText ? Math.ceil(ctx.measureText(chopText).width) + 8 : 0;
        const pillW = limW + tgtW + t2W + chopW + 18;
        const pillH = isMobile ? 18 : 20;

        // Position at the bottom of the chart ABOVE the time scale (uncovers time scale completely!)
        const timeScaleHeight = 30;
        const pillY = Math.round(H - timeScaleHeight - pillH - 5);
        const midY = Math.round(pillY + pillH / 2);

        let sigX = null;
        if (sig.time) {
            const rawX = C.chart.timeScale().timeToCoordinate(sig.time);
            if (rawX != null && rawX >= 10 && rawX <= chartPlotRight - 10) {
                sigX = Math.round(rawX);
            }
        }

        // Thin vertical laser guideline through signal bar if on screen
        if (sigX != null) {
            ctx.beginPath();
            ctx.strokeStyle = isMkt ? 'rgba(0, 242, 169, 0.35)' : 'rgba(0, 229, 255, 0.35)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 4]);
            ctx.moveTo(sigX, 0);
            ctx.lineTo(sigX, pillY);
            ctx.stroke();

            ctx.setLineDash([]);
            ctx.fillStyle = isMkt ? '#00f2a9' : '#00e5ff';
            ctx.beginPath();
            ctx.arc(sigX, 4, 2, 0, Math.PI * 2);
            ctx.arc(sigX, pillY - 2, 2, 0, Math.PI * 2);
            ctx.fill();
        }

        // Center under signal candle if on screen, otherwise dock nicely at bottom before price scale
        const pillX = sigX != null
            ? Math.round(Math.max(8, Math.min(chartPlotRight - pillW - 8, sigX - pillW / 2)))
            : Math.round(Math.max(8, Math.min(chartPlotRight - pillW - 8, (chartPlotRight - pillW) / 2)));

        ctx.beginPath();
        if (typeof ctx.roundRect === 'function') {
            ctx.roundRect(pillX, pillY, pillW, pillH, 4);
        } else {
            ctx.rect(pillX, pillY, pillW, pillH);
        }
        ctx.fillStyle = 'rgba(8, 14, 26, 0.94)';
        ctx.fill();
        ctx.strokeStyle = sig.isMiddayChop ? 'rgba(255, 107, 107, 0.7)' : (isMkt ? 'rgba(0, 242, 169, 0.7)' : 'rgba(0, 229, 255, 0.7)');
        ctx.lineWidth = 1.2;
        ctx.stroke();

        ctx.textBaseline = 'middle';
        ctx.textAlign = 'left';

        let curCursorX = pillX + 6;
        ctx.fillStyle = isMkt ? '#00f2a9' : '#00e5ff';
        ctx.fillText(limText, curCursorX, midY);
        curCursorX += limW + 4;

        ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';
        ctx.fillRect(Math.round(curCursorX), pillY + 3, 1, pillH - 6);
        curCursorX += 5;

        ctx.fillStyle = '#ffd166';
        ctx.fillText(tgtText, curCursorX, midY);
        curCursorX += tgtW + 4;

        if (t2Text) {
            ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';
            ctx.fillRect(Math.round(curCursorX), pillY + 3, 1, pillH - 6);
            curCursorX += 5;
            ctx.fillStyle = '#ffbe0b';
            ctx.fillText(t2Text, curCursorX, midY);
            curCursorX += t2W - 4;
        }

        if (chopText) {
            ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';
            ctx.fillRect(Math.round(curCursorX), pillY + 3, 1, pillH - 6);
            curCursorX += 5;
            ctx.fillStyle = '#ff6b6b';
            ctx.fillText(chopText, curCursorX, midY);
        }

        ctx.restore();
    }

    // ── 3. Indicator Value Badges with Background Colors & Smart Spacing (Requests 3 & 4)
    if (C.allRawSignalMarkers && C.allRawSignalMarkers.length > 0) {
        ctx.save();
        const isMobile = W <= 768 || window.innerWidth <= 768;
        ctx.font = isMobile ? 'bold 9px Inter, -apple-system, sans-serif' : 'bold 10px Inter, -apple-system, sans-serif';

        const sorted = [...C.allRawSignalMarkers].sort((a, b) => a.time - b.time);
        const drawnBelow = [];
        const drawnAbove = [];

        for (const sm of sorted) {
            const x = C.chart.timeScale().timeToCoordinate(sm.time);
            if (x == null || x < 20 || x > W - 20) continue;

            const c = candles.find(cand => cand.time === sm.time);
            if (!c) continue;

            const yLow = C.candleSeries.priceToCoordinate(c.low);
            const yHigh = C.candleSeries.priceToCoordinate(c.high);
            if (yLow == null || yHigh == null) continue;

            const isExit = sm.signalType === 'EXIT' || sm.signalType === 'DE_EXIT' || sm.position === 'aboveBar';

            let label = sm.text || '';
            let color = sm.color || '#00f2a9';

            // Clean up and shorten label
            if (label.includes('RE-ENTRY') || label.includes('RE-PB') || label.includes('RE-BO')) {
                const reType = label.includes('BREAKOUT') || label.includes('RE-BO') ? 'BO' : 'PB';
                const pMatch = label.match(/₹[\d.]+/);
                label = `🔄 RE-${reType}${pMatch ? ' ' + pMatch[0] : ''}`;
                color = '#00f2a9';
            } else if (label.includes('FAIL FAST')) {
                const rMatch = label.match(/\([^)]+\)/);
                label = `🛑 FAIL FAST${rMatch ? ' ' + rMatch[0] : ''}`;
                color = '#ff005d';
            } else if (label.includes('TRAIL BREAK') || label.includes('STALL')) {
                const rMatch = label.match(/\([^)]+\)/);
                label = `🛑 ${sm.exitReason || 'EXIT'}${rMatch ? ' ' + rMatch[0] : ''}`;
                color = '#ff005d';
            } else if (label === 'EXIT' || label === '▼ EXIT') {
                label = '▼ EXIT';
                color = '#ff9f43'; // Yellow/Orange SMF exit
            }

            const textW = Math.ceil(ctx.measureText(label).width);
            const badgeW = textW + (isMobile ? 8 : 12);
            const badgeH = isMobile ? 16 : 18;
            const badgeX = Math.round(x - badgeW / 2);

            if (!isExit) {
                // ── 3: Keep BUY, CONT, RE-ENTRY far away from candle (below)
                const baseBelowOffset = 38;
                let badgeY = Math.round(yLow + baseBelowOffset);

                // Check collision with recent below-bar badges
                for (const prev of drawnBelow) {
                    if (Math.abs(prev.x - x) < (badgeW + prev.w) / 2 + 6) {
                        badgeY = Math.max(badgeY, prev.y + badgeH + 8);
                    }
                }
                // Clamp so it does not collide with bottom time scale or summary pill
                badgeY = Math.min(badgeY, H - badgeH - 32);
                drawnBelow.push({ x, y: badgeY, w: badgeW });

                // Connector line from candle low to badge
                ctx.beginPath();
                ctx.strokeStyle = color;
                ctx.lineWidth = 1;
                ctx.setLineDash([2, 2]);
                ctx.moveTo(Math.round(x), Math.round(yLow + 4));
                ctx.lineTo(Math.round(x), Math.round(badgeY));
                ctx.stroke();
                ctx.setLineDash([]);

                // Small dot on candle low wick
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.arc(Math.round(x), Math.round(yLow + 3), 2, 0, Math.PI * 2);
                ctx.fill();

                // Background
                ctx.beginPath();
                if (typeof ctx.roundRect === 'function') {
                    ctx.roundRect(badgeX, badgeY, badgeW, badgeH, 4);
                } else {
                    ctx.rect(badgeX, badgeY, badgeW, badgeH);
                }
                ctx.fillStyle = 'rgba(8, 14, 26, 0.94)';
                ctx.fill();
                ctx.strokeStyle = color;
                ctx.lineWidth = 1.2;
                ctx.stroke();

                ctx.textBaseline = 'middle';
                ctx.textAlign = 'center';
                ctx.fillStyle = color;
                ctx.fillText(label, Math.round(x), Math.round(badgeY + badgeH / 2));

            } else {
                // ── 3: Keep EXIT far away from candle (above)
                const baseAboveOffset = 38;
                let badgeY = Math.round(yHigh - badgeH - baseAboveOffset);

                // Check collision with recent above-bar badges
                for (const prev of drawnAbove) {
                    if (Math.abs(prev.x - x) < (badgeW + prev.w) / 2 + 6) {
                        badgeY = Math.min(badgeY, prev.y - badgeH - 8);
                    }
                }
                // Clamp so it does not go off the top edge
                badgeY = Math.max(badgeY, 8);
                drawnAbove.push({ x, y: badgeY, w: badgeW });

                // Connector line from candle high to badge
                ctx.beginPath();
                ctx.strokeStyle = color;
                ctx.lineWidth = 1;
                ctx.setLineDash([2, 2]);
                ctx.moveTo(Math.round(x), Math.round(yHigh - 4));
                ctx.lineTo(Math.round(x), Math.round(badgeY + badgeH));
                ctx.stroke();
                ctx.setLineDash([]);

                // Small dot on candle high wick
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.arc(Math.round(x), Math.round(yHigh - 3), 2, 0, Math.PI * 2);
                ctx.fill();

                // Background
                ctx.beginPath();
                if (typeof ctx.roundRect === 'function') {
                    ctx.roundRect(badgeX, badgeY, badgeW, badgeH, 4);
                } else {
                    ctx.rect(badgeX, badgeY, badgeW, badgeH);
                }
                ctx.fillStyle = 'rgba(8, 14, 26, 0.94)';
                ctx.fill();
                ctx.strokeStyle = color;
                ctx.lineWidth = 1.2;
                ctx.stroke();

                ctx.textBaseline = 'middle';
                ctx.textAlign = 'center';
                ctx.fillStyle = color;
                ctx.fillText(label, Math.round(x), Math.round(badgeY + badgeH / 2));
            }
        }
        ctx.restore();
    }

    // ── 4. Interactive TradingView Limit Tool & Active Position Canvas Overlay ───
    renderTradingCanvasOverlay(ctx, W, H, chartPlotRight);
}

// ─────────────────────────────────────────────────────────────────────────────
// ── TIMEFRAME CONTROLS ────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

function initTfControls() {
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tf = parseInt(btn.dataset.tf, 10);
            if (tf === C.currentTf) return;
            C.currentTf = tf;
            document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            if (C.rawCandles.length > 0) {
                applyFullChart(C.rawCandles);
            }
            if (C.dualMode && C.secondaryRawCandles.length > 0 && C.secondaryCandleSeries) {
                const secAgg = aggregateCandles(C.secondaryRawCandles, C.currentTf);
                const secSmf = computeSMF(secAgg);
                C.secondarySmfLastResult = secSmf;
                const colored = secAgg.map((c, i) => ({
                    ...c,
                    color: secSmf.lastSignal[i] === 1 ? SMF.bullColor : SMF.bearColor,
                    borderColor: secSmf.lastSignal[i] === 1 ? SMF.bullColor : SMF.bearColor,
                    wickColor: secSmf.lastSignal[i] === 1 ? SMF.bullColor : SMF.bearColor,
                }));
                C.secondaryCandleSeries.setData(colored);
                requestAnimationFrame(renderSecondarySMFCanvas);
            }
        });
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// ── SMF TOGGLE ────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

function initSMFToggle() {
    const btn = document.getElementById('smf-toggle-btn');
    if (!btn) return;
    btn.addEventListener('click', () => {
        C.smfEnabled = !C.smfEnabled;
        btn.classList.toggle('off', !C.smfEnabled);
        if (C.rawCandles.length > 0) {
            const agg = aggregateCandles(C.rawCandles, C.currentTf);
            applySMFCloud(agg);
        }
        if (!C.smfEnabled) {
            const canvas = document.getElementById('contract-smf-canvas');
            if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
        }
    });
}

function initSMFPriceToggle() {
    const btn = document.getElementById('smf-price-toggle-btn');
    if (!btn) return;
    btn.classList.toggle('off', !SMF.showPrice);

    btn.addEventListener('click', () => {
        SMF.showPrice = !SMF.showPrice;
        localStorage.setItem('smf_show_price', SMF.showPrice ? '1' : '0');
        btn.classList.toggle('off', !SMF.showPrice);
        if (C.rawCandles.length > 0) {
            const agg = aggregateCandles(C.rawCandles, C.currentTf);
            applySMFCloud(agg);
        }
    });
}

// ── Persistent Price Line Manager (eliminates y-axis redraw flicker) ─────────

function setOrUpdatePriceLine(key, options) {
    if (!C.candleSeries) return;
    if (!C.priceLineMap) C.priceLineMap = new Map();

    const existing = C.priceLineMap.get(key);

    if (!options) {
        if (existing) {
            try { C.candleSeries.removePriceLine(existing); } catch (e) {}
            C.priceLineMap.delete(key);
        }
        return;
    }

    if (existing) {
        try {
            existing.applyOptions(options);
            return existing;
        } catch (e) {
            try { C.candleSeries.removePriceLine(existing); } catch (_) {}
            C.priceLineMap.delete(key);
        }
    }

    try {
        const line = C.candleSeries.createPriceLine(options);
        C.priceLineMap.set(key, line);
        return line;
    } catch (e) {
        console.warn('createPriceLine error for', key, e);
        return null;
    }
}

function clearTargetPriceLines() {
    C.activeTargetSignal = null;
    const keys = ['limit', 'be_lock', 't1_scalp', 't2_runner', 'sl'];
    for (const k of keys) {
        setOrUpdatePriceLine(k, null);
    }
    requestAnimationFrame(renderSMFCanvas);
}

function clearAllPriceLines() {
    C.activeTargetSignal = null;
    if (!C.priceLineMap || !C.candleSeries) return;
    for (const [key, line] of C.priceLineMap.entries()) {
        try { C.candleSeries.removePriceLine(line); } catch (e) {}
    }
    C.priceLineMap.clear();
    requestAnimationFrame(renderSMFCanvas);
}

function updateTargetPriceLines(sig, currentPrice) {
    const badge = document.getElementById('badge-target');
    if (!C.targetsEnabled || !sig || !C.candleSeries) {
        if (badge) badge.style.display = 'none';
        clearTargetPriceLines();
        return;
    }

    const cur = currentPrice || sig.entryPrice;
    const isDownRegime = C.smfLastResult && C.smfLastResult.latestRegime === -1;
    const isSlHit = sig.sl && cur < sig.sl;
    const isFlat = C.dynamicExitLastResult && C.dynamicExitLastResult.positionState === 'FLAT';

    let hasExitedAfterSig = false;
    if (C.allRawSignalMarkers && C.allRawSignalMarkers.length > 0) {
        hasExitedAfterSig = C.allRawSignalMarkers.some(m => 
            m.time > sig.time && (m.signalType === 'EXIT' || m.signalType === 'DE_EXIT' || m.position === 'aboveBar')
        );
    }

    if (sig.direction !== 'BUY' || isDownRegime || isSlHit || isFlat || hasExitedAfterSig) {
        if (badge) {
            badge.style.display = 'inline-flex';
            badge.innerHTML = `🛡️ <b>POSITION CLOSED</b> (Cash / Flat)`;
            badge.style.background = 'rgba(106, 143, 172, 0.15)';
            badge.style.borderColor = 'rgba(106, 143, 172, 0.3)';
            badge.style.color = '#6a8fac';
        }
        clearTargetPriceLines();
        return;
    }

    const hit = cur >= sig.maxTarget;

    // Update Header Badge with Suggested Limit Price, Order Recommendation, and Targets (Desktop only)
    if (badge) {
        if (window.innerWidth <= 768) {
            badge.style.display = 'none';
        } else {
            badge.style.display = 'inline-flex';
            const isMkt = sig.recommendedOrder === 'MARKET';
            const orderPart = isMkt 
                ? `<span style="color:#00f2a9;font-weight:700;">⚡ ORDER: MKT (Rocket Move)</span> | ` 
                : (sig.limitPrice ? `📥 <b>Limit:</b> ₹${sig.limitPrice.toFixed(2)} | ` : '');

            if (hit) {
                badge.innerHTML = `${orderPart}⚡ <b>RUNNER TARGET HIT!</b> (₹${sig.maxTarget.toFixed(2)})`;
                badge.style.background = 'rgba(255, 209, 102, 0.25)';
                badge.style.borderColor = '#ffd166';
                badge.style.color = '#ffd166';
            } else {
                const chopWarning = sig.isMiddayChop ? ` <span style="color:#ff6b6b;font-weight:700;">[⚠️ MIDDAY CHOP]</span>` : '';
                const beText = sig.beLockTrigger ? `🛡️ <b>BE Lock:</b> ₹${sig.beLockTrigger.toFixed(2)} | ` : '';
                const t2Text = sig.target2 ? `🚀 <b>T2 (Runner):</b> ₹${sig.target2.toFixed(2)} | ` : '';
                badge.innerHTML = `${orderPart}🎯 <b>T1 (Scalp 50%):</b> ₹${sig.target1.toFixed(2)} | ${beText}${t2Text}🛑 <b>SL:</b> ₹${sig.sl.toFixed(2)}${chopWarning}`;
                badge.style.background = sig.isMiddayChop ? 'rgba(255, 107, 107, 0.12)' : (isMkt ? 'rgba(0, 242, 169, 0.15)' : 'rgba(0, 229, 255, 0.12)');
                badge.style.borderColor = sig.isMiddayChop ? 'rgba(255, 107, 107, 0.4)' : (isMkt ? '#00f2a9' : 'rgba(0, 229, 255, 0.4)');
                badge.style.color = sig.isMiddayChop ? '#ff8787' : (isMkt ? '#00f2a9' : '#00e5ff');
            }
        }
    }

    C.activeTargetSignal = sig;

    // 1. SUGGESTED ENTRY PRICE line (transparent line, axis label visible on price scale)
    if (sig.limitPrice) {
        const isMkt = sig.recommendedOrder === 'MARKET';
        setOrUpdatePriceLine('limit', {
            price: Number(sig.limitPrice.toFixed(2)),
            color: 'rgba(0,0,0,0)',
            lineWidth: 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '',
        });
    } else {
        setOrUpdatePriceLine('limit', null);
    }

    // 2. Break-Even Lock trigger line (transparent line)
    if (sig.beLockTrigger) {
        setOrUpdatePriceLine('be_lock', {
            price: Number(sig.beLockTrigger.toFixed(2)),
            color: 'rgba(0,0,0,0)',
            lineWidth: 1,
            lineStyle: 1,
            axisLabelVisible: false,
            title: '',
        });
    } else {
        setOrUpdatePriceLine('be_lock', null);
    }

    // 3. T1 SCALP price line (transparent line, axis label visible on price scale)
    if (sig.target1) {
        setOrUpdatePriceLine('t1_scalp', {
            price: Number(sig.target1.toFixed(2)),
            color: 'rgba(0,0,0,0)',
            lineWidth: 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '',
        });
    } else {
        setOrUpdatePriceLine('t1_scalp', null);
    }

    // 4. T2 RUNNER price line (transparent line, axis label visible on price scale)
    if (sig.target2) {
        setOrUpdatePriceLine('t2_runner', {
            price: Number(sig.target2.toFixed(2)),
            color: 'rgba(0,0,0,0)',
            lineWidth: 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '',
        });
    } else {
        setOrUpdatePriceLine('t2_runner', null);
    }

    // 5. Stop Loss price line (transparent line, axis label visible on price scale)
    if (sig.sl) {
        setOrUpdatePriceLine('sl', {
            price: Number(sig.sl.toFixed(2)),
            color: 'rgba(0,0,0,0)',
            lineWidth: 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '',
        });
    } else {
        setOrUpdatePriceLine('sl', null);
    }
}


function initTargetToggle() {
    const btn = document.getElementById('smf-target-toggle-btn');
    if (!btn) return;
    btn.classList.toggle('off', !C.targetsEnabled);

    btn.addEventListener('click', () => {
        C.targetsEnabled = !C.targetsEnabled;
        localStorage.setItem('smf_targets_enabled', C.targetsEnabled ? '1' : '0');
        btn.classList.toggle('off', !C.targetsEnabled);
        if (C.rawCandles.length > 0) {
            const agg = aggregateCandles(C.rawCandles, C.currentTf);
            applySMFCloud(agg);
        }
    });
}

function dispatchTrendSignalEvent(payload) {
    if (!payload || !payload.symbol) return;
    try {
        fetch('/api/events/trend_signal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            keepalive: true
        }).catch(err => console.debug('Trend signal dispatch error:', err));
    } catch (e) {
        console.debug('dispatchTrendSignalEvent error:', e);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// ── FIT BUTTON ────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

function initFitButton() {
    const btn = document.getElementById('btn-fit-contract');
    if (btn) btn.addEventListener('click', () => {
        if (C.chart) C.chart.timeScale().fitContent();
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// ── MARKET STATUS ─────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

function checkMarketStatus() {
    const now = new Date();
    const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
    const day = ist.getDay(); // 0=Sun, 6=Sat
    const h = ist.getHours(), m = ist.getMinutes();
    const mins = h * 60 + m;
    const isWeekday = day >= 1 && day <= 5;
    const OPEN = 9 * 60 + 15;
    const CLOSE = 15 * 60 + 30;

    const badge = document.getElementById('market-status-badge');
    const label = document.getElementById('market-status-label');
    if (!badge || !label) return;

    if (!isWeekday) {
        badge.className = 'closed';
        label.textContent = 'Weekend — Closed';
    } else if (mins < OPEN) {
        badge.className = 'pre-market';
        label.textContent = 'Pre-Market';
    } else if (mins <= CLOSE) {
        badge.className = '';
        label.textContent = 'Market Open';
    } else {
        badge.className = 'closed';
        label.textContent = 'Market Closed';
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// ── UI HELPERS ────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

function showOverlay(icon, title, sub) {
    const overlay = document.getElementById('chart-overlay');
    overlay.innerHTML = `
        <div class="overlay-icon">${icon}</div>
        <div class="overlay-title">${escHtml(title)}</div>
        <div class="overlay-sub">${escHtml(sub)}</div>
    `;
    overlay.classList.add('visible');
}

function hideOverlay() {
    document.getElementById('chart-overlay').classList.remove('visible');
}

function showLoading(on) {
    const bar = document.getElementById('loading-bar');
    if (!bar) return;
    if (on) {
        bar.style.width = '0%';
        bar.style.transition = 'none';
        requestAnimationFrame(() => {
            bar.style.transition = 'width 1s ease';
            bar.style.width = '80%';
        });
    } else {
        bar.style.width = '100%';
        setTimeout(() => { bar.style.width = '0%'; bar.style.transition = 'none'; }, 400);
    }
}

function setConnStatus(state, text) {
    const pill = document.getElementById('conn-pill');
    const label = document.getElementById('conn-label');
    pill.className = state === 'live' ? '' : state;
    label.textContent = text;
}

function escHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ─────────────────────────────────────────────────────────────────────────────
// ── BAR REPLAY CONTROLLER MODULE ─────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

const Replay = {
    active: false,
    playing: false,
    intervalId: null,
    fullCandles: [],
    currentIndex: 0,
    speedMs: 600,
    mode: 'auto', // 'auto' (Auto-System Backtest) | 'manual' (Manual Practice)
    manualPosition: null, // { entryPrice, entryTime, lots, units, sl, current_sl, target1, target2, be_locked }
    manualTrades: [], // closed manual trades
    lastNotifiedTradeCount: 0,
};

function initReplayController() {
    const toggleBtn = document.getElementById('btn-master-replay') || document.getElementById('btn-replay-toggle');
    const dock = document.getElementById('replay-bar');
    const playBtn = document.getElementById('rp-btn-play');
    const startBtn = document.getElementById('rp-btn-start');
    const stepBackBtn = document.getElementById('rp-btn-step-back');
    const stepFwdBtn = document.getElementById('rp-btn-step-fwd');
    const endBtn = document.getElementById('rp-btn-end');
    const scrubber = document.getElementById('rp-scrubber');
    const speedSelect = document.getElementById('rp-speed-select');
    const exitBtn = document.getElementById('rp-btn-exit');
    const datePicker = document.getElementById('rp-date-picker');
    const loadDateBtn = document.getElementById('rp-btn-load-date');

    const modeAutoBtn = document.getElementById('rp-mode-auto');
    const modeManualBtn = document.getElementById('rp-mode-manual');
    const scorecardBtn = document.getElementById('btn-master-report') || document.getElementById('rp-btn-scorecard');
    const ffBtn = document.getElementById('bt-btn-fastforward');

    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleReplay);
    }
    if (playBtn) {
        playBtn.addEventListener('click', togglePlayPause);
    }
    if (startBtn) {
        startBtn.addEventListener('click', jumpToStart);
    }
    if (stepBackBtn) {
        stepBackBtn.addEventListener('click', stepBack);
    }
    if (stepFwdBtn) {
        stepFwdBtn.addEventListener('click', stepForward);
    }
    if (endBtn) {
        endBtn.addEventListener('click', jumpToEnd);
    }
    if (scrubber) {
        scrubber.addEventListener('input', onScrubberInput);
    }
    if (speedSelect) {
        speedSelect.addEventListener('change', (e) => {
            Replay.speedMs = parseInt(e.target.value, 10) || 600;
            if (Replay.playing) {
                pauseReplay();
                playReplay();
            }
        });
    }
    if (exitBtn) {
        exitBtn.addEventListener('click', exitReplay);
    }

    if (modeAutoBtn && modeManualBtn) {
        modeAutoBtn.addEventListener('click', () => {
            Replay.mode = 'auto';
            modeAutoBtn.classList.add('active');
            modeManualBtn.classList.remove('active');
            flashExecutionAlert('⚡ AUTO BACKTEST MODE', 'System executes SMF Dynamic Exit strategy automatically to measure accuracy.', 'info');
            renderReplayStep();
        });
        modeManualBtn.addEventListener('click', () => {
            Replay.mode = 'manual';
            modeManualBtn.classList.add('active');
            modeAutoBtn.classList.remove('active');
            flashExecutionAlert('✍️ MANUAL REPLAY MODE', 'Use BUY MKT and EXIT buttons to practice discretionary trading on replay candles.', 'info');
            renderReplayStep();
        });
    }

    if (scorecardBtn) {
        scorecardBtn.addEventListener('click', openBacktestModal);
    }
    if (ffBtn) {
        ffBtn.addEventListener('click', () => {
            jumpToEnd();
            openBacktestModal();
        });
    }

    if (loadDateBtn && datePicker) {
        loadDateBtn.addEventListener('click', async () => {
            const chosenDate = datePicker.value;
            if (!chosenDate || !C.symbol) return;
            showLoading(true);
            try {
                const res = await fetch(`/api/contracts/candles?symbol=${encodeURIComponent(C.symbol)}&date=${chosenDate}`);
                const data = await res.json();
                const candles = data.candles || [];
                if (candles.length > 0) {
                    C.selectedSessionDate = chosenDate;
                    const sessionSel = document.getElementById('session-date-select');
                    if (sessionSel) sessionSel.value = chosenDate;
                    C.rawCandles = formatCandles(candles);
                    Replay.fullCandles = [...C.rawCandles];
                    const scrubber = document.getElementById('rp-scrubber');
                    if (scrubber) {
                        scrubber.min = 5;
                        scrubber.max = Replay.fullCandles.length - 1;
                    }
                    jumpToEnd();
                } else {
                    alert(`No historical candle data found for ${C.symbol} on ${chosenDate}.`);
                }
            } catch (err) {
                console.error("Load historical replay error:", err);
            } finally {
                showLoading(false);
            }
        });
    }

    // Keyboard shortcuts for Bar Replay
    window.addEventListener('keydown', (e) => {
        if (e.target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
        if (e.shiftKey && (e.key === 'R' || e.key === 'r')) {
            e.preventDefault();
            toggleReplay();
            return;
        }
        if (!Replay.active) return;

        if (e.code === 'Space') {
            e.preventDefault();
            togglePlayPause();
        } else if (e.code === 'ArrowRight') {
            e.preventDefault();
            stepForward();
        } else if (e.code === 'ArrowLeft') {
            e.preventDefault();
            stepBack();
        } else if (e.code === 'Escape') {
            e.preventDefault();
            exitReplay();
        } else if (e.code === 'Home') {
            e.preventDefault();
            jumpToStart();
        } else if (e.code === 'End') {
            e.preventDefault();
            jumpToEnd();
        }
    });
}

function toggleReplay() {
    if (Replay.active) {
        exitReplay();
    } else {
        startReplay();
    }
}

function startReplay() {
    const ceCandles = (Stations.CE && Stations.CE.rawCandles && Stations.CE.rawCandles.length >= 5) ? Stations.CE.rawCandles : [];
    const peCandles = (Stations.PE && Stations.PE.rawCandles && Stations.PE.rawCandles.length >= 5) ? Stations.PE.rawCandles : [];
    const baseCandles = ceCandles.length >= peCandles.length ? ceCandles : peCandles;

    if (!baseCandles || baseCandles.length < 5) {
        alert("Please select or load option contracts first before starting Bar Replay.");
        return;
    }

    Replay.active = true;
    C.replayMode = true;
    Replay.fullCandles = [...baseCandles];

    if (Stations.CE && Stations.CE.rawCandles) {
        Stations.CE.fullCandlesStore = [...Stations.CE.rawCandles];
    }
    if (Stations.PE && Stations.PE.rawCandles) {
        Stations.PE.fullCandlesStore = [...Stations.PE.rawCandles];
    }

    const toggleBtn = document.getElementById('btn-master-replay') || document.getElementById('btn-replay-toggle');
    const dock = document.getElementById('replay-bar');
    if (toggleBtn) toggleBtn.classList.add('active');
    if (dock) dock.classList.remove('hidden');
    document.body.classList.add('replay-mode-active');
    updateKillSwitchVisibility();

    // Default starting point: 15-20 bars into the session so SMF has initial buffer
    const defaultStart = Math.min(20, Math.max(5, Math.floor(Replay.fullCandles.length * 0.15)));
    Replay.currentIndex = defaultStart;

    const scrubber = document.getElementById('rp-scrubber');
    if (scrubber) {
        scrubber.min = 5;
        scrubber.max = Replay.fullCandles.length - 1;
        scrubber.value = Replay.currentIndex;
    }

    // Initialize datepicker with current session date
    const datePicker = document.getElementById('rp-date-picker');
    if (datePicker && Replay.fullCandles.length > 0) {
        const sec = Number(Replay.fullCandles[0].time);
        const firstTime = new Date(sec * 1000);
        if (!isNaN(firstTime.getTime())) {
            const yyyy = firstTime.getFullYear();
            const mm = String(firstTime.getMonth() + 1).padStart(2, '0');
            const dd = String(firstTime.getDate()).padStart(2, '0');
            datePicker.value = `${yyyy}-${mm}-${dd}`;
        }
    }

    ['CE', 'PE'].forEach(stKey => {
        const station = Stations && Stations[stKey];
        if (station && station.chart) {
            station.chart.timeScale().applyOptions({
                barSpacing: 8,
                rightOffset: 5,
            });
        }
    });

    if (C.chart) {
        C.chart.timeScale().applyOptions({
            barSpacing: 8,
            rightOffset: 5,
        });
    }

    renderReplayStep();
}

function exitReplay() {
    pauseReplay();
    Replay.active = false;
    C.replayMode = false;

    const toggleBtn = document.getElementById('btn-master-replay') || document.getElementById('btn-replay-toggle');
    const dock = document.getElementById('replay-bar');
    if (toggleBtn) toggleBtn.classList.remove('active');
    if (dock) dock.classList.add('hidden');
    document.body.classList.remove('replay-mode-active');
    updateKillSwitchVisibility();

    const modeSelect = document.getElementById('trade-mode-select');
    if (modeSelect && modeSelect.value === 'replay') {
        modeSelect.value = 'paper';
        fetch('/api/trade/mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: 'paper' })
        }).catch(() => {});
    }

    // Restore full datasets to both parallel stations
    ['CE', 'PE'].forEach(stKey => {
        const station = Stations && Stations[stKey];
        if (station) {
            station.activePosition = null;
            updateStationPosHud(station);
        }
        if (!station || !station.fullCandlesStore || station.fullCandlesStore.length === 0) return;
        station.rawCandles = [...station.fullCandlesStore];
        const agg = aggregateCandles(station.rawCandles, MasterState.currentTf);
        const smf = computeSMF(agg);
        station.smfResult = smf;
        if (station.candleSeries) {
            const colored = agg.map((c, i) => {
                const isBull = (smf && smf.lastSignal && smf.lastSignal[i] === 1);
                const col = isBull ? SMF.bullColor : SMF.bearColor;
                return {
                    ...c,
                    color: col,
                    borderColor: col,
                    wickColor: col,
                };
            });
            station.candleSeries.setData(colored);
            let de = null;
            if (station.dynamicExitEnabled) {
                de = computeSMFDynamicExit(agg, smf, 'V5');
                station.dynamicExitResult = de;
                if (de) {
                    if (station.dynamicTrailSeries && de.trailPoints) station.dynamicTrailSeries.setData(de.trailPoints);
                    if (station.dynamicLadderSeries && de.ladderPoints) station.dynamicLadderSeries.setData(de.ladderPoints);
                }
            }
            const markers = [];
            if (de && de.markers) markers.push(...de.markers);
            else if (smf && smf.smfMarkers) markers.push(...smf.smfMarkers);
            station.candleSeries.setMarkers(markers);
            updateStationTopBar(station, agg);
            updateStationFooter(station);
            renderStationSMFCanvas(station);
            if (station.chart && colored.length > 0) {
                const total = colored.length;
                const visibleBars = Math.min(total, 65);
                station.chart.timeScale().setVisibleLogicalRange({
                    from: Math.max(0, total - visibleBars),
                    to: total + 15
                });
            }
        }
    });

    if (Replay.fullCandles && Replay.fullCandles.length > 0 && C.chart && C.candleSeries) {
        C.rawCandles = [...Replay.fullCandles];
        updateSessionStats(C.rawCandles);
        applyFullChart(C.rawCandles, true);
    }
}

function togglePlayPause() {
    if (Replay.playing) {
        pauseReplay();
    } else {
        playReplay();
    }
}

function playReplay() {
    if (Replay.currentIndex >= Replay.fullCandles.length - 1) {
        Replay.currentIndex = 5; // Loop back if at the end
    }
    Replay.playing = true;
    const playBtn = document.getElementById('rp-btn-play');
    if (playBtn) {
        playBtn.textContent = '⏸';
        playBtn.classList.add('playing');
        playBtn.title = 'Pause Replay (Space)';
    }

    clearInterval(Replay.intervalId);
    Replay.intervalId = setInterval(() => {
        if (Replay.currentIndex < Replay.fullCandles.length - 1) {
            Replay.currentIndex++;
            renderReplayStep();
        } else {
            pauseReplay();
        }
    }, Replay.speedMs);
}

function pauseReplay() {
    Replay.playing = false;
    clearInterval(Replay.intervalId);
    Replay.intervalId = null;
    const playBtn = document.getElementById('rp-btn-play');
    if (playBtn) {
        playBtn.textContent = '▶';
        playBtn.classList.remove('playing');
        playBtn.title = 'Play Replay (Space)';
    }
}

function stepForward() {
    pauseReplay();
    if (Replay.currentIndex < Replay.fullCandles.length - 1) {
        Replay.currentIndex++;
        renderReplayStep();
    }
}

function stepBack() {
    pauseReplay();
    if (Replay.currentIndex > 5) {
        Replay.currentIndex--;
        renderReplayStep();
    }
}

function jumpToStart() {
    pauseReplay();
    Replay.currentIndex = Math.min(20, Math.max(5, Math.floor(Replay.fullCandles.length * 0.15)));
    renderReplayStep();
}

function jumpToEnd() {
    pauseReplay();
    Replay.currentIndex = Replay.fullCandles.length - 1;
    renderReplayStep();
}

function onScrubberInput(e) {
    pauseReplay();
    Replay.currentIndex = parseInt(e.target.value, 10);
    renderReplayStep();
}

function formatReplayTime(ts) {
    if (!ts) return '--:--';
    let sec = Number(ts);
    if (isNaN(sec) || sec <= 0) {
        if (typeof ts === 'string') {
            const p = new Date(ts).getTime();
            sec = isNaN(p) ? 0 : Math.floor(p / 1000);
        } else if (ts && ts.year) {
            sec = Math.floor(new Date(ts.year, (ts.month || 1) - 1, ts.day || 1).getTime() / 1000);
        }
    }
    if (sec > 10000000000) sec = Math.floor(sec / 1000);
    if (isNaN(sec) || sec <= 0) return '--:--';
    const d = new Date(sec * 1000);
    if (isNaN(d.getTime())) return '--:--';
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `${hh}:${mm}`;
}

function renderReplayStep() {
    if (!Replay.fullCandles || Replay.fullCandles.length === 0) return;
    const totalBars = Replay.fullCandles.length;
    const curIdx = Math.min(Math.max(0, Replay.currentIndex), totalBars - 1);

    const curCandle = Replay.fullCandles[curIdx];
    const firstCandle = Replay.fullCandles[0];
    const lastCandle = Replay.fullCandles[totalBars - 1];
    if (!curCandle) return;

    const targetTime = Number(curCandle.time);

    // Update scrubber and time displays
    const scrubber = document.getElementById('rp-scrubber');
    if (scrubber) scrubber.value = curIdx;

    const curTimeEl = document.getElementById('rp-cur-time');
    const totTimeEl = document.getElementById('rp-total-time');
    const barCountEl = document.getElementById('rp-bar-count');

    if (curTimeEl) {
        const tStr = formatReplayTime(curCandle.time);
        if (tStr >= '09:15' && tStr < '09:25') {
            curTimeEl.innerHTML = `${tStr} <span style="font-size:10px;color:rgba(0,242,169,0.8);font-weight:700;letter-spacing:0.5px;">[WARMUP]</span>`;
        } else {
            curTimeEl.textContent = tStr;
        }
    }
    if (totTimeEl && lastCandle) totTimeEl.textContent = formatReplayTime(lastCandle.time);
    if (barCountEl) barCountEl.textContent = `${curIdx + 1} / ${totalBars}`;

    // Update LTP & Change badge
    const ltpEl = document.getElementById('badge-ltp');
    if (ltpEl) ltpEl.textContent = `₹${curCandle.close.toFixed(2)}`;
    const chgEl = document.getElementById('badge-chg');
    if (chgEl && firstCandle) {
        const chgAbs = curCandle.close - firstCandle.open;
        const chgPct = (chgAbs / firstCandle.open) * 100;
        chgEl.textContent = `${chgAbs >= 0 ? '+' : ''}${chgAbs.toFixed(2)} (${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(2)}%)`;
        chgEl.className = chgAbs >= 0 ? 'pos' : 'neg';
    }

    updateMarketBuyButton(curCandle.close);
    evaluateReplayCandleTrading(curCandle);

    // Update both parallel stations
    ['CE', 'PE'].forEach(stKey => {
        const station = Stations && Stations[stKey];
        if (!station) return;
        const src = station.fullCandlesStore || station.rawCandles;
        if (!src || src.length === 0) return;

        const sliced = src.filter(c => Number(c.time) <= targetTime);
        if (sliced.length === 0) return;
        station.rawCandles = sliced;

        if (station.candleSeries) {
            const agg = aggregateCandles(sliced, MasterState.currentTf);
            const smf = computeSMF(agg);
            station.smfResult = smf;
            if (station.type === 'CE') C.smfLastResult = smf;
            else C.secondarySmfLastResult = smf;

            const colored = agg.map((c, i) => ({
                ...c,
                color: smf.lastSignal[i] === 1 ? SMF.bullColor : SMF.bearColor,
                borderColor: smf.lastSignal[i] === 1 ? SMF.bullColor : SMF.bearColor,
                wickColor: smf.lastSignal[i] === 1 ? SMF.bullColor : SMF.bearColor,
            }));
            station.candleSeries.setData(colored);

            let de = null;
            if (station.dynamicExitEnabled) {
                de = computeSMFDynamicExit(agg, smf, 'V5');
                station.dynamicExitResult = de;
                if (station.type === 'CE') C.dynamicExitLastResult = de;
                if (de) {
                    if (station.dynamicTrailSeries && de.trailPoints) station.dynamicTrailSeries.setData(de.trailPoints);
                    if (station.dynamicLadderSeries && de.ladderPoints) station.dynamicLadderSeries.setData(de.ladderPoints);
                }
            }

            const markers = [];
            if (de && de.markers) markers.push(...de.markers);
            else if (smf && smf.smfMarkers) markers.push(...smf.smfMarkers);
            station.candleSeries.setMarkers(markers);

            updateStationTopBar(station, agg);
            updateStationFooter(station);
            renderStationSMFCanvas(station);

            // Dynamically auto-scroll chart and autoscale price scale
            if (station.chart && colored.length > 0) {
                const total = colored.length;
                const visibleBars = Math.min(total, 65);
                station.chart.timeScale().setVisibleLogicalRange({
                    from: Math.max(0, total - visibleBars),
                    to: total + 12
                });
            }

            // Check manual replay trade tracking (SL / Target / Trailing)
            if (Replay.active && station.activePosition && station.activePosition.isReplayManual) {
                const pos = station.activePosition;
                const lastC = agg[agg.length - 1];
                if (lastC) {
                    if (pos.current_sl && lastC.low <= pos.current_sl) {
                        const exitPx = pos.current_sl;
                        const netCash = (exitPx - pos.entry_price) * pos.units;
                        station.activePosition = null;
                        flashExecutionAlert(`🛑 ${station.type} STOP LOSS HIT`, `Exited @ ₹${exitPx.toFixed(1)} (${netCash >= 0 ? '+' : ''}₹${Math.round(netCash).toLocaleString('en-IN')})`, 'danger');
                    } else if (pos.target2 && lastC.high >= pos.target2) {
                        const exitPx = pos.target2;
                        const netCash = (exitPx - pos.entry_price) * pos.units;
                        station.activePosition = null;
                        flashExecutionAlert(`🎯 ${station.type} TARGET 2 HIT`, `Profit locked @ ₹${exitPx.toFixed(1)} (+₹${Math.round(netCash).toLocaleString('en-IN')})`, 'success');
                    } else if (pos.target1 && lastC.high >= pos.target1 && !pos.be_locked) {
                        pos.be_locked = true;
                        pos.current_sl = pos.entry_price;
                        flashExecutionAlert(`🛡️ ${station.type} BREAK-EVEN LOCKED`, `Target 1 hit. SL moved to entry ₹${pos.entry_price.toFixed(1)}`, 'info');
                    }
                }
            }
            updateStationPosHud(station);
        }
    });

    // Update legacy single chart if present
    if (C.chart && C.candleSeries) {
        const sliced = Replay.fullCandles.slice(0, curIdx + 1);
        C.rawCandles = sliced;
        applyFullChart(sliced, false);
        updateSessionStats(sliced);
        const range = C.chart.timeScale().getVisibleLogicalRange();
        if (range && (curIdx >= range.to - 1 || curIdx <= range.from)) {
            C.chart.timeScale().scrollToPosition(4, false);
        }
    }

    updatePortfolioSummary();
}

function evaluateReplayCandleTrading(candle) {
    if (!candle) return;
    const posBadge = document.getElementById('badge-pos-state');
    const posHud = document.getElementById('active-pos-hud');
    const exitBtn = document.getElementById('btn-quick-exit');
    const posPnlSub = document.getElementById('btn-pos-pnl');
    const dayPnlEl = document.getElementById('stat-day-pnl');
    const dayPnlPill = document.getElementById('pill-day-pnl');

    if (Replay.mode === 'auto') {
        const deResult = C.dynamicExitLastResult;
        if (deResult) {
            const activeT = deResult.activeTrade;
            if (activeT) {
                const pts = candle.close - activeT.entryPrice;
                const grossRs = pts * 65;
                const sign = pts >= 0 ? '+' : '';

                if (posBadge) {
                    posBadge.className = 'pos-badge open-long';
                    posBadge.textContent = `🟢 AUTO LONG (1 Lot) @ ₹${activeT.entryPrice.toFixed(1)} | ${sign}${pts.toFixed(1)}p`;
                }
                if (posHud) {
                    posHud.classList.remove('hidden');
                    const hPos = document.getElementById('hud-val-pos');
                    const hSl = document.getElementById('hud-val-sl');
                    const hT1 = document.getElementById('hud-val-t1');
                    const hT2 = document.getElementById('hud-val-t2');
                    if (hPos) hPos.textContent = `₹${activeT.entryPrice.toFixed(1)}`;
                    if (hSl) hSl.textContent = `₹${Math.max(activeT.initialStop, activeT.ladderFloor).toFixed(1)}`;
                    if (hT1) hT1.textContent = `₹${activeT.target1.toFixed(1)}`;
                    if (hT2) hT2.textContent = `₹${activeT.maxTarget.toFixed(1)}`;
                }
                if (exitBtn) {
                    exitBtn.classList.remove('hidden');
                    if (posPnlSub) {
                        posPnlSub.textContent = `${sign}₹${Math.round(grossRs)} (${sign}${pts.toFixed(1)}p)`;
                        posPnlSub.style.color = pts >= 0 ? '#ffffff' : '#ffccd5';
                    }
                }
            } else {
                if (posBadge) {
                    posBadge.className = 'pos-badge flat';
                    posBadge.textContent = deResult.reentryWatcher ? '🔄 ARMED RE-ENTRY' : '⚠️ FLAT (Waiting Signal)';
                }
                if (posHud) posHud.classList.add('hidden');
                if (exitBtn) exitBtn.classList.add('hidden');
            }

            // Real-time cumulative PnL for Auto Backtest
            const realizedPts = deResult.trades.reduce((acc, tr) => acc + (tr.pnlPts || 0), 0);
            const activePts = activeT ? (candle.close - activeT.entryPrice) : 0;
            const totalPts = realizedPts + activePts;
            const totalTradesCount = deResult.trades.length + (activeT ? 1 : 0);
            const totalCharges = totalTradesCount * 55;
            const netCash = (totalPts * 65) - totalCharges;
            const signCash = netCash >= 0 ? '+' : '';
            const signPts = totalPts >= 0 ? '+' : '';

            if (dayPnlEl && dayPnlPill) {
                dayPnlEl.textContent = `${signCash}₹${Math.round(netCash).toLocaleString('en-IN')} (${signPts}${totalPts.toFixed(1)}p)`;
                dayPnlPill.className = `stat-pill ${netCash > 0 ? 'pnl-positive' : (netCash < 0 ? 'pnl-negative' : 'pnl-neutral')}`;
            }

            // Toast alert when a new trade is booked while playing
            if (Replay.playing && deResult.trades.length > Replay.lastNotifiedTradeCount) {
                const latest = deResult.trades[deResult.trades.length - 1];
                const isWin = (latest.pnlPts || 0) >= 0;
                flashExecutionAlert(
                    isWin ? '🎯 AUTO PROFIT BOOKED' : '🛑 AUTO STOP EXITED',
                    `Trade #${latest.tradeNum}: ${latest.exitReason} (${(latest.pnlPts || 0) >= 0 ? '+' : ''}${(latest.pnlPts || 0).toFixed(1)} pts)`,
                    isWin ? 'success' : 'danger'
                );
                Replay.lastNotifiedTradeCount = deResult.trades.length;
            }
        }
    } else {
        // Manual Replay Practice Mode
        evaluateManualReplayTrading(candle);
    }
}

function evaluateManualReplayTrading(candle) {
    const pos = Replay.manualPosition;
    const posBadge = document.getElementById('badge-pos-state');
    const posHud = document.getElementById('active-pos-hud');
    const exitBtn = document.getElementById('btn-quick-exit');
    const posPnlSub = document.getElementById('btn-pos-pnl');
    const dayPnlEl = document.getElementById('stat-day-pnl');
    const dayPnlPill = document.getElementById('pill-day-pnl');

    if (pos) {
        // 0. Enforce strict intraday cutoff: no overnight holds past 15:15 IST
        const d = new Date(candle.time * 1000);
        const istH = (d.getUTCHours() + 5 + Math.floor((d.getUTCMinutes() + 30) / 60)) % 24;
        const istM = (d.getUTCMinutes() + 30) % 60;
        const cTimeStr = `${String(istH).padStart(2, '0')}:${String(istM).padStart(2, '0')}`;
        if (cTimeStr >= '15:15') {
            closeManualReplayPosition(candle.close, 'INTRADAY_TIME_EXIT (15:15 Cutoff)');
            return;
        }

        // 1. Check Stop Loss breach
        if (pos.current_sl && candle.low <= pos.current_sl) {
            closeManualReplayPosition(pos.current_sl, 'STOP_LOSS_HIT');
            return;
        }
        // 2. Check Target 2 breach
        if (pos.target2 && candle.high >= pos.target2) {
            closeManualReplayPosition(pos.target2, 'TARGET_2_HIT');
            return;
        }
        // 3. Check Target 1 breach -> Lock Break-Even
        if (pos.target1 && candle.high >= pos.target1 && !pos.be_locked) {
            pos.be_locked = true;
            pos.current_sl = Math.max(pos.current_sl || 0, pos.entry_price);
            flashExecutionAlert('🛡️ MANUAL BREAK-EVEN LOCKED', `Target 1 (₹${pos.target1.toFixed(1)}) hit. SL ratcheted to ₹${pos.entry_price.toFixed(1)}`, 'success');
        }

        const pts = candle.close - pos.entry_price;
        const grossRs = pts * pos.units;
        const sign = pts >= 0 ? '+' : '';

        if (posBadge) {
            posBadge.className = 'pos-badge open-long';
            posBadge.textContent = `🟢 MANUAL LONG (1 Lot) @ ₹${pos.entry_price.toFixed(1)} | ${sign}${pts.toFixed(1)}p`;
        }
        if (posHud) {
            posHud.classList.remove('hidden');
            const hPos = document.getElementById('hud-val-pos');
            const hSl = document.getElementById('hud-val-sl');
            const hT1 = document.getElementById('hud-val-t1');
            const hT2 = document.getElementById('hud-val-t2');
            if (hPos) hPos.textContent = `₹${pos.entry_price.toFixed(1)}`;
            if (hSl) hSl.textContent = `₹${(pos.current_sl || 0).toFixed(1)}`;
            if (hT1) hT1.textContent = pos.target1 ? `₹${pos.target1.toFixed(1)}` : '——';
            if (hT2) hT2.textContent = pos.target2 ? `₹${pos.target2.toFixed(1)}` : '——';
        }
        if (exitBtn) {
            exitBtn.classList.remove('hidden');
            if (posPnlSub) {
                posPnlSub.textContent = `${sign}₹${Math.round(grossRs)} (${sign}${pts.toFixed(1)}p)`;
                posPnlSub.style.color = pts >= 0 ? '#ffffff' : '#ffccd5';
            }
        }
    } else {
        if (posBadge) {
            posBadge.className = 'pos-badge flat';
            posBadge.textContent = '⚠️ MANUAL FLAT (Click BUY MKT)';
        }
        if (posHud) posHud.classList.add('hidden');
        if (exitBtn) exitBtn.classList.add('hidden');
    }

    // Cumulative Manual Day PnL
    const realizedPts = Replay.manualTrades.reduce((acc, tr) => acc + tr.pnlPts, 0);
    const activePts = pos ? (candle.close - pos.entry_price) : 0;
    const totalPts = realizedPts + activePts;
    const totalTradesCount = Replay.manualTrades.length + (pos ? 1 : 0);
    const totalCharges = totalTradesCount * 55;
    const netCash = (totalPts * 65) - totalCharges;
    const signCash = netCash >= 0 ? '+' : '';
    const signPts = totalPts >= 0 ? '+' : '';

    if (dayPnlEl && dayPnlPill) {
        dayPnlEl.textContent = `${signCash}₹${Math.round(netCash).toLocaleString('en-IN')} (${signPts}${totalPts.toFixed(1)}p)`;
        dayPnlPill.className = `stat-pill ${netCash > 0 ? 'pnl-positive' : (netCash < 0 ? 'pnl-negative' : 'pnl-neutral')}`;
    }
}

function closeManualReplayPosition(exitPx, reason = 'MANUAL_EXIT') {
    if (!Replay.manualPosition) return;
    const pos = Replay.manualPosition;
    const pts = Math.round((exitPx - pos.entry_price) * 100) / 100;
    const grossRs = pts * pos.units;
    const charges = 55;
    const netCash = grossRs - charges;
    const sign = pts >= 0 ? '+' : '';

    const curCandle = C.rawCandles.length > 0 ? C.rawCandles[C.rawCandles.length - 1] : null;
    const exitTimeStr = curCandle ? new Date(curCandle.time * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false }) : '--';

    Replay.manualTrades.push({
        tradeNum: Replay.manualTrades.length + 1,
        side: 'BUY',
        entryTime: pos.entryTime,
        entryPrice: pos.entry_price,
        exitTime: exitTimeStr,
        exitPrice: exitPx,
        exitReason: reason,
        pnlPts: pts,
        grossRs: grossRs,
        charges: charges,
        netCash: netCash,
        isWin: pts >= 0
    });

    Replay.manualPosition = null;
    flashExecutionAlert(
        pts >= 0 ? '🎯 MANUAL PROFIT LOCKED' : '🛑 MANUAL LOSS EXITED',
        `${reason}: Exited @ ₹${exitPx.toFixed(1)} (${sign}${pts.toFixed(1)} pts | ${sign}₹${Math.round(netCash)} Net)`,
        pts >= 0 ? 'success' : 'danger'
    );
    if (curCandle) evaluateManualReplayTrading(curCandle);
}

function setBacktestFilter(filter) {
    MasterState.btFilter = filter || 'ALL';
    const btnAll = document.getElementById('bt-filter-all');
    const btnCe = document.getElementById('bt-filter-ce');
    const btnPe = document.getElementById('bt-filter-pe');

    if (btnAll) btnAll.classList.toggle('active', MasterState.btFilter === 'ALL');
    if (btnCe) btnCe.classList.toggle('active', MasterState.btFilter === 'CE');
    if (btnPe) btnPe.classList.toggle('active', MasterState.btFilter === 'PE');

    renderBacktestModalContent();
}

function openBacktestModal() {
    const modal = document.getElementById('backtest-report-modal');
    if (!modal) return;
    renderBacktestModalContent();
    modal.classList.remove('hidden');
}

function renderBacktestModalContent() {
    const symEl = document.getElementById('bt-modal-symbol');
    const subEl = document.getElementById('bt-modal-sub');
    const kpiWin = document.getElementById('bt-kpi-winrate');
    const kpiWl = document.getElementById('bt-kpi-wl');
    const kpiNet = document.getElementById('bt-kpi-net-cash');
    const kpiPts = document.getElementById('bt-kpi-pts');
    const kpiGross = document.getElementById('bt-kpi-gross');
    const kpiCharges = document.getElementById('bt-kpi-charges');
    const kpiTrades = document.getElementById('bt-kpi-trades');
    const kpiCapture = document.getElementById('bt-kpi-capture');
    const kpiGiveback = document.getElementById('bt-kpi-giveback');
    const kpiBreakdown = document.getElementById('bt-kpi-breakdown');
    const tableBody = document.getElementById('bt-table-body');

    const countAllEl = document.getElementById('bt-count-all');
    const countCeEl = document.getElementById('bt-count-ce');
    const countPeEl = document.getElementById('bt-count-pe');

    // CE trades
    const ceTrades = (Stations.CE.dynamicExitResult?.trades || []).map(t => ({
        ...t,
        contractType: 'CE',
        side: 'BUY',
        symbolName: Stations.CE.displayName || Stations.CE.symbol || 'CE',
        grossRs: (t.pnlPts || 0) * (Stations.CE.lotUnits || 65),
        charges: 55,
        netCash: (t.pnlPts || 0) * (Stations.CE.lotUnits || 65) - 55,
        isWin: (t.pnlPts || 0) >= 0,
    }));

    // PE trades
    const peTrades = (Stations.PE.dynamicExitResult?.trades || []).map(t => ({
        ...t,
        contractType: 'PE',
        side: 'BUY',
        symbolName: Stations.PE.displayName || Stations.PE.symbol || 'PE',
        grossRs: (t.pnlPts || 0) * (Stations.PE.lotUnits || 65),
        charges: 55,
        netCash: (t.pnlPts || 0) * (Stations.PE.lotUnits || 65) - 55,
        isWin: (t.pnlPts || 0) >= 0,
    }));

    if (countAllEl) countAllEl.textContent = ceTrades.length + peTrades.length;
    if (countCeEl) countCeEl.textContent = ceTrades.length;
    if (countPeEl) countPeEl.textContent = peTrades.length;

    let activeTradesList = [];
    if (MasterState.btFilter === 'CE') {
        activeTradesList = ceTrades;
    } else if (MasterState.btFilter === 'PE') {
        activeTradesList = peTrades;
    } else {
        // Combined chronologically by entryTime
        activeTradesList = [...ceTrades, ...peTrades].sort((a, b) => (a.entryTime || '').localeCompare(b.entryTime || ''));
    }

    if (symEl) {
        if (MasterState.btFilter === 'CE') symEl.textContent = `${Stations.CE.displayName || 'CALL (CE)'} — Backtest Scorecard`;
        else if (MasterState.btFilter === 'PE') symEl.textContent = `${Stations.PE.displayName || 'PUT (PE)'} — Backtest Scorecard`;
        else symEl.textContent = `${MasterState.underlying} ${MasterState.currentStrike} (CE + PE) — Combined Scorecard`;
    }

    if (subEl) subEl.textContent = `Session: ${MasterState.selectedSessionDate || 'Replay'} | Filter: ${MasterState.btFilter} | Mode: ⚡ Auto-System Dependent`;

    const totalTrades = activeTradesList.length;
    const winTrades = activeTradesList.filter(t => t.isWin).length;
    const lossTrades = totalTrades - winTrades;
    const winRate = totalTrades > 0 ? (winTrades / totalTrades) * 100 : 0;
    const totalPts = activeTradesList.reduce((acc, t) => acc + (t.pnlPts || 0), 0);
    const totalGross = activeTradesList.reduce((acc, t) => acc + (t.grossRs || 0), 0);
    const totalCharges = activeTradesList.reduce((acc, t) => acc + (t.charges || 55), 0);
    const totalNetCash = totalGross - totalCharges;

    const winningTrades = activeTradesList.filter(t => t.isWin);
    const avgCapture = winningTrades.length > 0 ? (winningTrades.reduce((acc, t) => acc + (t.captureRatio || 0), 0) / winningTrades.length) * 100 : 0;
    const avgGiveback = winningTrades.length > 0 ? (winningTrades.reduce((acc, t) => acc + (t.giveBack || 0), 0) / winningTrades.length) * 100 : 0;

    if (kpiWin) kpiWin.textContent = `${winRate.toFixed(0)}%`;
    if (kpiWl) kpiWl.textContent = `${winTrades}W / ${lossTrades}L`;
    if (kpiNet) {
        const sign = totalNetCash >= 0 ? '+' : '';
        kpiNet.textContent = `${sign}₹${Math.round(totalNetCash).toLocaleString('en-IN')}`;
        kpiNet.className = `bt-kpi-val ${totalNetCash >= 0 ? 'positive' : 'negative'}`;
    }
    if (kpiPts) {
        const sign = totalPts >= 0 ? '+' : '';
        kpiPts.textContent = `${sign}${totalPts.toFixed(1)} pts`;
    }
    if (kpiGross) {
        const sign = totalGross >= 0 ? '+' : '';
        kpiGross.textContent = `${sign}₹${Math.round(totalGross).toLocaleString('en-IN')}`;
    }
    if (kpiCharges) kpiCharges.textContent = `-₹${totalCharges.toFixed(0)}`;
    if (kpiTrades) kpiTrades.textContent = totalTrades;
    if (kpiBreakdown) kpiBreakdown.textContent = `${ceTrades.length} CE · ${peTrades.length} PE`;
    if (kpiCapture) kpiCapture.textContent = `${avgCapture.toFixed(1)}%`;
    if (kpiGiveback) kpiGiveback.textContent = `Giveback: ${avgGiveback.toFixed(1)}%`;

    if (tableBody) {
        if (activeTradesList.length === 0) {
            tableBody.innerHTML = `<tr><td colspan="12" style="text-align: center; color: #64748b; padding: 24px;">No trades executed for this filter yet. Step forward in Replay or click "⚡ Full Day" to compute backtest.</td></tr>`;
        } else {
            tableBody.innerHTML = activeTradesList.map((t, idx) => {
                const signPts = (t.pnlPts || 0) >= 0 ? '+' : '';
                const signNet = (t.netCash || 0) >= 0 ? '+' : '';
                const isCe = t.contractType === 'CE';
                const badgeHtml = isCe
                    ? `<span class="station-badge ce-badge">CE</span>`
                    : `<span class="station-badge pe-badge">PE</span>`;
                const exitPriceHtml = (t.t1Booked && t.target1)
                    ? `₹${t.target1.toFixed(2)} <span style="font-size:10px;color:#fbbf24;">(50%)</span><br><span style="color:#94a3b8;font-size:11px;">₹${t.exitPrice ? t.exitPrice.toFixed(2) : '-'} (50%)</span>`
                    : `₹${t.exitPrice ? t.exitPrice.toFixed(2) : '-'}`;
                const exitReasonHtml = t.t1Booked
                    ? `<span style="color: #fbbf24; font-weight: 600;">🎯 T1 (50%)</span> + <span style="color: #94a3b8;">${t.exitReason} (50%)</span>`
                    : `${t.exitReason || 'EXIT'}`;

                return `
                    <tr>
                        <td><strong>#${idx + 1}</strong></td>
                        <td>${badgeHtml} <span style="font-size:11px;color:#cbd5e1;">${t.symbolName || t.contractType}</span></td>
                        <td style="color: #34d399; font-weight: 700;">BUY</td>
                        <td>${t.entryTime || '--'}</td>
                        <td>₹${(t.entryPrice || 0).toFixed(2)}</td>
                        <td>${t.exitTime || '--'}</td>
                        <td>${exitPriceHtml}</td>
                        <td style="font-size: 11px;">${exitReasonHtml}</td>
                        <td style="color: ${(t.pnlPts || 0) >= 0 ? '#34d399' : '#f87171'}; font-weight: 700;">${signPts}${(t.pnlPts || 0).toFixed(2)}</td>
                        <td style="color: #f59e0b;">-₹${t.charges}</td>
                        <td style="color: ${(t.netCash || 0) >= 0 ? '#34d399' : '#f87171'}; font-weight: 800;">${signNet}₹${Math.round(t.netCash).toLocaleString('en-IN')}</td>
                        <td><span class="${t.isWin ? 'bt-pill-win' : 'bt-pill-loss'}">${t.isWin ? 'WIN' : 'LOSS'}</span></td>
                    </tr>
                `;
            }).join('');
        }
    }
}

function closeBacktestModal() {
    const modal = document.getElementById('backtest-report-modal');
    if (modal) modal.classList.add('hidden');
}
window.setBacktestFilter = setBacktestFilter;
window.openBacktestModal = openBacktestModal;
window.closeBacktestModal = closeBacktestModal;

// ─────────────────────────────────────────────────────────────────────────────
// ── CONTRACT TRADING ENGINE TERMINAL (ON-CHART & RISK CONTROLS) ──────────────
// ─────────────────────────────────────────────────────────────────────────────

function updateMarketBuyButton(ltp) {
    const pxEl = document.getElementById('btn-mkt-px');
    if (!pxEl) return;
    if (ltp != null && ltp > 0) {
        pxEl.textContent = `₹${ltp.toFixed(1)}`;
    } else if (C.rawCandles.length > 0) {
        const c = C.rawCandles[C.rawCandles.length - 1].close;
        pxEl.textContent = `₹${c.toFixed(1)}`;
    } else {
        pxEl.textContent = `₹--`;
    }
}

function getLotSizeForSymbol(sym) {
    const s = (sym || C.symbol || C.underlying || 'NIFTY').toUpperCase();
    if (s.includes('BANKNIFTY')) return 30;
    if (s.includes('SENSEX') || s.includes('BANKEX')) return 20;
    if (s.includes('FINNIFTY')) return 65;
    if (s.includes('MIDCPNIFTY')) return 120;
    return 65; // NIFTY
}

function updateLotDisplay() {
    C.lotUnits = getLotSizeForSymbol(C.symbol);
    const units = C.tradingLots * C.lotUnits;
    const lotsValEl = document.getElementById('trade-lots-val');
    const unitsValEl = document.getElementById('trade-units-val');
    if (lotsValEl) lotsValEl.textContent = C.tradingLots;
    if (unitsValEl) unitsValEl.textContent = units;
}

async function fetchTradeState() {
    try {
        const res = await fetch('/api/trade/state');
        if (!res.ok) return;
        const state = await res.json();
        C.tradingState = state;
        updateTradingUI(state);
        requestAnimationFrame(renderSMFCanvas);
    } catch (e) {
        console.warn('fetchTradeState error:', e);
    }
}

function updateKillSwitchVisibility(state) {
    const ksBtn = document.getElementById('btn-master-kill-switch') || document.getElementById('btn-kill-switch');
    if (!ksBtn) return;

    // Rule 1: No need the kill switch button on replay mode
    if (Replay && Replay.active) {
        ksBtn.classList.add('hidden');
        return;
    }

    // Rule 2: Only required in paper test on live feed or live trade on live feed (selectedSessionDate === 'today')
    const isLiveFeed = (MasterState.selectedSessionDate === 'today' || C.selectedSessionDate === 'today');
    if (!isLiveFeed) {
        ksBtn.classList.add('hidden');
        return;
    }

    // Rule 3: Make it disappear if there is no trade is running
    const tradeState = state || MasterState.tradingState || C.tradingState;
    const hasLiveActivePos = tradeState && (tradeState.active_position != null || (tradeState.active_trades && tradeState.active_trades.length > 0));
    const hasStationPos = (Stations && Stations.CE && Stations.CE.activePosition) || (Stations && Stations.PE && Stations.PE.activePosition);

    if (hasLiveActivePos || hasStationPos) {
        ksBtn.classList.remove('hidden');
        if (tradeState && tradeState.is_kill_switch_active) {
            ksBtn.textContent = '🔒 LOCKED';
            ksBtn.classList.add('locked');
            ksBtn.title = 'Kill Switch ACTIVE (Locked). Click to Unlock.';
        } else {
            ksBtn.textContent = '🛑 KILL';
            ksBtn.classList.remove('locked');
            ksBtn.title = 'Emergency Flatten & Kill Switch';
        }
    } else {
        ksBtn.classList.add('hidden');
    }
}

function updateTradingUI(state) {
    if (!state) return;

    // 1. Day PnL and Balance without label (e.g. 1131.15/101131)
    const dayPnlEl = document.getElementById('val-day-pnl');
    const pnlBalPill = document.getElementById('stat-pnl-bal') || document.getElementById('stat-day-pnl');
    if (dayPnlEl) {
        const pnl = state.total_day_pnl || 0.0;
        const sign = pnl > 0 ? '+' : '';
        dayPnlEl.textContent = `${sign}${pnl.toFixed(2)}`;
        if (pnlBalPill) {
            pnlBalPill.className = `stat-pill pnl-bal-pill ${pnl > 0 ? 'pnl-positive' : (pnl < 0 ? 'pnl-negative' : 'pnl-neutral')}`;
        }
    }

    // 2. Capital Balance in Top Header
    const capEl = document.getElementById('val-capital');
    if (capEl) {
        const cap = Math.round(state.capital || 100000);
        capEl.textContent = `${cap}`;
    }

    // 3. Mode Selection in Top Header
    const currentMode = state.mode || 'paper';
    const modeSelect = document.getElementById('trade-mode-select');
    if (modeSelect && modeSelect.value !== currentMode) {
        modeSelect.value = currentMode;
    }

    // 4. Kill Switch Button & Visibility
    updateKillSwitchVisibility(state);

    // 5. Update Single Clean Market Buy Button with live premium
    const curPx = (C.rawCandles.length > 0 ? C.rawCandles[C.rawCandles.length - 1].close : 0.0);
    updateMarketBuyButton(curPx);

    // 6. Active Position: Show/Hide Single Market Exit Button in Header with live PnL
    const exitBtn = document.getElementById('btn-quick-exit');
    const posPnlSub = document.getElementById('btn-pos-pnl');

    if (state.active_position && state.active_position.symbol === C.symbol) {
        const pos = state.active_position;
        if (exitBtn) exitBtn.classList.remove('hidden');

        // Dynamically compute live PnL if curPx > 0
        if (curPx > 0 && pos.entry_price > 0) {
            const pts = curPx - pos.entry_price;
            pos.current_price = curPx;
            pos.unrealized_pts = Math.round(pts * 100) / 100;
            pos.unrealized_pnl = Math.round(pts * pos.units * 100) / 100;
            pos.unrealized_pnl_pct = Math.round((pts / pos.entry_price) * 1000) / 10;
        }

        const pnl = pos.unrealized_pnl || 0.0;
        const pts = pos.unrealized_pts || 0.0;
        const sign = pnl >= 0 ? '+' : '';

        if (posPnlSub) {
            posPnlSub.textContent = `${sign}₹${Math.round(pnl)} (${sign}${pts.toFixed(1)}p)`;
            posPnlSub.style.color = pnl >= 0 ? '#ffffff' : '#ffccd5';
        }

        // 7. Populate Active Position Overview HUD in Toolbar (POS, SL, T1, T2)
        const posHud = document.getElementById('active-pos-hud');
        const hudValPos = document.getElementById('hud-val-pos');
        const hudValSl = document.getElementById('hud-val-sl');
        const hudValT1 = document.getElementById('hud-val-t1');
        const hudValT2 = document.getElementById('hud-val-t2');

        if (posHud) posHud.classList.remove('hidden');
        if (hudValPos) hudValPos.textContent = `₹${pos.entry_price.toFixed(1)}`;
        if (hudValSl) hudValSl.textContent = `₹${(pos.current_sl || 0).toFixed(1)}`;
        if (hudValT1) hudValT1.textContent = pos.target1 ? `₹${pos.target1.toFixed(1)}` : '——';
        if (hudValT2) hudValT2.textContent = pos.target2 ? `₹${pos.target2.toFixed(1)}` : '——';

        // Also update Day PnL Pill to reflect active position's unrealized PnL!
        const totalPnl = (state.realized_day_pnl || 0.0) + pnl;
        if (dayPnlEl && dayPnlPill) {
            const dSign = totalPnl >= 0 ? '+' : '';
            dayPnlEl.textContent = `${dSign}₹${totalPnl.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
            dayPnlPill.className = `stat-pill ${totalPnl > 0 ? 'pnl-positive' : (totalPnl < 0 ? 'pnl-negative' : 'pnl-neutral')}`;
        }
    } else {
        if (exitBtn) exitBtn.classList.add('hidden');
        const posHud = document.getElementById('active-pos-hud');
        if (posHud) posHud.classList.add('hidden');
    }

    // 8. Update Station CE and Station PE Exit Buttons & HUDs
    ['ce', 'pe'].forEach((pfx) => {
        const station = pfx === 'ce' ? Stations.CE : Stations.PE;
        updateStationPosHud(station, state);
    });
}

function updateStationPosHud(station, state) {
    if (!station) return;
    const pfx = station.type.toLowerCase();
    const toolbar = document.getElementById(`${pfx}-toolbar`);
    const sExitBtn = document.getElementById(`${pfx}-btn-exit`);
    const sPosPnlSub = document.getElementById(`${pfx}-pos-pnl`);
    const sPosHud = document.getElementById(`${pfx}-pos-hud`);
    const sHudValPos = document.getElementById(`${pfx}-hud-pos`);
    const sHudValSl = document.getElementById(`${pfx}-hud-sl`);
    const sHudValT1 = document.getElementById(`${pfx}-hud-t1`);
    const sHudValT2 = document.getElementById(`${pfx}-hud-t2`);

    // Strictly resolve position belonging to this station ONLY (never cross-contaminate CE/PE)
    const tradeState = state || MasterState.tradingState || C.tradingState;
    let pos = null;
    if (tradeState && tradeState.active_positions && station.symbol && tradeState.active_positions[station.symbol]) {
        pos = tradeState.active_positions[station.symbol];
    } else if (tradeState && tradeState.active_position && station.symbol && tradeState.active_position.symbol === station.symbol) {
        pos = tradeState.active_position;
    } else if (station.activePosition && (station.activePosition.symbol === station.symbol || station.activePosition.type === station.type)) {
        pos = station.activePosition;
    }

    if (pos) {
        if (toolbar) toolbar.classList.add('has-pos');
        if (sExitBtn) sExitBtn.classList.remove('hidden');
        if (sPosHud) sPosHud.classList.remove('hidden');

        const curPx = station.rawCandles.length > 0 ? station.rawCandles[station.rawCandles.length - 1].close : (pos.entry_price || pos.entryPrice || 0);
        const entryPx = pos.entry_price || pos.entryPrice || 0;
        const pts = curPx - entryPx;
        const units = pos.units || (station.tradingLots * (station.lotUnits || 65)) || 65;
        const pnl = pts * units;
        const sign = pnl >= 0 ? '+' : '';
        const ptSign = pts >= 0 ? '+' : '';

        if (sPosPnlSub) {
            sPosPnlSub.textContent = `${sign}₹${Math.round(pnl)}`;
            sPosPnlSub.style.color = pnl >= 0 ? '#34d399' : '#f87171';
        }
        if (sHudValPos) sHudValPos.textContent = `₹${entryPx.toFixed(1)}`;
        if (sHudValSl) sHudValSl.textContent = `₹${(pos.current_sl || pos.sl || 0).toFixed(1)}`;
        if (sHudValT1) {
            if (pos.target1) {
                sHudValT1.textContent = `₹${pos.target1.toFixed(1)}`;
                sHudValT1.parentElement?.classList.remove('hidden');
            } else {
                sHudValT1.parentElement?.classList.add('hidden');
            }
        }
        if (sHudValT2) {
            if (pos.target2) {
                sHudValT2.textContent = `₹${pos.target2.toFixed(1)}`;
                sHudValT2.parentElement?.classList.remove('hidden');
            } else {
                sHudValT2.parentElement?.classList.add('hidden');
            }
        }
    } else {
        if (toolbar) toolbar.classList.remove('has-pos');
        if (sExitBtn) sExitBtn.classList.add('hidden');
        if (sPosHud) sPosHud.classList.add('hidden');
    }
}

// ── TradingView-Style Interactive Limit Order & Target/SL Canvas Overlay ───────

function renderTradingCanvasOverlay(ctx, W, H, chartPlotRight) {
    if (!C.candleSeries || !C.chart) return;

    // Reset hit boxes for mouse drag & click detection
    C.limitToolHitBoxes = [];

    const units = (C.tradingLots || 1) * (C.lotUnits || 65);
    const lastCandle = C.rawCandles.length > 0 ? C.rawCandles[C.rawCandles.length - 1] : null;
    const curCandleX = lastCandle
        ? Math.max(0, Math.min(chartPlotRight - 30, Math.round(C.chart.timeScale().timeToCoordinate(lastCandle.time) || 0)))
        : Math.max(0, chartPlotRight - 150);

    // ── 1. TRADINGVIEW-STYLE LIMIT ORDER TOOL (WHEN ACTIVE) ───────────────────
    if (C.limitTool && C.limitTool.active) {
        const lim = C.limitTool.limitPrice;
        const tgt = C.limitTool.targetPrice;
        const sl = C.limitTool.slPrice;

        const yLim = Math.round(C.candleSeries.priceToCoordinate(lim));
        const yTgt = Math.round(C.candleSeries.priceToCoordinate(tgt));
        const ySL = Math.round(C.candleSeries.priceToCoordinate(sl));

        if (yLim != null && yTgt != null && ySL != null) {
            ctx.save();

            // Order Box Geometry: TradingView Long Position Box
            const boxW = Math.min(260, Math.max(180, chartPlotRight - 60));
            const boxX = Math.max(10, Math.min(chartPlotRight - boxW - 8, curCandleX - 25));

            // A. Target (Profit) Green Zone
            const profitTop = Math.min(yLim, yTgt);
            const profitH = Math.max(3, Math.abs(yTgt - yLim));

            ctx.beginPath();
            ctx.fillStyle = 'rgba(16, 185, 129, 0.22)';
            ctx.fillRect(boxX, profitTop, boxW, profitH);
            ctx.strokeStyle = 'rgba(16, 185, 129, 0.75)';
            ctx.lineWidth = 1.5;
            ctx.strokeRect(boxX, profitTop, boxW, profitH);

            // B. Stop Loss (Risk) Red Zone
            const riskTop = Math.min(yLim, ySL);
            const riskH = Math.max(3, Math.abs(ySL - yLim));

            ctx.beginPath();
            ctx.fillStyle = 'rgba(239, 68, 68, 0.22)';
            ctx.fillRect(boxX, riskTop, boxW, riskH);
            ctx.strokeStyle = 'rgba(239, 68, 68, 0.75)';
            ctx.lineWidth = 1.5;
            ctx.strokeRect(boxX, riskTop, boxW, riskH);

            // C. Middle Entry Divider Line
            ctx.beginPath();
            ctx.strokeStyle = '#00e5ff';
            ctx.lineWidth = 2;
            ctx.moveTo(boxX, yLim);
            ctx.lineTo(boxX + boxW, yLim);
            ctx.stroke();

            // Center Grab Handles ⚪
            // Target Grab Handle ⚪
            ctx.beginPath();
            ctx.arc(boxX + boxW / 2, yTgt, 5, 0, Math.PI * 2);
            ctx.fillStyle = '#10b981';
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1.5;
            ctx.stroke();

            // Entry Grab Handle ⚪
            ctx.beginPath();
            ctx.arc(boxX + boxW / 2, yLim, 5, 0, Math.PI * 2);
            ctx.fillStyle = '#00e5ff';
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1.5;
            ctx.stroke();

            // SL Grab Handle ⚪
            ctx.beginPath();
            ctx.arc(boxX + boxW / 2, ySL, 5, 0, Math.PI * 2);
            ctx.fillStyle = '#ef4444';
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1.5;
            ctx.stroke();

            // Metrics calculations
            const tgtPts = tgt - lim;
            const tgtRs = tgtPts * units;
            const slPts = lim - sl;
            const slRs = slPts * units;
            const rr = slPts > 0 ? (tgtPts / slPts).toFixed(2) : '--';

            // Text inside Green Box (Target)
            ctx.font = 'bold 11px Inter, -apple-system, sans-serif';
            ctx.fillStyle = '#10b981';
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            const tgtYText = (yLim - yTgt > 26) ? (yTgt + 12) : (yTgt - 10);
            ctx.fillText(`🎯 TARGET ₹${tgt.toFixed(1)} (+${tgtPts.toFixed(1)}p | +₹${Math.round(tgtRs).toLocaleString('en-IN')})`, boxX + 8, tgtYText);

            // Text inside Red Box (SL)
            ctx.fillStyle = '#ef4444';
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            const slYText = (ySL - yLim > 26) ? (ySL - 12) : (ySL + 10);
            ctx.fillText(`🛑 STOP LOSS ₹${sl.toFixed(1)} (-${slPts.toFixed(1)}p | -₹${Math.round(slRs).toLocaleString('en-IN')})`, boxX + 8, slYText);

            // Floating Central Control Badge (Limit, R:R, BUY LIMIT, Cancel)
            const limText = `📥 ₹${lim.toFixed(1)}`;
            const rrText = `R:R 1:${rr}`;
            const placeText = `✔ BUY LIMIT`;
            const cancelText = `✕`;

            const limTextW = Math.ceil(ctx.measureText(limText).width);
            const rrTextW = Math.ceil(ctx.measureText(rrText).width);
            const placeTextW = Math.ceil(ctx.measureText(placeText).width);
            const cancelTextW = Math.ceil(ctx.measureText(cancelText).width);

            const badgeW = limTextW + rrTextW + placeTextW + cancelTextW + 40;
            const badgeH = 26;
            const badgeX = Math.round(boxX + (boxW - badgeW) / 2);
            const badgeY = Math.round(yLim - badgeH / 2);

            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') ctx.roundRect(badgeX, badgeY, badgeW, badgeH, 5);
            else ctx.rect(badgeX, badgeY, badgeW, badgeH);
            ctx.fillStyle = '#080e1a';
            ctx.fill();
            ctx.strokeStyle = '#00e5ff';
            ctx.lineWidth = 1.3;
            ctx.stroke();

            // Text: Limit & RR
            ctx.fillStyle = '#00e5ff';
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            ctx.fillText(limText, badgeX + 7, yLim);

            ctx.fillStyle = '#ffd166';
            ctx.fillText(rrText, badgeX + limTextW + 15, yLim);

            // Button: BUY LIMIT
            const btnPlaceX = badgeX + limTextW + rrTextW + 23;
            const btnPlaceW = placeTextW + 10;
            const btnPlaceH = badgeH - 6;
            const btnPlaceY = badgeY + 3;

            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') ctx.roundRect(btnPlaceX, btnPlaceY, btnPlaceW, btnPlaceH, 3);
            else ctx.rect(btnPlaceX, btnPlaceY, btnPlaceW, btnPlaceH);
            ctx.fillStyle = '#00f2a9';
            ctx.fill();

            ctx.fillStyle = '#040914';
            ctx.font = 'bold 11px Inter, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(placeText, btnPlaceX + btnPlaceW / 2, yLim);

            // Button: Cancel
            const btnCancelX = btnPlaceX + btnPlaceW + 6;
            const btnCancelW = cancelTextW + 8;
            const btnCancelH = btnPlaceH;
            const btnCancelY = btnPlaceY;

            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') ctx.roundRect(btnCancelX, btnCancelY, btnCancelW, btnCancelH, 3);
            else ctx.rect(btnCancelX, btnCancelY, btnCancelW, btnCancelH);
            ctx.fillStyle = 'rgba(255, 77, 109, 0.25)';
            ctx.fill();
            ctx.strokeStyle = '#ff4d6d';
            ctx.lineWidth = 1;
            ctx.stroke();

            ctx.fillStyle = '#ff4d6d';
            ctx.textAlign = 'center';
            ctx.fillText(cancelText, btnCancelX + btnCancelW / 2, yLim);

            // Register Hitboxes:
            // 1. Target Drag (entire green box surface OR top handle)
            C.limitToolHitBoxes.push({
                type: 'target_drag',
                x: boxX,
                y: profitTop,
                w: boxW,
                h: profitH,
                lineY: yTgt
            });
            // 2. SL Drag (entire red box surface OR bottom handle)
            C.limitToolHitBoxes.push({
                type: 'sl_drag',
                x: boxX,
                y: riskTop,
                w: boxW,
                h: riskH,
                lineY: ySL
            });
            // 3. Limit Entry Drag (middle line / handle)
            C.limitToolHitBoxes.push({
                type: 'limit_drag',
                x: boxX,
                y: yLim - 8,
                w: boxW,
                h: 16,
                lineY: yLim
            });
            // 4. Place button
            C.limitToolHitBoxes.push({
                type: 'limit_place',
                x: btnPlaceX,
                y: btnPlaceY,
                w: btnPlaceW,
                h: btnPlaceH
            });
            // 5. Cancel button
            C.limitToolHitBoxes.push({
                type: 'limit_cancel',
                x: btnCancelX,
                y: btnCancelY,
                w: btnCancelW,
                h: btnCancelH
            });

            ctx.restore();
        }
    }

    // ── 2. ACTIVE POSITION LIVE LINES (DRAGGABLE SL & TARGET) ────────────────
    if (C.tradingState && C.tradingState.active_position && C.tradingState.active_position.symbol === C.symbol) {
        const pos = C.tradingState.active_position;
        const entryPx = pos.entry_price;
        const curSl = pos.current_sl;
        const tgt1 = pos.target1;
        const tgt2 = pos.target2;

        // Dynamically compute fresh live PnL against the latest candle close
        const curPx = (lastCandle ? lastCandle.close : 0.0);
        if (curPx > 0 && entryPx > 0) {
            const pts = curPx - entryPx;
            pos.current_price = curPx;
            pos.unrealized_pts = Math.round(pts * 100) / 100;
            pos.unrealized_pnl = Math.round(pts * pos.units * 100) / 100;
            pos.unrealized_pnl_pct = Math.round((pts / entryPx) * 1000) / 10;
        }

        const pnl = pos.unrealized_pnl || 0.0;
        const pts = pos.unrealized_pts || 0.0;
        const sign = pnl >= 0 ? '+' : '';

        const yEntry = Math.round(C.candleSeries.priceToCoordinate(entryPx));
        const yCurSl = (curSl != null && curSl > 0) ? Math.round(C.candleSeries.priceToCoordinate(curSl)) : null;
        const yTgt1 = (tgt1 != null && tgt1 > 0) ? Math.round(C.candleSeries.priceToCoordinate(tgt1)) : null;
        const yTgt2 = (tgt2 != null && tgt2 > 0) ? Math.round(C.candleSeries.priceToCoordinate(tgt2)) : null;

        const isMobile = W <= 768 || window.innerWidth <= 768;
        ctx.save();
        ctx.font = isMobile ? 'bold 9px Inter, -apple-system, sans-serif' : 'bold 10.5px Inter, -apple-system, sans-serif';

        // Entry Line: Bright Emerald Solid (Stops before badge - no strike through!)
        if (yEntry != null && yEntry >= 0 && yEntry <= H) {
            const entryLabel = `POS: ${pos.lots}L @ ₹${entryPx.toFixed(1)} | ${sign}₹${pnl.toFixed(0)} (${sign}${pts.toFixed(1)}p)${pos.be_locked ? ' [BE 🛡️]' : ''}`;
            const textW = Math.ceil(ctx.measureText(entryLabel).width);
            const badgeW = textW + (isMobile ? 10 : 16);
            const badgeH = isMobile ? 18 : 22;
            const badgeX = Math.round(chartPlotRight - badgeW - 4);
            const badgeY = Math.round(yEntry - badgeH / 2);

            // 1. Horizontal line terminates at badgeX - 3 (NEVER enters the badge)
            ctx.beginPath();
            ctx.strokeStyle = pnl >= 0 ? '#00f2a9' : '#ff4d6d';
            ctx.lineWidth = 1.5;
            ctx.setLineDash([]);
            ctx.moveTo(curCandleX, yEntry);
            ctx.lineTo(badgeX - 3, yEntry);
            ctx.stroke();

            // 2. Clean, opaque badge container
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') ctx.roundRect(badgeX, badgeY, badgeW, badgeH, 3);
            else ctx.rect(badgeX, badgeY, badgeW, badgeH);
            ctx.fillStyle = '#080e1a';
            ctx.fill();
            ctx.strokeStyle = pnl >= 0 ? '#00f2a9' : '#ff4d6d';
            ctx.lineWidth = 1.2;
            ctx.stroke();

            // 3. Crisp text centered inside badge
            ctx.fillStyle = pnl >= 0 ? '#00f2a9' : '#ff4d6d';
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            ctx.fillText(entryLabel, badgeX + (isMobile ? 5 : 8), yEntry);
        }

        // Active SL Line: Draggable (Stops before badge - no strike through!)
        if (yCurSl != null && yCurSl >= 0 && yCurSl <= H) {
            const slLabel = `SL: ₹${curSl.toFixed(1)} ↕`;
            const slTextW = Math.ceil(ctx.measureText(slLabel).width);
            const slBadgeW = slTextW + (isMobile ? 10 : 16);
            const slBadgeH = isMobile ? 18 : 22;
            const slBadgeX = Math.round(chartPlotRight - slBadgeW - 4);
            const slBadgeY = Math.round(yCurSl - slBadgeH / 2);

            ctx.beginPath();
            ctx.strokeStyle = '#ff4d6d';
            ctx.lineWidth = 1.4;
            ctx.setLineDash([4, 4]);
            ctx.moveTo(curCandleX, yCurSl);
            ctx.lineTo(slBadgeX - 3, yCurSl);
            ctx.stroke();

            ctx.setLineDash([]);
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') ctx.roundRect(slBadgeX, slBadgeY, slBadgeW, slBadgeH, 3);
            else ctx.rect(slBadgeX, slBadgeY, slBadgeW, slBadgeH);
            ctx.fillStyle = '#080e1a';
            ctx.fill();
            ctx.strokeStyle = '#ff4d6d';
            ctx.lineWidth = 1.1;
            ctx.stroke();

            ctx.fillStyle = '#ff4d6d';
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            ctx.fillText(slLabel, slBadgeX + (isMobile ? 5 : 8), yCurSl);

            C.limitToolHitBoxes.push({
                type: 'pos_sl_drag',
                y: yCurSl,
                lineY: yCurSl,
                x: slBadgeX,
                w: slBadgeW,
                h: slBadgeH
            });
        }

        // Active Target 1 Line: Draggable (Stops before badge - no strike through!)
        if (yTgt1 != null && yTgt1 >= 0 && yTgt1 <= H) {
            const tgtLabel = `T1: ₹${tgt1.toFixed(1)} ↕`;
            const tgtTextW = Math.ceil(ctx.measureText(tgtLabel).width);
            const tgtBadgeW = tgtTextW + (isMobile ? 10 : 16);
            const tgtBadgeH = isMobile ? 18 : 22;
            const tgtBadgeX = Math.round(chartPlotRight - tgtBadgeW - 4);
            const tgtBadgeY = Math.round(yTgt1 - tgtBadgeH / 2);

            ctx.beginPath();
            ctx.strokeStyle = '#ffd166';
            ctx.lineWidth = 1.4;
            ctx.setLineDash([4, 4]);
            ctx.moveTo(curCandleX, yTgt1);
            ctx.lineTo(tgtBadgeX - 3, yTgt1);
            ctx.stroke();

            ctx.setLineDash([]);
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') ctx.roundRect(tgtBadgeX, tgtBadgeY, tgtBadgeW, tgtBadgeH, 3);
            else ctx.rect(tgtBadgeX, tgtBadgeY, tgtBadgeW, tgtBadgeH);
            ctx.fillStyle = '#080e1a';
            ctx.fill();
            ctx.strokeStyle = '#ffd166';
            ctx.lineWidth = 1.1;
            ctx.stroke();

            ctx.fillStyle = '#ffd166';
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            ctx.fillText(tgtLabel, tgtBadgeX + (isMobile ? 5 : 8), yTgt1);

            C.limitToolHitBoxes.push({
                type: 'pos_tgt_drag',
                y: yTgt1,
                lineY: yTgt1,
                x: tgtBadgeX,
                w: tgtBadgeW,
                h: tgtBadgeH
            });
        }

        // Active Target 2 Line: Draggable (Stops before badge - no strike through!)
        if (yTgt2 != null && yTgt2 >= 0 && yTgt2 <= H) {
            const tgt2Label = `POS T2: ₹${tgt2.toFixed(1)} ↕`;
            const tgt2TextW = Math.ceil(ctx.measureText(tgt2Label).width);
            const tgt2BadgeW = tgt2TextW + (isMobile ? 10 : 16);
            const tgt2BadgeH = isMobile ? 18 : 22;
            const tgt2BadgeX = Math.round(chartPlotRight - tgt2BadgeW - 4);
            const tgt2BadgeY = Math.round(yTgt2 - tgt2BadgeH / 2);

            ctx.beginPath();
            ctx.strokeStyle = '#ffbe0b';
            ctx.lineWidth = 1.4;
            ctx.setLineDash([4, 4]);
            ctx.moveTo(curCandleX, yTgt2);
            ctx.lineTo(tgt2BadgeX - 3, yTgt2);
            ctx.stroke();

            ctx.setLineDash([]);
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') ctx.roundRect(tgt2BadgeX, tgt2BadgeY, tgt2BadgeW, tgt2BadgeH, 3);
            else ctx.rect(tgt2BadgeX, tgt2BadgeY, tgt2BadgeW, tgt2BadgeH);
            ctx.fillStyle = '#080e1a';
            ctx.fill();
            ctx.strokeStyle = '#ffbe0b';
            ctx.lineWidth = 1.1;
            ctx.stroke();

            ctx.fillStyle = '#ffbe0b';
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            ctx.fillText(tgt2Label, tgt2BadgeX + (isMobile ? 5 : 8), yTgt2);

            C.limitToolHitBoxes.push({
                type: 'pos_tgt2_drag',
                y: yTgt2,
                lineY: yTgt2,
                x: tgt2BadgeX,
                w: tgt2BadgeW,
                h: tgt2BadgeH
            });
        }

        ctx.restore();
    }
}

// ── Execute Limit Order Function ──────────────────────────────────────────────

async function executeTradingViewLimitOrder() {
    if (!C.symbol) {
        flashExecutionAlert('⚠️ NO CONTRACT', 'Please select an option contract from the chain first.', 'warning');
        return;
    }
    const curPx = C.rawCandles.length > 0 ? C.rawCandles[C.rawCandles.length - 1].close : 0.0;
    const limitPx = C.limitTool.limitPrice;
    const sl = C.limitTool.slPrice;
    const t1 = C.limitTool.targetPrice;

    try {
        const res = await fetch('/api/trade/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol: C.symbol,
                side: 'BUY',
                order_type: 'LIMIT',
                lots: C.tradingLots,
                limit_price: limitPx,
                current_market_price: curPx,
                sl: sl,
                target1: t1,
                trailing_mode: 'be_lock'
            })
        });
        const data = await res.json();
        if (!res.ok) {
            flashExecutionAlert('❌ LIMIT FAILED', data.detail || 'Limit order failed.', 'danger');
        } else {
            flashExecutionAlert('📥 LIMIT ORDER PLACED', `Limit Buy @ ₹${limitPx.toFixed(1)} (SL ₹${sl.toFixed(1)} | Target ₹${t1.toFixed(1)})`, 'success');
            C.limitTool.active = false;
            document.getElementById('btn-draw-limit')?.classList.remove('active');
            await fetchTradeState();
        }
    } catch (e) {
        flashExecutionAlert('❌ ORDER ERROR', e.message, 'danger');
    }
}

// ── Interactive Mouse Dragging & Click Handlers on Chart ──────────────────────

function initTradingCanvasMouseHandlers() {
    const chartArea = document.getElementById('chart-area');
    if (!chartArea || C._tradingMouseInitialized) return;
    C._tradingMouseInitialized = true;

    let dragType = null;

    chartArea.addEventListener('mousemove', (e) => {
        const rect = chartArea.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;

        if (dragType && C.candleSeries) {
            const price = C.candleSeries.coordinateToPrice(mouseY);
            if (price != null && price > 0) {
                const tickPx = Math.round(price * 20) / 20;
                if (dragType === 'limit_drag') {
                    const delta = tickPx - C.limitTool.limitPrice;
                    C.limitTool.limitPrice = tickPx;
                    C.limitTool.targetPrice = Math.round((C.limitTool.targetPrice + delta) * 20) / 20;
                    C.limitTool.slPrice = Math.round((C.limitTool.slPrice + delta) * 20) / 20;
                } else if (dragType === 'target_drag') {
                    C.limitTool.targetPrice = Math.max(C.limitTool.limitPrice + 0.5, tickPx);
                } else if (dragType === 'sl_drag') {
                    C.limitTool.slPrice = Math.min(C.limitTool.limitPrice - 0.5, tickPx);
                } else if (dragType === 'pos_sl_drag') {
                    if (C.tradingState?.active_position) {
                        C.tradingState.active_position.current_sl = tickPx;
                    }
                } else if (dragType === 'pos_tgt_drag') {
                    if (C.tradingState?.active_position) {
                        C.tradingState.active_position.target1 = tickPx;
                    }
                } else if (dragType === 'pos_tgt2_drag') {
                    if (C.tradingState?.active_position) {
                        C.tradingState.active_position.target2 = tickPx;
                    }
                }
                requestAnimationFrame(renderSMFCanvas);
            }
            return;
        }

        // Hover cursor styling check
        const hitBoxes = C.limitToolHitBoxes || [];
        let hit = null;
        for (const hb of hitBoxes) {
            if (hb.type.includes('place') || hb.type.includes('cancel')) {
                if (mouseX >= hb.x && mouseX <= hb.x + hb.w && mouseY >= hb.y && mouseY <= hb.y + hb.h) {
                    hit = hb;
                    break;
                }
            } else if (hb.type.includes('drag')) {
                const isLineNear = hb.lineY != null ? Math.abs(mouseY - hb.lineY) <= 8 : Math.abs(mouseY - hb.y) <= 8;
                const isInsideRect = (mouseX >= hb.x && mouseX <= hb.x + hb.w && mouseY >= hb.y && mouseY <= hb.y + hb.h);
                if (isLineNear || isInsideRect) {
                    hit = hb;
                    break;
                }
            }
        }

        if (hit) {
            if (hit.type.includes('place') || hit.type.includes('cancel')) {
                chartArea.style.cursor = 'pointer';
            } else {
                chartArea.style.cursor = 'ns-resize';
            }
        } else {
            chartArea.style.cursor = '';
        }
    });

    chartArea.addEventListener('mousedown', (e) => {
        const rect = chartArea.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;

        const hitBoxes = C.limitToolHitBoxes || [];
        for (const hb of hitBoxes) {
            if (hb.type === 'limit_place') {
                if (mouseX >= hb.x && mouseX <= hb.x + hb.w && mouseY >= hb.y && mouseY <= hb.y + hb.h) {
                    e.stopPropagation();
                    executeTradingViewLimitOrder();
                    return;
                }
            } else if (hb.type === 'limit_cancel') {
                if (mouseX >= hb.x && mouseX <= hb.x + hb.w && mouseY >= hb.y && mouseY <= hb.y + hb.h) {
                    e.stopPropagation();
                    C.limitTool.active = false;
                    document.getElementById('btn-draw-limit')?.classList.remove('active');
                    requestAnimationFrame(renderSMFCanvas);
                    return;
                }
            } else if (hb.type.includes('drag')) {
                const isLineNear = hb.lineY != null ? Math.abs(mouseY - hb.lineY) <= 8 : Math.abs(mouseY - hb.y) <= 8;
                const isInsideRect = (mouseX >= hb.x && mouseX <= hb.x + hb.w && mouseY >= hb.y && mouseY <= hb.y + hb.h);
                if (isLineNear || isInsideRect) {
                    e.stopPropagation();
                    dragType = hb.type;
                    return;
                }
            }
        }

        // If Limit Tool is active and user clicks on empty chart space, reposition the box there!
        if (C.limitTool && C.limitTool.active && C.candleSeries) {
            const price = C.candleSeries.coordinateToPrice(mouseY);
            if (price != null && price > 0) {
                const newLim = Math.round(price * 20) / 20;
                const dTarget = Math.max(4.0, (C.limitTool.targetPrice || newLim + 6) - (C.limitTool.limitPrice || newLim));
                const dSL = Math.max(3.0, (C.limitTool.limitPrice || newLim) - (C.limitTool.slPrice || newLim - 4));
                C.limitTool.limitPrice = newLim;
                C.limitTool.targetPrice = Math.round((newLim + dTarget) * 20) / 20;
                C.limitTool.slPrice = Math.round((newLim - dSL) * 20) / 20;
                requestAnimationFrame(renderSMFCanvas);
            }
        }
    });

    window.addEventListener('mouseup', async () => {
        if (dragType) {
            if (dragType === 'pos_sl_drag' || dragType === 'pos_tgt_drag' || dragType === 'pos_tgt2_drag') {
                const pos = C.tradingState?.active_position;
                if (pos) {
                    try {
                        await fetch('/api/trade/modify', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                symbol: C.symbol,
                                sl: pos.current_sl,
                                target1: pos.target1,
                                target2: pos.target2
                            })
                        });
                        flashExecutionAlert('🛡️ LEVELS UPDATED', `SL: ₹${pos.current_sl.toFixed(1)} | T1: ₹${(pos.target1 || 0).toFixed(1)} | T2: ₹${(pos.target2 || 0).toFixed(1)}`, 'success');
                    } catch (err) {
                        console.warn('Modify position levels error:', err);
                    }
                }
            }
            dragType = null;
            chartArea.style.cursor = '';
        }
    });
}

// ── Initialization of Terminal Controls ───────────────────────────────────────

function initTradingTerminal() {
    // 1. Lots selector
    const btnMinus = document.getElementById('btn-qty-minus');
    const btnPlus = document.getElementById('btn-qty-plus');
    if (btnMinus) {
        btnMinus.addEventListener('click', () => {
            C.tradingLots = Math.max(1, C.tradingLots - 1);
            updateLotDisplay();
            requestAnimationFrame(renderSMFCanvas);
        });
    }
    if (btnPlus) {
        btnPlus.addEventListener('click', () => {
            C.tradingLots = Math.min(100, C.tradingLots + 1);
            updateLotDisplay();
            requestAnimationFrame(renderSMFCanvas);
        });
    }
    updateLotDisplay();

    // 2. Mode select dropdown
    const modeSelect = document.getElementById('trade-mode-select');
    if (modeSelect) {
        modeSelect.addEventListener('change', async (e) => {
            const mode = e.target.value;
            if (mode === 'replay') {
                if (!Replay.active && C.rawCandles && C.rawCandles.length > 0) {
                    startReplay();
                }
            } else {
                if (Replay.active) {
                    exitReplay();
                }
                if (C.selectedSessionDate && C.selectedSessionDate !== 'today') {
                    const sessionSel = document.getElementById('session-date-select');
                    if (sessionSel) {
                        sessionSel.value = 'today';
                        sessionSel.dispatchEvent(new Event('change'));
                    }
                }
            }
            try {
                const res = await fetch('/api/trade/mode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode })
                });
                if (res.ok) {
                    await fetchTradeState();
                }
            } catch (err) {
                console.warn('Set mode error:', err);
            }
        });
    }

    // 3. Single Clean Market BUY Button (1-Click instant execution at live market price)
    const btnBuyMkt = document.getElementById('btn-quick-buy-mkt');
    if (btnBuyMkt) {
        btnBuyMkt.addEventListener('click', async () => {
            if (!C.symbol) {
                flashExecutionAlert('⚠️ NO CONTRACT', 'Please select an option contract from the chain first.', 'warning');
                return;
            }
            const curPx = C.rawCandles.length > 0 ? C.rawCandles[C.rawCandles.length - 1].close : 0.0;
            if (curPx <= 0) {
                flashExecutionAlert('⚠️ NO PRICE', 'Waiting for live contract price...', 'warning');
                return;
            }

            // In Replay Mode: Handle Auto vs Manual execution
            if (C.replayMode || Replay.active) {
                if (Replay.mode === 'auto') {
                    flashExecutionAlert('⚡ AUTO BACKTEST MODE', 'System executes trades automatically on SMF signals. Switch to "✍️ Manual" on the replay dock to place manual orders.', 'info');
                    return;
                } else {
                    if (Replay.manualPosition) {
                        flashExecutionAlert('⚠️ POSITION ACTIVE', 'You already have an open manual position. Click EXIT to close it first.', 'warning');
                        return;
                    }
                    const curCandle = C.rawCandles.length > 0 ? C.rawCandles[C.rawCandles.length - 1] : null;
                    if (!curCandle) return;

                    const d = new Date(curCandle.time * 1000);
                    const istH = (d.getUTCHours() + 5 + Math.floor((d.getUTCMinutes() + 30) / 60)) % 24;
                    const istM = (d.getUTCMinutes() + 30) % 60;
                    const cTimeStr = `${String(istH).padStart(2, '0')}:${String(istM).padStart(2, '0')}`;

                    if (cTimeStr < '09:25') {
                        flashExecutionAlert('⚠️ WARMUP CANDLES (09:15 - 09:25)', 'Indicators are calibrating during the first 10 minutes. Trading opens at 09:25 AM.', 'warning');
                        return;
                    }
                    if (cTimeStr > '15:15') {
                        flashExecutionAlert('⚠️ INTRADAY CUTOFF REACHED (15:15 IST)', 'No trades allowed after 15:15 to prevent holding past close or next day.', 'warning');
                        return;
                    }

                    const smf = C.smfLastResult;
                    const curAtr = (smf && smf.atr && smf.atr.length > 0) ? smf.atr[smf.atr.length - 1] : 3.0;
                    const entryPrice = curCandle.close;
                    const entryTimeStr = cTimeStr;

                    Replay.manualPosition = {
                        entryPrice: entryPrice,
                        entryTime: entryTimeStr,
                        lots: C.tradingLots || 1,
                        units: (C.tradingLots || 1) * 65,
                        sl: Math.round((entryPrice - curAtr * 1.5) * 20) / 20,
                        current_sl: Math.round((entryPrice - curAtr * 1.5) * 20) / 20,
                        target1: Math.round((entryPrice + Math.max(8.0, curAtr * 1.5)) * 20) / 20,
                        target2: Math.round((entryPrice + curAtr * 3.0) * 20) / 20,
                        be_locked: false
                    };
                    flashExecutionAlert('⚡ MANUAL BUY FILLED', `Bought 1 Lot @ ₹${entryPrice.toFixed(1)} (SL ₹${Replay.manualPosition.sl} | T1 ₹${Replay.manualPosition.target1})`, 'success');
                    evaluateManualReplayTrading(curCandle);
                    return;
                }
            }

            // 1-Click Pure Market Buy for Live / Paper:
            const sl = null;
            const t1 = null;
            const t2 = null;

            try {
                const res = await fetch('/api/trade/order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        symbol: C.symbol,
                        side: 'BUY',
                        order_type: 'MARKET',
                        lots: C.tradingLots,
                        current_market_price: curPx,
                        sl: sl,
                        target1: t1,
                        target2: t2,
                        trailing_mode: 'manual'
                    })
                });
                const data = await res.json();
                if (!res.ok) {
                    flashExecutionAlert('❌ ORDER REJECTED', data.detail || 'Order execution failed.', 'danger');
                } else {
                    const fillPx = data.order?.fill_price || curPx;
                    flashExecutionAlert('⚡ MARKET ORDER FILLED', `Bought ${C.tradingLots} Lot @ ₹${fillPx.toFixed(1)}`, 'success');
                    await fetchTradeState();
                }
            } catch (e) {
                flashExecutionAlert('❌ ORDER ERROR', e.message, 'danger');
            }
        });
    }

    // 4. TradingView-Style Interactive Limit Tool Toggle
    const btnLimitTool = document.getElementById('btn-draw-limit');
    if (btnLimitTool) {
        btnLimitTool.addEventListener('click', () => {
            C.limitTool.active = !C.limitTool.active;
            btnLimitTool.classList.toggle('active', C.limitTool.active);

            if (C.limitTool.active) {
                const curPx = (C.rawCandles.length > 0 ? C.rawCandles[C.rawCandles.length - 1].close : 100.0);
                const suggestedLT = C.activeTargetSignal?.limitPrice;
                const basePx = suggestedLT || curPx;

                C.limitTool.limitPrice = Math.round(basePx * 20) / 20;
                C.limitTool.targetPrice = C.activeTargetSignal?.target1
                    ? Math.round(C.activeTargetSignal.target1 * 20) / 20
                    : Math.round((C.limitTool.limitPrice + Math.max(6.0, curPx * 0.08)) * 20) / 20;
                C.limitTool.slPrice = C.activeTargetSignal?.sl
                    ? Math.round(C.activeTargetSignal.sl * 20) / 20
                    : Math.round((C.limitTool.limitPrice - Math.max(4.0, curPx * 0.05)) * 20) / 20;

                flashExecutionAlert('📐 LIMIT TOOL ACTIVE', 'Drag the Limit, Target, and SL lines directly on the chart.', 'highlight');
            }
            requestAnimationFrame(renderSMFCanvas);
        });
    }

    // 5. Single Instant Exit 100% Button
    const btnExit = document.getElementById('btn-quick-exit');
    if (btnExit) {
        btnExit.addEventListener('click', async () => {
            const curPx = C.rawCandles.length > 0 ? C.rawCandles[C.rawCandles.length - 1].close : null;

            // In Replay Mode:
            if (C.replayMode || Replay.active) {
                if (Replay.mode === 'manual' && Replay.manualPosition) {
                    if (curPx) closeManualReplayPosition(curPx, 'MANUAL_QUICK_EXIT');
                    return;
                } else if (Replay.mode === 'auto') {
                    flashExecutionAlert('⚡ AUTO BACKTEST MODE', 'In auto backtest mode, the system automatically manages trade exits.', 'info');
                    return;
                }
            }

            try {
                const res = await fetch('/api/trade/exit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason: 'MANUAL_QUICK_EXIT', exit_price: curPx })
                });
                const data = await res.json();
                if (!res.ok) {
                    flashExecutionAlert('❌ EXIT FAILED', data.detail || 'Exit failed.', 'danger');
                } else {
                    const pnl = data.closed_trade?.net_realized_pnl || 0.0;
                    const sign = pnl >= 0 ? '+' : '';
                    flashExecutionAlert('🏁 POSITION CLOSED', `Closed @ ₹${data.closed_trade?.exit_price?.toFixed(1) || curPx?.toFixed(1)} | PnL: ${sign}₹${pnl.toFixed(2)}`, pnl >= 0 ? 'success' : 'danger');
                    await fetchTradeState();
                }
            } catch (e) {
                flashExecutionAlert('❌ EXIT ERROR', e.message, 'danger');
            }
        });
    }

    // 6. Emergency Kill Switch
    const btnKs = document.getElementById('btn-kill-switch');
    if (btnKs) {
        btnKs.addEventListener('click', async () => {
            const isLocked = C.tradingState?.is_kill_switch_active;
            const endpoint = isLocked ? '/api/trade/kill_switch/unlock' : '/api/trade/kill_switch';
            try {
                const res = await fetch(endpoint, { method: 'POST' });
                if (res.ok) {
                    await fetchTradeState();
                }
            } catch (e) {
                console.warn('Kill switch error:', e);
            }
        });
    }

    // 7. Initialize Canvas Mouse Dragging & Click Handlers for Interactive Chart Lines
    initTradingCanvasMouseHandlers();

    // Start background state polling
    fetchTradeState();
    if (C.tradingPollTimer) clearInterval(C.tradingPollTimer);
    C.tradingPollTimer = setInterval(fetchTradeState, 1500);
}

// ─────────────────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
// ── PARALLEL OPTION STATIONS ARCHITECTURE (CALL STATION + PUT STATION) ────────
// ─────────────────────────────────────────────────────────────────────────────

const Stations = {
    CE: {
        type: 'CE',
        symbol: null,
        displayName: null,
        chart: null,
        candleSeries: null,
        dynamicTrailSeries: null,
        dynamicLadderSeries: null,
        canvas: null,
        container: null,
        rawCandles: [],
        smfResult: null,
        dynamicExitResult: null,
        ws: null,
        wsPingInterval: null,
        tradingLots: 1,
        lotUnits: 65,
        activePosition: null,
        targetsEnabled: true,
        smfEnabled: true,
        showPrice: false,
        dynamicExitEnabled: true,
    },
    PE: {
        type: 'PE',
        symbol: null,
        displayName: null,
        chart: null,
        candleSeries: null,
        dynamicTrailSeries: null,
        dynamicLadderSeries: null,
        canvas: null,
        container: null,
        rawCandles: [],
        smfResult: null,
        dynamicExitResult: null,
        ws: null,
        wsPingInterval: null,
        tradingLots: 1,
        lotUnits: 65,
        activePosition: null,
        targetsEnabled: true,
        smfEnabled: true,
        showPrice: false,
        dynamicExitEnabled: true,
    }
};

const MasterState = {
    underlying: 'NIFTY',
    currentStrike: 22700,
    atmStrike: 22700,
    strikeStep: 50,
    selectedExpiry: '',
    selectedSessionDate: 'today',
    availableSessionDates: [],
    currentTf: 1,
    optionChainOpen: false,
    optionChainData: null,
    optionChainFullRange: false,
    tradingState: null,
    btFilter: 'ALL',
    combinedPortfolioPnL: 0,
};

async function initParallelStations() {
    createStationChart(Stations.CE, 'ce-chart-container', 'ce-smf-canvas');
    createStationChart(Stations.PE, 'pe-chart-container', 'pe-smf-canvas');

    C.chart = Stations.CE.chart;
    C.candleSeries = Stations.CE.candleSeries;
    C.secondaryChart = Stations.PE.chart;
    C.secondaryCandleSeries = Stations.PE.candleSeries;

    bindStationControls(Stations.CE);
    bindStationControls(Stations.PE);
    bindMasterHeaderControls();

    let syncLock = false;
    if (Stations.CE.chart && Stations.PE.chart) {
        Stations.CE.chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
            if (syncLock || !range) return;
            syncLock = true;
            Stations.PE.chart.timeScale().setVisibleLogicalRange(range);
            syncLock = false;
            requestAnimationFrame(() => {
                renderStationSMFCanvas(Stations.CE);
                renderStationSMFCanvas(Stations.PE);
            });
        });
        Stations.PE.chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
            if (syncLock || !range) return;
            syncLock = true;
            Stations.CE.chart.timeScale().setVisibleLogicalRange(range);
            syncLock = false;
            requestAnimationFrame(() => {
                renderStationSMFCanvas(Stations.CE);
                renderStationSMFCanvas(Stations.PE);
            });
        });
    }
}

function createStationChart(station, containerId, canvasId) {
    const container = document.getElementById(containerId);
    const canvas = document.getElementById(canvasId);
    if (!container) return;

    station.container = container;
    station.canvas = canvas;

    const chart = LightweightCharts.createChart(container, {
        layout: {
            background: { type: 'solid', color: '#070b14' },
            textColor: '#6a8fac',
            fontFamily: "'Inter', sans-serif",
            fontSize: 11,
        },
        grid: {
            vertLines: { color: 'rgba(0, 195, 255, 0.05)' },
            horzLines: { color: 'rgba(0, 195, 255, 0.05)' },
        },
        crosshair: {
            vertLine: { color: station.type === 'CE' ? 'rgba(0, 242, 169, 0.5)' : 'rgba(255, 0, 93, 0.5)', width: 1, style: 2 },
            horzLine: { color: station.type === 'CE' ? 'rgba(0, 242, 169, 0.5)' : 'rgba(255, 0, 93, 0.5)', width: 1, style: 2 },
        },
        rightPriceScale: {
            borderColor: 'rgba(0, 195, 255, 0.15)',
            textColor: '#6a8fac',
            autoScale: true,
            scaleMargins: { top: 0.10, bottom: 0.05 },
        },
        timeScale: {
            borderColor: 'rgba(0, 195, 255, 0.15)',
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 25,
            barSpacing: 8,
            minBarSpacing: 4,
            tickMarkFormatter: (t) => {
                let sec = Number(t);
                if (isNaN(sec) || sec <= 0) {
                    if (typeof t === 'string') sec = Math.floor(new Date(t).getTime() / 1000);
                    else if (t && t.year) sec = Math.floor(new Date(t.year, t.month - 1, t.day).getTime() / 1000);
                }
                const d = new Date(sec * 1000);
                if (isNaN(d.getTime())) return '';
                return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
            },
        },
        localization: {
            timeFormatter: (t) => {
                let sec = Number(t);
                if (isNaN(sec) || sec <= 0) {
                    if (typeof t === 'string') sec = Math.floor(new Date(t).getTime() / 1000);
                    else if (t && t.year) sec = Math.floor(new Date(t.year, t.month - 1, t.day).getTime() / 1000);
                }
                const d = new Date(sec * 1000);
                if (isNaN(d.getTime())) return '';
                return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
            },
        },
        handleScale: { axisPressedMouseMove: { time: true, price: true }, axisDoubleClickReset: { time: true, price: true }, mouseWheel: true, pinch: true },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
    });

    const candleSeries = chart.addCandlestickSeries({
        upColor: SMF.bullColor,
        downColor: SMF.bearColor,
        borderUpColor: SMF.bullColor,
        borderDownColor: SMF.bearColor,
        wickUpColor: SMF.bullColor,
        wickDownColor: SMF.bearColor,
        autoscaleInfoProvider: (original) => {
            const res = original();
            if (res && res.priceRange) {
                return { priceRange: { minValue: Math.max(0, res.priceRange.minValue), maxValue: Math.max(1, res.priceRange.maxValue) } };
            }
            return res;
        },
    });

    station.dynamicTrailSeries = chart.addLineSeries({
        color: '#f59e0b', lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
    });
    station.dynamicLadderSeries = chart.addLineSeries({
        color: '#06b6d4', lineWidth: 1.5, lineStyle: 2, priceLineVisible: false, lastValueVisible: false,
    });

    station.chart = chart;
    station.candleSeries = candleSeries;

    chart.subscribeCrosshairMove(() => {
        requestAnimationFrame(() => renderStationSMFCanvas(station));
    });

    const syncCanvas = () => {
        const w = container.clientWidth;
        const h = container.clientHeight;
        if (w > 0 && h > 0) {
            chart.applyOptions({ width: w, height: h });
            if (canvas) {
                canvas.width = w;
                canvas.height = h;
            }
        }
        requestAnimationFrame(() => renderStationSMFCanvas(station));
    };

    new ResizeObserver(syncCanvas).observe(container);
    window.addEventListener('resize', syncCanvas);
    syncCanvas();
}

function bindStationControls(station) {
    const pfx = station.type.toLowerCase();

    const lotMinus = document.getElementById(`${pfx}-lot-minus`);
    const lotPlus = document.getElementById(`${pfx}-lot-plus`);
    const lotVal = document.getElementById(`${pfx}-lot-val`);

    if (lotMinus) {
        lotMinus.addEventListener('click', () => {
            station.tradingLots = Math.max(1, station.tradingLots - 1);
            if (lotVal) lotVal.textContent = station.tradingLots;
        });
    }
    if (lotPlus) {
        lotPlus.addEventListener('click', () => {
            station.tradingLots = Math.min(20, station.tradingLots + 1);
            if (lotVal) lotVal.textContent = station.tradingLots;
        });
    }

    const buyBtn = document.getElementById(`${pfx}-btn-buy`);
    if (buyBtn) {
        buyBtn.addEventListener('click', async () => {
            if (!station.symbol) return;
            const units = station.tradingLots * station.lotUnits;
            const curPx = station.rawCandles.length > 0 ? station.rawCandles[station.rawCandles.length - 1].close : 0;

            // Handle Replay Mode locally (no broker orders on historical bars)
            if (Replay && Replay.active) {
                if (Replay.mode === 'auto') {
                    flashExecutionAlert('⚡ AUTO BACKTEST MODE', 'System executes trades automatically on SMF signals. Switch to "✍️ Manual" on the replay dock to place manual orders.', 'info');
                    return;
                }
                if (station.activePosition) {
                    flashExecutionAlert('⚠️ POSITION ACTIVE', `You already have an open ${station.type} position. Click EXIT to close it first.`, 'warning');
                    return;
                }
                const curCandle = station.rawCandles.length > 0 ? station.rawCandles[station.rawCandles.length - 1] : null;
                if (!curCandle || curPx <= 0) {
                    flashExecutionAlert('⚠️ NO PRICE', 'Waiting for replay candle price...', 'warning');
                    return;
                }

                const smf = station.smfResult;
                const curAtr = (smf && smf.atr && smf.atr.length > 0) ? smf.atr[smf.atr.length - 1] : 3.0;
                const sl = Math.round((curPx - curAtr * 1.5) * 20) / 20;
                const t1 = Math.round((curPx + Math.max(8.0, curAtr * 1.5)) * 20) / 20;
                const t2 = Math.round((curPx + curAtr * 3.0) * 20) / 20;

                station.activePosition = {
                    symbol: station.symbol,
                    type: station.type,
                    entry_price: curPx,
                    entryPrice: curPx,
                    entryTime: curCandle.time,
                    lots: station.tradingLots,
                    units: units,
                    sl: sl,
                    current_sl: sl,
                    target1: t1,
                    target2: t2,
                    be_locked: false,
                    isReplayManual: true
                };

                updateStationPosHud(station);
                flashExecutionAlert(`🟢 ${station.type} MANUAL BUY FILLED`, `Bought ${units} qty @ ₹${curPx.toFixed(1)} (SL ₹${sl} | T1 ₹${t1})`, 'success');
                return;
            }

            try {
                const res = await fetch('/api/trade/order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        symbol: station.symbol,
                        exchange: 'NFO',
                        side: 'BUY',
                        order_type: 'MARKET',
                        lots: station.tradingLots,
                        quantity: units,
                        product: 'MIS',
                        validity: 'DAY',
                        price: curPx,
                        current_market_price: curPx
                    })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    flashExecutionAlert(`🟢 ${station.type} BUY EXECUTED`, `Bought ${station.tradingLots} Lot (${units} qty) of ${station.symbol} @ ₹${curPx.toFixed(1)}`, 'success');
                    await fetchTradeState();
                } else {
                    flashExecutionAlert(`❌ ORDER REJECTED`, data.detail || 'Trade failed', 'danger');
                }
            } catch (e) {
                flashExecutionAlert(`❌ ERROR`, e.message, 'danger');
            }
        });
    }

    const exitBtn = document.getElementById(`${pfx}-btn-exit`);
    if (exitBtn) {
        exitBtn.addEventListener('click', async () => {
            if (!station.symbol) return;
            const curPx = station.rawCandles.length > 0 ? station.rawCandles[station.rawCandles.length - 1].close : 0;

            // Handle Replay Mode locally
            if (Replay && Replay.active) {
                if (station.activePosition) {
                    const pos = station.activePosition;
                    const pts = curPx - (pos.entry_price || pos.entryPrice);
                    const netCash = pts * pos.units;
                    station.activePosition = null;
                    updateStationPosHud(station);
                    flashExecutionAlert(`🏁 ${station.type} MANUAL EXIT`, `Closed @ ₹${curPx.toFixed(1)} (${netCash >= 0 ? '+' : ''}₹${Math.round(netCash).toLocaleString('en-IN')})`, 'success');
                    return;
                }
            }

            try {
                const res = await fetch('/api/trade/exit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbol: station.symbol })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    flashExecutionAlert(`🏁 ${station.type} POSITION CLOSED`, `Exited successfully`, 'success');
                    await fetchTradeState();
                } else {
                    flashExecutionAlert(`❌ EXIT FAILED`, data.detail || 'Exit failed', 'danger');
                }
            } catch (e) {
                flashExecutionAlert(`❌ EXIT ERROR`, e.message, 'danger');
            }
        });
    }

    // 🎛️ Single Indicators Popover Trigger
    const btnIndicators = document.getElementById(`${pfx}-btn-indicators`);
    const indicatorsPanel = document.getElementById(`${pfx}-indicators-panel`);

    if (btnIndicators && indicatorsPanel) {
        btnIndicators.addEventListener('click', (e) => {
            e.stopPropagation();
            indicatorsPanel.classList.toggle('hidden');
        });
        document.addEventListener('click', (e) => {
            if (!indicatorsPanel.contains(e.target) && !btnIndicators.contains(e.target)) {
                indicatorsPanel.classList.add('hidden');
            }
        });
    }

    const chkSmf = document.getElementById(`${pfx}-chk-smf`);
    if (chkSmf) {
        chkSmf.addEventListener('change', () => {
            station.smfEnabled = chkSmf.checked;
            renderStationSMFCanvas(station);
        });
    }

    const chkPrice = document.getElementById(`${pfx}-chk-price`);
    if (chkPrice) {
        chkPrice.addEventListener('change', () => {
            station.showPrice = chkPrice.checked;
            if (station.rawCandles.length > 0) {
                loadStationCandles(station, { symbol: station.symbol, display_name: station.displayName });
            }
        });
    }

    const chkTgt = document.getElementById(`${pfx}-chk-target`);
    if (chkTgt) {
        chkTgt.addEventListener('change', () => {
            station.targetsEnabled = chkTgt.checked;
            renderStationSMFCanvas(station);
        });
    }

    const chkExit = document.getElementById(`${pfx}-chk-exit`);
    if (chkExit) {
        chkExit.addEventListener('change', () => {
            station.dynamicExitEnabled = chkExit.checked;
            if (station.rawCandles.length > 0) {
                loadStationCandles(station, { symbol: station.symbol, display_name: station.displayName });
            }
        });
    }

    const btnToggleLog = document.getElementById(`${pfx}-btn-toggle-log`);
    const logDrawer = document.getElementById(`${pfx}-log-drawer`);
    const logClose = document.getElementById(`${pfx}-log-close`);

    if (btnToggleLog && logDrawer) {
        btnToggleLog.addEventListener('click', () => {
            logDrawer.classList.toggle('hidden');
        });
    }
    if (logClose && logDrawer) {
        logClose.addEventListener('click', () => {
            logDrawer.classList.add('hidden');
        });
    }
}

function bindMasterHeaderControls() {
    const undSelect = document.getElementById('master-underlying-select') || document.getElementById('oc-underlying-select');
    if (undSelect) {
        undSelect.addEventListener('change', async (e) => {
            MasterState.underlying = e.target.value;
            C.underlying = e.target.value;
            MasterState.strikeStep = (MasterState.underlying === 'NIFTY') ? 50 : 100;
            if (['FINNIFTY', 'MIDCPNIFTY'].includes(MasterState.underlying)) MasterState.strikeStep = 25;
            await initExpirySelector();
            await fetchOptionChain();
        });
    }

    const btnPrev = document.getElementById('btn-strike-prev');
    const btnNext = document.getElementById('btn-strike-next');
    if (btnPrev) {
        btnPrev.addEventListener('click', () => stepStrike(-1));
    }
    if (btnNext) {
        btnNext.addEventListener('click', () => stepStrike(+1));
    }

    const expSelect = document.getElementById('master-expiry-select');
    if (expSelect) {
        expSelect.addEventListener('change', (e) => {
            MasterState.selectedExpiry = e.target.value;
            C.selectedExpiry = e.target.value;
            loadStrikePair(MasterState.currentStrike);
        });
    }

    const tfToggle = document.getElementById('btn-timeframe-toggle');
    if (tfToggle) {
        tfToggle.addEventListener('click', () => {
            const current = MasterState.currentTf || 1;
            const next = current === 1 ? 5 : 1;
            setMasterTimeframe(next);
        });
    }

    const tf1 = document.getElementById('tf-1m');
    const tf5 = document.getElementById('tf-5m');
    if (tf1) {
        tf1.addEventListener('click', () => setMasterTimeframe(1));
    }
    if (tf5) {
        tf5.addEventListener('click', () => setMasterTimeframe(5));
    }

    const dateSelect = document.getElementById('master-session-date-select');
    if (dateSelect) {
        dateSelect.addEventListener('change', (e) => {
            MasterState.selectedSessionDate = e.target.value;
            C.selectedSessionDate = e.target.value;
            loadStrikePair(MasterState.currentStrike, MasterState.selectedExpiry, MasterState.selectedSessionDate);
        });
    }

    const btnChain = document.getElementById('btn-master-chain');
    if (btnChain) {
        btnChain.addEventListener('click', toggleOptionChain);
    }

    const btnReport = document.getElementById('btn-master-report');
    if (btnReport) {
        btnReport.addEventListener('click', openBacktestModal);
    }

    const btnKs = document.getElementById('btn-master-kill-switch');
    if (btnKs) {
        btnKs.addEventListener('click', async () => {
            const isLocked = MasterState.tradingState?.is_kill_switch_active;
            const endpoint = isLocked ? '/api/trade/kill_switch/unlock' : '/api/trade/kill_switch';
            try {
                const res = await fetch(endpoint, { method: 'POST' });
                if (res.ok) await fetchTradeState();
            } catch (e) {
                console.warn('Kill switch error:', e);
            }
        });
    }

    const ffBtn = document.getElementById('bt-btn-fastforward');
    if (ffBtn) {
        ffBtn.addEventListener('click', () => {
            if (Stations.CE.rawCandles.length > 0) {
                const agg = aggregateCandles(Stations.CE.rawCandles, MasterState.currentTf);
                const smf = computeSMF(agg);
                Stations.CE.smfResult = smf;
                Stations.CE.dynamicExitResult = computeSMFDynamicExit(agg, smf, 'V5');
                updateStationFooter(Stations.CE);
            }
            if (Stations.PE.rawCandles.length > 0) {
                const agg = aggregateCandles(Stations.PE.rawCandles, MasterState.currentTf);
                const smf = computeSMF(agg);
                Stations.PE.smfResult = smf;
                Stations.PE.dynamicExitResult = computeSMFDynamicExit(agg, smf, 'V5');
                updateStationFooter(Stations.PE);
            }
            updatePortfolioSummary();
            renderBacktestModalContent();
        });
    }
}

function stepStrike(dir) {
    const nextStrike = MasterState.currentStrike + (dir * MasterState.strikeStep);
    loadStrikePair(nextStrike);
}

function setMasterTimeframe(tf) {
    MasterState.currentTf = tf;
    C.currentTf = tf;
    const tfToggle = document.getElementById('btn-timeframe-toggle');
    if (tfToggle) {
        tfToggle.textContent = `${tf}m`;
        tfToggle.setAttribute('data-tf', tf);
        tfToggle.classList.toggle('tf-5m-active', tf === 5);
        tfToggle.title = `Chart Timeframe: ${tf}m (Click to switch to ${tf === 1 ? '5m' : '1m'})`;
    }
    const tf1 = document.getElementById('tf-1m');
    const tf5 = document.getElementById('tf-5m');
    if (tf1) tf1.classList.toggle('active', tf === 1);
    if (tf5) tf5.classList.toggle('active', tf === 5);

    if (Stations.CE.rawCandles.length > 0) {
        loadStationCandles(Stations.CE, { symbol: Stations.CE.symbol, display_name: Stations.CE.displayName });
    }
    if (Stations.PE.rawCandles.length > 0) {
        loadStationCandles(Stations.PE, { symbol: Stations.PE.symbol, display_name: Stations.PE.displayName });
    }
}

async function loadStrikePair(strike, optExpiry, optDate) {
    strike = Number(strike);
    MasterState.currentStrike = strike;
    const expiry = optExpiry || MasterState.selectedExpiry || '';
    const date = optDate || MasterState.selectedSessionDate || 'today';

    let ceContract = null;
    let peContract = null;

    if (MasterState.optionChainData && Array.isArray(MasterState.optionChainData.chain)) {
        const row = MasterState.optionChainData.chain.find(r => Number(r.strike) === strike);
        if (row) {
            ceContract = row.call;
            peContract = row.put;
        }
    }

    if (!ceContract || !ceContract.symbol) {
        ceContract = { symbol: `${MasterState.underlying}24OCT${strike}CE`, display_name: `${MasterState.underlying} ${strike} CE`, expiry };
    }
    if (!peContract || !peContract.symbol) {
        peContract = { symbol: `${MasterState.underlying}24OCT${strike}PE`, display_name: `${MasterState.underlying} ${strike} PE`, expiry };
    }

    Stations.CE.symbol = ceContract.symbol;
    Stations.CE.displayName = ceContract.display_name || `${strike} CE`;
    Stations.PE.symbol = peContract.symbol;
    Stations.PE.displayName = peContract.display_name || `${strike} PE`;

    C.symbol = Stations.CE.symbol;
    C.displayName = Stations.CE.displayName;
    C.secondaryContract = Stations.PE;

    const strikeVal = document.getElementById('cur-strike-val');
    const strikeTag = document.getElementById('cur-strike-tag');
    const btnPrev = document.getElementById('btn-strike-prev');
    const btnNext = document.getElementById('btn-strike-next');
    const ceSymEl = document.getElementById('ce-symbol-text');
    const peSymEl = document.getElementById('pe-symbol-text');

    if (strikeVal) strikeVal.textContent = strike;
    if (strikeTag) {
        const isAtm = strike === MasterState.atmStrike;
        const diff = strike - MasterState.atmStrike;
        strikeTag.textContent = isAtm ? 'ATM' : (diff > 0 ? `+${diff}` : `${diff}`);
        strikeTag.className = isAtm ? 'strike-tag atm' : 'strike-tag';
    }
    if (btnPrev) btnPrev.textContent = `-${MasterState.strikeStep}`;
    if (btnNext) btnNext.textContent = `+${MasterState.strikeStep}`;

    if (ceSymEl) ceSymEl.textContent = Stations.CE.displayName;
    if (peSymEl) peSymEl.textContent = Stations.PE.displayName;

    await Promise.all([
        loadStationCandles(Stations.CE, ceContract, date),
        loadStationCandles(Stations.PE, peContract, date)
    ]);

    if (date === 'today') {
        connectStationWS(Stations.CE);
        connectStationWS(Stations.PE);
    }

    updatePortfolioSummary();
}

async function loadStationCandles(station, contract, optDate) {
    if (!station || !contract || !contract.symbol) return;
    try {
        const targetDate = optDate || MasterState.selectedSessionDate || 'today';
        const url = `/api/contracts/candles?symbol=${encodeURIComponent(contract.symbol)}&date=${encodeURIComponent(targetDate)}`;
        const res = await fetch(url);
        if (!res.ok) return;
        const data = await res.json();
        const raw = data.candles || [];
        const candles = formatCandles(raw);
        station.rawCandles = candles;
        station.fullCandlesStore = [...candles];

        if (station.type === 'CE') {
            C.rawCandles = candles;
            Replay.fullCandles = candles;
        } else {
            C.secondaryRawCandles = candles;
        }

        if (candles.length === 0 || !station.candleSeries) return;

        const agg = aggregateCandles(candles, MasterState.currentTf);
        const smf = computeSMF(agg);
        station.smfResult = smf;

        if (station.type === 'CE') C.smfLastResult = smf;
        else C.secondarySmfLastResult = smf;

        const colored = agg.map((c, i) => ({
            ...c,
            color: smf.lastSignal[i] === 1 ? SMF.bullColor : SMF.bearColor,
            borderColor: smf.lastSignal[i] === 1 ? SMF.bullColor : SMF.bearColor,
            wickColor: smf.lastSignal[i] === 1 ? SMF.bullColor : SMF.bearColor,
        }));
        station.candleSeries.setData(colored);

        let deResult = null;
        if (station.dynamicExitEnabled) {
            deResult = computeSMFDynamicExit(agg, smf, 'V5');
            station.dynamicExitResult = deResult;
            if (station.type === 'CE') C.dynamicExitLastResult = deResult;

            if (deResult) {
                if (station.dynamicTrailSeries && deResult.trailPoints) {
                    station.dynamicTrailSeries.setData(deResult.trailPoints);
                }
                if (station.dynamicLadderSeries && deResult.ladderPoints) {
                    station.dynamicLadderSeries.setData(deResult.ladderPoints);
                }
            }
        }

        const markers = [];
        if (deResult && deResult.markers) {
            markers.push(...deResult.markers);
        } else if (smf && smf.smfMarkers) {
            markers.push(...smf.smfMarkers);
        }
        station.candleSeries.setMarkers(markers);

        updateStationTopBar(station, agg);
        updateStationFooter(station);
        renderStationSMFCanvas(station);

        if (station.chart && colored.length > 0) {
            const total = colored.length;
            const visibleBars = Math.min(total, 65);
            station.chart.timeScale().setVisibleLogicalRange({
                from: Math.max(0, total - visibleBars),
                to: total + 15
            });
        }

        const buyTxt = document.getElementById(station.type === 'CE' ? 'ce-buy-txt' : 'pe-buy-txt');
        if (buyTxt) buyTxt.textContent = station.type;

    } catch (e) {
        console.warn(`Failed to load ${station.type} candles:`, e);
    }
}

function updateStationTopBar(station, agg) {
    if (!agg || agg.length === 0) return;
    const lastC = agg[agg.length - 1];
    const firstC = agg[0];
    const prefix = station.type.toLowerCase();

    const ltpEl = document.getElementById(`${prefix}-ltp`);
    const chgEl = document.getElementById(`${prefix}-chg`);
    const trendIconEl = document.getElementById(`${prefix}-trend-icon`);

    if (ltpEl) ltpEl.textContent = `₹${lastC.close.toFixed(2)}`;
    if (chgEl && firstC) {
        const diff = lastC.close - firstC.open;
        const pct = (diff / firstC.open) * 100;
        const sign = diff >= 0 ? '+' : '';
        chgEl.textContent = `${sign}${diff.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
        chgEl.className = `station-chg ${diff >= 0 ? 'pos' : 'neg'}`;
    }
    if (trendIconEl && station.smfResult) {
        const n = station.smfResult.n || (station.smfResult.lastSignal ? station.smfResult.lastSignal.length : 0);
        const lastSig = n > 0 ? station.smfResult.lastSignal[n - 1] : 0;
        const isBull = lastSig === 1;
        trendIconEl.textContent = isBull ? '▲' : '▼';
        trendIconEl.className = `trend-arrow ${isBull ? 'up' : 'down'}`;
        trendIconEl.title = isBull ? 'Bullish (Buy)' : 'Bearish';
    }
}

function updateStationFooter(station) {
    const prefix = station.type.toLowerCase();
    const de = station.dynamicExitResult;
    const trades = de?.trades || [];
    const summary = de?.summary || {};

    const statTrades = document.getElementById(`${prefix}-stat-trades`);
    const statWinrate = document.getElementById(`${prefix}-stat-winrate`);
    const statPnl = document.getElementById(`${prefix}-stat-pnl`);
    const statPts = document.getElementById(`${prefix}-stat-pts`);
    const statCash = document.getElementById(`${prefix}-stat-cash`);
    const logCount = document.getElementById(`${prefix}-log-count`);
    const logWrap = document.getElementById(`${prefix}-log-table-wrap`);

    const totalTrades = trades.length;
    const winTrades = trades.filter(t => (t.pnlPts || 0) >= 0).length;
    const winRate = totalTrades > 0 ? (winTrades / totalTrades) * 100 : 0;
    const pts = trades.reduce((acc, t) => acc + (t.pnlPts || 0), 0);
    const gross = pts * (station.lotUnits || 65);
    const charges = totalTrades * 55;
    const netCash = gross - charges;
    const pnlR = summary.netPnlR || 0;

    const signPts = pts >= 0 ? '+' : '';
    const signCash = netCash >= 0 ? '+' : '-';
    const signR = pnlR >= 0 ? '+' : '';

    // Single-Line Footer Metrics (Trades | Win Rate without labels, Net Cash | Points gained)
    const summaryRateEl = document.getElementById(`${prefix}-stat-summary-rate`);
    const summaryCashEl = document.getElementById(`${prefix}-stat-summary-cash`);
    if (summaryRateEl) {
        summaryRateEl.textContent = `${totalTrades} | ${totalTrades > 0 ? `${winRate.toFixed(0)}%` : '0%'}`;
    }
    if (summaryCashEl) {
        const cashFormatted = `${signCash}₹${Math.abs(netCash).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        const ptsFormatted = `${signPts}${pts.toFixed(1)} pt`;
        summaryCashEl.textContent = `${cashFormatted} | ${ptsFormatted}`;
        summaryCashEl.className = `footer-stat-pnl highlight ${netCash >= 0 ? 'pos' : 'neg'}`;
    }

    if (statTrades) statTrades.textContent = totalTrades;
    if (statWinrate) statWinrate.textContent = totalTrades > 0 ? `${winRate.toFixed(0)}%` : '--%';
    if (statPnl) {
        statPnl.textContent = `${signR}${pnlR.toFixed(2)}R`;
        statPnl.className = `m-val highlight ${pnlR >= 0 ? 'pos' : 'neg'}`;
    }
    if (statPts) {
        statPts.textContent = `${signPts}${pts.toFixed(1)} pts`;
        statPts.className = `m-val highlight ${pts >= 0 ? 'pos' : 'neg'}`;
    }
    if (statCash) {
        statCash.textContent = `${signCash}₹${Math.round(netCash).toLocaleString('en-IN')}`;
        statCash.className = `m-val highlight ${netCash >= 0 ? 'pos' : 'neg'}`;
    }
    if (logCount) logCount.textContent = totalTrades;

    if (logWrap) {
        if (totalTrades === 0) {
            logWrap.innerHTML = `<div class="no-trades-msg">No trades on this session yet.</div>`;
        } else {
            logWrap.innerHTML = `
                <table class="bt-table mini-table">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Entry</th>
                            <th>Entry Px</th>
                            <th>Exit</th>
                            <th>Exit Px</th>
                            <th>Points</th>
                            <th>Net Cash</th>
                            <th>Reason</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${trades.map(t => {
                            const tSignPts = (t.pnlPts || 0) >= 0 ? '+' : '';
                            const tGross = (t.pnlPts || 0) * (station.lotUnits || 65);
                            const tNet = tGross - 55;
                            const tSignNet = tNet >= 0 ? '+' : '';
                            const isWin = (t.pnlPts || 0) >= 0;
                            return `
                                <tr>
                                    <td>#${t.tradeNum}</td>
                                    <td>${t.entryTime || '--'}</td>
                                    <td>₹${(t.entryPrice || 0).toFixed(2)}</td>
                                    <td>${t.exitTime || '--'}</td>
                                    <td>₹${(t.exitPrice || 0).toFixed(2)}</td>
                                    <td style="color:${isWin ? '#00f2a9' : '#ff005d'};font-weight:700;">${tSignPts}${(t.pnlPts || 0).toFixed(1)}</td>
                                    <td style="color:${isWin ? '#00f2a9' : '#ff005d'};font-weight:800;">${tSignNet}₹${Math.round(tNet).toLocaleString('en-IN')}</td>
                                    <td style="font-size:10px;color:#94a3b8;">${t.exitReason || 'EXIT'}</td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            `;
        }
    }
}

function updatePortfolioSummary() {
    const ceDe = Stations.CE.dynamicExitResult;
    const peDe = Stations.PE.dynamicExitResult;

    const ceTrades = ceDe?.trades || [];
    const peTrades = peDe?.trades || [];

    const cePts = ceTrades.reduce((acc, t) => acc + (t.pnlPts || 0), 0);
    const pePts = peTrades.reduce((acc, t) => acc + (t.pnlPts || 0), 0);

    const ceNetCash = cePts * (Stations.CE.lotUnits || 65) - ceTrades.length * 55;
    const peNetCash = pePts * (Stations.PE.lotUnits || 65) - peTrades.length * 55;

    let liveCePnl = 0;
    if (Stations.CE.activePosition && Stations.CE.rawCandles.length > 0) {
        const curPx = Stations.CE.rawCandles[Stations.CE.rawCandles.length - 1].close;
        liveCePnl = (curPx - Stations.CE.activePosition.entryPrice) * (Stations.CE.lotUnits || 65);
    }
    let livePePnl = 0;
    if (Stations.PE.activePosition && Stations.PE.rawCandles.length > 0) {
        const curPx = Stations.PE.rawCandles[Stations.PE.rawCandles.length - 1].close;
        livePePnl = (curPx - Stations.PE.activePosition.entryPrice) * (Stations.PE.lotUnits || 65);
    }

    const totalCombinedCash = ceNetCash + peNetCash + liveCePnl + livePePnl;
    MasterState.combinedPortfolioPnL = totalCombinedCash;

    const pnlPill = document.getElementById('portfolio-pnl-pill');
    const pnlVal = document.getElementById('val-portfolio-pnl');
    const capVal = document.getElementById('val-portfolio-cap');

    if (pnlVal) {
        const sign = totalCombinedCash >= 0 ? '+' : '';
        pnlVal.textContent = `${sign}₹${Math.round(totalCombinedCash).toLocaleString('en-IN')}`;
    }
    if (pnlPill) {
        pnlPill.className = `portfolio-pnl-pill ${totalCombinedCash > 0 ? 'pnl-positive' : (totalCombinedCash < 0 ? 'pnl-negative' : 'pnl-neutral')}`;
    }
    if (capVal && MasterState.tradingState) {
        capVal.textContent = `${Math.round(MasterState.tradingState.capital || 100000) / 1000}k`;
    }
}

function renderStationSMFCanvas(station) {
    if (!station || !station.canvas || !station.chart || !station.candleSeries) return;
    const canvas = station.canvas;
    const ctx = canvas.getContext('2d');
    if (!station.smfResult || !station.smfEnabled) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        return;
    }

    const smf = station.smfResult;
    const { n, upper, lower, bMain, lastSignal, candles } = smf;
    if (!n || n < 2) return;

    const W = station.container ? station.container.clientWidth : canvas.width;
    const H = station.container ? station.container.clientHeight : canvas.height;

    if (canvas.width !== W || canvas.height !== H) {
        canvas.width = W;
        canvas.height = H;
    }

    ctx.clearRect(0, 0, W, H);

    const points = [];
    for (let i = 0; i < n; i++) {
        const t = candles[i].time;
        const coord = station.chart.timeScale().timeToCoordinate(t);
        if (coord == null || coord < -80 || coord > W + 80) continue;
        const yU = station.candleSeries.priceToCoordinate(upper[i]);
        const yL = station.candleSeries.priceToCoordinate(lower[i]);
        const yB = station.candleSeries.priceToCoordinate(bMain[i]);
        if (yU == null || yL == null || yB == null) continue;
        points.push({ x: coord, yUpper: yU, yLower: yL, yBc: yB, regime: lastSignal[i] });
    }
    if (points.length < 2) return;

    const segs = [];
    let seg = [points[0]];
    for (let i = 1; i < points.length; i++) {
        if (points[i].regime !== points[i - 1].regime) {
            segs.push({ regime: points[i - 1].regime, pts: seg });
            seg = [points[i - 1], points[i]];
        } else {
            seg.push(points[i]);
        }
    }
    segs.push({ regime: points[points.length - 1].regime, pts: seg });

    for (const { regime, pts } of segs) {
        if (pts.length < 2) continue;
        const isBull = regime === 1;

        ctx.save();
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].yUpper);
        for (const p of pts) ctx.lineTo(p.x, p.yUpper);
        for (let j = pts.length - 1; j >= 0; j--) ctx.lineTo(pts[j].x, pts[j].yLower);
        ctx.closePath();

        const grad = ctx.createLinearGradient(0, 0, 0, H);
        if (isBull) {
            grad.addColorStop(0, 'rgba(0, 200, 255, 0.22)');
            grad.addColorStop(1, 'rgba(0, 200, 255, 0.04)');
        } else {
            grad.addColorStop(0, 'rgba(255, 0, 93, 0.04)');
            grad.addColorStop(1, 'rgba(255, 0, 93, 0.22)');
        }
        ctx.fillStyle = grad;
        ctx.fill();
        ctx.restore();
    }

    ctx.save();
    ctx.lineWidth = 1.5;
    for (const { regime, pts } of segs) {
        ctx.strokeStyle = regime === 1 ? 'rgba(0, 200, 255, 0.55)' : 'rgba(255, 0, 93, 0.55)';
        ctx.beginPath();
        for (let i = 0; i < pts.length; i++) {
            if (i === 0) ctx.moveTo(pts[i].x, pts[i].yBc);
            else ctx.lineTo(pts[i].x, pts[i].yBc);
        }
        ctx.stroke();
    }
    ctx.restore();

    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.25)';
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

function connectStationWS(station) {
    if (!station || !station.symbol) return;
    disconnectStationWS(station);

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}/ws/contracts?symbol=${encodeURIComponent(station.symbol)}`;

    station.ws = new WebSocket(url);
    station.wsPingInterval = setInterval(() => {
        if (station.ws && station.ws.readyState === WebSocket.OPEN) {
            station.ws.send(JSON.stringify({ type: 'PING' }));
        }
    }, 5000);

    station.ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'CONTRACT_TICK' && data.ltp != null) {
                const pfx = station.type.toLowerCase();
                const ltpEl = document.getElementById(`${pfx}-ltp`);
                if (ltpEl) ltpEl.textContent = `₹${data.ltp.toFixed(2)}`;

                const buyTxt = document.getElementById(`${pfx}-buy-txt`);
                if (buyTxt) buyTxt.textContent = `${station.type} · ₹${data.ltp.toFixed(1)}`;

                if (station.candleSeries && data.forming_candle) {
                    const fc = data.forming_candle;
                    const oneMinCandle = {
                        time: Number(fc.time),
                        open: Number(fc.open),
                        high: Number(fc.high),
                        low: Number(fc.low),
                        close: Number(fc.close),
                    };
                    const existingIdx = station.rawCandles.findIndex(c => c.time === oneMinCandle.time);
                    if (existingIdx >= 0) {
                        station.rawCandles[existingIdx] = oneMinCandle;
                    } else {
                        station.rawCandles.push(oneMinCandle);
                    }
                    station.candleSeries.update(oneMinCandle);
                }
            }
        } catch (e) {
            console.warn(`${station.type} WS error:`, e);
        }
    };
}

function disconnectStationWS(station) {
    if (station.wsPingInterval) clearInterval(station.wsPingInterval);
    if (station.ws) {
        try { station.ws.close(); } catch (e) {}
        station.ws = null;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Backward Compatibility Handlers & Window Exports
// ─────────────────────────────────────────────────────────────────────────────
window.Stations = Stations;
window.MasterState = MasterState;
window.loadStrikePair = loadStrikePair;
window.stepStrike = stepStrike;
