#!/usr/bin/env python3
"""
little_greed installer — stdlib only, no external dependencies required.
Run with: python install.py
"""
import os
import platform
import subprocess
import sys
from pathlib import Path


# ── ANSI colours (suppressed on Windows if not supported) ──────────────────
def _colour(code, text):
    if platform.system() == "Windows":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7)
        except Exception:
            return text
    return f"\033[{code}m{text}\033[0m"


def green(t): return _colour("92", t)
def red(t):   return _colour("91", t)
def bold(t):  return _colour("1",  t)
def yellow(t):return _colour("93", t)


def die(msg):
    print(red(f"\n✗ {msg}"))
    sys.exit(1)


def run(cmd, check=True, capture=False):
    kw = dict(capture_output=capture, text=True) if capture else {}
    result = subprocess.run(cmd, **kw)
    if check and result.returncode != 0:
        if capture:
            print(red(result.stderr or result.stdout or ""))
        return False
    return result if capture else True


# ── 1. Python version ───────────────────────────────────────────────────────
if sys.version_info < (3, 12):
    die(f"Python 3.12+ required. You have {sys.version.split()[0]}.\n"
        "Download from https://python.org/downloads/")

# ── 2. OS check ─────────────────────────────────────────────────────────────
os_name = platform.system()
if os_name not in ("Windows", "Darwin"):
    die("Linux is not supported: IBKR TWS/Gateway does not run on Linux servers.\n"
        "Run this bot on Windows or macOS where TWS is installed.")

print(bold(f"\nlittle_greed installer  (Python {sys.version.split()[0]}, {os_name})\n"))

# ── 3. Create .venv ─────────────────────────────────────────────────────────
venv_dir = Path(".venv")
if not venv_dir.exists():
    print("Creating virtual environment…")
    run([sys.executable, "-m", "venv", str(venv_dir)])
    print(green("  ✓ .venv created"))
else:
    print(green("  ✓ .venv already exists"))

venv_python = (
    venv_dir / "Scripts" / "python.exe" if os_name == "Windows"
    else venv_dir / "bin" / "python"
)

# ── 4. Install dependencies ─────────────────────────────────────────────────
print("\nUpgrading pip…")
run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "-q"])
print(green("  ✓ pip up to date"))

print("Installing requirements (this may take a minute)…")
result = run(
    [str(venv_python), "-m", "pip", "install", "-r", "requirements.txt", "-q"],
    capture=True,
)
if result is False:
    die("pip install failed. Check your internet connection and try again.")
print(green("  ✓ All packages installed"))

# ── 5. .env setup ───────────────────────────────────────────────────────────
env_path = Path(".env")
template_path = Path(".env.template")

if not env_path.exists():
    if template_path.exists():
        env_path.write_text(template_path.read_text())
        print(green("\n  ✓ .env created from template"))
    else:
        env_path.write_text(
            "IBKR_HOST=127.0.0.1\nIBKR_PORT=7497\nIBKR_CLIENT_ID=2\n"
            "IBKR_EXEC_CLIENT_ID=3\nPAPER_TRADING=true\n"
            "PORTFOLIO_VALUE_USD=25000\nMAX_TRADE_SIZE_USD=2500\n"
            "MAX_TRADES_PER_DAY=5\nMAX_RISK_PER_TRADE_PCT=1.0\n"
            "TELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n"
        )

# Parse existing .env
env_data = {}
for line in env_path.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env_data[k.strip()] = v.strip()

# ── 6. Interactive prompts ───────────────────────────────────────────────────
print(bold("\n── Configuration ──────────────────────────────────────────"))
print("Press Enter to accept the default shown in brackets.\n")


def prompt(label, default, secret=False):
    display = "[hidden]" if secret and default else f"[{default}]"
    try:
        if secret:
            import getpass
            val = getpass.getpass(f"  {label} {display}: ").strip()
        else:
            val = input(f"  {label} {display}: ").strip()
    except (EOFError, KeyboardInterrupt):
        val = ""
    return val if val else default


