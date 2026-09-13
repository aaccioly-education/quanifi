#!/usr/bin/env python3
"""Screenshot a NiFi process group, and one close-up per labelled block.

Written for the AllComponents group (tools/add_all_components_group.py), whose
only purpose is to be photographed for the documentation, but it works on any
group: every label becomes a block, and each processor is assigned to the
nearest label at or to its left, which is how that tool lays them out.

Two things here are deliberate rather than incidental.

It drives headless Chrome over the DevTools protocol with a hand-rolled
websocket client (about sixty lines below) instead of adding Playwright or
Selenium. The whole job is: set one cookie, navigate, screenshot. A browser
automation dependency for that is not worth the install.

And it measures the rendered geometry rather than computing it. After the
canvas settles it reads getBoundingClientRect() for every processor and label
in the same session that takes the screenshot, so the crops cannot drift from
the image no matter what zoom NiFi chose.

Authentication: NiFi's single-user provider stores a bcrypt hash, so a password
is required to mint a token. Supply it out of band --

    export NIFI_USER=... NIFI_PASSWORD=...
    python tools/nifi_screenshot.py --group <uuid> --out-dir docs/screenshots

-- or point --token-file at a file holding a JWT you minted yourself.

Requires Pillow, Chrome, and a running NiFi holding the group.
"""
import base64
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9222
NIFI = os.environ.get("NIFI_URL", "https://localhost:8443")
COOKIE = "__Secure-Authorization-Bearer"


class WS:
    """Minimum viable websocket client: handshake, send text, receive text."""

    def __init__(self, url):
        _, rest = url.split("://", 1)
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if accept.encode() not in buf:
            raise RuntimeError("websocket handshake rejected")
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def _send_frame(self, payload):
        data = payload.encode()
        header = bytearray([0x81])           # FIN + text
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 1 << 16:
            header.append(0x80 | 126); header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127); header += struct.pack(">Q", n)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv_frame(self):
        b0, b1 = self._read(2)
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read(8))[0]
        payload = self._read(length)
        if b0 & 0x0F == 0x08:                # close
            raise RuntimeError("closed by peer")
        return payload.decode("utf-8", "replace")

    def call(self, method, params=None, timeout=90):
        self.next_id += 1
        mid = self.next_id
        self._send_frame(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self._recv_frame())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)


def start_chrome(profile):
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--ignore-certificate-errors",
         "--hide-scrollbars", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={profile}", "--window-size=2000,1300", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as r:
                json.load(r)
                return proc
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("chrome did not expose its debug port")


def start_chrome(profile, width=2000, height=1300):
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--ignore-certificate-errors",
         "--hide-scrollbars", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={profile}", f"--window-size={width},{height}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as r:
                json.load(r)
                return proc
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("chrome did not expose its debug port")


def page_target():
    """Reuse the about:blank tab Chrome already opened.

    /json/new wants PUT in current Chrome and answers 405 to anything else, so
    taking the existing page target is both simpler and version-proof.
    """
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=10) as r:
        targets = json.load(r)
    pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if pages:
        return pages[0]["webSocketDebuggerUrl"]
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)["webSocketDebuggerUrl"]



HIDE_PANELS_CSS = """
  navigation-control, operation-control, .navigation-control, .operation-control,
  .birdseye-container, .context-menu { display: none !important; }
"""


def js(ws, expression, timeout=90):
    r = ws.call("Runtime.evaluate",
                {"expression": expression, "returnByValue": True}, timeout=timeout)
    return r.get("result", {}).get("value")


def api(path, token):
    req = urllib.request.Request(NIFI + "/nifi-api" + path,
                                 headers={"Authorization": "Bearer " + token})
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return json.load(r)


def mint_token(user, password):
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    data = urllib.parse.urlencode({"username": user, "password": password}).encode()
    req = urllib.request.Request(NIFI + "/nifi-api/access/token", data=data)
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return r.read().decode().strip()


