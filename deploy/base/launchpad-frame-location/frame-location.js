// Tells the launchpad which page this app is on while the launchpad frames it,
// so its address bar follows. Sends only this page's address, only to the launchpad.
// Opened on its own (a new tab from inside this app, which the edge doesn't
// redirect), the page reopens inside the launchpad.
(function () {
  var launchpad = 'https://${server_hostname}';
  if (window.parent === window) {
    var here = window.location;
    window.location.replace(
      launchpad + '/open/' + here.host + here.pathname + here.search + here.hash,
    );
    return;
  }
  function clean(href) {
    var url = new URL(href, window.location.href);
    if (url.origin !== window.location.origin) return null;
    if (url.searchParams.has('XNAT_CSRF')) url.searchParams.delete('XNAT_CSRF');
    return url.href;
  }
  var last = '';
  function report() {
    var href = clean(window.location.href);
    if (href === last) return;
    last = href;
    window.parent.postMessage({ type: 'scout:location', href: href }, launchpad);
  }
  report();
  window.addEventListener('popstate', report);
  window.addEventListener('hashchange', report);
  window.setInterval(report, 500);

  // A page this script doesn't run on (XNAT's viewer, for one) can't report
  // itself, so name where a link-style navigation is going; the launchpad uses it
  // only if the frame then loads a new page. Form posts and downloads are left out.
  if (!window.navigation) return;
  window.navigation.addEventListener('navigate', function (event) {
    if (event.destination.sameDocument || event.formData || event.downloadRequest) return;
    var href = clean(event.destination.url);
    if (href) window.parent.postMessage({ type: 'scout:leaving', href: href }, launchpad);
  });
})();
