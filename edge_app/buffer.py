"""Store-and-forward for MQTT: no telemetry is lost while the broker / IT network is down.

Design (an "outbox"):
  - EVERY message is first written to a SQLite table (id = arrival order). Nothing is published
    from the main loop directly.
  - One background thread sends the oldest row, waits for the broker's PUBACK (MQTT QoS 1), and
    only then deletes the row. Exactly one message is in flight, so the order is always kept.
  - While the broker is down the thread waits; rows pile up in SQLite. After reconnect it drains
    them in order, then keeps up with new messages.
  - Messages stored while the broker was down get "buffered": true, so the dashboard can show
    that they arrived late.

Why SQLite and not paho's own queue: paho keeps unsent QoS 1 messages in memory only. A restart
of the edge app (power cut, container update) would lose them. The SQLite file survives that.

Delivery is "at least once": if the connection breaks after the broker got a message but before
we got the PUBACK, that message is sent again. Consumers drop duplicates by `seq`.
The buffer has a size limit (BUFFER_MAX_ROWS); when full, the OLDEST rows are dropped and logged.
"""
import json
import logging
import os
import sqlite3
import threading

import paho.mqtt.client as mqtt

BUFFER_PATH = os.getenv("BUFFER_PATH", "./data/outbox.db")
BUFFER_MAX_ROWS = int(os.getenv("BUFFER_MAX_ROWS", "100000"))  # ~5.5 h at 5 Hz

log = logging.getLogger("buffer")


class Outbox:
    """Ordered, persistent message queue on SQLite. Thread-safe (one lock around the connection)."""

    def __init__(self, path: str = BUFFER_PATH, max_rows: int = BUFFER_MAX_ROWS):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")    # fast appends, safe on crash
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS outbox ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, payload TEXT)")
        self.db.commit()
        self.max_rows = max_rows
        self.lock = threading.Lock()
        self.dropped = 0

    def put(self, topic: str, payload: str):
        with self.lock:
            self.db.execute("INSERT INTO outbox (topic, payload) VALUES (?, ?)", (topic, payload))
            n = self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            if n > self.max_rows:  # full: drop the oldest rows
                extra = n - self.max_rows
                self.db.execute("DELETE FROM outbox WHERE id IN "
                                "(SELECT id FROM outbox ORDER BY id LIMIT ?)", (extra,))
                self.dropped += extra
                log.warning("outbox full (%d rows): dropped %d oldest message(s)", self.max_rows, extra)
            self.db.commit()

    def oldest(self):
        """(id, topic, payload) of the oldest row, or None."""
        with self.lock:
            return self.db.execute("SELECT id, topic, payload FROM outbox ORDER BY id LIMIT 1").fetchone()

    def delete(self, row_id: int):
        with self.lock:
            self.db.execute("DELETE FROM outbox WHERE id = ?", (row_id,))
            self.db.commit()

    def __len__(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


class StoreAndForward:
    """Sends Outbox rows over MQTT in order, one at a time, deleting each after its PUBACK."""

    def __init__(self, client: mqtt.Client, outbox: Outbox):
        self.client = client
        self.outbox = outbox
        self.acked: set[int] = set()        # MQTT message ids confirmed by the broker
        self.cond = threading.Condition()   # wakes the sender: new row or new PUBACK
        self.stopping = False
        client.on_publish = self._on_publish
        self.thread = threading.Thread(target=self._run, name="store-and-forward", daemon=True)
        self.thread.start()

    def send(self, topic: str, msg: dict):
        """Called from the main loop for every telemetry message."""
        msg["buffered"] = not self.client.is_connected()
        msg["buffer_backlog"] = len(self.outbox)
        self.outbox.put(topic, json.dumps(msg))
        with self.cond:
            self.cond.notify()

    def stop(self):
        with self.cond:
            self.stopping = True
            self.cond.notify()
        self.thread.join(timeout=2)

    def _on_publish(self, client, userdata, mid, reason_code, properties):
        # paho calls this (in its network thread) when the broker acknowledges a QoS 1 message.
        with self.cond:
            self.acked.add(mid)
            self.cond.notify()

    def _run(self):
        while not self.stopping:
            row = self.outbox.oldest()
            if row is None or not self.client.is_connected():
                with self.cond:
                    self.cond.wait(timeout=0.2)  # nothing to send, or broker down: wait
                continue

            row_id, topic, payload = row
            with self.cond:
                self.acked.clear()  # forget old acks (e.g. the status message) before a new send
            info = self.client.publish(topic, payload, qos=1)
            # If the connection drops now, paho keeps this message in memory and re-sends it after
            # reconnect; on_publish then fires with the same mid. So we simply wait for that PUBACK
            # (never publish the same row twice ourselves -> order kept, no message-id confusion).
            with self.cond:
                self.cond.wait_for(lambda: info.mid in self.acked or self.stopping)
                acked = info.mid in self.acked
                self.acked.discard(info.mid)
            if acked:
                self.outbox.delete(row_id)
            # Stopped before the PUBACK: the row stays in SQLite and is sent again after a restart
            # (the broker may then see it twice: at least once delivery).