ibkr_port = prompt("IBKR Port (7497=TWS paper, 4002=Gateway paper)", env_data.get("IBKR_PORT", "7497"))
portfolio = prompt("Portfolio value USD", env_data.get("PORTFOLIO_VALUE_USD", "25000"))
tg_token  = prompt("Telegram bot token (optional, Enter to skip)",
                    env_data.get("TELEGRAM_BOT_TOKEN", ""), secret=True)
tg_chat   = prompt("Telegram chat ID (optional, Enter to skip)",
                    env_data.get("TELEGRAM_CHAT_ID", ""))

env_data.update({
    "IBKR_PORT": ibkr_port,
    "PORTFOLIO_VALUE_USD": portfolio,
    "TELEGRAM_BOT_TOKEN": tg_token,
    "TELEGRAM_CHAT_ID": tg_chat,
})

# Atomic write
tmp = Path(".env.tmp")
tmp.write_text("\n".join(f"{k}={v}" for k, v in env_data.items()) + "\n")
os.replace(tmp, env_path)
print(green("  ✓ .env saved"))

# ── 7. TWS check ────────────────────────────────────────────────────────────
print(bold("\n── TWS / IB Gateway ───────────────────────────────────────"))
print("  The bot connects to TWS or IB Gateway running on this machine.")
print("  Download: https://www.interactivebrokers.com/en/trading/tws.php\n")

try:
    tws_ready = input("  Have you installed and logged into TWS/IB Gateway? [y/n]: ").strip().lower()
except (EOFError, KeyboardInterrupt):
    tws_ready = "y"

if tws_ready != "y":
    print(yellow(
        "\n  ➜ Steps:\n"
        "    1. Download TWS from interactivebrokers.com\n"
        "    2. Log in with your paper trading credentials\n"
        "    3. In TWS: Edit → Settings → API → Settings\n"
        "       ✓ Enable ActiveX and Socket Clients\n"
        "       ✓ Socket port: 7497\n"
        "    4. Re-run this installer: python install.py\n"
    ))
    sys.exit(0)

# ── 8. Connection test ───────────────────────────────────────────────────────
print("\nTesting IBKR connection…")
test_script = Path("test_ib.py")
if not test_script.exists():
    test_script.write_text(
        "from ib_async import IB\nimport os\nfrom dotenv import load_dotenv\n"
        "load_dotenv()\nib=IB()\n"
        "ib.connect(os.getenv('IBKR_HOST','127.0.0.1'),int(os.getenv('IBKR_PORT',7497)),clientId=99)\n"
        "print('Connected:',ib.isConnected())\nprint('Accounts:',ib.managedAccounts())\n"
        "ib.disconnect()\n"
    )

result = run([str(venv_python), "test_ib.py"], capture=True)
if result is False or "Connected: True" not in (result.stdout or ""):
    stderr = getattr(result, "stderr", "") or ""
    stdout = getattr(result, "stdout", "") or ""
    print(red("\n  ✗ Could not connect to IBKR."))
    print(f"    {(stderr or stdout).strip()[:200]}")
    print(yellow(
        "\n  Possible fixes:\n"
        "    • Make sure TWS or IB Gateway is open and logged in\n"
        "    • In TWS: Edit → Settings → API → Settings → Enable socket clients\n"
        "    • Check port matches IBKR_PORT in .env\n"
        "    • Once bot is running, visit http://localhost:8000/preflight\n"
    ))
    sys.exit(1)

print(green(f"  ✓ {result.stdout.strip()}"))

# ── 9. Success banner ────────────────────────────────────────────────────────
start_cmd = "start.bat" if os_name == "Windows" else "./start.sh"
try:
    print(green(bold("""
╔══════════════════════════════════════════════════════════╗
║           Install complete! Bot is ready.                ║
╚══════════════════════════════════════════════════════════╝
""")))
except UnicodeEncodeError:
    print(green(bold("""
============================================================
           Install complete! Bot is ready.
============================================================
""")))
print(f"  Start the bot:  {bold(start_cmd)}")
print(f"  Dashboard:      {bold('http://localhost:8000')}")
print(f"  Run preflight:  {bold('http://localhost:8000/preflight')}\n")
