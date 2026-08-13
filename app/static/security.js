/**
 * Advanced Frontend Security & Anti-DevTools Protection
 * Disables inspection, right-click, shortcuts, console logs, DevTools resize detection, and debugger traps.
 */
(function () {
  'use strict';

  // 1. Disable Right-Click Context Menu
  document.addEventListener('contextmenu', function (e) {
    e.preventDefault();
    return false;
  }, false);

  // 2. Disable Keyboard Shortcuts (F12, Ctrl+Shift+I, Ctrl+Shift+J, Ctrl+Shift+C, Ctrl+U, Ctrl+S)
  document.addEventListener('keydown', function (e) {
    if (e.keyCode === 123) { // F12
      e.preventDefault();
      return false;
    }
    if (e.ctrlKey && e.shiftKey && (e.keyCode === 73 || e.keyCode === 74 || e.keyCode === 67)) {
      e.preventDefault();
      return false;
    }
    if (e.ctrlKey && (e.keyCode === 85 || e.keyCode === 83)) {
      e.preventDefault();
      return false;
    }
  }, false);

  // 3. Override Console Logging
  (function () {
    const emptyFn = function () {};
    window.console.log = emptyFn;
    window.console.warn = emptyFn;
    window.console.error = emptyFn;
    window.console.debug = emptyFn;
    window.console.info = emptyFn;
  })();

  // 4. DevTools Action & Access Denied Handler
  function blockDevToolsAccess() {
    try {
      document.body.innerHTML = '<div style="display:flex;justify-content:center;align-items:center;height:100vh;background:#0f172a;color:#ef4444;font-family:sans-serif;font-size:24px;font-weight:bold;text-align:center;">Security Alert: Developer Tools Access Denied.</div>';
    } catch(e) {}
    try {
      window.location.href = "about:blank";
    } catch(e) {}
  }

  // 5. Detect DevTools Opening via Menu (Window Outer vs Inner Dimensions)
  function checkDevToolsDimensions() {
    const widthDiff = window.outerWidth - window.innerWidth;
    const heightDiff = window.outerHeight - window.innerHeight;
    if (widthDiff > 160 || heightDiff > 160) {
      blockDevToolsAccess();
    }
  }

  window.addEventListener('resize', checkDevToolsDimensions);
  setInterval(checkDevToolsDimensions, 500);

  // 6. Anti-Debugging Timing Loop (Detects when DevTools pauses on debugger)
  setInterval(function () {
    const start = performance.now();
    (function () {}['constructor']('debugger')());
    const end = performance.now();
    if (end - start > 100) {
      blockDevToolsAccess();
    }
  }, 500);
})();
