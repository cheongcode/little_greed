import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_NOTIFY_URL = os.getenv("NOTIFY_URL", "")


def notify(title: str, body: str, priority: str = "default") -> None:
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    if _TELEGRAM_BOT_TOKEN and _TELEGRAM_CHAT_ID:
        try:
            import requests
            url = f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(
                url,
                json={
                    "chat_id": _TELEGRAM_CHAT_ID,
                    "text": f"*{title}*\n{body}",
                    "parse_mode": "Markdown",
                },
                timeout=5,
            )
        except Exception as exc:
            with open(logs_dir / "notify_errors.log", "a") as f:
                f.write(f"[telegram] {title}: {exc}\n")

    if _NOTIFY_URL:
        try:
            import requests
            requests.post(
                _NOTIFY_URL,
                data=body.encode(),
                headers={"Title": title, "Priority": priority},
                timeout=5,
            )
        except Exception as exc:
            with open(logs_dir / "notify_errors.log", "a") as f:
                f.write(f"[ntfy] {title}: {exc}\n")