def capture(token, group, png_path, rects_path, width, height, scale, wait):
    # The Chrome profile is a few dozen megabytes of caches and must not land in
    # the output directory, which is a documentation folder under version control.
    import tempfile, shutil
    profile = tempfile.mkdtemp(prefix="nifi-shot-")
    proc = start_chrome(profile, width, height)
    try:
        ws = WS(page_target())
        for domain in ("Network", "Page", "Runtime"):
            ws.call(domain + ".enable")
        ws.call("Network.setCookie", {"name": COOKIE, "value": token,
                                      "domain": "localhost", "path": "/",
                                      "secure": True, "url": NIFI + "/"})
        ws.call("Emulation.setDeviceMetricsOverride",
                {"width": width, "height": height,
                 "deviceScaleFactor": scale, "mobile": False})
        ws.call("Page.navigate", {"url": "%s/nf/#/process-groups/%s" % (NIFI, group)})
        time.sleep(wait)

        js(ws, """
          (() => {
            const b = [...document.querySelectorAll('button')].find(x => /fit/i.test(
              (x.getAttribute('title') || '') + (x.getAttribute('aria-label') || '')));
            if (b) b.click();
          })()
        """)
        time.sleep(3)
        js(ws, "(() => { const s = document.createElement('style');"
               "s.textContent = %s; document.head.appendChild(s); })()"
               % json.dumps(HIDE_PANELS_CSS))
        time.sleep(1)

        rects = js(ws, """
          (() => {
            const out = {};
            document.querySelectorAll('g.processor, g.label').forEach(g => {
              const id = (g.getAttribute('id') || '').replace(/^id-/, '');
              const r = g.getBoundingClientRect();
              if (id && r.width > 0) out[id] = [r.left, r.top, r.width, r.height];
            });
            return JSON.stringify(out);
          })()
        """)
        open(rects_path, "w").write(rects)
        shot = ws.call("Page.captureScreenshot", {"format": "png"})
        open(png_path, "wb").write(base64.b64decode(shot["data"]))
    finally:
        proc.terminate()
        time.sleep(1)
        shutil.rmtree(profile, ignore_errors=True)


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+\d+$", "", s)


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--group", required=True, help="process group UUID")
    ap.add_argument("--out-dir", default="docs/screenshots")
    ap.add_argument("--token-file", help="file holding a NiFi JWT (else NIFI_USER/NIFI_PASSWORD)")
    ap.add_argument("--width", type=int, default=3400)
    ap.add_argument("--height", type=int, default=1500)
    ap.add_argument("--scale", type=float, default=2.0, help="device pixel ratio")
    ap.add_argument("--wait", type=float, default=16.0, help="seconds for the canvas to settle")
    ap.add_argument("--overview-only", action="store_true")
    args = ap.parse_args()

    from PIL import Image

    if args.token_file:
        token = open(args.token_file).read().strip()
    else:
        user, password = os.environ.get("NIFI_USER"), os.environ.get("NIFI_PASSWORD")
        if not (user and password):
            raise SystemExit("set NIFI_USER and NIFI_PASSWORD, or pass --token-file")
        token = mint_token(user, password)

    out = args.out_dir
    os.makedirs(out, exist_ok=True)
    png = os.path.join(out, "_capture.png")
    rects_path = os.path.join(out, "_capture.rects.json")
    capture(token, args.group, png, rects_path,
            args.width, args.height, args.scale, args.wait)

    flow = api("/flow/process-groups/" + args.group, token)["processGroupFlow"]["flow"]
    rects = json.load(open(rects_path))
    image = Image.open(png)
    dpr = image.size[0] / float(args.width)

    labels = sorted(((l["component"]["position"]["x"], l["component"]["label"],
                      l["component"]["id"]) for l in flow["labels"]), key=lambda t: t[0])
    if not labels:
        raise SystemExit("no labels in this group; nothing to split into blocks")

    blocks = {lid: {"name": name, "ids": [lid]} for _, name, lid in labels}
    for p in flow["processors"]:
        px = p["component"]["position"]["x"]
        owner = max((l for l in labels if l[0] <= px + 1), key=lambda t: t[0])
        blocks[owner[2]]["ids"].append(p["component"]["id"])

    # the overview: everything, with the empty canvas below the blocks trimmed off
    boxes = [rects[i] for b in blocks.values() for i in b["ids"] if i in rects]
    bottom = int((max(r[1] + r[3] for r in boxes) + 30) * dpr)
    image.crop((0, 0, image.size[0], min(image.size[1], bottom))).save(
        os.path.join(out, "all-components-overview.png"))
    print("overview -> all-components-overview.png")

    if args.overview_only:
        os.remove(png)
        os.remove(rects_path)
        return

    PAD = 26
    for lid, block in blocks.items():
        boxes = [rects[i] for i in block["ids"] if i in rects]
        if not boxes:
            continue
        x0 = min(r[0] for r in boxes) - PAD
        y0 = min(r[1] for r in boxes) - PAD
        x1 = max(r[0] + r[2] for r in boxes) + PAD
        y1 = max(r[1] + r[3] for r in boxes) + PAD
        crop = image.crop((max(0, int(x0 * dpr)), max(0, int(y0 * dpr)),
                           min(image.size[0], int(x1 * dpr)),
                           min(image.size[1], int(y1 * dpr))))
        name = slug(block["name"]) + ".png"
        crop.save(os.path.join(out, name))
        print("%-52s %3d processors -> %s" % (block["name"], len(block["ids"]) - 1, name))

    os.remove(png)
    os.remove(rects_path)


if __name__ == "__main__":
    main()
