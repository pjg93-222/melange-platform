/* Loaded only by the local dashboard. No model or source data is changed. */
(() => {
  'use strict';
  const source = document.currentScript.src;
  const target = new URL('./', source).href;
  for (const header of document.querySelectorAll('header')) {
    if (header.querySelector('[data-staffer-live-link]')) continue;
    const link = document.createElement('a');
    link.dataset.stafferLiveLink = 'true';
    link.href = target;
    link.textContent = 'Staffer SNA · LIVE';
    link.title = 'Open the live staffer network — collection and reconciliation in progress';
    link.style.cssText = 'color:#EECD5C;border:1px solid #564019;border-radius:5px;padding:5px 8px;text-decoration:none;white-space:nowrap;font:11px Segoe UI,sans-serif;flex-shrink:0';
    header.appendChild(link);
  }
})();
