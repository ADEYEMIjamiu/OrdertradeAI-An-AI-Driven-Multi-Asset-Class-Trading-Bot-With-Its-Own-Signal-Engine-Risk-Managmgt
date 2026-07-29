"""
One-off utility: archives your current trade_journal.db (which contains
~651 trades, mostly from before all the bug fixes -- including the
force-BUY-everything era) and starts a fresh, empty journal.

Your old data is NOT deleted -- it's renamed with a timestamp so you can
still look at it later if you want. Only trade_journal.db in the current
directory is affected.

Run from the project root:
    python3 archive_trade_journal.py
"""

import os
import shutil
from datetime import datetime

from trade_journal import init_trade_journal, DB_NAME

if not os.path.exists(DB_NAME):
    print(f"No {DB_NAME} found -- nothing to archive. A fresh one will be created.")
else:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"trade_journal_archive_{timestamp}.db"

    shutil.move(DB_NAME, archive_name)
    print(f"✅ Old journal archived as: {archive_name}")

init_trade_journal()
print(f"✅ Fresh, empty {DB_NAME} created.")
print("Win Rate / Profit Factor / Trades Closed will now reflect only NEW trades from this point forward.")
