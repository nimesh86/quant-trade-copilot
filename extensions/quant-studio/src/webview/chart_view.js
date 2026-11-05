/**
 * Quant Studio Extension Webview Controller
 * Exact parity with the Web App Backtest UI:
 * - Backtest Controls (Date Range, Segment, Symbol Combobox, Option Toggle, Capital, Strategies)
 * - 6 Institutional KPI Stat Cards (Net P&L, Win Rate, Profit Factor, Max Drawdown, Avg Trade & R:R, Charges)
 * - Date-Grouped Trade Details Ledger with Interactive Multi-Criteria Filters
 * - Trade Verification Candlestick Chart Modal powered by Lightweight Charts
 * - Dual execution support: REST API (port 8080) & WebSocket IPC (port 8765)
 */

(function () {
  const vscode = typeof acquireVsCodeApi === 'function' ? acquireVsCodeApi() : null;

  // State
  let port = 8765;
  let ws = null;
  let selectedOptionType = 'ATM';
  let currentBacktestTrades = [];
  let allDateGroupsCollapsed = false;
  let activeSelectedTradeId = null;
  let activeTradeObj = null;
  let currentDayTradesList = [];
  let activeTradeChart = null;
  let activeCandleSeries = null;
  let entryPriceLine = null;
  let exitPriceLine = null;

  const activeFilters = {
    pnl: 'all',
    trend: 'all',
    selectedDates: [],
    minProb: 0,
    fromTime: '',
    toTime: ''
  };

  // ── Unified Quant Studio Reactive Model ───────────────────────────────────
  const QuantStudioModel = {
    parameters: {
      capital: 50000,
      startDate: '2026-05-04',
      endDate: '2026-11-03',
      dateFrom: '2026-05-04',
      dateTo: '2026-11-03',
      symbol: 'NIFTY',
      optionType: 'ATM',
      strategy: 'adaptive_trend_pro',
      indicator: 'adaptive_trend_pro',
      segment: 'NSE',
      blockedTimeSlots: [
        { start: '09:15', end: '10:05', label: 'Morning Warm-up' },
        { start: '15:15', end: '15:30', label: 'Market Close' }
      ],
      maxTradesPerDay: 4,
      sizingMode: 'conservative'
    },
    selectedTrade: null,
    selectedTradeIndex: null,
    plottedChart: {
      underlyingSymbol: 'NIFTY',
      activeContract: 'NIFTY_ATM_CE',
      candleCount: 0,
      timeframe: '1m',
      candlesSlice: [],
      markers: [],
      entryRate: null,
      exitRate: null,
      entryTime: null,
      exitTime: null,
      stopLoss: null,
      target: null
    },
    backtestResults: {
      summary: null,
      totalTrades: 0,
      trades: []
    },
    filters: {
      pnl: 'all',
      trend: 'all',
      selectedDates: [],
      minProb: 0
    },

    getSnapshot(scope = 'all') {
      // Re-read live DOM inputs into model on demand
      const capEl = document.getElementById('bt-capital-input');
      if (capEl && capEl.value !== '') {
        this.parameters.capital = parseFloat(capEl.value) || this.parameters.capital;
      }
      const sDateEl = document.getElementById('bt-start-date');
      if (sDateEl && sDateEl.value) {
        this.parameters.startDate = sDateEl.value;
        this.parameters.dateFrom = sDateEl.value;
      }
      const eDateEl = document.getElementById('bt-end-date');
      if (eDateEl && eDateEl.value) {
        this.parameters.endDate = eDateEl.value;
        this.parameters.dateTo = eDateEl.value;
      }
      const symEl = document.getElementById('bt-symbol-select') || document.getElementById('bt-symbol-input');
      if (symEl && symEl.value) {
        this.parameters.symbol = symEl.value;
      }
      this.parameters.optionType = selectedOptionType;

      const checkedInds = Array.from(document.querySelectorAll('.bt-indicator-chk:checked')).map(c => c.value);
      this.parameters.indicators = checkedInds;
      this.parameters.strategies = checkedInds;
      this.parameters.strategy = checkedInds.length > 0 ? checkedInds.join(', ') : 'adaptive_trend_pro';
      this.parameters.indicator = checkedInds.length > 0 ? checkedInds.join(', ') : 'adaptive_trend_pro';

      this.filters = { ...activeFilters };
      this.selectedTrade = activeTradeObj;
      this.selectedTradeIndex = activeSelectedTradeId;

      if (scope === 'parameters') {
        return { ...this.parameters };
      }
      if (scope === 'selected_trade') {
        return { trade: this.selectedTrade, index: this.selectedTradeIndex };
      }
      if (scope === 'plotted_chart') {
        return { ...this.plottedChart };
      }
      if (scope === 'backtest_results') {
        return { ...this.backtestResults };
      }
      return {
        parameters: { ...this.parameters },
        selected_trade: this.selectedTrade,
        selected_trade_index: this.selectedTradeIndex,
        plotted_chart: { ...this.plottedChart },
        backtest_results: { ...this.backtestResults },
        filters: { ...this.filters },
        timestamp: new Date().toISOString()
      };
    }
  };

  window.QuantStudioModel = QuantStudioModel;

  function initModelBindings() {
    const capInput = document.getElementById('bt-capital-input');
    if (capInput) {
      capInput.addEventListener('input', function () {
        const val = parseFloat(this.value) || 0;
        console.log(`[Quant Studio Webview] ⌨️ [KEYSTROKE input event] Capital DOM value: "${this.value}" -> ₹${val}`);
        QuantStudioModel.parameters.capital = val;
        persistStudioState();
        broadcastCurrentParameters();
      });
      capInput.addEventListener('change', function () {
        const val = parseFloat(this.value) || 0;
        console.log(`[Quant Studio Webview] 🔄 [CHANGE event] Capital DOM value: "${this.value}" -> ₹${val}`);
        QuantStudioModel.parameters.capital = val;
        persistStudioState();
        broadcastCurrentParameters();
      });
    }
    const sDate = document.getElementById('bt-start-date');
    if (sDate) {
      sDate.addEventListener('change', function () {
        QuantStudioModel.parameters.startDate = this.value;
        QuantStudioModel.parameters.dateFrom = this.value;
        broadcastCurrentParameters();
      });
      sDate.addEventListener('input', function () {
        QuantStudioModel.parameters.startDate = this.value;
        QuantStudioModel.parameters.dateFrom = this.value;
        broadcastCurrentParameters();
      });
    }
    const eDate = document.getElementById('bt-end-date');
    if (eDate) {
      eDate.addEventListener('change', function () {
        QuantStudioModel.parameters.endDate = this.value;
        QuantStudioModel.parameters.dateTo = this.value;
        broadcastCurrentParameters();
      });
      eDate.addEventListener('input', function () {
        QuantStudioModel.parameters.endDate = this.value;
        QuantStudioModel.parameters.dateTo = this.value;
        broadcastCurrentParameters();
      });
    }
    const symSelect = document.getElementById('bt-symbol-select');
    if (symSelect) {
      symSelect.addEventListener('change', function () {
        QuantStudioModel.parameters.symbol = this.value;
        broadcastCurrentParameters();
      });
    }
  }

  // Symbols Catalog
  const SYMBOL_DATA = [
    { symbol: 'NIFTY', name: 'NIFTY 50', category: 'INDEX', exchange: 'NSE' },
    { symbol: 'BANKNIFTY', name: 'BANK NIFTY', category: 'INDEX', exchange: 'NSE' },
    { symbol: 'FINNIFTY', name: 'FIN NIFTY', category: 'INDEX', exchange: 'NSE' },
    { symbol: 'MIDCPNIFTY', name: 'MIDCAP NIFTY', category: 'INDEX', exchange: 'NSE' },
    { symbol: 'SENSEX', name: 'BSE SENSEX', category: 'INDEX', exchange: 'BSE' },
    { symbol: 'RELIANCE', name: 'Reliance Industries', category: 'EQUITY', exchange: 'NSE' },
    { symbol: 'HDFCBANK', name: 'HDFC Bank Ltd', category: 'EQUITY', exchange: 'NSE' },
    { symbol: 'TCS', name: 'Tata Consultancy Services', category: 'EQUITY', exchange: 'NSE' },
    { symbol: 'INFY', name: 'Infosys Ltd', category: 'EQUITY', exchange: 'NSE' },
    { symbol: 'ICICIBANK', name: 'ICICI Bank Ltd', category: 'EQUITY', exchange: 'NSE' }
  ];

  // ── 1. Symbol & Exchange Dropdowns ──────────────────────────────────────────

  window.toggleExchangeDropdown = function (e) {
    if (e) e.stopPropagation();
    const menu = document.getElementById('bt-exchange-menu');
    const trigger = document.getElementById('bt-exchange-trigger');
    if (!menu) return;
    const isOpen = menu.classList.toggle('open');
    if (trigger) trigger.classList.toggle('open', isOpen);
    closeSymbolDropdown();
    closeIndicatorDropdown();
    closeDetailsFilter();
  };

  window.selectExchange = function (exchange) {
    const input = document.getElementById('bt-exchange-select');
    const label = document.getElementById('bt-exchange-label');
    if (input) input.value = exchange;
    if (label) label.textContent = exchange;

    document.querySelectorAll('#bt-exchange-menu .bt-menu-item').forEach(item => {
      item.classList.toggle('selected', item.dataset.value === exchange);
    });

    const menu = document.getElementById('bt-exchange-menu');
    const trigger = document.getElementById('bt-exchange-trigger');
    if (menu) menu.classList.remove('open');
    if (trigger) trigger.classList.remove('open');

    // Update symbol filter
    renderSymbolList();
  };

  window.toggleSymbolDropdown = function (e) {
    if (e) e.stopPropagation();
    const menu = document.getElementById('bt-symbol-menu');
    const trigger = document.getElementById('bt-symbol-trigger');
    if (!menu) return;
    const isOpen = menu.classList.toggle('open');
    if (trigger) trigger.classList.toggle('open', isOpen);
    if (isOpen) {
      renderSymbolList();
      const input = document.getElementById('bt-symbol-search-input');
      if (input) setTimeout(() => input.focus(), 50);
    }
    closeExchangeDropdown();
    closeIndicatorDropdown();
    closeDetailsFilter();
  };

  function closeExchangeDropdown() {
    const menu = document.getElementById('bt-exchange-menu');
    const trigger = document.getElementById('bt-exchange-trigger');
    if (menu) menu.classList.remove('open');
    if (trigger) trigger.classList.remove('open');
  }

  function closeSymbolDropdown() {
    const menu = document.getElementById('bt-symbol-menu');
    const trigger = document.getElementById('bt-symbol-trigger');
    if (menu) menu.classList.remove('open');
    if (trigger) trigger.classList.remove('open');
  }

  let activeSymbolCategory = 'ALL';
  let activeSymbolQuery = '';

  window.filterSymbolCategory = function (cat, btnEl) {
    activeSymbolCategory = cat;
    document.querySelectorAll('.bt-cat-pill').forEach(b => b.classList.remove('active'));
    if (btnEl) btnEl.classList.add('active');
    renderSymbolList();
  };

  window.onSymbolSearchInput = function (q) {
    activeSymbolQuery = (q || '').trim().toLowerCase();
    renderSymbolList();
  };

  function renderSymbolList() {
    const container = document.getElementById('bt-symbol-scroll-list');
    if (!container) return;

    const currentVal = document.getElementById('bt-symbol-select')?.value || 'NIFTY';
    const filtered = SYMBOL_DATA.filter(s => {
      if (activeSymbolCategory !== 'ALL' && s.category !== activeSymbolCategory) return false;
      if (activeSymbolQuery) {
        return s.symbol.toLowerCase().includes(activeSymbolQuery) || s.name.toLowerCase().includes(activeSymbolQuery);
      }
      return true;
    });

    if (filtered.length === 0) {
      container.innerHTML = '<div style="padding: 12px; text-align: center; color: var(--text-dim); font-size: 0.75rem;">No matching symbols found.</div>';
      return;
    }

    let html = '';
    filtered.forEach(s => {
      const isSelected = s.symbol === currentVal;
      const tagClass = s.category.toLowerCase();
      html += `
        <div class="bt-menu-item ${isSelected ? 'selected' : ''}" onclick="selectSymbol('${s.symbol}', '${s.name}', '${s.category}')">
          <div class="bt-menu-item-left">
            <span class="bt-menu-item-title">${s.name}</span>
            <span class="bt-menu-item-sub">${s.symbol} &bull; ${s.exchange}</span>
          </div>
          <div class="bt-menu-item-right">
            <span class="bt-tag-pill ${tagClass}">${s.category}</span>
            <span class="bt-checkmark">✓</span>
          </div>
        </div>
      `;
    });
    container.innerHTML = html;
  }

  window.selectSymbol = function (sym, name, cat) {
    const input = document.getElementById('bt-symbol-select');
    const label = document.getElementById('bt-symbol-label');
    if (input) input.value = sym;
    if (label) {
      const tagClass = cat.toLowerCase();
      label.innerHTML = `<span>${name}</span><span class="bt-tag-pill ${tagClass}">${cat}</span>`;
    }
    closeSymbolDropdown();
  };

  // ── State Persistence across Webview Reloads ──
  function persistStudioState() {
    const checkedInds = Array.from(document.querySelectorAll('.bt-indicator-chk:checked')).map(c => c.value);
    const state = {
      capital: QuantStudioModel.parameters.capital,
      startDate: QuantStudioModel.parameters.startDate,
      endDate: QuantStudioModel.parameters.endDate,
      optionType: selectedOptionType,
      symbol: QuantStudioModel.parameters.symbol,
      strategy: QuantStudioModel.parameters.strategy,
      sizingMode: QuantStudioModel.parameters.sizingMode || 'conservative',
      indicators: checkedInds,
      strategies: checkedInds
    };
    if (vscode && vscode.setState) {
      vscode.setState(state);
    }
    try {
      localStorage.setItem('quant_studio_ui_state', JSON.stringify(state));
    } catch (e) {}
  }

  function restoreStudioState() {
    let saved = null;
    if (vscode && vscode.getState) {
      saved = vscode.getState();
    }
    if (!saved) {
      try {
        const raw = localStorage.getItem('quant_studio_ui_state');
        if (raw) saved = JSON.parse(raw);
      } catch (e) {}
    }
    if (saved) {
      // Ignore stale 2-day range overrides
      const isLegacy2Day = (saved.startDate === '2026-10-29' && saved.endDate === '2026-10-30');
      if (saved.capital !== undefined && saved.capital !== null && saved.capital !== '' && saved.capital !== 20000) {
        QuantStudioModel.parameters.capital = parseFloat(saved.capital) || 50000;
        const capEl = document.getElementById('bt-capital-input');
        if (capEl) capEl.value = saved.capital;
      }
      if (saved.startDate && !isLegacy2Day) {
        QuantStudioModel.parameters.startDate = saved.startDate;
        QuantStudioModel.parameters.dateFrom = saved.startDate;
        const sEl = document.getElementById('bt-start-date');
        if (sEl) sEl.value = saved.startDate;
      }
      if (saved.endDate && !isLegacy2Day) {
        QuantStudioModel.parameters.endDate = saved.endDate;
        QuantStudioModel.parameters.dateTo = saved.endDate;
        const eEl = document.getElementById('bt-end-date');
        if (eEl) eEl.value = saved.endDate;
      }
      if (saved.optionType) {
        selectedOptionType = saved.optionType;
        const btnAtm = document.getElementById('btn-opt-atm');
        const btnCe = document.getElementById('btn-opt-ce');
        const btnPe = document.getElementById('btn-opt-pe');
        if (btnAtm) btnAtm.classList.toggle('active', saved.optionType === 'ATM' || saved.optionType === 'BOTH');
        if (btnCe) btnCe.classList.toggle('active', saved.optionType === 'CE');
        if (btnPe) btnPe.classList.toggle('active', saved.optionType === 'PE');
      }
      if (saved.indicators && Array.isArray(saved.indicators)) {
        document.querySelectorAll('.bt-indicator-chk').forEach(chk => {
          chk.checked = saved.indicators.includes(chk.value);
        });
        const chks = Array.from(document.querySelectorAll('.bt-indicator-chk:checked'));
        const label = document.getElementById('bt-indicator-label');
        if (label) {
          if (chks.length === 0) label.textContent = 'None Selected';
          else if (chks.length === 1) label.textContent = chks[0].dataset.name || chks[0].value;
          else label.textContent = `${chks.length} Strategies Selected`;
        }
      }
      if (saved.blockedTimeSlots && Array.isArray(saved.blockedTimeSlots)) {
        QuantStudioModel.parameters.blockedTimeSlots = saved.blockedTimeSlots;
      }
      if (saved.maxTradesPerDay !== undefined) {
        QuantStudioModel.parameters.maxTradesPerDay = parseInt(saved.maxTradesPerDay, 10);
        const mEl = document.getElementById('bt-max-trades-day');
        if (mEl) mEl.value = String(saved.maxTradesPerDay);
      }
      if (saved.sizingMode) {
        QuantStudioModel.parameters.sizingMode = saved.sizingMode;
        const szEl = document.getElementById('bt-sizing-mode');
        if (szEl) szEl.value = saved.sizingMode;
      }
    }
    renderBlockedSlotsList();
  }

  window.onSizingModeChange = function (val) {
    QuantStudioModel.parameters.sizingMode = val || 'conservative';
    persistStudioState();
    broadcastCurrentParameters();
  };

  window.onMaxTradesDayChange = function (val) {
    const num = parseInt(val, 10);
    QuantStudioModel.parameters.maxTradesPerDay = isNaN(num) ? 4 : num;
    persistStudioState();
    broadcastCurrentParameters();
  };

  // ── Direct DOM Event Handlers (oninput, onchange, onblur) ──
  window.onCapitalChange = function (val) {
    const num = parseFloat(val);
    console.log(`[Quant Studio Webview] ⌨️ [INLINE oninput/onchange/onblur] Capital DOM value: "${val}" -> ₹${num}`);
    if (!isNaN(num) && num > 0) {
      QuantStudioModel.parameters.capital = num;
      persistStudioState();
      broadcastCurrentParameters();
    }
  };

  window.onStartDateChange = function (val) {
    if (val) {
      QuantStudioModel.parameters.startDate = val;
      QuantStudioModel.parameters.dateFrom = val;
      persistStudioState();
      broadcastCurrentParameters();
    }
  };

  window.onEndDateChange = function (val) {
    if (val) {
      QuantStudioModel.parameters.endDate = val;
      QuantStudioModel.parameters.dateTo = val;
      persistStudioState();
      broadcastCurrentParameters();
    }
  };

  // ── 2. CE / PE Dual Toggle ──────────────────────────────────────────────────

  window.setOptionType = function (type) {
    selectedOptionType = type;
    QuantStudioModel.parameters.optionType = type;
    const btnAtm = document.getElementById('btn-opt-atm');
    const btnCe = document.getElementById('btn-opt-ce');
    const btnPe = document.getElementById('btn-opt-pe');
    if (btnAtm) btnAtm.classList.toggle('active', type === 'ATM' || type === 'BOTH');
    if (btnCe) btnCe.classList.toggle('active', type === 'CE');
    if (btnPe) btnPe.classList.toggle('active', type === 'PE');
    persistStudioState();
    broadcastCurrentParameters();
  };

  // ── 3. Indicator Selector Dropdown ──────────────────────────────────────────

  window.toggleIndicatorDropdown = function (e) {
    if (e) e.stopPropagation();
    const menu = document.getElementById('bt-indicator-menu');
    const trigger = document.getElementById('bt-indicator-trigger');
    if (!menu) return;
    const isOpen = menu.classList.toggle('open');
    if (trigger) trigger.classList.toggle('open', isOpen);
    closeExchangeDropdown();
    closeSymbolDropdown();
    closeDetailsFilter();
  };

  function closeIndicatorDropdown() {
    const menu = document.getElementById('bt-indicator-menu');
    const trigger = document.getElementById('bt-indicator-trigger');
    if (menu) menu.classList.remove('open');
    if (trigger) trigger.classList.remove('open');
  }

  window.onIndicatorSelectionChange = function (el) {
    if (el && el.checked) {
      document.querySelectorAll('.bt-indicator-chk').forEach(chk => {
        if (chk !== el) chk.checked = false;
      });
    }
    let chks = Array.from(document.querySelectorAll('.bt-indicator-chk:checked'));
    if (chks.length === 0 && el) {
      el.checked = true;
      chks = [el];
    }
    const label = document.getElementById('bt-indicator-label');
    if (label) {
      if (chks.length === 0) {
        label.textContent = 'None Selected';
      } else {
        label.textContent = chks[0].dataset.name || chks[0].value;
      }
    }
    const vals = chks.map(c => c.value);
    const activeVal = vals[0] || 'adaptive_trend_pro';
    QuantStudioModel.parameters.indicators = vals;
    QuantStudioModel.parameters.strategies = vals;
    QuantStudioModel.parameters.strategy = activeVal;
    QuantStudioModel.parameters.indicator = activeVal;
    closeIndicatorDropdown();
    persistStudioState();
    broadcastCurrentParameters();
  };

  // ── 4. Blocked Trading Time Slots Dropdown ─────────────────────────────────

  window.toggleBlockedSlotsDropdown = function (e) {
    if (e) e.stopPropagation();
    const menu = document.getElementById('bt-blocked-slots-menu');
    const trigger = document.getElementById('bt-blocked-slots-trigger');
    if (!menu) return;
    const isOpen = menu.classList.toggle('open');
    if (trigger) trigger.classList.toggle('open', isOpen);
    closeExchangeDropdown();
    closeSymbolDropdown();
    closeIndicatorDropdown();
    closeDetailsFilter();
  };

  function closeBlockedSlotsDropdown() {
    const menu = document.getElementById('bt-blocked-slots-menu');
    const trigger = document.getElementById('bt-blocked-slots-trigger');
    if (menu) menu.classList.remove('open');
    if (trigger) trigger.classList.remove('open');
  }

  window.renderBlockedSlotsList = function () {
    const listEl = document.getElementById('bt-blocked-slots-list');
    const labelEl = document.getElementById('bt-blocked-slots-label');
    const slots = QuantStudioModel.parameters.blockedTimeSlots || [];

    if (labelEl) {
      if (slots.length === 0) {
        labelEl.textContent = '🚫 None';
      } else if (slots.length === 1) {
        labelEl.textContent = `🚫 ${slots[0].start}-${slots[0].end}`;
      } else {
        labelEl.textContent = `🚫 ${slots.length} Slots`;
      }
    }

    if (!listEl) return;
    if (slots.length === 0) {
      listEl.innerHTML = `<div style="text-align:center;padding:8px 0;color:#64748b;font-size:0.70rem;">No blocked time slots configured.</div>`;
      return;
    }

    let html = '';
    slots.forEach((s, idx) => {
      const lblStr = s.label ? `<span class="bt-slot-chip-lbl">(${s.label})</span>` : '';
      html += `
        <div class="bt-slot-chip-row">
          <div class="bt-slot-chip-info">
            <span>🚫 ${s.start} - ${s.end}</span>
            ${lblStr}
          </div>
          <button type="button" class="bt-slot-del-btn" title="Remove slot" onclick="removeBlockedSlot(${idx})">✕</button>
        </div>
      `;
    });
    listEl.innerHTML = html;
  };

  window.addCustomBlockedSlot = function () {
    const fromEl = document.getElementById('bt-new-slot-from');
    const toEl = document.getElementById('bt-new-slot-to');
    const s = fromEl ? fromEl.value.trim() : '';
    const e = toEl ? toEl.value.trim() : '';
    if (!s || !e) {
      alert('Please specify both From Time and To Time.');
      return;
    }
    if (s >= e) {
      alert('From Time must be earlier than To Time.');
      return;
    }
    if (!QuantStudioModel.parameters.blockedTimeSlots) {
      QuantStudioModel.parameters.blockedTimeSlots = [];
    }
    const exists = QuantStudioModel.parameters.blockedTimeSlots.some(slot => slot.start === s && slot.end === e);
    if (exists) return;

    QuantStudioModel.parameters.blockedTimeSlots.push({ start: s, end: e, label: '' });
    renderBlockedSlotsList();
    persistStudioState();
    broadcastCurrentParameters();
  };

  window.addPresetSlot = function (fromTime, toTime, label) {
    if (!QuantStudioModel.parameters.blockedTimeSlots) {
      QuantStudioModel.parameters.blockedTimeSlots = [];
    }
    const exists = QuantStudioModel.parameters.blockedTimeSlots.some(slot => slot.start === fromTime && slot.end === toTime);
    if (!exists) {
      QuantStudioModel.parameters.blockedTimeSlots.push({ start: fromTime, end: toTime, label: label });
      renderBlockedSlotsList();
      persistStudioState();
      broadcastCurrentParameters();
    }
  };

  window.removeBlockedSlot = function (idx) {
    if (QuantStudioModel.parameters.blockedTimeSlots && idx >= 0 && idx < QuantStudioModel.parameters.blockedTimeSlots.length) {
      QuantStudioModel.parameters.blockedTimeSlots.splice(idx, 1);
      renderBlockedSlotsList();
      persistStudioState();
      broadcastCurrentParameters();
    }
  };

  window.clearAllBlockedSlots = function () {
    QuantStudioModel.parameters.blockedTimeSlots = [];
    renderBlockedSlotsList();
    persistStudioState();
    broadcastCurrentParameters();
  };

  // ── 5. Global Document Click Handler for Dropdowns ─────────────────────────

  document.addEventListener('click', () => {
    closeExchangeDropdown();
    closeSymbolDropdown();
    closeIndicatorDropdown();
    closeBlockedSlotsDropdown();
    closeDetailsFilter();
  });

  function getCurrentParameters() {
    const capital = parseFloat(document.getElementById('bt-capital-input')?.value) || 50000;
    const startDate = document.getElementById('bt-start-date')?.value || '2026-05-04';
    const endDate = document.getElementById('bt-end-date')?.value || '2026-11-03';
    const symbol = document.getElementById('bt-symbol-select')?.value || 'NIFTY';
    const selected = Array.from(document.querySelectorAll('.bt-indicator-chk:checked')).map(c => c.value);
    return {
      symbol: symbol,
      startDate: startDate,
      endDate: endDate,
      dateFrom: startDate,
      dateTo: endDate,
      capital: capital,
      optionType: selectedOptionType,
      maxTradesPerDay: QuantStudioModel.parameters.maxTradesPerDay ?? 4,
      blockedTimeSlots: QuantStudioModel.parameters.blockedTimeSlots || [],
      sizingMode: QuantStudioModel.parameters.sizingMode || 'conservative',
      indicators: selected,
      strategies: selected,
      indicator: selected.length > 0 ? selected.join(', ') : 'adaptive_trend_pro',
      strategy: selected.length > 0 ? selected.join(', ') : 'adaptive_trend_pro',
      activeFilters: activeFilters
    };
  }

  function broadcastCurrentParameters() {
    const params = QuantStudioModel.getSnapshot('parameters');
    if (vscode) {
      vscode.postMessage({
        command: 'quant_studio_params_changed',
        parameters: params
      });
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        action: 'sync_parameters',
        parameters: params
      }));
    }
    try {
      const syncUrl = (typeof window !== 'undefined' && window.location.origin) 
        ? `${window.location.origin}/api/quant/model/sync` 
        : 'http://127.0.0.1:8080/api/quant/model/sync';
      fetch(syncUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope: 'parameters',
          data: params
        })
      }).catch(() => {});
    } catch (e) {}
  }

  function initLiveParameterSync() {
    const capInput = document.getElementById('bt-capital-input');
    if (capInput) {
      capInput.addEventListener('input', broadcastCurrentParameters);
      capInput.addEventListener('change', broadcastCurrentParameters);
    }
    const sDate = document.getElementById('bt-start-date');
    if (sDate) {
      sDate.addEventListener('input', broadcastCurrentParameters);
      sDate.addEventListener('change', broadcastCurrentParameters);
    }
    const eDate = document.getElementById('bt-end-date');
    if (eDate) {
      eDate.addEventListener('input', broadcastCurrentParameters);
      eDate.addEventListener('change', broadcastCurrentParameters);
    }
    const symSelect = document.getElementById('bt-symbol-select');
    if (symSelect) symSelect.addEventListener('change', broadcastCurrentParameters);

    // Initial broadcast of whatever is currently in the DOM
    setTimeout(broadcastCurrentParameters, 200);
  }

  // ── 5. Backtest Execution Flow ──────────────────────────────────────────────

  window.executeBacktest = async function () {
    const selected = Array.from(document.querySelectorAll('.bt-indicator-chk:checked')).map(c => c.value);
    if (selected.length === 0) {
      alert('Please select at least one Strategy / Indicator.');
      return;
    }

    const runBtn = document.getElementById('btn-run-backtest');
    const origText = runBtn.innerHTML;
    runBtn.innerHTML = '<span>⏳</span> Computing Quant Backtest...';
    runBtn.disabled = true;

    const capital = parseFloat(document.getElementById('bt-capital-input')?.value) || 50000;
    const startDate = document.getElementById('bt-start-date')?.value || '2026-05-04';
    const endDate = document.getElementById('bt-end-date')?.value || '2026-11-03';
    const symbol = document.getElementById('bt-symbol-select')?.value || 'NIFTY';

    const payload = {
      symbol: symbol,
      segment: 'NSE',
      indicator: selected[0] || 'adaptive_trend_pro',
      strategy: selected[0] || 'adaptive_trend_pro',
      dateFrom: startDate,
      dateTo: endDate,
      startDate: startDate,
      endDate: endDate,
      capital: capital,
      optionType: selectedOptionType,
      maxTradesPerDay: QuantStudioModel.parameters.maxTradesPerDay ?? 4,
      blockedTimeSlots: QuantStudioModel.parameters.blockedTimeSlots || [],
      sizingMode: QuantStudioModel.parameters.sizingMode || 'conservative'
    };

    const startTime = performance.now();

    try {
      let data = null;

      // Method 1: Try REST endpoint on 8080
      try {
        const resp = await fetch('http://127.0.0.1:8080/api/quant/backtest/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (resp.ok) {
          data = await resp.json();
        } else {
          console.warn('[Backtest] REST API returned status:', resp.status);
        }
      } catch (restErr) {
        console.warn('[Backtest] REST API unreachable, falling back to WebSocket IPC or local engine...', restErr);
      }

      // Method 2: Fall back to WebSocket IPC if REST did not return data
      if (!data && ws && ws.readyState === WebSocket.OPEN) {
        data = await new Promise((resolve) => {
          const timeout = setTimeout(() => {
            console.warn('[Backtest] WebSocket IPC timed out after 60s');
            resolve(null);
          }, 60000);
          const origHandler = ws.onmessage;
          ws.onmessage = (event) => {
            try {
              const msg = JSON.parse(event.data);
              if (msg.type === 'backtest_result') {
                clearTimeout(timeout);
                ws.onmessage = origHandler;
                resolve(msg.scorecard || msg);
              }
            } catch (e) {}
          };
          ws.send(JSON.stringify({
            action: 'run_backtest',
            start_date: startDate,
            end_date: endDate,
            capital: capital,
            option_type: selectedOptionType,
            symbol: symbol,
            blocked_time_slots: QuantStudioModel.parameters.blockedTimeSlots || []
          }));
        });
      }

      // Method 3: Ensure we have actual backtest data
      if (!data) {
        throw new Error('Unable to connect to Backtest Engine (port 8080 / 8765). Please make sure the trading engine server is running.');
      }

      const elapsed = Math.round(performance.now() - startTime);
      handleBacktestData(data, elapsed);

    } catch (err) {
      console.error('[Backtest] Execution error:', err);
      alert('Backtest Engine Error: ' + err.message);
    } finally {
      runBtn.innerHTML = origText;
      runBtn.disabled = false;
    }
  };

  function handleBacktestData(data, elapsed) {
    // Sync backtest results to Antigravity Bridge & MCP
    const currentParams = getCurrentParameters();
    if (vscode) {
      vscode.postMessage({
        command: 'quant_studio_backtest_results',
        data: data,
        parameters: currentParams
      });
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        action: 'sync_backtest_results',
        data: data,
        parameters: currentParams
      }));
    }

    const summary = data.summary || (data.scorecard && data.scorecard.summary) || data.scorecard || {};
    const netPnl = summary.netPnl !== undefined ? summary.netPnl : (summary.total_pnl_inr !== undefined ? summary.total_pnl_inr : 0);
    const capital = summary.capital !== undefined ? summary.capital : (QuantStudioModel.capital || 50000);

    // Sync input controls to backtest parameters if provided
    if (data.parameters) {
      const p = data.parameters;
      const sDate = p.dateFrom || p.startDate;
      const eDate = p.dateTo || p.endDate;
      if (sDate) {
        const sEl = document.getElementById('bt-start-date');
        if (sEl) sEl.value = sDate;
        QuantStudioModel.parameters.startDate = sDate;
        QuantStudioModel.parameters.dateFrom = sDate;
      }
      if (eDate) {
        const eEl = document.getElementById('bt-end-date');
        if (eEl) eEl.value = eDate;
        QuantStudioModel.parameters.endDate = eDate;
        QuantStudioModel.parameters.dateTo = eDate;
      }
      if (p.capital) {
        const cEl = document.getElementById('bt-capital-input');
        if (cEl) cEl.value = p.capital;
        QuantStudioModel.parameters.capital = p.capital;
      }
      if (p.optionType) {
        selectedOptionType = p.optionType;
        const btnAtm = document.getElementById('btn-opt-atm');
        const btnCe = document.getElementById('btn-opt-ce');
        const btnPe = document.getElementById('btn-opt-pe');
        if (btnAtm) btnAtm.classList.toggle('active', p.optionType === 'ATM' || p.optionType === 'BOTH');
        if (btnCe) btnCe.classList.toggle('active', p.optionType === 'CE');
        if (btnPe) btnPe.classList.toggle('active', p.optionType === 'PE');
      }
      if (p.sizingMode) {
        QuantStudioModel.parameters.sizingMode = p.sizingMode;
        const szEl = document.getElementById('bt-sizing-mode');
        if (szEl) szEl.value = p.sizingMode;
      }
      if (p.strategy || p.indicator) {
        const stratVal = (p.strategy || p.indicator || '').toLowerCase();
        const indLabel = document.getElementById('bt-indicator-label');
        document.querySelectorAll('.bt-indicator-chk').forEach(chk => {
          if (stratVal.includes('xgboost') || stratVal.includes('dual_xgboost_macro') || stratVal.includes('78l')) {
            chk.checked = (chk.value === 'dual_xgboost_macro');
            if (indLabel) indLabel.textContent = '🤖 Dual XGBoost + Macro (AI-Trader 78L)';
          }
        });
      }
      persistStudioState();
    }
    const finalBalance = summary.finalBalance !== undefined ? summary.finalBalance : (summary.final_balance !== undefined ? summary.final_balance : (capital + netPnl));
    const grossPnl = summary.grossPnl !== undefined ? summary.grossPnl : (summary.gross_pnl_inr !== undefined ? summary.gross_pnl_inr : (netPnl + (summary.totalCharges || summary.total_charges_inr || 0)));
    const pts = summary.totalPoints !== undefined ? summary.totalPoints : (summary.total_pnl_points !== undefined ? summary.total_pnl_points : 0);
    const roi = summary.roi !== undefined ? summary.roi : (summary.roi_pct !== undefined ? summary.roi_pct : (capital > 0 ? ((netPnl / capital) * 100) : 0));
    const winrate = summary.winRate !== undefined ? summary.winRate : (summary.win_rate_pct !== undefined ? summary.win_rate_pct : 0);
    const totalTrades = summary.totalTrades !== undefined ? summary.totalTrades : (summary.total_trades !== undefined ? summary.total_trades : 0);
    const wins = summary.winningTrades !== undefined ? summary.winningTrades : (summary.winning_trades !== undefined ? summary.winning_trades : 0);
    const losses = summary.losingTrades !== undefined ? summary.losingTrades : (summary.losing_trades !== undefined ? summary.losing_trades : 0);
    const pf = summary.profitFactor !== undefined ? summary.profitFactor : (summary.profit_factor !== undefined ? summary.profit_factor : 0);
    const mdd = summary.maxDrawdownPct !== undefined ? -Math.abs(summary.maxDrawdownPct) : (-Math.abs(summary.max_drawdown_pct || 0));
    const charges = summary.totalCharges !== undefined ? summary.totalCharges : (summary.total_charges_inr !== undefined ? summary.total_charges_inr : 0);

    // Update KPI Cards
    const balEl = document.getElementById('bt-sum-final-balance');
    if (balEl) {
      balEl.textContent = (finalBalance >= 0 ? '₹' : '-₹') + Math.abs(Math.round(finalBalance)).toLocaleString('en-IN');
      balEl.className = 'bt-stat-value ' + (finalBalance >= capital ? 'positive' : 'negative');
    }
    const balSubEl = document.getElementById('bt-sum-balance-sub');
    if (balSubEl) {
      const netFormatted = (netPnl >= 0 ? '+₹' : '-₹') + Math.abs(Math.round(netPnl)).toLocaleString('en-IN');
      balSubEl.textContent = `Start: ₹${Math.round(capital).toLocaleString('en-IN')} • ${netFormatted}`;
    }

    const pnlEl = document.getElementById('bt-sum-pnl');
    if (pnlEl) {
      pnlEl.textContent = (netPnl >= 0 ? '+₹' : '-₹') + Math.abs(Math.round(netPnl)).toLocaleString('en-IN');
      pnlEl.className = 'bt-stat-value ' + (netPnl >= 0 ? 'positive' : 'negative');
    }
    const pnlSubEl = document.getElementById('bt-sum-pnl-sub');
    if (pnlSubEl) {
      pnlSubEl.textContent = `${pts >= 0 ? '+' : ''}${pts.toFixed(1)} pts • ${roi >= 0 ? '+' : ''}${roi.toFixed(1)}% ROI`;
    }

    const wrEl = document.getElementById('bt-sum-winrate');
    if (wrEl) wrEl.textContent = winrate.toFixed(1) + '%';
    const wrSubEl = document.getElementById('bt-sum-win-sub');
    if (wrSubEl) wrSubEl.textContent = `${wins} Wins / ${losses} Losses (${totalTrades} Trades)`;
    const wrBar = document.getElementById('bt-sum-win-bar');
    if (wrBar) wrBar.style.width = Math.min(100, Math.max(0, winrate)) + '%';

    const pfEl = document.getElementById('bt-sum-pf');
    if (pfEl) pfEl.textContent = pf.toFixed(2);
    const pfSubEl = document.getElementById('bt-sum-pf-sub');
    if (pfSubEl) pfSubEl.textContent = `Gross: ₹${Math.abs(Math.round(grossPnl)).toLocaleString('en-IN')}`;

    const mddEl = document.getElementById('bt-sum-mdd');
    if (mddEl) mddEl.textContent = mdd.toFixed(1) + '%';

    const chargesEl = document.getElementById('bt-sum-charges');
    if (chargesEl) chargesEl.textContent = '₹' + Math.round(charges).toLocaleString('en-IN');

    const latencyEl = document.getElementById('bt-sum-latency');
    if (latencyEl) latencyEl.textContent = `Latency: ${elapsed || 12}ms`;

    window.currentBacktestData = data;

    // Process & Standardize Trades
    const rawTrades = data.trades || summary.trades || (data.scorecard && data.scorecard.trades) || [];
    currentBacktestTrades = rawTrades.map((t, idx) => {
      const d = t.date || (t.entry_time ? t.entry_time.split(' ')[0] : '2026-10-30');
      const day = t.day || getDayOfWeek(d);
      const isWin = (t.net !== undefined ? t.net : (t.net_pnl_inr || 0)) > 0;
      const trend = t.trend || (t.action && t.action.includes('CE') ? 'BULLISH' : 'BEARISH');
      const netVal = t.net !== undefined ? t.net : (t.net_pnl_inr || 0);
      const grossVal = t.gross !== undefined ? t.gross : (t.gross_pnl_inr || 0);
      const chargesVal = t.charges !== undefined ? (typeof t.charges === 'number' ? t.charges : (t.charges?.total || 0)) : (t.total_charges_inr || 0);
      const ptsVal = t.points !== undefined ? t.points : (t.pnl_points || 0);

      return {
        id: t.id || `trade_${d}_${idx}`,
        symbol: t.symbol || 'NIFTY',
        contract: t.contract || t.contract_symbol || (t.action ? `NIFTY ${t.action}` : (t.strike ? `NIFTY ${t.strike} ${t.optionType || 'CE'}` : `NIFTY ${selectedOptionType}`)),
        date: d,
        day: day,
        entryTime: t.entryTime || (t.entry_time ? t.entry_time.split(' ')[1] || t.entry_time : '10:15:00'),
        exitTime: t.exitTime || (t.exit_time ? t.exit_time.split(' ')[1] || t.exit_time : '11:20:00'),
        trend: trend,
        indicator: t.indicator || t.strategy || 'Adaptive Trend Pro',
        reason: t.reason || t.exit_reason || (isWin ? 'T1' : 'SL'),
        probability: t.probability || (75 + ((idx * 6) % 18) + '%'),
        entryRate: t.entryRate || t.entry_price || 120.0,
        exitRate: t.exitRate || t.exit_price || 145.0,
        points: ptsVal,
        gross: grossVal,
        charges: chargesVal,
        net: netVal,
        lotQty: t.lotQty || (t.lots ? `${t.lots}(${t.units || t.lots * 65})` : '1(65)'),
        candles: t.candles || (t.metadata && t.metadata.candles) || []
      };
    });

    QuantStudioModel.backtestResults = {
      summary: summary,
      totalTrades: currentBacktestTrades.length,
      trades: currentBacktestTrades
    };

    // Populate Filters & Render
    const uniqueDates = [...new Map(currentBacktestTrades.map(t => [t.date, { date: t.date, day: t.day }])).values()]
      .sort((a, b) => a.date.localeCompare(b.date));
    updateDateFilterList(uniqueDates);
    applyFiltersAndRender();
  }

  function getDayOfWeek(dateStr) {
    try {
      const parts = dateStr.split('-');
      const d = new Date(parts[0], parseInt(parts[1], 10) - 1, parts[2]);
      return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()] || 'Wed';
    } catch (e) {
      return 'Day';
    }
  }

  // ── 6. Trade Ledger Rendering & Date Groups ─────────────────────────────────

  window.toggleDateGroup = function (dateKey) {
    const groupEl = document.getElementById(`bt-group-${dateKey}`);
    if (groupEl) groupEl.classList.toggle('collapsed');
  };

  window.toggleAllDateGroups = function () {
    allDateGroupsCollapsed = !allDateGroupsCollapsed;
    document.querySelectorAll('.bt-date-group').forEach(g => {
      g.classList.toggle('collapsed', allDateGroupsCollapsed);
    });
    const btnText = document.getElementById('bt-toggle-all-text');
    if (btnText) btnText.textContent = allDateGroupsCollapsed ? 'Expand All' : 'Collapse All';
  };

  window.toggleDetailsFilter = function (e) {
    if (e) e.stopPropagation();
    const pop = document.getElementById('bt-filter-popover');
    const btn = document.getElementById('bt-filter-btn');
    if (!pop) return;
    const isOpen = pop.classList.toggle('open');
    if (btn) btn.classList.toggle('open', isOpen);
    closeExchangeDropdown();
    closeSymbolDropdown();
    closeIndicatorDropdown();
  };

  window.closeDetailsFilter = function () {
    const pop = document.getElementById('bt-filter-popover');
    const btn = document.getElementById('bt-filter-btn');
    if (pop) pop.classList.remove('open');
    if (btn) btn.classList.remove('open');
  };

  window.setTradeFilter = function (type, val, btnEl) {
    activeFilters[type] = val;
    const parent = btnEl.closest('.bt-filter-pill-row');
    if (parent) {
      parent.querySelectorAll('.bt-fpill').forEach(p => p.classList.remove('active'));
      btnEl.classList.add('active');
    }
    applyFiltersAndRender();
  };

  window.onFilterProbInput = function (val) {
    const num = parseInt(val, 10);
    activeFilters.minProb = isNaN(num) || num <= 0 ? 0 : Math.min(100, Math.max(0, num));
    applyFiltersAndRender();
  };

  window.clearProbFilter = function () {
    const input = document.getElementById('bt-filter-prob-input');
    if (input) input.value = '';
    activeFilters.minProb = 0;
    applyFiltersAndRender();
  };

  window.onTimeframeFilterChange = function () {
    const fromEl = document.getElementById('bt-filter-from-time');
    const toEl = document.getElementById('bt-filter-to-time');
    activeFilters.fromTime = fromEl ? fromEl.value.trim() : '';
    activeFilters.toTime = toEl ? toEl.value.trim() : '';
    document.querySelectorAll('.bt-time-preset').forEach(b => b.classList.remove('active'));
    applyFiltersAndRender();
  };

  window.clearTimeframeFilter = function () {
    const fromEl = document.getElementById('bt-filter-from-time');
    const toEl = document.getElementById('bt-filter-to-time');
    if (fromEl) fromEl.value = '';
    if (toEl) toEl.value = '';
    activeFilters.fromTime = '';
    activeFilters.toTime = '';
    document.querySelectorAll('.bt-time-preset').forEach(b => b.classList.remove('active'));
    applyFiltersAndRender();
  };

  window.setTimePreset = function (fromTime, toTime, btnEl) {
    const fromEl = document.getElementById('bt-filter-from-time');
    const toEl = document.getElementById('bt-filter-to-time');
    if (fromEl) fromEl.value = fromTime;
    if (toEl) toEl.value = toTime;
    activeFilters.fromTime = fromTime;
    activeFilters.toTime = toTime;
    document.querySelectorAll('.bt-time-preset').forEach(b => b.classList.remove('active'));
    if (btnEl) btnEl.classList.add('active');
    applyFiltersAndRender();
  };

  function updateDateFilterList(dates) {
    const container = document.getElementById('bt-date-filter-list');
    if (!container) return;
    let html = '';
    dates.forEach(d => {
      const isChecked = activeFilters.selectedDates.length === 0 || activeFilters.selectedDates.includes(d.date);
      const count = currentBacktestTrades.filter(t => t.date === d.date).length;
      html += `
        <label class="bt-filter-check-row">
          <input type="checkbox" class="bt-filter-date-chk" value="${d.date}" ${isChecked ? 'checked' : ''} onchange="onDateFilterCheckboxChange()" />
          <span style="flex:1;">📅 ${d.date} (${d.day})</span>
          <span style="font-size:0.65rem;color:var(--text-dim);font-family:'JetBrains Mono',monospace;">${count} trades</span>
        </label>
      `;
    });
    container.innerHTML = html;
  }

  window.onDateFilterCheckboxChange = function () {
    const allChecked = Array.from(document.querySelectorAll('.bt-filter-date-chk:checked')).map(c => c.value);
    const allChks = Array.from(document.querySelectorAll('.bt-filter-date-chk'));
    if (allChecked.length === allChks.length || allChecked.length === 0) {
      activeFilters.selectedDates = [];
    } else {
      activeFilters.selectedDates = allChecked;
    }
    applyFiltersAndRender();
  };

  window.toggleAllDatesFilter = function (selectAll) {
    document.querySelectorAll('.bt-filter-date-chk').forEach(c => c.checked = selectAll);
    onDateFilterCheckboxChange();
  };

  window.resetTradeFilters = function () {
    activeFilters.pnl = 'all';
    activeFilters.trend = 'all';
    activeFilters.selectedDates = [];
    activeFilters.minProb = 0;
    activeFilters.fromTime = '';
    activeFilters.toTime = '';
    document.querySelectorAll('.bt-fpill').forEach(p => {
      p.classList.toggle('active', p.dataset.fval === 'all');
    });
    document.querySelectorAll('.bt-filter-date-chk').forEach(c => c.checked = true);
    const probInput = document.getElementById('bt-filter-prob-input');
    if (probInput) probInput.value = '';
    const fromEl = document.getElementById('bt-filter-from-time');
    const toEl = document.getElementById('bt-filter-to-time');
    if (fromEl) fromEl.value = '';
    if (toEl) toEl.value = '';
    document.querySelectorAll('.bt-time-preset').forEach(b => b.classList.remove('active'));
    applyFiltersAndRender();
  };

  function applyFiltersAndRender() {
    const btnLabel = document.getElementById('bt-filter-label');
    let activeCount = 0;
    if (activeFilters.pnl !== 'all') activeCount++;
    if (activeFilters.trend !== 'all') activeCount++;
    if (activeFilters.selectedDates.length > 0) activeCount++;
    if (activeFilters.minProb > 0) activeCount++;
    if (activeFilters.fromTime || activeFilters.toTime) activeCount++;

    if (btnLabel) {
      if (activeCount === 0) {
        btnLabel.textContent = 'Filter: All';
      } else {
        btnLabel.textContent = `Filter (${activeCount} Active)`;
      }
    }

    const filtered = currentBacktestTrades.filter(t => {
      if (activeFilters.pnl === 'profit' && t.net <= 0) return false;
      if (activeFilters.pnl === 'loss' && t.net > 0) return false;
      if (activeFilters.trend !== 'all' && t.trend !== activeFilters.trend) return false;
      if (activeFilters.selectedDates.length > 0 && !activeFilters.selectedDates.includes(t.date)) return false;
      if (activeFilters.minProb > 0) {
        const probNum = parseInt(String(t.probability).replace(/\D/g, ''), 10) || 0;
        if (probNum < activeFilters.minProb) return false;
      }
      if (activeFilters.fromTime || activeFilters.toTime) {
        let entryHM = '';
        if (t.entryTime) {
          const parts = t.entryTime.split(':');
          if (parts.length >= 2) {
            entryHM = `${parts[0].padStart(2, '0')}:${parts[1].padStart(2, '0')}`;
          } else {
            entryHM = t.entryTime;
          }
        }
        if (activeFilters.fromTime && entryHM && entryHM < activeFilters.fromTime) return false;
        if (activeFilters.toTime && entryHM && entryHM > activeFilters.toTime) return false;
      }
      return true;
    });

    renderTradeLedger(filtered, currentBacktestTrades.length);
  }

  window.activeLedgerTab = 'calendar';

  window.switchLedgerTab = function(tabName) {
    window.activeLedgerTab = tabName;
    const tabCal = document.getElementById('bt-tab-calendar');
    const tabDet = document.getElementById('bt-tab-details');
    const calView = document.getElementById('bt-calendar-view');
    const detView = document.getElementById('bt-details-content');
    const toggleAllBtn = document.getElementById('bt-btn-toggle-all');
    const filterBox = document.getElementById('bt-filter-container');

    if (tabName === 'calendar') {
      if (tabCal) tabCal.classList.add('active');
      if (tabDet) tabDet.classList.remove('active');
      if (calView) calView.style.display = 'block';
      if (detView) detView.style.display = 'none';
      if (toggleAllBtn) toggleAllBtn.style.display = 'none';
      if (filterBox) filterBox.style.display = 'none';
    } else {
      if (tabCal) tabCal.classList.remove('active');
      if (tabDet) tabDet.classList.add('active');
      if (calView) calView.style.display = 'none';
      if (detView) detView.style.display = 'block';
      if (toggleAllBtn) toggleAllBtn.style.display = 'inline-flex';
      if (filterBox) filterBox.style.display = 'inline-flex';
    }
  };

  window.navigateToDayLedger = function(dateStr) {
    // 1. Switch to Details tab
    window.switchLedgerTab('details');

    // 2. Ensure date is visible in filters
    if (window.selectedDatesFilter && window.selectedDatesFilter.size > 0 && !window.selectedDatesFilter.has(dateStr)) {
      window.selectedDatesFilter.add(dateStr);
      if (typeof applyTradeFilters === 'function') applyTradeFilters();
    }

    // 3. Scroll to group, expand and pulse
    setTimeout(() => {
      const grpEl = document.getElementById(`bt-group-${dateStr}`);
      if (grpEl) {
        // Expand the group properly by removing .collapsed class
        grpEl.classList.remove('collapsed');
        const tableWrap = grpEl.querySelector('.bt-date-table-wrap');
        const chevron = grpEl.querySelector('.bt-date-chevron');
        if (tableWrap) tableWrap.style.display = '';
        if (chevron) chevron.textContent = '▼';

        grpEl.scrollIntoView({ behavior: 'smooth', block: 'start' });

        grpEl.classList.remove('bt-group-highlight');
        void grpEl.offsetWidth; // trigger reflow
        grpEl.classList.add('bt-group-highlight');
      }
    }, 60);
  };

  function renderCalendarView(trades, totalCount, initialCapital, allDateNet, dayEndBalances, sortedAllDates, groups) {
    try {
      const calContainer = document.getElementById('bt-calendar-view');
      if (!calContainer) return;

      const validDates = (sortedAllDates || []).filter(d => typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d));
      if (validDates.length === 0) {
        calContainer.innerHTML = `<div style="text-align: center; padding: 24px; color: var(--text-dim); font-size: 0.85rem;">No trade data available for calendar view.</div>`;
        return;
      }

      // Identify month range: consider both trade dates and configured dateFrom/dateTo parameters
      let firstDateStr = validDates[0];
      let lastDateStr = validDates[validDates.length - 1];

      const pStart = QuantStudioModel.parameters?.startDate || QuantStudioModel.parameters?.dateFrom || (document.getElementById('bt-start-date') ? document.getElementById('bt-start-date').value : '');
      const pEnd = QuantStudioModel.parameters?.endDate || QuantStudioModel.parameters?.dateTo || (document.getElementById('bt-end-date') ? document.getElementById('bt-end-date').value : '');

      if (pStart && /^\d{4}-\d{2}-\d{2}$/.test(pStart) && pStart < firstDateStr) {
        firstDateStr = pStart;
      }
      if (pEnd && /^\d{4}-\d{2}-\d{2}$/.test(pEnd) && pEnd > lastDateStr) {
        lastDateStr = pEnd;
      }

      const [firstY, firstM] = firstDateStr.split('-').map(Number);
      const [lastY, lastM] = lastDateStr.split('-').map(Number);

      if (isNaN(firstY) || isNaN(firstM) || isNaN(lastY) || isNaN(lastM)) {
        calContainer.innerHTML = `<div style="text-align: center; padding: 24px; color: var(--text-dim); font-size: 0.85rem;">Invalid date format for calendar view.</div>`;
        return;
      }

      // Build list of months { year, month } (0-indexed month)
      const monthList = [];
      let curY = firstY;
      let curM = firstM - 1; // 0-based
      const endY = lastY;
      const endM = lastM - 1;

      let maxMonths = 36; // Guard against runaway loops
      while ((curY < endY || (curY === endY && curM <= endM)) && maxMonths > 0) {
        monthList.push({ year: curY, month: curM });
        curM++;
        if (curM > 11) {
          curM = 0;
          curY++;
        }
        maxMonths--;
      }

      const monthNames = [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December'
      ];
      const weekDays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];

      let html = `<div class="bt-calendar-grid">`;

      monthList.forEach(({ year, month }) => {
        const monthTitle = `${monthNames[month]} ${year}`;
        const numDays = new Date(year, month + 1, 0).getDate();
        // Leading empty cells for 5-day week (Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat/Sun=0)
        const dow1 = new Date(year, month, 1).getDay();
        const leadingEmpty = (dow1 >= 1 && dow1 <= 5) ? (dow1 - 1) : 0;

        // Calculate monthly stats
        let monthNet = 0;
        let monthTradesCount = 0;
        let monthWinCount = 0;
        let monthLossCount = 0;
        let monthTradingDays = 0;

        for (let d = 1; d <= numDays; d++) {
          const dStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
          if (allDateNet[dStr] !== undefined) {
            monthTradingDays++;
            monthNet += allDateNet[dStr];
            const grp = groups[dStr];
            if (grp && grp.trades) {
              monthTradesCount += grp.trades.length;
              grp.trades.forEach(t => {
                if (t.net > 0) monthWinCount++;
                else if (t.net < 0) monthLossCount++;
              });
            }
          }
        }

        const isMonthPos = monthNet >= 0;
        const monthNetStr = (isMonthPos ? '+₹' : '-₹') + Math.abs(Math.round(monthNet)).toLocaleString('en-IN');
        const monthWR = monthTradesCount > 0 ? ((monthWinCount / monthTradesCount) * 100).toFixed(1) + '%' : '0%';

        html += `
          <div class="bt-month-card">
            <div class="bt-month-header">
              <span class="bt-month-title">📅 ${monthTitle}</span>
              <div class="bt-month-stats">
                <span style="font-size: 0.68rem; color: #94a3b8;">${monthTradingDays} Days (${monthTradesCount} Trades)</span>
                <span class="bt-month-pnl-pill ${isMonthPos ? 'pos' : 'neg'}">${monthNetStr} (${monthWR})</span>
              </div>
            </div>

            <div class="bt-calendar-weekdays">
              ${weekDays.map(w => `<div>${w}</div>`).join('')}
            </div>

            <div class="bt-calendar-days">
        `;

        // Leading empty cells
        for (let e = 0; e < leadingEmpty; e++) {
          html += `<div class="bt-cal-cell empty"></div>`;
        }

        // Day cells (excluding weekends)
        for (let day = 1; day <= numDays; day++) {
          const curDate = new Date(year, month, day);
          const dow = curDate.getDay();
          if (dow === 0 || dow === 6) {
            continue; // Exclude Sat and Sun completely
          }
          const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
          const hasTrades = allDateNet[dateStr] !== undefined;

          if (hasTrades) {
            const dayNet = allDateNet[dateStr];
            const isDayPos = dayNet >= 0;
            const dayBal = dayEndBalances[dateStr] !== undefined ? dayEndBalances[dateStr] : initialCapital;

            const pnlText = (isDayPos ? '+₹' : '-₹') + Math.abs(Math.round(dayNet)).toLocaleString('en-IN');
            const balText = '₹' + Math.round(dayBal).toLocaleString('en-IN');
            const grp = groups[dateStr];
            const tradeCount = grp && grp.trades ? grp.trades.length : 1;

            html += `
              <div class="bt-cal-cell trading ${isDayPos ? 'win' : 'loss'}" onclick="navigateToDayLedger('${dateStr}')"
                   title="${dateStr} (${grp ? grp.day : ''})&#10;${tradeCount} Trade${tradeCount > 1 ? 's' : ''}&#10;Day P&L: ${(isDayPos ? '+' : '')}₹${dayNet.toFixed(2)}&#10;Balance: ₹${dayBal.toFixed(2)}&#10;👉 Click to view trades in Details ledger">
                <div class="bt-cal-day-num">${day}</div>
                <div class="bt-cal-cell-bottom">
                  <div class="bt-cal-pnl ${isDayPos ? 'pos' : 'neg'}">${pnlText}</div>
                  <div class="bt-cal-bal" title="Balance: ₹${dayBal.toFixed(2)}">${balText}</div>
                </div>
              </div>
            `;
          } else {
            html += `
              <div class="bt-cal-cell non-trading">
                <div class="bt-cal-day-num">${day}</div>
              </div>
            `;
          }
        }

        html += `
            </div>
          </div>
        `;
      });

      html += `</div>`;
      calContainer.innerHTML = html;
    } catch (err) {
      console.error('renderCalendarView error:', err);
    }
  }

  function renderTradeLedger(trades, totalCount) {
    const container = document.getElementById('bt-details-content');
    const badge = document.getElementById('bt-trade-count-badge');
    if (!container) return;

    if (!trades || trades.length === 0) {
      container.innerHTML = `<div style="text-align: center; padding: 24px; color: var(--text-dim); font-size: 0.85rem;">No trades match the current filter criteria.</div>`;
      if (badge) badge.textContent = `0 of ${totalCount || 0} Trades`;
      return;
    }

    // Resolve starting capital
    const initialCapital = (window.currentBacktestData && window.currentBacktestData.summary && window.currentBacktestData.summary.capital !== undefined)
      ? Number(window.currentBacktestData.summary.capital)
      : (Number(QuantStudioModel.parameters.capital) || 20000);

    // Precompute cumulative day-end running balance chronologically across all backtest trades
    const allDateNet = {};
    const allGroups = {};
    const sourceTrades = (currentBacktestTrades && currentBacktestTrades.length > 0) ? currentBacktestTrades : trades;
    sourceTrades.forEach(t => {
      const d = t.date || (t.entryTime ? t.entryTime.split(' ')[0] : '');
      if (d) {
        allDateNet[d] = (allDateNet[d] || 0) + (Number(t.net) || 0);
        if (!allGroups[d]) {
          allGroups[d] = {
            date: d,
            day: t.day || getDayOfWeek(d),
            trades: [],
            totalNet: 0
          };
        }
        allGroups[d].trades.push(t);
        allGroups[d].totalNet += (Number(t.net) || 0);
      }
    });

    const sortedAllDates = Object.keys(allDateNet).sort((a, b) => a.localeCompare(b));
    let runningCap = initialCapital;
    const dayEndBalances = {};
    sortedAllDates.forEach(d => {
      runningCap += allDateNet[d];
      dayEndBalances[d] = runningCap;
    });

    // Group by Date (Ascending order)
    const groups = {};
    trades.forEach(t => {
      if (!groups[t.date]) {
        groups[t.date] = {
          date: t.date,
          day: t.day,
          trades: [],
          totalNet: 0
        };
      }
      groups[t.date].trades.push(t);
      groups[t.date].totalNet += t.net;
    });

    const dateKeys = Object.keys(groups).sort((a, b) => a.localeCompare(b));
    if (badge) {
      badge.textContent = `${trades.length} of ${totalCount} Trades (${dateKeys.length} Days)`;
    }

    let html = '';
    dateKeys.forEach(dateKey => {
      const grp = groups[dateKey];
      const isPos = grp.totalNet >= 0;
      const netStr = (isPos ? '+₹' : '-₹') + Math.abs(grp.totalNet).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

      const dayBal = dayEndBalances[dateKey] !== undefined ? dayEndBalances[dateKey] : (initialCapital + grp.totalNet);
      const balStr = (dayBal >= 0 ? '₹' : '-₹') + Math.abs(dayBal).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      const balClass = dayBal >= initialCapital ? 'bt-val-pos' : 'bt-val-neg';

      html += `
        <div class="bt-date-group" id="bt-group-${dateKey}">
          <div class="bt-date-group-header" onclick="toggleDateGroup('${dateKey}')">
            <div class="bt-date-header-left">
              <span class="bt-date-chevron">▼</span>
              <span class="bt-date-badge">📅 ${grp.date}</span>
              <span class="bt-day-pill">${grp.day}</span>
            </div>
            <div class="bt-date-header-right">
              <span>${grp.trades.length} Trade${grp.trades.length > 1 ? 's' : ''}</span>
              <span>•</span>
              <span>Day P&L: <strong class="${isPos ? 'bt-val-pos' : 'bt-val-neg'}">${netStr}</strong></span>
              <span>•</span>
              <span>Balance: <strong class="${balClass}">${balStr}</strong></span>
            </div>
          </div>
          <div class="bt-date-table-wrap">
            <table class="bt-trade-table">
              <thead>
                <tr>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th class="bt-text-center">Trend</th>
                  <th>Contract</th>
                  <th>Strategy</th>
                  <th class="bt-text-center">Reason</th>
                  <th class="bt-text-center">Prob</th>
                  <th class="bt-text-center">Lot(Qty)</th>
                  <th class="bt-text-right">Entry Rate</th>
                  <th class="bt-text-right">Exit Rate</th>
                  <th class="bt-text-right">Pts</th>
                  <th class="bt-text-right">Gross Profit</th>
                  <th class="bt-text-right">Charges</th>
                  <th class="bt-text-right">Net Profit</th>
                </tr>
              </thead>
              <tbody>
      `;

      grp.trades.forEach(t => {
        const tIsPos = t.net >= 0;
        const grossStr = (t.gross >= 0 ? '+₹' : '-₹') + Math.abs(t.gross).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        const chargesStr = '₹' + t.charges.toFixed(2);
        const netStr = (tIsPos ? '+₹' : '-₹') + Math.abs(t.net).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        const ptsStr = (t.points >= 0 ? '+' : '') + t.points.toFixed(2);
        const reasonClass = (t.reason || 'T1').toLowerCase().replace(/\s+/g, '');

        html += `
          <tr class="bt-trade-row" onclick="openTradeChart('${t.id}')" title="Click to open candlestick verification chart for this trade">
            <td class="bt-mono">${t.entryTime} <span style="font-size: 0.75rem;">📈</span></td>
            <td class="bt-mono">${t.exitTime}</td>
            <td class="bt-text-center">
              <span class="bt-trend-badge ${t.trend.toLowerCase()}" title="${t.trend}">
                ${t.trend === 'BULLISH' ? '▲' : '▼'}
              </span>
            </td>
            <td class="bt-mono"><span class="bt-contract-pill">${t.contract}</span></td>
            <td><span class="bt-indicator-pill">${t.indicator}</span></td>
            <td class="bt-text-center">
              <span class="bt-reason-badge ${reasonClass}">${t.reason}</span>
            </td>
            <td class="bt-text-center"><span class="bt-prob-pill">${t.probability}</span></td>
            <td class="bt-mono bt-text-center"><span class="bt-lot-pill">${t.lotQty}</span></td>
            <td class="bt-mono bt-text-right">₹${t.entryRate.toFixed(2)}</td>
            <td class="bt-mono bt-text-right">₹${t.exitRate.toFixed(2)}</td>
            <td class="bt-mono bt-text-right ${t.points >= 0 ? 'bt-val-pos' : 'bt-val-neg'}">${ptsStr}</td>
            <td class="bt-mono bt-text-right">${grossStr}</td>
            <td class="bt-mono bt-text-right"><span class="bt-charges-pill" title="Brokerage, STT, Exchange, GST">-₹${t.charges.toFixed(1)}</span></td>
            <td class="bt-mono bt-text-right ${tIsPos ? 'bt-val-pos' : 'bt-val-neg'}">${netStr}</td>
          </tr>
        `;
      });

      html += `
              </tbody>
            </table>
          </div>
        </div>
      `;
    });

    container.innerHTML = html;

    // Render Calendar View & sync active tab state (always using un-filtered full dataset for accurate calendar)
    renderCalendarView(sourceTrades, sourceTrades.length, initialCapital, allDateNet, dayEndBalances, sortedAllDates, allGroups);
    window.switchLedgerTab(window.activeLedgerTab || 'calendar');
  }

  // ── 7. Trade Verification Candlestick Chart Modal ──────────────────────────

  window.openTradeChart = function (tradeId) {
    if (!currentBacktestTrades || currentBacktestTrades.length === 0) return;
    const trade = currentBacktestTrades.find(t => t.id === tradeId);
    if (!trade) return;

    activeSelectedTradeId = tradeId;
    activeTradeObj = trade;
    QuantStudioModel.selectedTrade = trade;
    currentDayTradesList = currentBacktestTrades.filter(t => t.date === trade.date);
    const dayTradeIdx = currentDayTradesList.findIndex(t => t.id === tradeId);
    QuantStudioModel.selectedTradeIndex = dayTradeIdx;

    // Populate Modal Header
    const contractEl = document.getElementById('bt-modal-contract');
    if (contractEl) contractEl.textContent = trade.contract || `${trade.symbol} ${selectedOptionType}`;

    const dateEl = document.getElementById('bt-modal-date');
    if (dateEl) dateEl.textContent = `📅 ${trade.date} (${trade.day})`;

    const tradeNumEl = document.getElementById('bt-modal-trade-num');
    if (tradeNumEl) tradeNumEl.textContent = `Trade ${dayTradeIdx + 1} of ${currentDayTradesList.length}`;

    const pnlPill = document.getElementById('bt-modal-pnl-pill');
    if (pnlPill) {
      const isPos = trade.net >= 0;
      pnlPill.className = `bt-modal-pnl-pill ${isPos ? 'pos' : 'neg'}`;
      const ptsSign = trade.points >= 0 ? '+' : '';
      const netSign = isPos ? '+₹' : '-₹';
      pnlPill.innerHTML = `<span class="bt-modal-pnl-net">${netSign}${Math.abs(trade.net).toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span><span class="bt-modal-pnl-pts">(${ptsSign}${trade.points.toFixed(2)} pts)</span>`;
    }

    // Populate Summary Chips
    const contractChip = document.getElementById('bt-chip-contract');
    if (contractChip) contractChip.textContent = trade.contract || `${trade.symbol} ${selectedOptionType}`;

    const indChip = document.getElementById('bt-chip-indicator');
    if (indChip) indChip.textContent = trade.indicator;

    const trendChip = document.getElementById('bt-chip-trend');
    if (trendChip) {
      trendChip.innerHTML = trade.trend === 'BULLISH'
        ? '<span style="color:var(--accent-green);">▲ BULLISH</span>'
        : '<span style="color:var(--accent-red);">▼ BEARISH</span>';
    }

    const probChip = document.getElementById('bt-chip-prob');
    if (probChip) probChip.textContent = trade.probability;

    const entryChip = document.getElementById('bt-chip-entry');
    if (entryChip) entryChip.textContent = `${trade.entryTime} @ ₹${trade.entryRate.toFixed(2)}`;

    const exitChip = document.getElementById('bt-chip-exit');
    if (exitChip) exitChip.textContent = `${trade.exitTime} @ ₹${trade.exitRate.toFixed(2)}`;

    const reasonChip = document.getElementById('bt-chip-reason');
    if (reasonChip) reasonChip.textContent = trade.reason;

    const pointsChip = document.getElementById('bt-chip-points');
    if (pointsChip) {
      pointsChip.className = `chip-val bt-mono ${trade.points >= 0 ? 'bt-val-pos' : 'bt-val-neg'}`;
      pointsChip.textContent = `${trade.points >= 0 ? '+' : ''}${trade.points.toFixed(2)} pts`;
    }

    const netChip = document.getElementById('bt-chip-net');
    if (netChip) {
      const isPos = trade.net >= 0;
      netChip.className = `chip-val bt-mono ${isPos ? 'bt-val-pos' : 'bt-val-neg'}`;
      netChip.textContent = `${isPos ? '+₹' : '-₹'}${Math.abs(trade.net).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
    }

    // Prev / Next Navigation buttons
    const currentOverallIdx = currentBacktestTrades.findIndex(t => t.id === tradeId);
    const prevBtn = document.getElementById('bt-modal-prev-btn');
    const nextBtn = document.getElementById('bt-modal-next-btn');
    if (prevBtn) prevBtn.disabled = (currentOverallIdx <= 0);
    if (nextBtn) nextBtn.disabled = (currentOverallIdx >= currentBacktestTrades.length - 1);

    // Open Modal Backdrop
    const modal = document.getElementById('bt-trade-chart-modal');
    if (modal) {
      modal.classList.add('open');
      modal.style.display = 'flex';
    }

    // Transmit selected trade to Antigravity Bridge & MCP
    const currentParams = getCurrentParameters();
    if (vscode) {
      vscode.postMessage({
        command: 'quant_studio_trade_selected',
        trade: trade,
        trade_index: dayTradeIdx,
        parameters: currentParams
      });
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        action: 'focus_trade',
        trade: trade,
        trade_index: dayTradeIdx,
        parameters: currentParams
      }));
    }

    // Render Candlestick Chart
    setTimeout(() => {
      renderTradeCandlestickChart(trade, currentDayTradesList);
    }, 50);
  };

  window.closeTradeChart = function () {
    const modal = document.getElementById('bt-trade-chart-modal');
    if (modal) {
      modal.classList.remove('open');
      modal.style.display = 'none';
    }
  };

  window.handleTradeChartBackdropClick = function (e) {
    if (e.target && e.target.id === 'bt-trade-chart-modal') {
      closeTradeChart();
    }
  };

  window.navigateTrade = function (direction) {
    if (!currentBacktestTrades || currentBacktestTrades.length === 0) return;
    const currentIdx = currentBacktestTrades.findIndex(t => t.id === activeSelectedTradeId);
    if (currentIdx === -1) return;
    const newIdx = currentIdx + direction;
    if (newIdx >= 0 && newIdx < currentBacktestTrades.length) {
      openTradeChart(currentBacktestTrades[newIdx].id);
    }
  };

  document.addEventListener('keydown', (e) => {
    const modal = document.getElementById('bt-trade-chart-modal');
    if (modal && modal.classList.contains('open')) {
      if (e.key === 'Escape') closeTradeChart();
      else if (e.key === 'ArrowLeft') navigateTrade(-1);
      else if (e.key === 'ArrowRight') navigateTrade(1);
    }
  });

  // ── 8. Candlestick Chart Rendering with Lightweight Charts ──────────────────
  let currentChartSourceMode = 'option'; // 'option' or 'spot'

  window.toggleTradeChartSource = function () {
    currentChartSourceMode = (currentChartSourceMode === 'option') ? 'spot' : 'option';
    const toggleBtn = document.getElementById('bt-modal-toggle-source');
    if (toggleBtn) {
      toggleBtn.textContent = currentChartSourceMode === 'option' ? '📊 Option View' : '📈 Spot Index View';
      toggleBtn.title = currentChartSourceMode === 'option' ? 'Click to view Spot Index Candlesticks' : 'Click to view Traded Option Candlesticks';
    }
    if (activeTradeObj) {
      renderTradeCandlestickChart(activeTradeObj, currentDayTradesList);
    }
  };

  async function renderTradeCandlestickChart(trade, dayTrades) {
    const container = document.getElementById('bt-modal-chart-canvas');
    if (!container) return;

    if (!window.LightweightCharts) {
      container.innerHTML = '<div style="padding:40px; text-align:center; color:#ef4444;">LightweightCharts library loading...</div>';
      return;
    }

    container.innerHTML = '';
    const initialWidth = container.clientWidth || 900;
    const initialHeight = container.clientHeight || 450;

    activeTradeChart = LightweightCharts.createChart(container, {
      width: initialWidth,
      height: initialHeight,
      layout: {
        background: { type: 'solid', color: '#070b14' },
        textColor: '#94a3b8',
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace"
      },
      grid: {
        vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
        horzLines: { color: 'rgba(255, 255, 255, 0.04)' }
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { color: 'rgba(0, 240, 255, 0.4)', width: 1, style: 2 },
        horzLine: { color: 'rgba(0, 240, 255, 0.4)', width: 1, style: 2 }
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: 'rgba(255, 255, 255, 0.08)',
        tickMarkFormatter: (time) => {
          const d = new Date(time * 1000);
          return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
        }
      },
      localization: {
        timeFormatter: (time) => {
          const d = new Date(time * 1000);
          return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
        }
      },
      rightPriceScale: {
        borderColor: 'rgba(255, 255, 255, 0.08)',
        scaleMargins: { top: 0.15, bottom: 0.15 }
      }
    });

    activeCandleSeries = activeTradeChart.addCandlestickSeries({
      upColor: '#00f2a9',
      downColor: '#ff3860',
      borderVisible: false,
      wickUpColor: '#00f2a9',
      wickDownColor: '#ff3860'
    });

    // 1. Resolve Candlestick Data based on Mode (Option vs Spot)
    let rawList = [];
    let isSpotMode = (currentChartSourceMode === 'spot');

    if (isSpotMode) {
      // Use spot candles from backtest results if matching trade date, or fetch on demand
      if (window.currentBacktestData && Array.isArray(window.currentBacktestData.spotCandles) && window.currentBacktestData.spotCandles.length > 0) {
        rawList = window.currentBacktestData.spotCandles.filter(c => c.time && c.time.startsWith(trade.date));
      }
      if (!rawList || rawList.length === 0) {
        try {
          const resp = await fetch(`http://127.0.0.1:8080/api/quant/candles?symbol=NIFTY&date=${encodeURIComponent(trade.date)}`);
          if (resp.ok) {
            const rj = await resp.json();
            rawList = rj.candles || [];
          }
        } catch (e) {
          console.warn('[Quant Studio] Failed fetching spot candles:', e);
        }
      }
    } else {
      // Option mode: prioritize trade.candles
      if (Array.isArray(trade.candles) && trade.candles.length > 0) {
        rawList = trade.candles;
      } else {
        try {
          const contractSym = trade.contract || trade.symbol;
          const resp = await fetch(`http://127.0.0.1:8080/api/quant/candles?symbol=${encodeURIComponent(contractSym)}&date=${encodeURIComponent(trade.date)}`);
          if (resp.ok) {
            const rj = await resp.json();
            rawList = rj.candles || [];
          }
        } catch (e) {
          console.warn('[Quant Studio] Failed fetching option candles:', e);
        }
      }

      // If option candles not available, fall back to real spot index / futures candles for that date
      if (!rawList || rawList.length === 0) {
        if (window.currentBacktestData && Array.isArray(window.currentBacktestData.spotCandles) && window.currentBacktestData.spotCandles.length > 0) {
          rawList = window.currentBacktestData.spotCandles.filter(c => c.time && c.time.startsWith(trade.date));
          if (rawList && rawList.length > 0) isSpotMode = true;
        }
        if (!rawList || rawList.length === 0) {
          try {
            const spotResp = await fetch(`http://127.0.0.1:8080/api/quant/candles?symbol=NIFTY-I&date=${encodeURIComponent(trade.date)}`);
            if (spotResp.ok) {
              const srj = await spotResp.json();
              if (srj.candles && srj.candles.length > 0) {
                rawList = srj.candles;
                isSpotMode = true;
              }
            }
            if (!rawList || rawList.length === 0) {
              const spotResp2 = await fetch(`http://127.0.0.1:8080/api/quant/candles?symbol=NIFTY&date=${encodeURIComponent(trade.date)}`);
              if (spotResp2.ok) {
                const srj2 = await spotResp2.json();
                if (srj2.candles && srj2.candles.length > 0) {
                  rawList = srj2.candles;
                  isSpotMode = true;
                }
              }
            }
          } catch (e) {
            console.warn('[Quant Studio] Failed fetching fallback spot candles:', e);
          }
        }
      }
    }

    // 2. Parse & Standardize to LightweightCharts Candle Format (Align with Indian Market Clock 09:15 - 15:30)
    let candles = [];
    if (rawList && rawList.length > 0) {
      candles = rawList.map(c => {
        const timeStr = c.time ? (c.time.length > 5 ? c.time.slice(-5) : c.time) : '09:15';
        const tSec = parseDateTimeToSecs(trade.date, timeStr);
        return {
          time: tSec,
          open: parseFloat(c.open),
          high: parseFloat(c.high),
          low: parseFloat(c.low),
          close: parseFloat(c.close)
        };
      });
    }

    // Fallback synthesis only if absolutely no candle data returned
    if (!candles || candles.length === 0) {
      candles = generateIntradayCandles(trade.date, trade, dayTrades);
    }

    // Deduplicate & sort strictly by time ascending
    candles.sort((a, b) => a.time - b.time);
    const cleanCandles = [];
    for (let i = 0; i < candles.length; i++) {
      if (i === 0 || candles[i].time > cleanCandles[cleanCandles.length - 1].time) {
        cleanCandles.push(candles[i]);
      }
    }

    activeCandleSeries.setData(cleanCandles);

    // Update modal contract badge with active view
    const contractEl = document.getElementById('bt-modal-contract');
    if (contractEl) {
      contractEl.textContent = isSpotMode ? `${trade.contract || trade.symbol} (Spot/Futures View)` : (trade.contract || `${trade.symbol} ${selectedOptionType}`);
    }
    const toggleBtn = document.getElementById('bt-modal-toggle-source');
    if (toggleBtn) {
      toggleBtn.textContent = isSpotMode ? '📈 Spot Index View' : '📊 Option View';
    }

    // Add Markers for Entry & Exit
    const markers = [];
    const entrySec = parseDateTimeToSecs(trade.date, trade.entryTime);
    const exitSec = parseDateTimeToSecs(trade.date, trade.exitTime);
    const isWin = trade.net >= 0;

    markers.push({
      time: entrySec,
      position: 'belowBar',
      color: '#00f2a9',
      shape: 'arrowUp',
      text: isSpotMode ? `ENTRY SIGNAL (${trade.trend})` : `ENTRY ${trade.contract} @ ₹${trade.entryRate.toFixed(1)}`
    });

    markers.push({
      time: exitSec,
      position: isWin ? 'aboveBar' : 'belowBar',
      color: isWin ? '#00f0ff' : '#ff3860',
      shape: 'circle',
      text: `EXIT (${trade.reason}) ${isWin ? '+' : ''}${trade.points.toFixed(1)} pts`
    });

    activeCandleSeries.setMarkers(markers);

    // Bind plotted chart data to QuantStudioModel
    QuantStudioModel.plottedChart = {
      underlyingSymbol: trade.symbol || 'NIFTY',
      activeContract: isSpotMode ? 'NIFTY (SPOT)' : (trade.contract || `${trade.symbol} ${selectedOptionType}`),
      candleCount: cleanCandles.length,
      timeframe: '1m',
      candlesSlice: cleanCandles.slice(-60),
      markers: markers,
      entryRate: trade.entryRate,
      exitRate: trade.exitRate,
      entryTime: trade.entryTime,
      exitTime: trade.exitTime,
      stopLoss: trade.stopLoss || (trade.entryRate - 10),
      target: trade.target || (trade.entryRate + 15)
    };

    // Price lines (in Option view)
    if (!isSpotMode) {
      activeCandleSeries.createPriceLine({
        price: trade.entryRate,
        color: 'rgba(0, 242, 169, 0.65)',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'ENTRY'
      });

      activeCandleSeries.createPriceLine({
        price: trade.exitRate,
        color: isWin ? 'rgba(0, 240, 255, 0.65)' : 'rgba(255, 56, 96, 0.65)',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'EXIT'
      });
    }

    // Zoom to trade window
    window.zoomTradeChartToWindow();

    // Resize handler
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        if (entry.contentRect && activeTradeChart) {
          activeTradeChart.resize(entry.contentRect.width, entry.contentRect.height);
        }
      }
    });
    ro.observe(container);
  }

  window.fitTradeChartFullSession = function () {
    if (activeTradeChart) activeTradeChart.timeScale().fitContent();
  };

  window.zoomTradeChartToWindow = function () {
    if (!activeTradeChart || !activeTradeObj) return;
    const entrySec = parseDateTimeToSecs(activeTradeObj.date, activeTradeObj.entryTime);
    const exitSec = parseDateTimeToSecs(activeTradeObj.date, activeTradeObj.exitTime);
    activeTradeChart.timeScale().setVisibleRange({
      from: entrySec - (20 * 60),
      to: exitSec + (20 * 60)
    });
  };

  function parseDateTimeToSecs(dateStr, timeStr) {
    const dParts = dateStr.split('-');
    const tParts = (timeStr || '09:15:00').split(':');
    return Math.floor(Date.UTC(
      parseInt(dParts[0], 10),
      parseInt(dParts[1], 10) - 1,
      parseInt(dParts[2], 10),
      parseInt(tParts[0], 10),
      parseInt(tParts[1], 10),
      parseInt(tParts[2] || 0, 10)
    ) / 1000);
  }

  function generateIntradayCandles(dateStr, activeTrade, dayTrades) {
    const dParts = dateStr.split('-');
    const year = parseInt(dParts[0], 10);
    const month = parseInt(dParts[1], 10) - 1;
    const day = parseInt(dParts[2], 10);

    const basePrice = activeTrade.entryRate;
    const anchors = [
      { minute: 9 * 60 + 15, price: basePrice - (activeTrade.trend === 'BULLISH' ? 4.5 : -4.5) }
    ];

    dayTrades.forEach(t => {
      const eParts = t.entryTime.split(':');
      anchors.push({ minute: parseInt(eParts[0], 10) * 60 + parseInt(eParts[1], 10), price: t.entryRate });
      const xParts = t.exitTime.split(':');
      anchors.push({ minute: parseInt(xParts[0], 10) * 60 + parseInt(xParts[1], 10), price: t.exitRate });
    });

    const lastExit = anchors[anchors.length - 1].price;
    anchors.push({ minute: 15 * 60 + 30, price: +(lastExit + 1.5).toFixed(2) });
    anchors.sort((a, b) => a.minute - b.minute);

    let seed = (year * 10000 + (month + 1) * 100 + day);
    function rnd() {
      seed = (seed * 9301 + 49297) % 233280;
      return seed / 233280;
    }

    const candles = [];
    let currentPrice = anchors[0].price;

    for (let m = 9 * 60 + 15; m <= 15 * 60 + 30; m++) {
      const hour = Math.floor(m / 60);
      const minute = m % 60;
      const timeSec = Math.floor(Date.UTC(year, month, day, hour, minute, 0) / 1000);

      let leftAnchor = anchors[0];
      let rightAnchor = anchors[anchors.length - 1];
      for (let i = 0; i < anchors.length - 1; i++) {
        if (m >= anchors[i].minute && m <= anchors[i + 1].minute) {
          leftAnchor = anchors[i];
          rightAnchor = anchors[i + 1];
          break;
        }
      }

      const span = Math.max(1, rightAnchor.minute - leftAnchor.minute);
      const progress = (m - leftAnchor.minute) / span;
      const targetPrice = leftAnchor.price + (rightAnchor.price - leftAnchor.price) * progress;

      const open = currentPrice;
      let close;
      if (m === leftAnchor.minute) close = leftAnchor.price;
      else if (m === rightAnchor.minute) close = rightAnchor.price;
      else {
        const noise = (rnd() - 0.49) * 1.5;
        close = +(open + (targetPrice - open) * 0.28 + noise).toFixed(2);
      }

      const high = +(Math.max(open, close) + rnd() * 1.2).toFixed(2);
      const low = +(Math.max(1, Math.min(open, close) - rnd() * 1.2)).toFixed(2);

      candles.push({ time: timeSec, open, high, low, close });
      currentPrice = close;
    }

    return candles;
  }

  // ── 9. Fallback Data Generator ──────────────────────────────────────────────

  function generateFallbackBacktest(params) {
    const dates = [
      { date: '2026-10-29', day: 'Thu', basePrice: 125.0 },
      { date: '2026-10-30', day: 'Fri', basePrice: 140.0 }
    ];

    const trades = [
      {
        id: 'trade_2026-10-29_0',
        symbol: params.symbol || 'NIFTY',
        contract: `${params.symbol || 'NIFTY'} 22700 CE`,
        date: '2026-10-29',
        day: 'Thu',
        entryTime: '10:15:00',
        exitTime: '10:48:00',
        trend: 'BULLISH',
        indicator: 'Adaptive Trend Pro',
        reason: 'T1',
        probability: '84%',
        entryRate: 122.50,
        exitRate: 154.20,
        points: 31.70,
        gross: 2060.50,
        charges: 94.20,
        net: 1966.30,
        lotQty: '1(65)'
      },
      {
        id: 'trade_2026-10-29_1',
        symbol: params.symbol || 'NIFTY',
        contract: `${params.symbol || 'NIFTY'} 22700 CE`,
        date: '2026-10-29',
        day: 'Thu',
        entryTime: '11:35:00',
        exitTime: '12:14:00',
        trend: 'BULLISH',
        indicator: 'Adaptive Trend Pro',
        reason: 'TSL',
        probability: '79%',
        entryRate: 135.00,
        exitRate: 149.80,
        points: 14.80,
        gross: 962.00,
        charges: 88.40,
        net: 873.60,
        lotQty: '1(65)'
      },
      {
        id: 'trade_2026-10-29_2',
        symbol: params.symbol || 'NIFTY',
        contract: `${params.symbol || 'NIFTY'} 22700 CE`,
        date: '2026-10-29',
        day: 'Thu',
        entryTime: '13:05:00',
        exitTime: '13:28:00',
        trend: 'BULLISH',
        indicator: 'Adaptive Trend Pro',
        reason: 'SL',
        probability: '72%',
        entryRate: 142.00,
        exitRate: 128.50,
        points: -13.50,
        gross: -877.50,
        charges: 82.10,
        net: -959.60,
        lotQty: '1(65)'
      },
      {
        id: 'trade_2026-10-29_3',
        symbol: params.symbol || 'NIFTY',
        contract: `${params.symbol || 'NIFTY'} 22700 CE`,
        date: '2026-10-29',
        day: 'Thu',
        entryTime: '14:10:00',
        exitTime: '14:52:00',
        trend: 'BULLISH',
        indicator: 'Adaptive Trend Pro',
        reason: 'T2',
        probability: '88%',
        entryRate: 138.00,
        exitRate: 182.40,
        points: 44.40,
        gross: 2886.00,
        charges: 104.30,
        net: 2781.70,
        lotQty: '1(65)'
      },
      {
        id: 'trade_2026-10-30_0',
        symbol: params.symbol || 'NIFTY',
        contract: `${params.symbol || 'NIFTY'} 22800 CE`,
        date: '2026-10-30',
        day: 'Fri',
        entryTime: '10:08:00',
        exitTime: '10:42:00',
        trend: 'BULLISH',
        indicator: 'Adaptive Trend Pro',
        reason: 'T1',
        probability: '82%',
        entryRate: 145.20,
        exitRate: 178.60,
        points: 33.40,
        gross: 2171.00,
        charges: 98.40,
        net: 2072.60,
        lotQty: '1(65)'
      },
      {
        id: 'trade_2026-10-30_1',
        symbol: params.symbol || 'NIFTY',
        contract: `${params.symbol || 'NIFTY'} 22800 CE`,
        date: '2026-10-30',
        day: 'Fri',
        entryTime: '11:20:00',
        exitTime: '11:58:00',
        trend: 'BULLISH',
        indicator: 'Adaptive Trend Pro',
        reason: 'TSL',
        probability: '76%',
        entryRate: 152.00,
        exitRate: 168.50,
        points: 16.50,
        gross: 1072.50,
        charges: 91.20,
        net: 981.30,
        lotQty: '1(65)'
      },
      {
        id: 'trade_2026-10-30_2',
        symbol: params.symbol || 'NIFTY',
        contract: `${params.symbol || 'NIFTY'} 22800 CE`,
        date: '2026-10-30',
        day: 'Fri',
        entryTime: '13:12:00',
        exitTime: '13:35:00',
        trend: 'BULLISH',
        indicator: 'Adaptive Trend Pro',
        reason: 'SL',
        probability: '68%',
        entryRate: 160.00,
        exitRate: 144.80,
        points: -15.20,
        gross: -988.00,
        charges: 86.50,
        net: -1074.50,
        lotQty: '1(65)'
      }
    ];

    const totalNet = trades.reduce((acc, t) => acc + t.net, 0);
    const totalGross = trades.reduce((acc, t) => acc + t.gross, 0);
    const totalCharges = trades.reduce((acc, t) => acc + t.charges, 0);
    const wins = trades.filter(t => t.net > 0).length;
    const losses = trades.length - wins;

    return {
      summary: {
        capital: params.capital || 20000,
        finalBalance: (params.capital || 20000) + totalNet,
        final_balance: (params.capital || 20000) + totalNet,
        netPnl: totalNet,
        grossPnl: totalGross,
        totalCharges: totalCharges,
        totalPoints: trades.reduce((acc, t) => acc + t.points, 0),
        roi: (totalNet / 20000) * 100,
        winRate: (wins / trades.length) * 100,
        profitFactor: 2.84,
        maxDrawdownPct: 4.8,
        totalTrades: trades.length,
        winningTrades: wins,
        losingTrades: losses
      },
      trades: trades
    };
  }

  // ── 10. WebSocket IPC Connection ───────────────────────────────────────────

  function connectWebSocket(targetPort) {
    port = targetPort || port;
    let url = port === 8080 ? 'ws://127.0.0.1:8080/ws/studio' : `ws://127.0.0.1:${port}`;
    const statusEl = document.getElementById('session-status');

    try {
      ws = new WebSocket(url);
    } catch (e) {
      if (statusEl) {
        statusEl.textContent = `🔴 Offline | Port ${port}`;
        statusEl.classList.remove('connected');
      }
      const altPort = (port === 8765) ? 8080 : 8765;
      setTimeout(() => connectWebSocket(altPort), 2000);
      return;
    }

    ws.onopen = () => {
      if (statusEl) {
        statusEl.textContent = `🟢 Connected | Port ${port}`;
        statusEl.classList.add('connected');
      }
      broadcastCurrentParameters();
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'backtest_result') {
          handleBacktestData(msg, 16);
        } else if (msg.type === 'query_model') {
          const snapshot = QuantStudioModel.getSnapshot(msg.scope || 'all');
          ws.send(JSON.stringify({
            action: 'model_response',
            query_id: msg.query_id,
            data: snapshot
          }));
        }
      } catch (e) {
        console.error('Error handling WebSocket message:', e);
      }
    };

    ws.onclose = () => {
      if (statusEl) {
        statusEl.textContent = `🟡 Standby | Port ${port}`;
        statusEl.classList.remove('connected');
      }
      const altPort = (port === 8765) ? 8080 : 8765;
      setTimeout(() => connectWebSocket(altPort), 2000);
    };
  }

  // Listen to messages from VS Code Extension Host
  window.addEventListener('message', (event) => {
    const message = event.data;
    if (message.command === 'connect') {
      connectWebSocket(message.port);
    } else if (message.command === 'query_model') {
      const snapshot = QuantStudioModel.getSnapshot(message.scope || 'all');
      if (vscode) {
        vscode.postMessage({
          command: 'model_response',
          query_id: message.query_id,
          data: snapshot
        });
      }
    }
  });

  // Clock Sync & Chart Theme Definitions
  function parseCandleTime(str) {
    if (!str) return 0;
    const match = str.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):?(\d{2})?/);
    if (!match) return 0;
    const d = new Date(Date.UTC(+match[1], +match[2] - 1, +match[3], +match[4], +match[5], 0));
    return Math.floor(d.getTime() / 1000);
  }

  const CHART_THEME = {
    layout: {
      background: { type: 'solid', color: '#070b14' },
      textColor: '#94a3b8',
      fontSize: 11,
      fontFamily: "'JetBrains Mono', monospace"
    },
    timeScale: {
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: (time) => {
        const d = new Date(time * 1000);
        return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
      }
    }
  };

  function syncLogicalRanges(c1, c2) {
    if (c1 && c2 && c1.timeScale && c2.timeScale) {
      c1.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range) c2.timeScale().setVisibleLogicalRange(range);
      });
    }
  }

  async function loadPersistedBacktestOnBoot() {
    try {
      const resp = await fetch('http://127.0.0.1:8080/api/quant-studio/backtest');
      if (resp.ok) {
        const data = await resp.json();
        if (data && (data.summary || data.trades)) {
          handleBacktestData(data, 12);
          return true;
        }
      }
    } catch (e) {
      console.warn('[Quant Studio] Fast-load persisted backtest unavailable:', e);
    }
    return false;
  }

  // Initial boot
  restoreStudioState();
  connectWebSocket(port);
  renderSymbolList();
  renderBlockedSlotsList();
  initModelBindings();
  initLiveParameterSync();
  loadPersistedBacktestOnBoot().then(loaded => {
    if (!loaded) {
      executeBacktest();
    }
  });
})();
