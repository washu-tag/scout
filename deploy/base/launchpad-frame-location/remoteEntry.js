// JupyterLab loads this file as a prebuilt extension's module-federation entry.
// It loads the frame-location script next to it; the one plugin it exposes does nothing.
(function () {
  var script = document.createElement('script');
  script.src = new URL('frame-location.js', document.currentScript.src).href;
  document.head.appendChild(script);
})();

(window._JUPYTERLAB = window._JUPYTERLAB || {})['scout-frame-location'] = {
  init: function () {},
  get: function () {
    // JupyterLab reads a module without __esModule as the plugin itself.
    return Promise.resolve(function () {
      return { id: 'scout-frame-location:plugin', autoStart: true, activate: function () {} };
    });
  },
};
