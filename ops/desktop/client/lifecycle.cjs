'use strict';
const os = require('node:os');

// This only detects local network changes. Trust still comes from the enrolled TLS pin.
function networkFingerprint(online, interfaces = os.networkInterfaces()) {
  if (!online) return 'offline';
  return JSON.stringify(Object.entries(interfaces).flatMap(([name, entries]) =>
    (entries || []).filter(entry => !entry.internal).map(entry => [name, entry.family, entry.address]))
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))));
}

function watchNetwork({isOnline, onReconnect, interval = 5000, interfaces = os.networkInterfaces}) {
  let previous = networkFingerprint(isOnline(), interfaces());
  const poll = () => {
    const current = networkFingerprint(isOnline(), interfaces());
    if (current !== previous && current !== 'offline' && current !== '[]') onReconnect();
    previous = current;
  };
  const timer = setInterval(poll, interval);
  timer.unref();
  return {poll, close: () => clearInterval(timer)};
}

module.exports = {networkFingerprint, watchNetwork};
