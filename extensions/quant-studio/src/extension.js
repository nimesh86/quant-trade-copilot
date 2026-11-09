/**
 * Quant Studio Extension Entry Point for Visual Studio Code & Antigravity IDE.
 * Manages Webview Chart Panels, Activity Bar Views, and Native Debug Integration.
 */

const vscode = require('vscode');
const path = require('path');
const fs = require('fs');
const http = require('http');

let currentActiveModel = {};
let pendingModelResolvers = {};

function startMcpBridgeServer(port = 8766) {
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('Access-Control-Allow-Origin', '*');

    try {
      const parsedUrl = new URL(req.url, `http://127.0.0.1:${port}`);
      if (parsedUrl.pathname === '/model') {
        const scope = parsedUrl.searchParams.get('scope') || 'all';

        if (QuantStudioChartPanel.currentPanel && QuantStudioChartPanel.currentPanel._panel) {
          const queryId = 'q_' + Date.now();
          const timer = setTimeout(() => {
            if (pendingModelResolvers[queryId]) {
              delete pendingModelResolvers[queryId];
              res.writeHead(200);
              const fallback = currentActiveModel[scope] !== undefined ? currentActiveModel[scope] : currentActiveModel;
              res.end(JSON.stringify({ status: 'cached', scope: scope, data: fallback }));
            }
          }, 1500);

          pendingModelResolvers[queryId] = (data) => {
            clearTimeout(timer);
            delete pendingModelResolvers[queryId];
            res.writeHead(200);
            res.end(JSON.stringify({ status: 'success', scope: scope, data: data }));
          };

          QuantStudioChartPanel.currentPanel._panel.webview.postMessage({
            command: 'query_model',
            query_id: queryId,
            scope: scope
          });
          return;
        }

        res.writeHead(200);
        const fallback = currentActiveModel[scope] !== undefined ? currentActiveModel[scope] : currentActiveModel;
        res.end(JSON.stringify({ status: 'cached', scope: scope, data: fallback }));
        return;
      }
    } catch (e) {
      console.error('[Quant Studio] Error processing HTTP bridge request:', e);
    }

    res.writeHead(404);
    res.end(JSON.stringify({ error: 'Endpoint not found' }));
  });

  server.listen(port, '127.0.0.1', () => {
    console.log(`[Quant Studio] Live Model MCP Bridge listening on http://127.0.0.1:${port}`);
  });

  server.on('error', (err) => {
    console.warn('[Quant Studio] MCP Bridge server port busy or error:', err.message);
  });

  return server;
}

/**
 * Manages the singleton Webview panel hosting the TradingView Chart.
 */
class QuantStudioChartPanel {
  static currentPanel = undefined;
  static viewType = 'quantStudioChart';

