"""Before/after execution proof over a sealed local HTTP origin.

usage: run_proof.py <out dir> <label>=<tree> [<label>=<tree> ...]

Each tree must hold backend/ and this directory under tests/. The app runs in a --network none
container on a Unix socket. A loopback proxy on this host forwards to that socket, and headless
Chromium reaches only that proxy: every other host is refused by proxy and resolver rules.
Payloads are synthetic. Nothing reaches a provider or any external host.
"""
import html
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse as up
import urllib.request

IMAGE = "ridecheck-crm-test:l4.7b2"
CHROME = os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-1223/"
                            "chrome-headless-shell-linux64/chrome-headless-shell")
ATTACKS = {
    "img": "<img src=x onerror=__pwn.push('@')>",
    "script": "<script>__pwn.push('@')</script>",
    "dq": '" onmouseover="__pwn.push(`@`)" x="',
    "attrtag": "\"><img src=x onerror=__pwn.push('@')>",
    "svg": "<svg onload=__pwn.push('@')>",
}
FIELDS = ("q", "marca", "modelo", "tipo_vehiculo", "anio", "zone_group", "zone_detail",
          "estado_revision", "canal", "estado", "flag")


def pump(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    for s in (a, b):
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def proxy(path):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(32)

    def loop():
        while True:
            try:
                client, _ = server.accept()
            except OSError:
                return
            upstream = socket.socket(socket.AF_UNIX)
            upstream.connect(path)
            threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
            threading.Thread(target=pump, args=(upstream, client), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return server


def visit(url):
    run = subprocess.run(
        [CHROME, "--no-sandbox", "--disable-gpu", "--headless", "--proxy-server=127.0.0.1:9",
         "--proxy-bypass-list=<-loopback>;127.0.0.1", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
         "--virtual-time-budget=9000", "--dump-dom", url],
        capture_output=True, text=True, timeout=120)
    match = re.search(r'<pre id="__result">(.*?)</pre>', run.stdout, re.S)
    if match:
        return json.loads(html.unescape(match.group(1)))
    return {"NO_RESULT": True, "dom_bytes": len(run.stdout)}


def pages(base, thread):
    urls = {name: base + path for name, path in (
        ("kanban_stored", "/kanban"), ("table_stored", "/table"),
        ("profesionales_stored", "/profesionales"), ("agencias_stored", "/agencias"),
        ("control_stored", "/control"), ("inbox_stored", "/whatsapp/inbox"),
        ("thread_stored", f"/whatsapp/thread/{thread}"))}
    for a, attack in ATTACKS.items():
        for route, fields in (("kanban", FIELDS), ("table", FIELDS)):
            query = up.urlencode({f: attack.replace("@", f"{a}:{route}.{f}") for f in fields})
            urls[f"{route}_reflect_{a}"] = f"{base}/{route}?{query}"
        if "/" in attack:       # an encoded slash is not a path segment: the route 404s
            continue
        turn = up.quote(attack.replace("@", f"{a}:turn"), safe="")
        urls[f"control_turn_reflect_{a}"] = f"{base}/control/turn/{turn}"
        urls[f"thread_reflect_{a}"] = f"{base}/whatsapp/thread/" + up.quote(
            attack.replace("@", f"{a}:thread"), safe="")
    return urls


def main():
    out = sys.argv[1]
    here = os.path.dirname(os.path.abspath(__file__))
    report = {}
    for pair in sys.argv[2:]:
        label, tree = pair.split("=", 1)
        sock_dir = os.path.join(out, f"sock_{label}")
        os.makedirs(sock_dir, exist_ok=True)
        os.chmod(sock_dir, 0o777)
        path = os.path.join(sock_dir, "app.sock")
        name = f"escproof-{label}"
        server = subprocess.Popen(
            ["docker", "run", "--rm", "--name", name, "--network", "none",
             "-e", "DATABASE_URL=sqlite://", "-e", "PYTHONDONTWRITEBYTECODE=1",
             "-v", f"{tree}:/work:ro", "-v", f"{sock_dir}:/sock", "-v", f"{here}:/proof:ro",
             "-w", "/work/backend", IMAGE, "python", "/proof/serve_hostile.py",
             "/sock/app.sock", "/proof/driver.js"],
            stdout=open(os.path.join(out, f"server_{label}.log"), "w"), stderr=subprocess.STDOUT)
        try:
            for _ in range(240):
                if os.path.exists(path):
                    break
                time.sleep(0.25)
            time.sleep(1)
            listener = proxy(path)
            base = f"http://127.0.0.1:{listener.getsockname()[1]}"
            thread = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                base + "/__thread", timeout=30).read().decode()
            for key, url in pages(base, thread).items():
                report[f"{label}/{key}"] = visit(url)
            listener.close()
        finally:
            subprocess.run(["docker", "stop", "-t", "2", name], capture_output=True)
            server.wait(timeout=30)
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    totals = {}
    for key, r in report.items():
        label = key.split("/")[0]
        t = totals.setdefault(label, {"pages": 0, "no_result": 0, "executions": 0, "img": 0,
                                      "script": 0, "handlers": 0})
        t["pages"] += 1
        if "NO_RESULT" in r:
            t["no_result"] += 1
            print(key, "NO RESULT", r["dom_bytes"])
            continue
        t["executions"] += len(r["pwn"])
        t["img"] += r["dom"]["injectedImg"]
        t["script"] += r["dom"]["injectedScript"]
        t["handlers"] += r["dom"]["handlers"]
        print(f"{key:40} exec {len(r['pwn']):3} img {r['dom']['injectedImg']:3} "
              f"script {r['dom']['injectedScript']:3} handlers {r['dom']['handlers']:3} "
              f"{r['actions']} {r['panels']}")
    print("TOTALS", json.dumps(totals))


if __name__ == "__main__":
    main()
