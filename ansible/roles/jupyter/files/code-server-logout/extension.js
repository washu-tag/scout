const vscode = require('vscode');

function logoutUrl() {
  const base = process.env.JUPYTERHUB_PUBLIC_URL || process.env.JUPYTERHUB_PUBLIC_HUB_URL;
  return base ? vscode.Uri.parse(new URL('logout', base).toString()) : null;
}

async function logout(url) {
  const choice = await vscode.window.showWarningMessage(
    'Log out of Scout?',
    { modal: true, detail: 'This stops your server and any running notebooks or terminals.' },
    'Log out',
  );
  if (choice !== 'Log out') {
    return;
  }
  const opened = await vscode.env.openExternal(url);
  if (opened) {
    vscode.window.showInformationMessage(
      'Logging out in a new tab. Once it finishes, you can close this tab.',
    );
  } else {
    vscode.window.showErrorMessage(`Could not open the log out page. Go to ${url} to log out.`);
  }
}

function activate(context) {
  const url = logoutUrl();

  context.subscriptions.push(
    vscode.commands.registerCommand('scout.logout', () => {
      if (!url) {
        vscode.window.showErrorMessage('Scout log out is unavailable: no JupyterHub URL is set.');
        return;
      }
      return logout(url);
    }),
  );

  if (url) {
    const item = vscode.window.createStatusBarItem(
      'scout.logout',
      vscode.StatusBarAlignment.Right,
      -1000,
    );
    item.name = 'Scout Log Out';
    item.text = '$(sign-out) Log out';
    item.tooltip = 'Stop your server and log out of Scout';
    item.command = 'scout.logout';
    item.show();
    context.subscriptions.push(item);
  }
}

module.exports = { activate };
