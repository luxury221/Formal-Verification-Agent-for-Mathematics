"""Independent progress worker; original providers own mathematical evidence."""

import sqlite3
import threading

from .tasks import WorkerBusy


class ArticleRecheckPump:
    def __init__(self, store):
        self.store = store
        self.stopped = threading.Event()
        self.store.stop_requested = self.stopped.is_set
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.status = {"state": "STARTING", "error": None}
        self.thread = threading.Thread(target=self.run, name="mra-article-recheck-worker")

    def set_status(self, state, error=None):
        with self.lock:
            self.status = {"state": state, "error": error}

    def snapshot(self):
        with self.lock:
            return dict(self.status)

    def run(self):
        while not self.stopped.is_set():
            try:
                # Retain ownership during idle periods and between graph reads.
                # Another server cannot mistake a live owner for a crashed one.
                with self.store.worker_session():
                    self.store.recover()
                    while not self.stopped.is_set():
                        result = self.store.work_once()
                        state = result["worker_state"]
                        self.set_status(state)
                        if state == "EXECUTED":
                            continue
                        self.wake.wait(1)
                        self.wake.clear()
            except WorkerBusy:
                self.set_status("BUSY", "Another article recheck worker owns the progress queue")
            except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                self.set_status("UNAVAILABLE", str(error)[:2000])
            if not self.stopped.is_set():
                self.wake.wait(1)
                self.wake.clear()
        self.set_status("STOPPED")

    def close(self):
        self.stopped.set()
        self.wake.set()
        # The default isolated reader observes this stop event, kills/reaps its
        # process and discards its observation. An explicitly injected in-process
        # provider still owns its own interruption contract.
        if self.thread.ident is not None:
            self.thread.join()
