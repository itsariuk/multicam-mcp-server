"""THROWAWAY spike (PRD phase 1): can a phone browser use its camera against a
self-signed HTTPS server on the LAN and POST JPEG frames back? Stdlib only.

    python3 tls_spike.py    # then open the printed URL on the phone
"""

import json
import shutil
import socket
import ssl
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8443
MAX_BODY = 20 * 1024 * 1024
HERE = Path(__file__).parent
CERT, KEY, FRAMES = HERE / "cert.pem", HERE / "key.pem", HERE / "frames"

PAGE = b"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>framegrab TLS spike</title>
<button id=go style="font-size:2em">Start camera</button>
<video id=video playsinline autoplay muted style="width:100%"></video>
<pre id=log style="white-space:pre-wrap"></pre>
<script>
const log = m => { document.getElementById('log').textContent += m + '\\n'; };
log('isSecureContext=' + isSecureContext + ' mediaDevices=' + !!navigator.mediaDevices);
document.getElementById('go').onclick = async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia(
      {video: {facingMode: 'environment', width: {ideal: 4096}, height: {ideal: 2160}}});
    const video = document.getElementById('video');
    video.srcObject = stream; await video.play();
    const track = stream.getVideoTracks()[0];
    const caps = track.getCapabilities ? track.getCapabilities() : {};
    let wake = 'unsupported';
    if ('wakeLock' in navigator) {
      try { await navigator.wakeLock.request('screen'); wake = 'ok'; }
      catch (e) { wake = 'error: ' + e.message; }
    }
    const report = {ua: navigator.userAgent, secure: isSecureContext,
      settings: track.getSettings(), maxW: caps.width && caps.width.max,
      maxH: caps.height && caps.height.max, torch: 'torch' in caps,
      imageCapture: 'ImageCapture' in window, wakeLock: wake};
    log(JSON.stringify(report, null, 1));
    await fetch('/report', {method: 'POST', body: JSON.stringify(report)});
    const canvas = document.createElement('canvas');
    setInterval(() => {
      canvas.width = video.videoWidth; canvas.height = video.videoHeight;
      canvas.getContext('2d').drawImage(video, 0, 0);
      canvas.toBlob(async b => {
        try { const r = await fetch('/frame', {method: 'POST', body: b});
              log('sent ' + b.size + 'B ' + canvas.width + 'x' + canvas.height + ' -> ' + r.status); }
        catch (e) { log('POST ERR ' + e.message); }
      }, 'image/jpeg', 0.85);
    }, 1000);
  } catch (e) { log('ERR ' + e.name + ': ' + e.message); }
};
</script>"""


def lan_ip() -> str:
    # UDP connect sends no packets; it just asks the kernel which interface it would use.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: bytes = b"", ctype: str = "text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._reply(200, PAGE, "text/html")
        else:
            self._reply(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return self._reply(413)
        body = self.rfile.read(length)
        if self.path == "/report":
            print("REPORT", json.dumps(json.loads(body), indent=1), file=sys.stderr)
        elif self.path == "/frame":
            if body[:2] != b"\xff\xd8":
                return self._reply(400, b"not a jpeg")
            (FRAMES / "latest.jpg").write_bytes(body)
            print(f"FRAME {len(body)}B from {self.client_address[0]}", file=sys.stderr)
        else:
            return self._reply(404)
        self._reply(200, b"ok")

    def log_message(self, *args):  # FRAME/REPORT lines are enough
        pass


def main():
    ip = lan_ip()
    FRAMES.mkdir(exist_ok=True)
    if not CERT.exists():
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30",
             "-keyout", str(KEY), "-out", str(CERT), "-subj", "/CN=framegrab-spike",
             "-addext", f"subjectAltName=IP:{ip},DNS:localhost"],
            check=True, capture_output=True,
        )  # fmt: skip
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    url = f"https://{ip}:{PORT}/"
    print(f"Open on the phone: {url}   (started {time.strftime('%H:%M:%S')})", file=sys.stderr)
    if shutil.which("qrencode"):  # optional nicety
        subprocess.run(["qrencode", "-t", "ANSIUTF8", url], check=False)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
