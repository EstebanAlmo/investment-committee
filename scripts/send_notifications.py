"""Send the queued notifications to Telegram - NOT part of the automated tests
and NOT scheduled by anything in this repository.

Without --enviar it only LISTS what is pending (dry run, nothing sent,
nothing marked). With --enviar it sends each PENDIENTE message to the
allow-listed users of TELEGRAM_ALLOWED_USER_IDS (.env) and marks it ENVIADA.
Plain text only: no button can execute anything.

Usage, from the repository root:
    .venv\\Scripts\\python.exe scripts\\send_notifications.py            (dry run)
    .venv\\Scripts\\python.exe scripts\\send_notifications.py --enviar   (sends; needs your authorization)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from investment_committee.application.notifications import mark, pending
from investment_committee.memory.database import DEFAULT_DB_PATH, connect


async def _send(texts: list[tuple[int, str]], db: Path) -> int:
    from telegram import Bot

    from investment_committee.interfaces.telegram.config import load_config
    from investment_committee.interfaces.telegram.notifier import TelegramNotifier

    config = load_config(os.environ)
    sent = 0
    async with Bot(config.bot_token) as bot:
        notifier = TelegramNotifier(bot, config.allowed_user_ids)
        for notification_id, text in texts:
            await notifier.send(text)
            with connect(db) as conn:
                mark(conn, notification_id, "ENVIADA", datetime.now(timezone.utc))
            sent += 1
    return sent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enviar", action="store_true")
    parser.add_argument("--base", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    with connect(args.base) as conn:
        items = pending(conn)
    if not args.enviar:
        print(f"{len(items)} notificaciones pendientes (ensayo: no se envía nada):")
        for n in items:
            print(f"--- #{n.notification_id} {n.kind} {n.created_at}\n{n.text}")
        return 0
    load_dotenv(find_dotenv(usecwd=True))
    print(f"Enviadas: {asyncio.run(_send([(n.notification_id, n.text) for n in items], args.base))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
