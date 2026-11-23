const vscode = require('vscode');
const { execFile } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const PLACEHOLDER = 'REPLACE_WITH_YOUR_GATEWAY_KEY';
const SET_KEY = '/usr/local/bin/scout-set-key';
const DONT_ASK_KEY = 'scout.gatewayKey.dontAsk';
const CHAT_MODELS = '.local/share/code-server/User/chatLanguageModels.json';

let refreshKeyItem = () => {};

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

function chatModelsNeedKey(home) {
  const providers = readJson(path.join(home, CHAT_MODELS));
  return (
    Array.isArray(providers) &&
    providers.some((provider) =>
      (provider?.models || []).some((model) =>
        Object.entries(model?.requestHeaders || {}).some(
          ([name, value]) => name.toLowerCase() === 'x-api-key' && value === PLACEHOLDER,
        ),
      ),
    )
  );
}

function needsKey() {
  const home = os.homedir();
  const claudeToken = readJson(path.join(home, '.claude', 'settings.json'))?.env
    ?.ANTHROPIC_AUTH_TOKEN;
  const codexKey = readJson(path.join(home, '.codex', 'auth.json'))?.OPENAI_API_KEY;
  if (claudeToken === PLACEHOLDER || codexKey === PLACEHOLDER || chatModelsNeedKey(home)) {
    return true;
  }
  return !claudeToken && !process.env.ANTHROPIC_AUTH_TOKEN && !!process.env.ANTHROPIC_BASE_URL;
}

function saveKey(key) {
  return new Promise((resolve, reject) => {
    const child = execFile(SET_KEY, ['--stdin'], (err, _stdout, stderr) => {
      if (err) {
        reject(new Error(stderr.trim() || 'the key setup script failed'));
      } else {
        resolve();
      }
    });
    child.stdin.end(`${key}\n`);
  });
}

async function setGatewayKey() {
  if (!fs.existsSync(SET_KEY)) {
    vscode.window.showErrorMessage('Key setup is unavailable on this server.');
    return;
  }
  const key = await vscode.window.showInputBox({
    title: 'AI Gateway Key',
    prompt: 'Paste your AI gateway key. Claude Code, Codex, and VS Code chat all use it.',
    password: true,
    ignoreFocusOut: true,
    validateInput: (value) => (/\s/.test(value.trim()) ? 'The key cannot contain spaces.' : null),
  });
  if (!key || !key.trim()) {
    return;
  }
  try {
    await saveKey(key.trim());
  } catch (err) {
    vscode.window.showErrorMessage(`Could not save the key: ${err.message}`);
    return;
  }
  refreshKeyItem();
  const choice = await vscode.window.showInformationMessage(
    'Key saved. Reload the window so Claude Code and chat use it, and open a new terminal before running codex.',
    'Reload Window',
  );
  if (choice === 'Reload Window') {
    vscode.commands.executeCommand('workbench.action.reloadWindow');
  }
}

async function promptForKey(context) {
  if (context.globalState.get(DONT_ASK_KEY) || !needsKey()) {
    return;
  }
  const choice = await vscode.window.showInformationMessage(
    'Add your AI gateway key to set up Claude Code, Codex, and VS Code chat.',
    'Add Key',
    'Later',
    "Don't Ask Again",
  );
  if (choice === 'Add Key') {
    await setGatewayKey();
  } else if (choice === "Don't Ask Again") {
    await context.globalState.update(DONT_ASK_KEY, true);
    refreshKeyItem();
  }
}

function activate(context) {
  const item = vscode.window.createStatusBarItem(
    'scout.gatewayKey',
    vscode.StatusBarAlignment.Right,
    -1000,
  );
  item.name = 'Scout AI Gateway Key';
  item.text = '$(key) Add AI Gateway Key';
  item.tooltip = 'Claude Code, Codex, and VS Code chat need your AI gateway key';
  item.command = 'scout.setGatewayKey';
  refreshKeyItem = () => {
    if (!context.globalState.get(DONT_ASK_KEY) && needsKey()) {
      item.show();
    } else {
      item.hide();
    }
  };

  context.subscriptions.push(
    item,
    vscode.commands.registerCommand('scout.setGatewayKey', setGatewayKey),
  );
  refreshKeyItem();
  promptForKey(context);
}

module.exports = { activate };
