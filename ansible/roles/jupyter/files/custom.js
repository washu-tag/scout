// JupyterLab Scout Logo Click Handler
// Make the Scout logo clickable to return to Launchpad

(function () {
  'use strict';

  // Wait for DOM to be ready
  function addLogoClickHandler() {
    const logo = document.querySelector('#jp-MainLogo');
    if (logo) {
      logo.addEventListener('click', function () {
        // Get the base hostname (remove jupyter subdomain)
        const hostname = window.location.hostname.replace(/^jupyter\./, '');
        const launchpadUrl = `${window.location.protocol}//${hostname}`;
        window.open(launchpadUrl, '_blank');
      });
    }
  }

  // Try immediately
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', addLogoClickHandler);
  } else {
    addLogoClickHandler();
  }

  // Also retry after a delay in case JupyterLab loads the logo later
  setTimeout(addLogoClickHandler, 1000);
})();