  static createOrShow(extensionUri, port = 8765) {
    const column = vscode.window.activeTextEditor
      ? vscode.ViewColumn.Beside
      : vscode.ViewColumn.One;

    if (QuantStudioChartPanel.currentPanel) {
      QuantStudioChartPanel.currentPanel._update();
      QuantStudioChartPanel.currentPanel._panel.reveal(column);
      QuantStudioChartPanel.currentPanel._sendConnect(port);
      return QuantStudioChartPanel.currentPanel;
    }

    const panel = vscode.window.createWebviewPanel(
      QuantStudioChartPanel.viewType,
      'Quant Studio: Live Chart',
      column,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(extensionUri, 'src', 'webview')],
      }
    );

    QuantStudioChartPanel.currentPanel = new QuantStudioChartPanel(panel, extensionUri, port);
    return QuantStudioChartPanel.currentPanel;
  }

  constructor(panel, extensionUri, port) {
    this._panel = panel;
    this._extensionUri = extensionUri;
    this._port = port;

    this._update();

    this._panel.webview.onDidReceiveMessage(message => {
      this._handleWebviewMessage(message);
    });

    // Auto-reload webview when chart.html or chart_view.js changes on disk during development
    try {
      const webviewDir = path.join(this._extensionUri.fsPath, 'src', 'webview');
      if (fs.existsSync(webviewDir)) {
        this._watcher = fs.watch(webviewDir, (eventType, filename) => {
          if (filename && (filename.endsWith('.html') || filename.endsWith('.js') || filename.endsWith('.css'))) {
            clearTimeout(this._reloadTimer);
            this._reloadTimer = setTimeout(() => {
              if (this._panel && this._panel.webview) {
                console.log(`[Quant Studio] Webview asset ${filename} modified. Reloading webview...`);
                this._update();
              }
            }, 250);
          }
        });
      }
    } catch (e) {
      console.warn('[Quant Studio] Webview auto-reload watcher error:', e);
    }

    this._panel.onDidDispose(() => this.dispose(), null);
  }

  _handleWebviewMessage(message) {
    if (!message) return;
    try {
      const workspaceFolders = vscode.workspace.workspaceFolders;
      if (!workspaceFolders || workspaceFolders.length === 0) return;
      const rootPath = workspaceFolders[0].uri.fsPath;
      const reportDir = path.join(rootPath, 'reports');
      if (!fs.existsSync(reportDir)) {
        fs.mkdirSync(reportDir, { recursive: true });
      }
      const stateFile = path.join(reportDir, 'quant_studio_state.json');
      let currentState = {};
      if (fs.existsSync(stateFile)) {
        try {
          currentState = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
        } catch (e) {}
      }

      if (message.command === 'quant_studio_params_changed') {
        currentState.parameters = message.parameters;
        currentState.last_updated = new Date().toISOString();
        fs.writeFileSync(stateFile, JSON.stringify(currentState, null, 2), 'utf8');
        try {
          const modelFile = path.join(reportDir, 'quant_studio_model.json');
          fs.writeFileSync(modelFile, JSON.stringify({ parameters: message.parameters, last_updated: currentState.last_updated }, null, 2), 'utf8');
        } catch (e) {}
      } else if (message.command === 'quant_studio_trade_selected') {
        if (!currentState.viewport) currentState.viewport = {};
        currentState.viewport.focused_trade = message.trade;
        currentState.viewport.focused_trade_index = message.trade_index;
        if (message.parameters) currentState.parameters = message.parameters;
        currentState.last_updated = new Date().toISOString();
        fs.writeFileSync(stateFile, JSON.stringify(currentState, null, 2), 'utf8');

        const tradeFile = path.join(reportDir, 'selected_trade.json');
        fs.writeFileSync(tradeFile, JSON.stringify(message.trade, null, 2), 'utf8');
      } else if (message.command === 'quant_studio_backtest_results') {
        currentState.last_backtest = message.data;
        if (message.parameters) currentState.parameters = message.parameters;
        currentState.last_updated = new Date().toISOString();
        fs.writeFileSync(stateFile, JSON.stringify(currentState, null, 2), 'utf8');

        const btFile = path.join(reportDir, 'last_backtest.json');
        fs.writeFileSync(btFile, JSON.stringify(message.data, null, 2), 'utf8');
      } else if (message.command === 'model_response') {
        if (message.query_id && pendingModelResolvers[message.query_id]) {
          pendingModelResolvers[message.query_id](message.data);
        }
        if (message.data) {
          currentActiveModel = message.data;
          try {
            const modelFile = path.join(reportDir, 'quant_studio_model.json');
            fs.writeFileSync(modelFile, JSON.stringify(message.data, null, 2), 'utf8');
          } catch (e) {}
        }
      }
    } catch (err) {
      console.error('[Quant Studio] Error handling webview message:', err);
    }
  }

  _sendConnect(port) {
    if (this._panel && this._panel.webview) {
      this._panel.webview.postMessage({ command: 'connect', port: port });
    }
  }

  dispose() {
    QuantStudioChartPanel.currentPanel = undefined;
    if (this._reloadTimer) clearTimeout(this._reloadTimer);
    if (this._watcher) {
      try { this._watcher.close(); } catch (e) {}
    }
    this._panel.dispose();
  }

  _update() {
    const webview = this._panel.webview;
    const webviewDir = vscode.Uri.joinPath(this._extensionUri, 'src', 'webview');

    const htmlPath = path.join(this._extensionUri.fsPath, 'src', 'webview', 'chart.html');
    let htmlContent = fs.readFileSync(htmlPath, 'utf8');

    const scriptPath = path.join(this._extensionUri.fsPath, 'src', 'webview', 'chart_view.js');
    const scriptContent = fs.readFileSync(scriptPath, 'utf8');

    const libUri = webview.asWebviewUri(
      vscode.Uri.joinPath(webviewDir, 'lightweight-charts.standalone.production.js')
    );

    htmlContent = htmlContent.replace('{{LIGHTWEIGHT_CHARTS_URI}}', libUri.toString());
    htmlContent = htmlContent.replace('<script src="{{CHART_VIEW_URI}}"></script>', `<script>\n${scriptContent}\n</script>`);

    webview.html = htmlContent;
  }
}

