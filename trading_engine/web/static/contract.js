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
    dynamicExitLastResult: null,
    _canvasRenderPending: false,
    _executedTargets: new Set(),
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
    initChart();
    initSearch();
    await initExpirySelector();
    initOptionChain();
    initKeyboardShortcuts();
    initTfControls();
    initSMFToggle();
    initSMFPriceToggle();
    initTargetToggle();
    initFitButton();
    initReplayController();
    initDynamicExitController();
    checkMarketStatus();
    setInterval(checkMarketStatus, 30_000);

    // Check URL parameters for symbol, or load ATM contract from option chain
    const urlParams = new URLSearchParams(window.location.search);
    const paramSym = urlParams.get('symbol');
    if (paramSym) {
        selectContract({ symbol: paramSym, display_name: paramSym });
    } else {
        // Fetch option chain to auto-chart ATM strike
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
        const w = chartArea.clientWidth || container.clientWidth;
        const h = chartArea.clientHeight || container.clientHeight;
        if (C.chart && w > 0 && h > 0) {
            C.chart.applyOptions({ width: w, height: h });
        }
        canvas.width = w;
        canvas.height = h;
        if (C.rawCandles.length >= 5) {
            requestAnimationFrame(renderSMFCanvas);
        }
    };
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
// ── EXPIRY DATES & OPTION CHAIN CONTROLLER ───────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

