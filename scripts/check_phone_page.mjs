// Execute the phone's actual capture loop with browser and network substitutes.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const html = fs.readFileSync(new URL('../multicam_mcp_phone.html', import.meta.url), 'utf8');
const source = html.match(/<script>([\s\S]*)<\/script>/)[1];
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {value: '', textContent: '', disabled: false,
    videoWidth: 640, videoHeight: 480, currentTime: 1,
    addEventListener() {}, getAttribute() {}, setAttribute() {},
  });
  return elements.get(id);
};
let captureCount = 0, polls = 0, uploads = [];
let pending = null;
const context = vm.createContext({
  document: {getElementById: element, createElement: () => ({}), visibilityState: 'visible', addEventListener() {}},
  window: {isSecureContext: true}, navigator: {mediaDevices: {}},
  localStorage: {getItem() {}, setItem() {}, removeItem() {}},
  location: {hash: '#pin=123456', pathname: '/', search: ''},
  history: {replaceState() {}}, performance: {now: () => 1},
  setTimeout() {}, clearTimeout() {}, console,
  fetch: async (url, options = {}) => {
    if (url.endsWith('/request')) {
      polls++;
      return {ok: true, json: async () => ({request_id: pending})};
    }
    uploads.push({url, options});
    return {ok: true, json: async () => ({accepted: true})};
  },
  testCapture: async () => { captureCount++; return 'photo-bytes'; },
});
vm.runInContext(source, context);
assert.equal(element('pin').value, '', 'URL fragments must not fill in a PIN');
vm.runInContext("running = true; name = 'bench'; cameraToken = 'authorized-token'; capturePhoto = testCapture;", context);
for (let i = 0; i < 5; i++) await vm.runInContext('loop(session)', context);
assert.equal(polls, 5);
assert.equal(captureCount, 0, 'An idle phone must not capture images');
assert.equal(uploads.length, 0, 'An idle phone must not upload images');
pending = 'single-use-request';
await vm.runInContext('loop(session)', context);
assert.equal(captureCount, 1);
assert.equal(uploads.length, 1);
assert.equal(uploads[0].options.headers['x-capture-request'], pending);
assert.equal(uploads[0].options.headers.authorization, 'Bearer authorized-token');
pending = null;
await vm.runInContext('loop(session)', context);
assert.equal(uploads.length, 1);
console.log('Phone loop: no idle capture/uploads; one authenticated response per request; no PIN from URL.');