/**
 * Tree Data Provider for Workspace Indicators in the Activity Bar.
 */
class IndicatorsTreeProvider {
  constructor(workspaceRoot) {
    this.workspaceRoot = workspaceRoot;
    this._onDidChangeTreeData = new vscode.EventEmitter();
    this.onDidChangeTreeData = this._onDidChangeTreeData.event;
  }

  refresh() {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element) {
    return element;
  }

  getChildren(element) {
    if (!this.workspaceRoot) {
      return Promise.resolve([]);
    }

    const indicatorsDir = path.join(this.workspaceRoot, 'trading_engine', 'indicators');
    if (!fs.existsSync(indicatorsDir)) {
      return Promise.resolve([]);
    }

    try {
      const files = fs.readdirSync(indicatorsDir).filter((f) => f.endsWith('.py') && !f.startsWith('__'));
      const items = files.map((file) => {
        const fullPath = path.join(indicatorsDir, file);
        const item = new vscode.TreeItem(file, vscode.TreeItemCollapsibleState.None);
        item.description = 'Institutional Indicator';
        item.tooltip = fullPath;
        item.iconPath = new vscode.ThemeIcon('symbol-field');
        item.command = {
          command: 'vscode.open',
          title: 'Open Indicator Code',
          arguments: [vscode.Uri.file(fullPath)],
        };
        item.contextValue = 'indicatorItem';
        return item;
      });
      return Promise.resolve(items);
    } catch (e) {
      return Promise.resolve([]);
    }
  }
}

/**
 * Tree Data Provider for Replay Session Status in the Activity Bar.
 */
class SessionTreeProvider {
  constructor() {
    this._onDidChangeTreeData = new vscode.EventEmitter();
    this.onDidChangeTreeData = this._onDidChangeTreeData.event;
  }

  refresh() {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element) {
    return element;
  }

  getChildren(element) {
    const config = vscode.workspace.getConfiguration('quantStudio');
    const port = config.get('serverPort', 8765);
    const symbol = config.get('defaultSymbol', 'NIFTY');
    const contract = config.get('defaultContract', 'NIFTY_ATM_CE');

    const items = [
      new vscode.TreeItem(`Status: Standby (Port ${port})`, vscode.TreeItemCollapsibleState.None),
      new vscode.TreeItem(`Underlying: ${symbol}`, vscode.TreeItemCollapsibleState.None),
      new vscode.TreeItem(`Contract: ${contract}`, vscode.TreeItemCollapsibleState.None),
      new vscode.TreeItem(`IPC Stream: ws://127.0.0.1:${port}`, vscode.TreeItemCollapsibleState.None),
    ];
    items[0].iconPath = new vscode.ThemeIcon('pulse');
    items[1].iconPath = new vscode.ThemeIcon('graph');
    items[2].iconPath = new vscode.ThemeIcon('layers');
    items[3].iconPath = new vscode.ThemeIcon('radio-tower');

    return Promise.resolve(items);
  }
}

/**
 * Extension Activation Hook
 */