async function initExpirySelector() {
    const headerSel = document.getElementById('expiry-select');
    const ocSel = document.getElementById('oc-expiry-select');
    if (!headerSel || !ocSel) return;

    try {
        const res = await fetch(`/api/contracts/expiries?underlying=${encodeURIComponent(C.underlying)}`);
        const data = await res.json();
        const expiries = data.expiries || [];

        headerSel.innerHTML = '<option value="">All Expiries</option>';
        ocSel.innerHTML = '';

        expiries.forEach((exp) => {
            const opt1 = document.createElement('option');
            opt1.value = exp.date;
            opt1.textContent = exp.display;
            headerSel.appendChild(opt1);

            const opt2 = document.createElement('option');
            opt2.value = exp.date;
            opt2.textContent = exp.display;
            ocSel.appendChild(opt2);
        });

        // Set default to first active upcoming expiry (e.g. 27 Oct 2026)
        if (expiries.length > 0) {
            const activeExp = expiries.find(e => e.days_to_expiry >= 0) || expiries[0];
            C.selectedExpiry = activeExp.date;
            headerSel.value = C.selectedExpiry;
            ocSel.value = C.selectedExpiry;
            updateExpiryBadge(activeExp.date);
        }

        // Listener for header select
        headerSel.addEventListener('change', () => {
            C.selectedExpiry = headerSel.value;
            ocSel.value = C.selectedExpiry;
            updateExpiryBadge(C.selectedExpiry);
            const input = document.getElementById('search-input');
            if (input && input.value.trim()) {
                runSearch(input.value);
            }
            if (C.optionChainOpen) {
                fetchOptionChain();
            }
        });

        // Listener for option chain modal select
        ocSel.addEventListener('change', () => {
            C.selectedExpiry = ocSel.value;
            headerSel.value = C.selectedExpiry;
            updateExpiryBadge(C.selectedExpiry);
            fetchOptionChain();
        });

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

function initOptionChain() {
    const toggleBtn = document.getElementById('btn-toggle-oc');
    const closeBtn = document.getElementById('oc-close-btn');
    const rangeBtn = document.getElementById('oc-range-toggle');
    const refreshBtn = document.getElementById('oc-refresh-btn');
    const panel = document.getElementById('option-chain-panel');

    if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
            C.optionChainOpen = !C.optionChainOpen;
            toggleBtn.classList.toggle('active', C.optionChainOpen);
            panel.classList.toggle('collapsed', !C.optionChainOpen);
            if (C.optionChainOpen) {
                fetchOptionChain();
                if (window.innerWidth <= 900) {
                    setTimeout(() => {
                        panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    }, 50);
                }
            }
        });
    }

    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            C.optionChainOpen = false;
            if (toggleBtn) toggleBtn.classList.remove('active');
            panel.classList.add('collapsed');
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

        renderOptionChainTable(data);

        // If no contract is charted yet, automatically chart the ATM Call contract
        if (!C.symbol && data.chain && data.chain.length > 0) {
            const atmRow = data.chain.find(i => i.is_atm) || data.chain[Math.floor(data.chain.length / 2)];
            if (atmRow && atmRow.call && atmRow.call.symbol) {
                selectContract({
                    symbol: atmRow.call.symbol,
                    display_name: atmRow.call.display_name || `${intOrRound(atmRow.strike)} CE`,
                    expiry: data.expiry
                });
            }
        }

        if (summary) {
            const count = data.chain ? data.chain.length : 0;
            summary.textContent = `${count} strikes around ATM (${data.atm_strike})`;
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

    // Update UI
    document.getElementById('search-input').value = C.displayName;
    document.getElementById('badge-symbol').textContent = C.displayName;
    document.getElementById('badge-ltp').textContent = '——';
    document.getElementById('badge-chg').textContent = '';
    document.getElementById('stat-symbol').textContent = symbol;

    if (contract.expiry) {
        C.selectedExpiry = contract.expiry;
        const hSel = document.getElementById('expiry-select');
        const ocSel = document.getElementById('oc-expiry-select');
        if (hSel) hSel.value = contract.expiry;
        if (ocSel) ocSel.value = contract.expiry;
        updateExpiryBadge(contract.expiry);
    }
    if (C.optionChainData) {
        renderOptionChainTable(C.optionChainData);
    }

    // Auto-close Option Chain drawer on contract select so user gets full chart view
    const ocPanel = document.getElementById('option-chain-panel');
    const toggleBtn = document.getElementById('btn-toggle-oc');
    if (ocPanel && C.optionChainOpen) {
        C.optionChainOpen = false;
        if (toggleBtn) toggleBtn.classList.remove('active');
        ocPanel.classList.add('collapsed');
    }

    hideOverlay();
    showLoading(true);

    // Disconnect old WS
    disconnectContractWS();

    // Reset chart
    clearChart();

    // Fetch today's candles (catch-up)
    loadContractCandles(symbol).then(() => {
        showLoading(false);
        // Then connect WebSocket for live stream
        connectContractWS(symbol);
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// ── CANDLE LOADING ────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

async function loadContractCandles(symbol) {
    try {
        const res = await fetch(`/api/contracts/candles?symbol=${encodeURIComponent(symbol)}`);
        const data = await res.json();

        const candles = data.candles || [];
        if (!candles.length) {
            if (data.market_status === 'PRE_MARKET') {
                showOverlay('⏰', 'Market Opens at 09:15 AM IST', 'Chart will auto-populate once the market opens. Live streaming will begin automatically.');
            } else {
                showOverlay('📉', 'No Data Available', `No trading candles found for ${symbol}.`);
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
        if (data.is_last_day_data && data.date) {
            const statusLabel = document.getElementById('market-status-label');
            if (statusLabel) {
                statusLabel.textContent = `Market Closed (${data.date})`;
            }
        }

        updateSessionStats(formatted);
        applyFullChart(formatted);

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
    document.getElementById('stat-open').textContent = fmt(open);
    document.getElementById('stat-high').textContent = fmt(high);
    document.getElementById('stat-low').textContent  = fmt(low);
    document.getElementById('stat-ltp').textContent  = fmt(ltp);
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

    // Update badge
    document.getElementById('badge-ltp').textContent = `₹${ltp.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
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
            const col = C.smfLastResult.latestRegime === 1 ? SMF.bullColor : SMF.bearColor;
            candleToPush.color = col;
            candleToPush.borderColor = col;
            candleToPush.wickColor = col;
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

    // 2. Scalp Target 1 (50% Qty Lock): 1.0x Risk or +8.0 pts (~65% hit rate)
    const scalpPts = Math.max(6.0, Math.min(12.0, risk * 1.0));
    const target1 = Math.round((suggestedLimit + scalpPts) * 20) / 20;

    // 3. Break-Even Lock Trigger Level (+6.0 pts)
    const beLockTrigger = Math.round((suggestedLimit + 6.0) * 20) / 20;

    // 4. Runner Target 2 (50% Qty Expansion): 2.5x Risk or +20.0 pts
    const target2 = Math.round((suggestedLimit + Math.max(18.0, risk * 2.5)) * 20) / 20;
    const maxTarget = Math.round((suggestedLimit + Math.max(28.0, risk * 3.5)) * 20) / 20;

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

        const isBelow = c.close < bMain[i] - buf;
        const isAbove = c.close > bMain[i] + buf;

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

        // Multi-Bar Confirmed Trend Reversal:
        // Requires consecutive closes beyond the buffer to confirm a flip.
        let currLS = prevRegime;
        if (prevRegime === 1 && belowCount >= reqBars) {
            currLS = -1;
            belowCount = 0; // reset streak after confirmed flip
        } else if (prevRegime === -1 && aboveCount >= reqBars) {
            currLS = 1;
            aboveCount = 0; // reset streak after confirmed flip
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

        // Confirmed trend switch: fires ONLY on true confirmed regime transition
        const isSwitchUp = (currLS === 1 && prevSignal === -1);
        const isSwitchDown = (currLS === -1 && prevSignal === 1);
        prevRegime = currLS;

        if (SMF.showSwitch) {
            const ext = Number((Math.abs(c.close - bMain[i]) / (atr[i] || 1.0)).toFixed(2));
            const priceStr = c.close.toFixed(2);
            if (isSwitchUp) {
                // Strict 5 trades per day cap
                if (dailyTradesCount < 5) {
                    dailyTradesCount++;
                    inLongPosition = true;
                    target1Achieved = false;
                    continuationCount = 0;
                    lastBuyBar = i;

                    const tradeMetrics = calculateSMFLimitAndTarget(candles, i, atr, bMain, vsma, rsi);
                    activeTradeSignal = tradeMetrics;
                    const limStr = tradeMetrics.limitPrice.toFixed(2);
                    const label = '▲ BUY';

                    smfMarkers.push({
                        time: c.time,
                        position: 'belowBar',
                        color: SMF.buyColor, // Opaque vibrant green
                        shape: 'arrowUp',    // Clean directional arrow
                        text: label,
                        signalType: 'BUY',
                        limitPrice: tradeMetrics.limitPrice,
                        maxLimitCeiling: tradeMetrics.maxLimitCeiling,
                        targetPrice: tradeMetrics.target1,
                        target2: tradeMetrics.target2,
                        maxTarget: tradeMetrics.maxTarget,
                        beLockTrigger: tradeMetrics.beLockTrigger,
                        stopLoss: tradeMetrics.stopLoss,
                        isMiddayChop: tradeMetrics.isMiddayChop,
                        sessionLabel: tradeMetrics.sessionLabel,
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

    // Color candles
    const coloredCandles = agg.map((c, i) => {
        let col = smf.lastSignal[i] === 1 ? SMF.bullColor : SMF.bearColor;
        if (!C.smfEnabled) {
            col = c.close >= c.open ? '#00f2a9' : '#ff4d6d';
        }
        return {
            ...c,
            color: col,
            borderColor: col,
            wickColor: col,
        };
    });
    C.candleSeries.setData(coloredCandles);

    if (C.smfEnabled) {
        C.smfBcSeries.setData(smf.bcPoints);
        C.smfUpperSeries.setData(smf.upPoints);
        C.smfLowerSeries.setData(smf.loPoints);
        C.smfBoSeries.setData(smf.boPoints);
    } else {
        if (C.smfBcSeries) C.smfBcSeries.setData([]);
        if (C.smfUpperSeries) C.smfUpperSeries.setData([]);
        if (C.smfLowerSeries) C.smfLowerSeries.setData([]);
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

    // Combine markers from SMF Cloud based on active toggles
    const allMarkers = [];
    if (C.smfEnabled && smf.smfMarkers && smf.smfMarkers.length > 0) {
        allMarkers.push(...smf.smfMarkers);
    }

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
            if (deResult.markers && deResult.markers.length > 0) {
                allMarkers.push(...deResult.markers);
            }
        }
    } else {
        if (C.dynamicTrailSeries) C.dynamicTrailSeries.setData([]);
        if (C.dynamicLadderSeries) C.dynamicLadderSeries.setData([]);
    }
    updateDynamicExitHUD(deResult, agg);

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
        if (t.mfeR >= 3.5) return roundTick(t.entryPrice + 0.50 * (t.hh - t.entryPrice));
        if (t.mfeR >= 2.5) return roundTick(t.entryPrice + 0.50 * t.R);
        if (t.mfeR >= 1.8) return roundTick(t.entryPrice + COSTS);
        if (t.mfeR >= 1.0) return roundTick(t.entryPrice - 0.50 * t.R);
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
                    exitReason = 'EOD_EXIT';
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
                    if (c.close < hardStop) {
                        exitReason = (hardStop > t.initialStop) ? 'LADDER_FLOOR' : 'INITIAL_STOP';
                        exitPrice = c.close;
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
                    if (t.mfeR >= 2.5) t.phase = 'RUNNER';
                    else if (t.mfeR >= 1.0) t.phase = 'SECURED';
                    else t.phase = 'PROTECT';

                    // Update Ladder Floor
                    t.ladderFloor = Math.max(t.ladderFloor, calcRelaxedLadder(t));

                    // Partial Booking P1 (+1.5R)
                    if (enableP1 && !t.p1Booked && t.mfeR >= 1.5) {
                        t.p1Booked = true;
                        const partialP = roundTick(t.entryPrice + 1.5 * t.R);
                        t.realizedPts += 0.25 * (partialP - t.entryPrice);
                        t.remQty = 0.75;
                        markers.push({
                            time: c.time,
                            position: 'aboveBar',
                            color: '#fbbf24',
                            shape: 'circle',
                            text: `P1 BOOKED (+1.5R)`,
                        });
                    }

                    // Structural exits
                    if (isSwitchDown) {
                        exitReason = 'REGIME_FLIP';
                        exitPrice = c.close;
                    } else if (cTime >= '15:15') {
                        exitReason = 'EOD_EXIT';
                        exitPrice = c.close;
                    } else if (variantUpper !== 'V1') {
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
                            // Chandelier Trail
                            let k = (variantUpper === 'V2') ? 2.2 : (K_BY_STATE[t.state] || 2.2);
                            if (['V4', 'V5', 'V5_P1'].includes(variantUpper)) {
                                const span = c.high - c.low;
                                const isClimax = (rvol[i] >= 2.5) && (span >= 2.0 * atr[i]) && (sellerPressure[i] >= 0.50) && (ext[i] >= 2.5);
                                if (isClimax) t.climaxUntilBar = i + 3;
                                if (i <= t.climaxUntilBar) k = Math.min(k, 0.8);
                                if (ext[i] > 3.5 || cTime >= '14:30') k = Math.min(k, 1.4);
                            }

                            const rawTrail = roundTick(t.hh - k * atr[i]);
                            t.trail = Math.max(t.trail, rawTrail);

                            // Trail break (evaluated when MFE >= +1.0R and close < trail)
                            if (t.mfeR >= 1.0 && c.close < t.trail) {
                                exitReason = (i <= t.climaxUntilBar) ? 'CLIMAX_TIGHTEN' : 'TRAIL_BREAK';
                                exitPrice = c.close;
                            } else if (['V4', 'V5', 'V5_P1'].includes(variantUpper)) {
                                // Time Stops
                                if ((i - t.entryBar >= 8) && t.mfeR < 0.5 && c.close < t.entryPrice) {
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

            activeTrade = null;
        }

        // 2. Check Re-Entry if no active trade (V5, V5_P1)
        if (!activeTrade && ['V5', 'V5_P1'].includes(variantUpper) && reentryWatcher && !dayHalted) {
            const w = reentryWatcher;
            if (lastSignal[i] === w.regime && w.reentryCount < 2 && i > w.pausedUntilBar && dailyTradeCount < 3) {
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
                    const rawStop = Math.max(bMain[i] - 0.35 * atr[i], entryP - 1.5 * atr[i]);
                    const rawR = entryP - rawStop;
                    const clampedR = Math.min(Math.max(rawR, 1.5), 0.12 * entryP);
                    const initStop = roundTick(entryP - clampedR);
                    const R = roundTick(entryP - initStop);

                    activeTrade = {
                        entryPrice: entryP,
                        entryBar: i,
                        entryTime: cTime,
                        initialStop: initStop,
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
                        p1Booked: false,
                        realizedPts: 0.0,
                        isReentry: true,
                        reentryType: reType,
                        legReentries: w.reentryCount + 1,
                    };
                    dailyTradeCount++;
                    reentryWatcher = null;

                    const reLabel = reType === 'PULLBACK' ? `🔄 RE-PB ₹${entryP.toFixed(1)}` : `🔄 RE-BO ₹${entryP.toFixed(1)}`;
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

        // 3. Check New Entry on SMF Switch Up
        if (!activeTrade && isSwitchUp && !dayHalted && dailyTradeCount < 3) {
            const inTimeWindow = (cTime >= '09:30' && cTime <= '14:30');
            const needsIgnition = ['V4', 'V5', 'V5_P1'].includes(variantUpper);
            if (inTimeWindow && (!needsIgnition || isGammaIgnition(i))) {
                const entryP = roundTick(c.close + slip(c.close));
                const rawStop = Math.max(bMain[i] - 0.35 * atr[i], entryP - 1.5 * atr[i]);
                const rawR = entryP - rawStop;
                const clampedR = Math.min(Math.max(rawR, 1.5), 0.12 * entryP);
                const initStop = roundTick(entryP - clampedR);
                const R = roundTick(entryP - initStop);

                activeTrade = {
                    entryPrice: entryP,
                    entryBar: i,
                    entryTime: cTime,
                    initialStop: initStop,
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
                    p1Booked: false,
                    realizedPts: 0.0,
                    isReentry: false,
                    reentryType: 'NONE',
                    legReentries: 0,
                };
                dailyTradeCount++;
                reentryWatcher = null;

                markers.push({
                    time: c.time,
                    position: 'belowBar',
                    color: '#00f2a9',
                    shape: 'arrowUp',
                    text: `▲ BUY ₹${entryP.toFixed(1)}`,
                    signalType: 'DE_BUY',
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
        const levels = [
            { key: 't2', price: sig.target2, label: `🚀 T2 ₹${sig.target2?.toFixed(1)}`, color: '#ffbe0b', lightColor: 'rgba(255, 190, 11, 0.40)', dash: [] },
            { key: 't1', price: sig.target1, label: `🎯 T1 ₹${sig.target1?.toFixed(1)}`, color: '#ffd166', lightColor: 'rgba(255, 209, 102, 0.40)', dash: [5, 3] },
            { key: 'lt', price: sig.limitPrice, label: (isMkt ? `⚡ MKT ₹${sig.limitPrice?.toFixed(1)}` : `📥 LT ₹${sig.limitPrice?.toFixed(1)}`), color: (isMkt ? '#00f2a9' : '#00e5ff'), lightColor: (isMkt ? 'rgba(0, 242, 169, 0.40)' : 'rgba(0, 229, 255, 0.40)'), dash: [] },
            { key: 'sl', price: sig.sl, label: `🛑 SL ₹${sig.sl?.toFixed(1)}`, color: '#ff4d6d', lightColor: 'rgba(255, 77, 109, 0.40)', dash: [5, 3] },
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

            // A. Past timeframe (Request 2): Clean visible light line from signal candle up to current candle
            if (curCandleX > sigX) {
                ctx.beginPath();
                ctx.strokeStyle = lvl.lightColor;
                ctx.lineWidth = 1;
                ctx.setLineDash([4, 4]);
                ctx.moveTo(sigX, y);
                ctx.lineTo(curCandleX, y);
                ctx.stroke();
            }

            // B. Active timeframe (Request 2): Darker vibrant solid line from the current candle onwards into the blank area
            ctx.beginPath();
            ctx.strokeStyle = lvl.color;
            ctx.lineWidth = 1.5;
            ctx.setLineDash(lvl.dash);
            ctx.moveTo(curCandleX, y);
            ctx.lineTo(chartPlotRight, y);
            ctx.stroke();

            // C. Dedicated Right Column Badge in preserved margin before price scale (Request 3)
            ctx.setLineDash([]);
            ctx.font = 'bold 11px Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            const textW = Math.ceil(ctx.measureText(lvl.label).width);
            const badgeW = textW + 16;
            const badgeH = 20;
            // Preserved area before Y-axis (leaves 6px margin before chartPlotRight, zero overlap on price scale numbers!)
            const badgeX = Math.round(chartPlotRight - badgeW - 6);
            const badgeY = Math.round(y - badgeH / 2);

            // Badge Background (Request 4e)
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(badgeX, badgeY, badgeW, badgeH, 4);
            } else {
                ctx.rect(badgeX, badgeY, badgeW, badgeH);
            }
            ctx.fillStyle = 'rgba(8, 14, 26, 0.94)';
            ctx.fill();
            ctx.strokeStyle = lvl.color;
            ctx.lineWidth = 1.2;
            ctx.stroke();

            // Badge Text
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            ctx.fillStyle = lvl.color;
            ctx.fillText(lvl.label, badgeX + 8, badgeY + badgeH / 2);
        }
        ctx.restore();
    }

    // ── 2. Visual Projections & Bottom Summary Pill (⚡ MKT, 🎯 T1, 🚀 T2) (Request 4) ───────────
    if (C.targetsEnabled && C.activeTargetSignal) {
        const sig = C.activeTargetSignal;
        const isMkt = sig.recommendedOrder === 'MARKET';
        const limText = isMkt ? `⚡ MKT ₹${sig.limitPrice.toFixed(1)}` : `📥 LT ₹${sig.limitPrice.toFixed(1)}`;
        const tgtText = `🎯 T1 ₹${sig.target1.toFixed(1)}`;
        const t2Text = sig.target2 ? `🚀 T2 ₹${sig.target2.toFixed(1)}` : '';
        const chopText = sig.isMiddayChop ? `⚠️ CHOP` : '';

        ctx.save();
        ctx.font = 'bold 11px Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        const limW = Math.ceil(ctx.measureText(limText).width);
        const tgtW = Math.ceil(ctx.measureText(tgtText).width);
        const t2W = t2Text ? Math.ceil(ctx.measureText(t2Text).width) + 10 : 0;
        const chopW = chopText ? Math.ceil(ctx.measureText(chopText).width) + 10 : 0;
        const pillW = limW + tgtW + t2W + chopW + 24;
        const pillH = 22;

        // Position at the bottom of the chart above time scale
        const pillY = Math.round(H - pillH - 6);
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

        let curCursorX = pillX + 7;
        ctx.fillStyle = isMkt ? '#00f2a9' : '#00e5ff';
        ctx.fillText(limText, curCursorX, midY);
        curCursorX += limW + 5;

        ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';
        ctx.fillRect(Math.round(curCursorX), pillY + 4, 1, pillH - 8);
        curCursorX += 6;

        ctx.fillStyle = '#ffd166';
        ctx.fillText(tgtText, curCursorX, midY);
        curCursorX += tgtW + 5;

        if (t2Text) {
            ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';
            ctx.fillRect(Math.round(curCursorX), pillY + 4, 1, pillH - 8);
            curCursorX += 6;
            ctx.fillStyle = '#ffbe0b';
            ctx.fillText(t2Text, curCursorX, midY);
            curCursorX += t2W - 5;
        }

        if (chopText) {
            ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';
            ctx.fillRect(Math.round(curCursorX), pillY + 4, 1, pillH - 8);
            curCursorX += 6;
            ctx.fillStyle = '#ff6b6b';
            ctx.fillText(chopText, curCursorX, midY);
        }

        ctx.restore();
    }

    // ── 3. Indicator Value Badges with Background Colors & Smart Spacing (Requests 3 & 4)
    if (C.allRawSignalMarkers && C.allRawSignalMarkers.length > 0) {
        ctx.save();
        ctx.font = 'bold 11px Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

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
            const badgeW = textW + 14;
            const badgeH = 20;
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
};

function initReplayController() {
    const toggleBtn = document.getElementById('btn-replay-toggle');
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
                    C.rawCandles = formatCandles(candles);
                    Replay.fullCandles = [...C.rawCandles];
                    jumpToStart();
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
    if (!C.rawCandles || C.rawCandles.length < 5) {
        alert("Please select or chart an option contract first before starting Bar Replay.");
        return;
    }

    Replay.active = true;
    C.replayMode = true;
    Replay.fullCandles = [...C.rawCandles];

    const toggleBtn = document.getElementById('btn-replay-toggle');
    const dock = document.getElementById('replay-bar');
    if (toggleBtn) toggleBtn.classList.add('active');
    if (dock) dock.classList.remove('hidden');

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
        const firstTime = new Date(Replay.fullCandles[0].time * 1000);
        const yyyy = firstTime.getFullYear();
        const mm = String(firstTime.getMonth() + 1).padStart(2, '0');
        const dd = String(firstTime.getDate()).padStart(2, '0');
        datePicker.value = `${yyyy}-${mm}-${dd}`;
    }

    // Lock stable barSpacing and rightOffset so candles never stretch or squish on steps
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

    const toggleBtn = document.getElementById('btn-replay-toggle');
    const dock = document.getElementById('replay-bar');
    if (toggleBtn) toggleBtn.classList.remove('active');
    if (dock) dock.classList.add('hidden');

    // Restore full dataset
    if (Replay.fullCandles.length > 0) {
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

function renderReplayStep() {
    if (!Replay.fullCandles || Replay.fullCandles.length === 0) return;
    const totalBars = Replay.fullCandles.length;
    const curIdx = Math.min(Math.max(0, Replay.currentIndex), totalBars - 1);

    // Slice candles up to current replay index
    const sliced = Replay.fullCandles.slice(0, curIdx + 1);
    C.rawCandles = sliced;

    // Apply indicators & chart progressively WITHOUT refitting timescale zoom
    applyFullChart(sliced, false);
    updateSessionStats(sliced);

    // Keep visible viewport steady: smoothly shift right only when bar nears right edge
    if (C.chart) {
        const range = C.chart.timeScale().getVisibleLogicalRange();
        if (range) {
            if (curIdx >= range.to - 1 || curIdx <= range.from) {
                C.chart.timeScale().scrollToPosition(4, false);
            }
        }
    }

    // Update Scrubber and time text
    const scrubber = document.getElementById('rp-scrubber');
    if (scrubber) scrubber.value = curIdx;

    const curCandle = Replay.fullCandles[curIdx];
    const firstCandle = Replay.fullCandles[0];
    const lastCandle = Replay.fullCandles[totalBars - 1];

    const fmtTime = (ts) => {
        const d = new Date(ts * 1000);
        return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
    };

    const curTimeEl = document.getElementById('rp-cur-time');
    const totTimeEl = document.getElementById('rp-total-time');
    const barCountEl = document.getElementById('rp-bar-count');

    if (curTimeEl && curCandle) curTimeEl.textContent = fmtTime(curCandle.time);
    if (totTimeEl && lastCandle) totTimeEl.textContent = fmtTime(lastCandle.time);
    if (barCountEl) barCountEl.textContent = `${curIdx + 1} / ${totalBars}`;

    // Update LTP & Change badge to reflect candle at current replay index
    if (curCandle) {
        const ltpEl = document.getElementById('badge-ltp');
        if (ltpEl) ltpEl.textContent = `₹${curCandle.close.toFixed(2)}`;
        const chgEl = document.getElementById('badge-chg');
        if (chgEl && firstCandle) {
            const chgAbs = curCandle.close - firstCandle.open;
            const chgPct = (chgAbs / firstCandle.open) * 100;
            chgEl.textContent = `${chgAbs >= 0 ? '+' : ''}${chgAbs.toFixed(2)} (${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(2)}%)`;
            chgEl.className = chgAbs >= 0 ? 'pos' : 'neg';
        }
    }
}



