"""Persistent research service; no second Telegram poller and no financial writes."""
import os
from pathlib import Path
import signal
import threading
import time

from iva_integration.tender_research import Research, DATABASE, ARTIFACTS
from iva_integration.memory_scheduler import lock


def main():
    from local_finance.bot import load_env, Telegram, OWNER
    from iva_integration.tender_research import ROOT
    load_env(ROOT/'.env')
    if os.environ.get('FINANCE_ALLOWED_USER_ID')!=str(OWNER):raise ValueError('Owner configuration mismatch')
    telegram=Telegram(os.environ['TELEGRAM_BOT_TOKEN'])
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    with lock(ROOT/'data/tender-research.worker.lock'):
        research=Research()
        # An interrupted send may already have reached Telegram. Never resend it.
        with research.db:research.db.execute("UPDATE research_outbox SET state='uncertain' WHERE state='sending'")
        def pulse():
            connection=Research()
            try:
                while not stop.wait(20):connection.heartbeat()
            finally:connection.close()
        threading.Thread(target=pulse,daemon=True).start()
        try:
            while not stop.is_set():
                research.step()
                research.deliver_one(telegram,OWNER)
                stop.wait(2)
        finally:research.close()


if __name__=='__main__':main()