function activate(context) {
  const workspaceRoot =
    vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders.length > 0
      ? vscode.workspace.workspaceFolders[0].uri.fsPath
      : undefined;

  // Register Activity Bar Tree Views
  const sessionProvider = new SessionTreeProvider();
  vscode.window.registerTreeDataProvider('quantStudio.sessionView', sessionProvider);

  const indicatorsProvider = new IndicatorsTreeProvider(workspaceRoot);
  vscode.window.registerTreeDataProvider('quantStudio.indicatorsView', indicatorsProvider);

  // Start Live Model MCP Bridge HTTP Server on port 8766
  try {
    const bridgeServer = startMcpBridgeServer(8766);
    context.subscriptions.push({
      dispose: () => {
        try {
          bridgeServer.close();
        } catch (e) {}
      }
    });
  } catch (e) {
    console.warn('[Quant Studio] Could not start bridge server:', e);
  }

  // Command: Open Chart Webview
  const openChartCmd = vscode.commands.registerCommand('quantStudio.openChart', () => {
    const config = vscode.workspace.getConfiguration('quantStudio');
    const port = config.get('serverPort', 8765);
    QuantStudioChartPanel.createOrShow(context.extensionUri, port);
  });
  context.subscriptions.push(openChartCmd);

  // Command: Debug Indicator
  const debugIndicatorCmd = vscode.commands.registerCommand('quantStudio.debugIndicator', async (resource) => {
    let targetFile = '';
    if (resource && resource.fsPath) {
      targetFile = resource.fsPath;
    } else if (vscode.window.activeTextEditor && vscode.window.activeTextEditor.document.fileName.endsWith('.py')) {
      targetFile = vscode.window.activeTextEditor.document.fileName;
    } else {
      // Default to adaptive_trend_pro.py if available
      const defaultInd = path.join(workspaceRoot || '', 'trading_engine', 'indicators', 'adaptive_trend_pro.py');
      if (fs.existsSync(defaultInd)) {
        targetFile = defaultInd;
      } else {
        vscode.window.showErrorMessage('Please open a Python indicator file (e.g. adaptive_trend_pro.py) to debug.');
        return;
      }
    }

    const config = vscode.workspace.getConfiguration('quantStudio');
    const port = config.get('serverPort', 8765);
    const symbol = config.get('defaultSymbol', 'NIFTY');

    // Auto open chart
    if (config.get('autoOpenChartOnDebug', true)) {
      QuantStudioChartPanel.createOrShow(context.extensionUri, port);
    }

    // Launch debugpy session
    const venvPython = path.join(workspaceRoot || '', 'venv', 'bin', 'python');
    const debugConfig = {
      name: 'Quant Studio: Debug Indicator',
      type: 'debugpy',
      request: 'launch',
      program: path.join(workspaceRoot || '', 'trading_engine', 'replay', 'debug_runner.py'),
      args: ['--file', targetFile, '--symbol', symbol, '--port', String(port)],
      console: 'integratedTerminal',
      justMyCode: false,
      python: venvPython,
      env: {
        PYTHONPATH: workspaceRoot || '',
      },
    };

    const launched = await vscode.debug.startDebugging(undefined, debugConfig);
    if (!launched) {
      vscode.window.showWarningMessage('Could not launch debugpy debugger. Launching debug runner in terminal.');
      const term = vscode.window.createTerminal('Quant Studio Debug Runner');
      term.show();
      term.sendText(
        `./venv/bin/python -m trading_engine.replay.debug_runner --file "${targetFile}" --port ${port}`
      );
    }
  });
  context.subscriptions.push(debugIndicatorCmd);

  // Command: Run Fast Replay
  const runReplayCmd = vscode.commands.registerCommand('quantStudio.runReplay', () => {
    vscode.commands.executeCommand('quantStudio.debugIndicator');
  });
  context.subscriptions.push(runReplayCmd);

  // Command: Kill Session
  const killSessionCmd = vscode.commands.registerCommand('quantStudio.killSession', () => {
    if (vscode.debug.activeDebugSession) {
      vscode.debug.stopDebugging(vscode.debug.activeDebugSession);
    }
    vscode.window.showInformationMessage('Quant Studio session terminated.');
  });
  context.subscriptions.push(killSessionCmd);

  // Auto-reveal chart whenever a Quant Studio debug session starts
  context.subscriptions.push(
    vscode.debug.onDidStartDebugSession((session) => {
      if (session.name.includes('Quant Studio')) {
        const config = vscode.workspace.getConfiguration('quantStudio');
        const port = config.get('serverPort', 8765);
        QuantStudioChartPanel.createOrShow(context.extensionUri, port);
      }
    })
  );
}

function deactivate() {
  if (QuantStudioChartPanel.currentPanel) {
    QuantStudioChartPanel.currentPanel.dispose();
  }
}

module.exports = {
  activate,
  deactivate,
  QuantStudioChartPanel,
  IndicatorsTreeProvider,
  SessionTreeProvider,
};
