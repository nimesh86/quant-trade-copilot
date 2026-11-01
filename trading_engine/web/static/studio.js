/**
 * Indicator Studio & Replay Debugger Frontend Controller
 * Connects Monaco Editor, Lightweight Charts, REST APIs, and WebSocket Replay Channel.
 * Implements VSCode-style interactive debugging, breakpoints, watch inspector, and asset management.
 */

(function () {
    let editor = null;
    let chartMgr = null;
    let currentSessionId = null;
    let ws = null;
    let activePreset = "bss_pro_v5";
    let isPlaying = false;
    let currentChartMode = "spot";
    let cachedFrames = [];
    let activeBreakpoints = new Set();
    let breakpointDecorations = [];
    let debugLineDecorations = [];
    let customWatchExpressions = [];

    // DOM Elements
    const gutter = document.getElementById("split-gutter");
    const topPanel = document.getElementById("panel-chart");
    const bottomPanel = document.getElementById("panel-editor");
    const fallbackTextarea = document.getElementById("editor-fallback");
    const watchTableBody = document.getElementById("watch-table-body");
    const inputsContainer = document.getElementById("dynamic-inputs-container");
    const consoleLogs = document.getElementById("console-logs");
    const compilationBadge = document.getElementById("compilation-badge");
    const scrubber = document.getElementById("replay-scrubber");
    const scrubberBadge = document.getElementById("replay-bar-info");
    const playBtn = document.getElementById("btn-replay-play");
    const chkBreakSignal = document.getElementById("chk-break-signal");
    const selectPreset = document.getElementById("select-preset");
    const selectDate = document.getElementById("select-replay-date");
    const indicatorNameInput = document.getElementById("indicator-name-input");
    const dbgStatusPill = document.getElementById("dbg-status-pill");

    // Modal Elements
    const saveModal = document.getElementById("save-modal");
    const modalIndicatorName = document.getElementById("modal-indicator-name");
    const modalIndicatorDesc = document.getElementById("modal-indicator-desc");
    const modalCloseBtn = document.getElementById("modal-close-btn");
    const modalCancelBtn = document.getElementById("modal-cancel-btn");
    const modalConfirmSaveBtn = document.getElementById("modal-confirm-save-btn");

    // Initialize application on DOM ready
    window.addEventListener("DOMContentLoaded", async () => {
        initSplitter();
        initChart();
        initMonaco();
        initWebSocket();
        initControls();
        await initAvailableDates();
        loadUserIndicators();
        loadPreset(activePreset);
    });

    // ── 1. Split-Screen Draggable Resizer ──────────────────────────────────
    function initSplitter() {
        const savedRatio = localStorage.getItem("studio_split_ratio");
        if (savedRatio) {
            topPanel.style.height = `${parseFloat(savedRatio) * 100}%`;
        }

        let isDragging = false;

        gutter.addEventListener("mousedown", (e) => {
            isDragging = true;
            gutter.classList.add("dragging");
            document.body.style.cursor = "row-resize";
        });

        window.addEventListener("mousemove", (e) => {
            if (!isDragging) return;
            const headerHeight = 48;
            const containerHeight = window.innerHeight - headerHeight;
            const minHeight = 120; // Low minimum allowing wide split adjustment

            const newTopHeight = Math.max(minHeight, Math.min(containerHeight - minHeight, e.clientY - headerHeight));
            const ratio = newTopHeight / containerHeight;
            topPanel.style.height = `${ratio * 100}%`;
            localStorage.setItem("studio_split_ratio", ratio.toFixed(4));

            if (chartMgr) chartMgr.resize();
            if (editor) editor.layout();
        });

        window.addEventListener("mouseup", () => {
            if (isDragging) {
                isDragging = false;
                gutter.classList.remove("dragging");
                document.body.style.cursor = "default";
                if (chartMgr) {
                    chartMgr.resize();
                    chartMgr.fitContent();
                }
                if (editor) editor.layout();
            }
        });

        // Double click resets to 50/50
        gutter.addEventListener("dblclick", () => {
            topPanel.style.height = "50%";
            localStorage.setItem("studio_split_ratio", "0.5");
            if (chartMgr) {
                chartMgr.resize();
                chartMgr.fitContent();
            }
            if (editor) editor.layout();
        });

        window.addEventListener("resize", () => {
            if (chartMgr) chartMgr.resize();
            if (editor) editor.layout();
        });
    }

    // ── 2. Chart Initialization ───────────────────────────────────────────
    function initChart() {
        chartMgr = new StudioChartManager("chart-container", (time, price) => {
            updateWatchTableFromHover(time, price);
        });
        window.chartMgr = chartMgr;

        document.querySelectorAll(".chart-tab").forEach(tab => {
            tab.addEventListener("click", () => {
                document.querySelectorAll(".chart-tab").forEach(t => t.classList.remove("active"));
                tab.classList.add("active");
                currentChartMode = tab.dataset.chart;
                if (cachedFrames.length > 0) {
                    chartMgr.setCandles(cachedFrames, currentChartMode);
                    const lastFrame = cachedFrames[cachedFrames.length - 1];
                    updateChartLegend(lastFrame);
                    if (chartMgr) chartMgr.fitContent();
                }
            });
        });

        document.getElementById("btn-fit-chart").addEventListener("click", () => {
            if (chartMgr) chartMgr.fitContent();
        });
    }

    function updateWatchTableFromHover(timeVal, price) {
        const row = cachedFrames.find(f => {
            const t = chartMgr ? chartMgr._parseTime(f.timestamp) : f.timestamp;
            return t === timeVal;
        });

        if (row && row.variable_watch) {
            renderWatchTable(row.variable_watch);
            updateChartLegend(row);
        } else if (price) {
            renderWatchTable({
                "hover_open": price.open,
                "hover_high": price.high,
                "hover_low": price.low,
                "hover_close": price.close
            });
            const elOhlc = document.getElementById("legend-ohlc");
            if (elOhlc) {
                elOhlc.textContent = `O: ${Number(price.open).toFixed(2)}  H: ${Number(price.high).toFixed(2)}  L: ${Number(price.low).toFixed(2)}  C: ${Number(price.close).toFixed(2)}`;
            }
        }
    }

    // ── 3. Monaco Editor with Breakpoints & Font Alignment ────────────────
    function initMonaco() {
        if (!window.require) {
            fallbackTextarea.style.display = "block";
            return;
        }

        require.config({ paths: { vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs" } });
        require(["vs/editor/editor.main"], function () {
            // Register custom dark theme
            monaco.editor.defineTheme("quant-dark", {
                base: "vs-dark",
                inherit: true,
                rules: [
                    { token: "keyword", foreground: "00f0ff", fontStyle: "bold" },
                    { token: "identifier", foreground: "f0f4fc" },
                    { token: "string", foreground: "10b981" },
                    { token: "number", foreground: "f59e0b" },
                    { token: "comment", foreground: "64748b", fontStyle: "italic" }
                ],
                colors: {
                    "editor.background": "#070b14",
                    "editor.foreground": "#f0f4fc",
                    "editor.lineHighlightBackground": "#0f172a",
                    "editorLineNumber.foreground": "#475569",
                    "editorLineNumber.activeForeground": "#00f0ff",
                    "editorCursor.foreground": "#00f0ff",
                    "editorGutter.background": "#070b14"
                }
            });

            // Autocompletion provider
            monaco.languages.registerCompletionItemProvider("python", {
                provideCompletionItems: function (model, position) {
                    const textUntilPosition = model.getValueInRange({
                        startLineNumber: position.lineNumber,
                        startColumn: 1,
                        endLineNumber: position.lineNumber,
                        endColumn: position.column
                    });

                    const suggestions = [];

                    if (textUntilPosition.endsWith("ta.")) {
                        const taFunctions = [
                            { label: "ema", insertText: "ema(${1:series}, ${2:length})", detail: "Exponential Moving Average" },
                            { label: "sma", insertText: "sma(${1:series}, ${2:length})", detail: "Simple Moving Average" },
                            { label: "rsi", insertText: "rsi(${1:series}, ${2:length=14})", detail: "Relative Strength Index" },
                            { label: "dynamic_sr", insertText: "dynamic_sr(${1:open}, ${2:high}, ${3:low}, ${4:close})", detail: "Dynamic S/R Body Clustering" },
                            { label: "breakout_channels", insertText: "breakout_channels(${1:open}, ${2:high}, ${3:low}, ${4:close}, box_len=${5:14})", detail: "Consolidation Box Breakout" },
                            { label: "supertrend", insertText: "supertrend(${1:high}, ${2:low}, ${3:close}, ${4:period=10}, ${5:multiplier=3.0})", detail: "Supertrend Cloud" },
                            { label: "crossover", insertText: "crossover(${1:series_a}, ${2:series_b})", detail: "Series A crosses above B" },
                            { label: "crossunder", insertText: "crossunder(${1:series_a}, ${2:series_b})", detail: "Series A crosses below B" }
                        ];
                        taFunctions.forEach(f => {
                            suggestions.push({
                                label: f.label,
                                kind: monaco.languages.CompletionItemKind.Function,
                                insertText: f.insertText,
                                insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                                detail: f.detail
                            });
                        });
                    } else if (textUntilPosition.endsWith("input.")) {
                        ["int", "float", "bool", "string"].forEach(type => {
                            suggestions.push({
                                label: type,
                                kind: monaco.languages.CompletionItemKind.Method,
                                insertText: `${type}(\${1:default}, "\${2:Title}")`,
                                insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                                detail: `Declare interactive ${type} parameter control`
                            });
                        });
                    }

                    return { suggestions: suggestions };
                }
            });

            // Create Monaco Editor with explicit line height and breakpoint gutter
            editor = monaco.editor.create(document.getElementById("editor-container"), {
                value: "# Quant Studio IDE Loading...",
                language: "python",
                theme: "quant-dark",
                fontSize: 13,
                lineHeight: 20, // Prevents line number highlight desync
                fontFamily: "'JetBrains Mono', 'Fira Code', Menlo, Monaco, monospace",
                lineNumbers: "on",
                lineNumbersMinChars: 3,
                glyphMargin: true, // Enables breakpoint gutter
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                automaticLayout: true
            });

            // Resynchronize font metrics once web fonts finish loading
            if (document.fonts && document.fonts.ready) {
                document.fonts.ready.then(() => {
                    monaco.editor.remeasureFonts();
                });
            }

            // Gutter Breakpoint Toggle
            editor.onMouseDown((e) => {
                if (e.target.type === monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN ||
                    e.target.type === monaco.editor.MouseTargetType.GUTTER_LINE_NUMBERS) {
                    const line = e.target.position.lineNumber;
                    toggleBreakpoint(line);
                }
            });

            // Real-time inline syntax validation debounced
            let debounceTimer = null;
            editor.onDidChangeModelContent(() => {
                clearTimeout(debounceTimer);
                debounceTimer = setTimeout(validateCode, 400);
            });

            if (activePreset) {
                loadPreset(activePreset);
            }
        });
    }

    function toggleBreakpoint(line) {
        if (activeBreakpoints.has(line)) {
            activeBreakpoints.delete(line);
            logConsole(`[Breakpoint Removed] Line ${line}`);
        } else {
            activeBreakpoints.add(line);
            logConsole(`[Breakpoint Set] Line ${line}`);
        }
        updateBreakpointDecorations();
        renderBreakpointsList();

        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                action: "UPDATE_BREAKPOINTS",
                breakpoints: Array.from(activeBreakpoints)
            }));
        }
    }
    window.toggleBreakpoint = toggleBreakpoint;

    function updateBreakpointDecorations() {
        if (!editor) return;
        const decorations = Array.from(activeBreakpoints).map(line => ({
            range: new monaco.Range(line, 1, line, 1),
            options: {
                isWholeLine: false,
                glyphMarginClassName: "debug-breakpoint-glyph",
                glyphMarginHoverMessage: { value: `Breakpoint at Line ${line}` }
            }
        }));
        breakpointDecorations = editor.deltaDecorations(breakpointDecorations, decorations);
    }

    function renderBreakpointsList() {
        const container = document.getElementById("breakpoints-list");
        if (activeBreakpoints.size === 0) {
            container.innerHTML = '<span class="bp-hint">Click gutter next to line numbers to set breakpoint.</span>';
            return;
        }

        container.innerHTML = "";
        Array.from(activeBreakpoints).sort((a, b) => a - b).forEach(line => {
            const item = document.createElement("div");
            item.className = "bp-item";
            item.innerHTML = `<span class="bp-dot"></span><span>Line ${line}</span>`;
            item.addEventListener("click", () => {
                if (editor) {
                    editor.revealLineInCenter(line);
                    editor.setPosition({ lineNumber: line, column: 1 });
                }
            });
            container.appendChild(item);
        });
    }

    function highlightPausedLine(line) {
        if (!editor || !line) return;
        editor.revealLineInCenter(line);
        editor.setPosition({ lineNumber: line, column: 1 });
        debugLineDecorations = editor.deltaDecorations(debugLineDecorations, [{
            range: new monaco.Range(line, 1, line, 1),
            options: {
                isWholeLine: true,
                className: "debug-line-highlight",
                glyphMarginClassName: "debug-current-line-arrow",
                glyphMarginHoverMessage: { value: `Execution paused at Line ${line}` }
            }
        }]);
    }

    function clearPausedLine() {
        if (!editor) return;
        debugLineDecorations = editor.deltaDecorations(debugLineDecorations, []);
    }

    function getCode() {
        return editor ? editor.getValue() : fallbackTextarea.value;
    }

    function setCode(code) {
        if (editor) {
            editor.setValue(code);
        } else {
            fallbackTextarea.value = code;
        }
    }

    // ── 4. Real-time Syntax Validation & Error Squigglies ─────────────────
    async function validateCode() {
        const code = getCode();
        if (!code || code.trim().length === 0) return;

        try {
            const resp = await fetch("/api/studio/validate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ code })
            });
            const data = await resp.json();

            const errBanner = document.getElementById("compilation-error-banner");
            const errText = document.getElementById("compilation-error-text");
            const btnJump = document.getElementById("btn-jump-to-error");

            if (data.valid) {
                compilationBadge.textContent = "AST: OK (Valid)";
                compilationBadge.style.color = "var(--green-bull)";
                if (errBanner) errBanner.classList.remove("active");
                if (editor) {
                    monaco.editor.setModelMarkers(editor.getModel(), "quant_compiler", []);
                }
            } else {
                const firstErr = (data.errors && data.errors[0]) || { line: 1, col: 1, message: "Syntax error" };
                compilationBadge.textContent = `Syntax Error: Line ${firstErr.line || 1}`;
                compilationBadge.style.color = "var(--red-bear)";

                if (errBanner && errText) {
                    errText.textContent = `Line ${firstErr.line}: ${firstErr.message}`;
                    errBanner.classList.add("active");
                    if (btnJump) {
                        btnJump.textContent = `🎯 Jump to Line ${firstErr.line}`;
                        btnJump.onclick = () => {
                            if (editor) {
                                editor.revealLineInCenter(firstErr.line);
                                editor.setPosition({ lineNumber: firstErr.line, column: firstErr.col || 1 });
                                editor.focus();
                            }
                        };
                    }
                }

                if (editor && data.errors) {
                    const markers = data.errors.map(err => ({
                        severity: monaco.MarkerSeverity.Error,
                        message: err.message,
                        startLineNumber: err.line || 1,
                        startColumn: err.col || 1,
                        endLineNumber: err.line || 1,
                        endColumn: 100
                    }));
                    monaco.editor.setModelMarkers(editor.getModel(), "quant_compiler", markers);
                }
            }
        } catch (e) {
            console.error("Validation error:", e);
        }
    }

    // ── 5. Presets, Custom Indicators & Dynamic Inputs ────────────────────
    async function loadPreset(presetId) {
        if (!presetId) return;
        try {
            const resp = await fetch("/api/studio/presets");
            const presets = await resp.json();
            const found = presets.find(p => p.id === presetId);
            if (found) {
                indicatorNameInput.value = found.name;
                setCode(found.code);
                if (chartMgr) chartMgr.clearPlotSeries();
                logConsole(`[Preset] Loaded '${found.name}' template.`);
                mountCurrentScript();
            } else {
                // Check if user indicator
                const userResp = await fetch("/api/studio/indicators/list");
                const userList = await userResp.json();
                const userFound = userList.find(p => p.id === presetId);
                if (userFound) {
                    indicatorNameInput.value = userFound.name;
                    setCode(userFound.code);
                    if (chartMgr) chartMgr.clearPlotSeries();
                    logConsole(`[Custom Indicator] Loaded '${userFound.name}'.`);
                    mountCurrentScript();
                }
            }
        } catch (e) {
            console.error("Failed to load preset:", e);
        }
    }

    async function loadUserIndicators() {
        try {
            const resp = await fetch("/api/studio/indicators/list");
            const indicators = await resp.json();
            const optgroup = document.getElementById("optgroup-user-indicators");
            optgroup.innerHTML = "";
            indicators.forEach(ind => {
                const opt = document.createElement("option");
                opt.value = ind.id;
                opt.textContent = `⭐ ${ind.name}`;
                optgroup.appendChild(opt);
            });
        } catch (e) {
            console.error("Error loading user indicators:", e);
        }
    }

    function updateStrikesForUnderlying(symbol) {
        const strikeEl = document.getElementById("select-strike");
        if (!strikeEl) return;
        const config = {
            "NIFTY": { base: 24200, step: 50 },
            "BANKNIFTY": { base: 52000, step: 100 },
            "FINNIFTY": { base: 24000, step: 50 },
            "SENSEX": { base: 79400, step: 100 }
        };
        const cfg = config[symbol] || { base: 24200, step: 50 };
        strikeEl.innerHTML = "";
        const steps = [-2, -1, 0, 1, 2];
        steps.forEach(offset => {
            const strike = cfg.base + offset * cfg.step;
            const opt = document.createElement("option");
            opt.value = strike;
            let tag = "OTM";
            if (offset === 0) tag = "ATM";
            else if (offset < 0) tag = "ITM";
            opt.textContent = `${strike.toLocaleString()} Strike (${tag})`;
            if (offset === 0) opt.selected = true;
            strikeEl.appendChild(opt);
        });
        updateContractTabs(strikeEl.value);
    }

    function updateContractTabs(strike) {
        const tabCe = document.getElementById("tab-ce");
        const tabPe = document.getElementById("tab-pe");
        if (tabCe) tabCe.textContent = `⚪ ${strike} CE`;
        if (tabPe) tabPe.textContent = `⚪ ${strike} PE`;
    }

    async function mountCurrentScript() {
        const code = getCode();
        const symbol = document.getElementById("select-underlying") ? document.getElementById("select-underlying").value : "NIFTY";
        const strikeEl = document.getElementById("select-strike");
        const strikeVal = strikeEl ? strikeEl.value : "24200";
        updateContractTabs(strikeVal);
        const contract = `${symbol}_${strikeVal}_CE`;

        try {
            const resp = await fetch("/api/studio/mount", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ code, symbol, contract })
            });
            const data = await resp.json();

            if (data.success) {
                renderDynamicInputs(data.inputs_schema);
                logConsole(`[Compiler] Mounted '${contract}' to execution engine.`);
                initReplaySession(code);
            } else {
                logConsole(`[Error] Mount failed: ${data.detail}`);
            }
        } catch (e) {
            logConsole(`[Error] Mount network error: ${e.message}`);
        }
    }

    function renderDynamicInputs(schema) {
        inputsContainer.innerHTML = "";
        if (!schema || Object.keys(schema).length === 0) {
            inputsContainer.innerHTML = '<span style="color:var(--text-muted);font-size:10px;">No input.* declared.</span>';
            return;
        }

        for (const [key, desc] of Object.entries(schema)) {
            const row = document.createElement("div");
            row.className = "input-row";

            const labelRow = document.createElement("div");
            labelRow.className = "input-label-row";
            labelRow.innerHTML = `<span>${desc.title}</span><span id="badge-${key}" class="input-val-badge">${desc.current}</span>`;

            const slider = document.createElement("input");
            slider.type = "range";
            slider.min = desc.minval ?? 1;
            slider.max = desc.maxval ?? 100;
            slider.step = desc.step ?? 1;
            slider.value = desc.current;

            slider.addEventListener("input", (e) => {
                document.getElementById(`badge-${key}`).textContent = e.target.value;
                stepReplay("+1");
            });

            row.appendChild(labelRow);
            row.appendChild(slider);
            inputsContainer.appendChild(row);
        }
    }

    // ── 6. Replay Debugger & WebSocket Controller ──────────────────────────
    function initWebSocket() {
        const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${wsProto}//${window.location.host}/ws/studio`;

        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            logConsole("[WebSocket] Connected to studio replay engine.");
            if (currentSessionId) {
                ws.send(JSON.stringify({ action: "BIND_SESSION", session_id: currentSessionId }));
            }
            if (activeBreakpoints.size > 0) {
                ws.send(JSON.stringify({
                    action: "UPDATE_BREAKPOINTS",
                    breakpoints: Array.from(activeBreakpoints)
                }));
            }
        };

        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.type === "REPLAY_FRAME") {
                handleFrameUpdate(msg.frame, msg.cursor, msg.total_bars);
            } else if (msg.type === "BREAKPOINT_HIT") {
                pausePlayback();
                dbgStatusPill.textContent = `Paused: ${msg.reason || "Breakpoint"}`;
                dbgStatusPill.className = "dbg-status paused";
                logConsole(`[BREAKPOINT HALT] ${msg.reason} at Bar ${msg.bar_index}`);
                const line = msg.line || 74;
                highlightPausedLine(line);
            }
        };

        ws.onclose = () => {
            setTimeout(initWebSocket, 2000);
        };
    }

    async function initAvailableDates() {
        try {
            const resp = await fetch("/api/replay/available-dates");
            const data = await resp.json();
            if (selectDate && data.dates && data.dates.length > 0) {
                selectDate.innerHTML = "";
                data.dates.forEach((d, idx) => {
                    const opt = document.createElement("option");
                    opt.value = d;
                    opt.textContent = idx === 0 ? `${d} (Latest)` : d;
                    if (d === (data.default_date || "2026-10-30")) opt.selected = true;
                    selectDate.appendChild(opt);
                });
            } else if (data.default_date && selectDate) {
                selectDate.value = data.default_date;
            }
        } catch (e) {
            console.error("Error loading available dates:", e);
        }
    }

    async function initReplaySession(code, targetDate) {
        const symbol = document.getElementById("select-underlying") ? document.getElementById("select-underlying").value : "NIFTY";
        const strikeEl = document.getElementById("select-strike");
        const strikeVal = strikeEl ? strikeEl.value : "24200";
        const contract = `${symbol}_${strikeVal}_CE`;
        const date = targetDate || (selectDate ? selectDate.value : "2026-10-30");
        const tf = document.querySelector(".tf-btn.active")?.dataset.tf || "1m";

        try {
            const resp = await fetch("/api/replay/init", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    symbol,
                    contract,
                    date,
                    timeframe: tf,
                    code: code || getCode(),
                    warmup_bars: 40,
                    breakpoints: Array.from(activeBreakpoints)
                })
            });
            const data = await resp.json();
            currentSessionId = data.session_id;

            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ action: "BIND_SESSION", session_id: currentSessionId }));
            }

            scrubber.max = data.total_ticks ? data.total_ticks - 1 : (data.total_bars - 1);
            scrubber.value = data.tick_cursor != null ? data.tick_cursor : data.cursor;
            scrubberBadge.textContent = data.total_ticks
                ? `Tick #${data.tick_cursor || 0} / ${data.total_ticks} | Bar ${data.cursor} / ${data.total_bars}`
                : `Bar ${data.cursor} / ${data.total_bars}`;

            // Initialize candlestick chart with historical bars up to current cursor
            if (data.history_bars && data.history_bars.length > 0) {
                cachedFrames = [...data.history_bars];
                chartMgr.setCandles(cachedFrames, currentChartMode);
            }

            if (data.current_frame) {
                handleFrameUpdate(data.current_frame, data.cursor, data.total_bars, data.tick_cursor, data.total_ticks);
            } else if (data.history_bars && data.history_bars.length > 0) {
                updateChartLegend(data.history_bars[data.history_bars.length - 1]);
            }

            dbgStatusPill.textContent = "Debugger Ready";
            dbgStatusPill.className = "dbg-status ready";
            clearPausedLine();
            logConsole(`[Replay Session] Initialized for ${date} (${data.total_ticks || 0} ticks, ${data.total_bars} bars).`);
        } catch (e) {
            console.error("Replay init error:", e);
        }
    }

    async function stepReplay(direction = "+1", mode = "tick") {
        try {
            clearPausedLine();
            const resp = await fetch("/api/replay/step", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: currentSessionId, direction, count: 1, mode })
            });
            const data = await resp.json();
            if (data.success && data.frame) {
                handleFrameUpdate(data.frame, data.cursor, data.total_bars, data.tick_cursor, data.total_ticks);

                // Check active breakpoint hit on this step
                if (data.frame.breakpoint_hit) {
                    pausePlayback();
                    dbgStatusPill.textContent = `Hit: ${data.frame.breakpoint_reason}`;
                    dbgStatusPill.className = "dbg-status paused";
                    const line = data.frame.breakpoint_line || 89;
                    highlightPausedLine(line);
                }
            }
        } catch (e) {
            console.error("Step error:", e);
        }
    }

    function handleFrameUpdate(frame, cursor, totalBars, tickCursor, totalTicks) {
        if (!frame) return;

        // Update chart candle (either whole bar or forming candle)
        const candleToDraw = currentChartMode !== "spot" ? frame.option_bar : frame.spot_bar;
        chartMgr.addOrUpdateCandle(candleToDraw, currentChartMode);

        // Update plot overlays
        if (frame.indicator_plots) {
            chartMgr.updateIndicatorPlots(frame.timestamp, frame.indicator_plots);
        }

        // Add signal marker if generated
        if (frame.active_signal) {
            chartMgr.addSignalMarker(frame.timestamp, frame.active_signal);
            logConsole(`[Signal] ${frame.active_signal.action} @ ${frame.active_signal.trigger_price} (${frame.timestamp})`);
        }

        // Update Scrubber
        const curTick = frame.tick_index != null ? frame.tick_index : (tickCursor != null ? tickCursor : cursor);
        const maxTicks = frame.total_ticks != null ? frame.total_ticks : (totalTicks || totalBars);
        scrubber.value = curTick;
        const timePart = frame.timestamp ? (frame.timestamp.split(" ")[1] || frame.timestamp) : "";
        scrubberBadge.textContent = `Tick #${curTick} / ${maxTicks} | Bar ${cursor} / ${totalBars} (${timePart})`;

        // Update Dynamic Legend
        updateChartLegend(frame);

        // Update Variable Watch table
        if (frame.variable_watch) {
            renderWatchTable(frame.variable_watch);
        }

        if (cachedFrames.length > 0 && cachedFrames[cachedFrames.length - 1].timestamp === frame.timestamp) {
            cachedFrames[cachedFrames.length - 1] = frame;
        } else {
            cachedFrames.push(frame);
        }
    }

    function updateChartLegend(frame) {
        if (!frame) return;
        const isOpt = currentChartMode !== "spot";
        const bar = isOpt ? (frame.option_bar || frame.forming_candle || frame) : (frame.spot_bar || frame);
        const symSelect = document.getElementById("select-underlying");
        const strikeEl = document.getElementById("select-strike");
        const strikeVal = strikeEl ? strikeEl.value : "24200";

        let symText = "NIFTY 50";
        if (currentChartMode === "ce") {
            symText = `${strikeVal} CE`;
        } else if (currentChartMode === "pe") {
            symText = `${strikeVal} PE`;
        } else {
            symText = symSelect && symSelect.selectedOptions && symSelect.selectedOptions[0]
                ? symSelect.selectedOptions[0].text
                : "NIFTY 50";
        }

        const elSym = document.getElementById("legend-symbol");
        const elOhlc = document.getElementById("legend-ohlc");
        const elPlots = document.getElementById("legend-plots");

        if (elSym) elSym.textContent = symText;
        if (elOhlc && bar) {
            const op = Number(bar.open || bar.opt_open || bar.spot_open || 0).toFixed(2);
            const hi = Number(bar.high || bar.opt_high || bar.spot_high || 0).toFixed(2);
            const lo = Number(bar.low || bar.opt_low || bar.spot_low || 0).toFixed(2);
            const cl = Number(bar.close || bar.opt_close || bar.spot_close || 0).toFixed(2);
            elOhlc.textContent = `O: ${op}  H: ${hi}  L: ${lo}  C: ${cl}`;
        }
        if (elPlots && frame.indicator_plots) {
            const p = frame.indicator_plots;
            if (isOpt) {
                const rsi = p.rsi != null ? Number(p.rsi).toFixed(1) : "-";
                const buyer = p.buyer_pct != null ? `${Number(p.buyer_pct).toFixed(0)}%` : "-";
                const seller = p.seller_pct != null ? `${Number(p.seller_pct).toFixed(0)}%` : "-";
                elPlots.textContent = `RSI: ${rsi} | Buyer: ${buyer} | Seller: ${seller}`;
            } else {
                const emaFast = p.ema_fast != null ? Number(p.ema_fast).toFixed(1) : "-";
                const emaSlow = p.ema_slow != null ? Number(p.ema_slow).toFixed(1) : "-";
                const st = p.supertrend != null ? Number(p.supertrend).toFixed(1) : "-";
                elPlots.textContent = `Fast EMA: ${emaFast} | Slow EMA: ${emaSlow} | ST: ${st}`;
            }
        }
    }

    function renderWatchTable(vars) {
        watchTableBody.innerHTML = "";
        for (const [k, v] of Object.entries(vars)) {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td class="var-name">${k}</td><td class="var-val">${typeof v === "number" ? v.toFixed(2) : v}</td>`;
            watchTableBody.appendChild(tr);
        }

        // Evaluate custom watch expressions if any
        customWatchExpressions.forEach(expr => {
            const tr = document.createElement("tr");
            let val = "-";
            try {
                // Safe numeric evaluation against local variables
                const evalFn = new Function(...Object.keys(vars), `try { return (${expr}); } catch(e) { return "err"; }`);
                val = evalFn(...Object.values(vars));
                if (typeof val === "number") val = val.toFixed(2);
            } catch (e) {
                val = "NaN";
            }
            tr.innerHTML = `<td class="var-name">🔍 ${expr}</td><td class="var-val">${val}</td>`;
            watchTableBody.appendChild(tr);
        });
    }

    function startPlayback() {
        isPlaying = true;
        playBtn.textContent = "⏸ Pause";
        playBtn.classList.add("playing");
        document.getElementById("dbg-play").textContent = "⏸ Pause";
        dbgStatusPill.textContent = "Running...";
        dbgStatusPill.className = "dbg-status ready";
        clearPausedLine();

        const speed = parseFloat(document.getElementById("select-replay-speed").value);
        const breakSig = chkBreakSignal.checked;
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: "START_REPLAY", speed, break_on_signal: breakSig }));
        }
    }

    function pausePlayback() {
        isPlaying = false;
        playBtn.textContent = "▶ Play";
        playBtn.classList.remove("playing");
        document.getElementById("dbg-play").textContent = "▶ Run";
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: "PAUSE_REPLAY" }));
        }
    }

    // ── 7. Backtest & Scorecard Execution ─────────────────────────────────
    async function runBacktest() {
        logConsole("[Backtester] Initiating Vectorized Institutional Backtest...");
        const code = getCode();
        const symbol = document.getElementById("select-underlying") ? document.getElementById("select-underlying").value : "NIFTY";
        const strikeEl = document.getElementById("select-strike");
        const strikeVal = strikeEl ? strikeEl.value : "24200";
        const contract = `${symbol}_${strikeVal}_CE`;

        const t0 = performance.now();
        try {
            const resp = await fetch("/api/backtest/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ code, symbol, contract, slippage_pts: 0.5 })
            });
            const sc = await resp.json();
            const elapsed = (performance.now() - t0).toFixed(1);

            document.getElementById("sc-trades").textContent = sc.total_trades;
            document.getElementById("sc-winrate").textContent = `${sc.win_rate_pct}%`;
            document.getElementById("sc-pf").textContent = sc.profit_factor;
            document.getElementById("sc-pnl").textContent = `₹${sc.total_pnl_inr.toLocaleString()}`;
            document.getElementById("sc-dd").textContent = `₹${sc.max_drawdown_inr.toLocaleString()} (${sc.max_drawdown_pct}%)`;
            document.getElementById("sc-sharpe").textContent = sc.sharpe_ratio;

            // Switch drawer tab to scorecard
            document.querySelectorAll(".drawer-tab").forEach(t => t.classList.remove("active"));
            document.querySelectorAll(".drawer-content").forEach(c => c.classList.remove("active"));
            document.querySelector('[data-tab="scorecard"]').classList.add("active");
            document.getElementById("tab-scorecard-content").classList.add("active");

            logConsole(`[Backtest Complete] Evaluated in ${elapsed}ms. Net PnL: ₹${sc.total_pnl_inr} across ${sc.total_trades} trades.`);
        } catch (e) {
            logConsole(`[Backtest Error] ${e.message}`);
        }
    }

    // ── 8. UI Controls & Listeners ────────────────────────────────────────
    function initControls() {
        document.getElementById("btn-mount-live").addEventListener("click", mountCurrentScript);
        document.getElementById("btn-run-backtest").addEventListener("click", runBacktest);

        // Sidebar Nav Tabs
        document.querySelectorAll(".sidebar-tab-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                document.querySelectorAll(".sidebar-tab-btn").forEach(b => b.classList.remove("active"));
                document.querySelectorAll(".sidebar-tab-pane").forEach(p => p.classList.remove("active"));
                btn.classList.add("active");
                const targetTab = btn.dataset.tab;
                const targetPane = document.getElementById(targetTab);
                if (targetPane) targetPane.classList.add("active");
            });
        });

        selectPreset.addEventListener("change", (e) => {
            if (e.target.value) {
                loadPreset(e.target.value);
            }
        });

        // Date Picker
        if (selectDate) {
            selectDate.addEventListener("change", (e) => {
                logConsole(`[Date Changed] Switched session date to ${e.target.value}`);
                initReplaySession(getCode(), e.target.value);
            });
        }

        // Underlying & Strike Select
        const selUnderlying = document.getElementById("select-underlying");
        const selStrike = document.getElementById("select-strike");

        if (selUnderlying) {
            selUnderlying.addEventListener("change", (e) => {
                updateStrikesForUnderlying(e.target.value);
                mountCurrentScript();
            });
        }

        if (selStrike) {
            selStrike.addEventListener("change", (e) => {
                updateContractTabs(e.target.value);
                mountCurrentScript();
            });
        }

        // Timeframe Buttons
        document.querySelectorAll(".tf-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                document.querySelectorAll(".tf-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                initReplaySession(getCode(), selectDate ? selectDate.value : "2026-10-30");
            });
        });

        // Replay Controls: Granular Tick and Candle controls
        const btnNextTick = document.getElementById("btn-replay-next-tick") || document.getElementById("btn-replay-next");
        const btnPrevTick = document.getElementById("btn-replay-prev-tick") || document.getElementById("btn-replay-prev");
        const btnNextCandle = document.getElementById("btn-replay-next-candle");
        const btnPrevCandle = document.getElementById("btn-replay-prev-candle");

        if (btnNextTick) btnNextTick.addEventListener("click", () => stepReplay("+1", "tick"));
        if (btnPrevTick) btnPrevTick.addEventListener("click", () => stepReplay("-1", "tick"));
        if (btnNextCandle) btnNextCandle.addEventListener("click", () => stepReplay("+1", "bar"));
        if (btnPrevCandle) btnPrevCandle.addEventListener("click", () => stepReplay("-1", "bar"));

        document.getElementById("btn-replay-rewind").addEventListener("click", () => {
            scrubber.value = 0;
            if (chartMgr) chartMgr.clearMarkers();
            stepReplay("+1", "tick");
        });

        playBtn.addEventListener("click", () => {
            if (isPlaying) pausePlayback(); else startPlayback();
        });

        // VSCode Debugger Bar
        document.getElementById("dbg-play").addEventListener("click", () => {
            if (isPlaying) pausePlayback(); else startPlayback();
        });
        document.getElementById("dbg-pause").addEventListener("click", pausePlayback);
        document.getElementById("dbg-step-over").addEventListener("click", () => stepReplay("+1", "tick"));
        const dbgStepCandle = document.getElementById("dbg-step-candle");
        if (dbgStepCandle) dbgStepCandle.addEventListener("click", () => stepReplay("+1", "bar"));
        document.getElementById("dbg-step-back").addEventListener("click", () => stepReplay("-1", "tick"));
        document.getElementById("dbg-restart").addEventListener("click", () => {
            pausePlayback();
            initReplaySession(getCode(), selectDate ? selectDate.value : "2026-10-30");
        });

        // Keyboard Shortcuts (F5 = Run/Pause, F10 = Step Tick)
        window.addEventListener("keydown", (e) => {
            if (e.key === "F5") {
                e.preventDefault();
                if (isPlaying) pausePlayback(); else startPlayback();
            } else if (e.key === "F10") {
                e.preventDefault();
                stepReplay("+1", "tick");
            } else if ((e.metaKey || e.ctrlKey) && e.key === "s") {
                e.preventDefault();
                saveIndicatorQuick();
            }
        });

        // Scrubber with abort controller, debounce, and sequence check to eliminate jitter/race conditions
        let scrubAbortCtrl = null;
        let scrubDebounceTimer = null;
        let scrubSeq = 0;

        async function applyScrub(targetIdx) {
            if (scrubAbortCtrl) {
                scrubAbortCtrl.abort();
            }
            scrubAbortCtrl = new AbortController();
            const thisSeq = ++scrubSeq;

            try {
                const resp = await fetch("/api/replay/scrub", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ session_id: currentSessionId, tick_index: targetIdx, mode: "tick" }),
                    signal: scrubAbortCtrl.signal
                });
                const data = await resp.json();
                if (thisSeq !== scrubSeq) return; // Out of order response discarded

                if (data.success && data.frame) {
                    if (data.history_bars && data.history_bars.length > 0) {
                        cachedFrames = [...data.history_bars];
                        chartMgr.setCandles(cachedFrames, currentChartMode);
                    }
                    handleFrameUpdate(data.frame, data.cursor, data.total_bars, data.tick_cursor, data.total_ticks);
                    if (chartMgr) chartMgr.scrollToCurrent();
                }
            } catch (err) {
                if (err.name !== "AbortError") {
                    console.error("Scrub error:", err);
                }
            }
        }

        scrubber.addEventListener("input", (e) => {
            const targetIdx = parseInt(e.target.value, 10);
            const maxTicks = parseInt(scrubber.max, 10) || 3750;
            const curBar = Math.floor(targetIdx / 10);
            const totalBars = Math.ceil(maxTicks / 10);
            const totalMinutes = 9 * 60 + 15 + curBar;
            const hh = String(Math.floor(totalMinutes / 60)).padStart(2, "0");
            const mm = String(totalMinutes % 60).padStart(2, "0");
            const ss = String((targetIdx % 10) * 6).padStart(2, "0");
            scrubberBadge.textContent = `Tick #${targetIdx} / ${maxTicks} | Bar ${curBar} / ${totalBars} (${hh}:${mm}:${ss})`;
            if (scrubDebounceTimer) clearTimeout(scrubDebounceTimer);
            scrubDebounceTimer = setTimeout(() => {
                applyScrub(targetIdx);
            }, 30);
        });

        scrubber.addEventListener("change", (e) => {
            if (scrubDebounceTimer) clearTimeout(scrubDebounceTimer);
            const targetIdx = parseInt(e.target.value, 10);
            applyScrub(targetIdx);
        });

        // Watch Window Custom Expression Toggle
        document.getElementById("btn-add-watch").addEventListener("click", () => {
            const inputContainer = document.getElementById("watch-input-container");
            const isVisible = inputContainer.style.display !== "none";
            inputContainer.style.display = isVisible ? "none" : "block";
            if (!isVisible) {
                document.getElementById("watch-expression-input").focus();
            }
        });

        document.getElementById("watch-expression-input").addEventListener("keydown", (e) => {
            if (e.key === "Enter" && e.target.value.trim()) {
                customWatchExpressions.push(e.target.value.trim());
                e.target.value = "";
                document.getElementById("watch-input-container").style.display = "none";
                if (cachedFrames.length > 0) {
                    const last = cachedFrames[cachedFrames.length - 1];
                    if (last && last.variable_watch) renderWatchTable(last.variable_watch);
                }
            }
        });

        // Console Drawer Tabs
        document.querySelectorAll(".drawer-tab").forEach(tab => {
            tab.addEventListener("click", () => {
                document.querySelectorAll(".drawer-tab").forEach(t => t.classList.remove("active"));
                document.querySelectorAll(".drawer-content").forEach(c => c.classList.remove("active"));
                tab.classList.add("active");
                const tabKey = tab.dataset.tab;
                document.getElementById(`tab-${tabKey}-content`).classList.add("active");
            });
        });

        // Indicator Saving
        document.getElementById("btn-save-script").addEventListener("click", saveIndicatorQuick);
        document.getElementById("btn-save-as-script").addEventListener("click", openSaveModal);

        modalCloseBtn.addEventListener("click", closeSaveModal);
        modalCancelBtn.addEventListener("click", closeSaveModal);
        modalConfirmSaveBtn.addEventListener("click", handleModalSave);
    }

    async function saveIndicatorQuick() {
        const name = indicatorNameInput.value.trim() || "My Indicator";
        const code = getCode();
        try {
            const resp = await fetch("/api/studio/indicators/save", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, code, description: "Auto-saved from Studio" })
            });
            const data = await resp.json();
            if (data.success) {
                localStorage.setItem("studio_saved_code", code);
                logConsole(`[Saved] Indicator '${name}' saved to storage.`);
                loadUserIndicators();
                dbgStatusPill.textContent = "Saved ✅";
                setTimeout(() => {
                    if (dbgStatusPill.textContent === "Saved ✅") dbgStatusPill.textContent = "Debugger Ready";
                }, 2000);
            }
        } catch (e) {
            logConsole(`[Save Error] ${e.message}`);
        }
    }

    function openSaveModal() {
        modalIndicatorName.value = indicatorNameInput.value;
        modalIndicatorDesc.value = "";
        saveModal.style.display = "flex";
    }

    function closeSaveModal() {
        saveModal.style.display = "none";
    }

    async function handleModalSave() {
        const name = modalIndicatorName.value.trim() || "My Indicator";
        const desc = modalIndicatorDesc.value.trim();
        const code = getCode();

        try {
            const resp = await fetch("/api/studio/indicators/save", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, code, description: desc })
            });
            const data = await resp.json();
            if (data.success) {
                indicatorNameInput.value = name;
                closeSaveModal();
                loadUserIndicators();
                logConsole(`[Library] Indicator '${name}' saved successfully.`);
            }
        } catch (e) {
            alert("Error saving indicator: " + e.message);
        }
    }

    function logConsole(text) {
        const timeStr = new Date().toLocaleTimeString();
        consoleLogs.textContent = `[${timeStr}] ${text}\n` + consoleLogs.textContent;
    }

})();
