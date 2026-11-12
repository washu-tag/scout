const vscode = require('vscode');

function activate(context) {
  const hubUrl = process.env.JUPYTERHUB_PUBLIC_HUB_URL;
  const logoutUrl = hubUrl ? vscode.Uri.parse(new URL('logout', hubUrl).toString()) : null;

  context.subscriptions.push(
    vscode.commands.registerCommand('scout.logout', () => {
      if (!logoutUrl) {
        vscode.window.showErrorMessage(
          'Scout log out is unavailable: JUPYTERHUB_PUBLIC_HUB_URL is not set.',
        );
        return;
      }
      vscode.env.openExternal(logoutUrl);
    }),
  );

  if (logoutUrl) {
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
