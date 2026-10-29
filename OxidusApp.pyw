"""OxidusApp.pyw - non-blocking app launcher (no prompts, no WSL, no Electron)."""
import os, secrets, socket, subprocess, sys, time, shutil
from pathlib import Path
ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
(ROOT / "logs").mkdir(exist_ok=True)
URL = "http://127.0.0.1:5000"
def port_open():
    try:
        with socket.create_connection(("127.0.0.1", 5000), timeout=1): return True
    except OSError: return False
tok = ROOT / "config" / "admin_token.txt"
if not os.environ.get("OXIDUS_ADMIN_TOKEN"):
    if not tok.exists():
        tok.parent.mkdir(exist_ok=True)
        tok.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    os.environ["OXIDUS_ADMIN_TOKEN"] = tok.read_text(encoding="utf-8").strip()
if not port_open():
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    log = open(ROOT / "logs" / "oxidus_app_server.log", "a", encoding="utf-8")
    subprocess.Popen([str(py), "web_gui.py"], cwd=ROOT, stdout=log, stderr=log,
                     creationflags=0x08000000)
    for _ in range(180):
        if port_open(): break
        time.sleep(1)
edge = shutil.which("msedge") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if Path(edge).exists():
    subprocess.Popen([edge, f"--app={URL}"])
else:
    import webbrowser; webbrowser.open(URL)
