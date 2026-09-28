"""Dashboard de clientes (consola, tiempo real).

Consume la cola de resultados y muestra el estado de cada solicitud enviada:
  Accepted        -> la solicitud paso las validaciones y ya esta disponible para los transportistas
  Cancelled       -> el payload no era valido (el motivo va en "notes")
  CarrierAssigned -> un transportista tomo el pedido

"""
from __future__ import annotations

import json
import sys
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from newcron import settings, topics
from newcron.broker import connect_or_exit, open_queue_receiver_or_exit, payload_text, shutdown
from newcron.console import clear, log, truncate

HISTORY_FILE = Path(__file__).resolve().parent / "data" / "shipper_history.jsonl"


class ResultsBoard:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: "OrderedDict[str, dict]" = OrderedDict()
        self._changed = threading.Event()

    def record(self, event: dict) -> None:
        key = str(event.get("shipperOrderId") or "unknown")
        with self._lock:
            self._latest.pop(key, None) 
            self._latest[key] = event
        self._changed.set()

    def snapshot(self) -> List[dict]:
        with self._lock:
            return list(reversed(self._latest.values())) 

    def wait_for_change(self, timeout: float) -> bool:
        if self._changed.wait(timeout):
            self._changed.clear()
            return True
        return False


def load_history(board: ResultsBoard) -> int:
    if not HISTORY_FILE.exists():
        return 0
    count = 0
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        try:
            board.record(json.loads(line))
            count += 1
        except ValueError:
            continue
    return count


def save_event(event: dict) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def receiver_worker(receiver, board: ResultsBoard, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            message = receiver.receive_message(500)
            if message is None:
                continue
            try:
                event = json.loads(payload_text(message))
                if not isinstance(event, dict):
                    raise ValueError("not an object")
            except ValueError:
                log("Mensaje ilegible descartado de la cola de resultados.")
                receiver.ack(message)
                continue
            event["receivedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_event(event)      
            board.record(event)
            receiver.ack(message)  
        except Exception as exc:
            log(f"Error en el receptor: {exc}")
            stop.wait(1)


def render(board: ResultsBoard) -> None:
    clear()
    rows = board.snapshot()
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row.get("status", "?")] = counts.get(row.get("status", "?"), 0) + 1
    summary = " | ".join(f"{status}: {n}" for status, n in sorted(counts.items())) or "no requests yet"

    print(f"=== NEWCRON - SHIPPER DASHBOARD | {summary} ===\n")
    header = f"{'Received':<20} {'Order':<10} {'Status':<16} Notes"
    print(header)
    print("-" * 100)
    if not rows:
        print("  (waiting for results... send requests with shipper_submit.py)")
    for row in rows:
        notes = row.get("notes", "")
        if row.get("carrier"):
            notes = f"[{row['carrier']}] {notes}"
        print(
            f"{row.get('receivedAt', '-'):<20} {truncate(row.get('shipperOrderId'), 10):<10} "
            f"{truncate(row.get('status'), 16):<16} {truncate(notes, 70)}"
        )
    print("\nLive view - updates automatically. Ctrl+C to exit.")


def main() -> int:
    board = ResultsBoard()
    restored = load_history(board)

    service = connect_or_exit()
    receiver = open_queue_receiver_or_exit(service, settings.QUEUE_RESULTS, topics.SUBSCRIPTION_RESULTS, exclusive=True)

    stop = threading.Event()
    worker = threading.Thread(target=receiver_worker, args=(receiver, board, stop), daemon=True)
    worker.start()

    render(board)
    if restored:
        print(f"({restored} historical events restored from {HISTORY_FILE.name})")
    try:
        while True:
            if board.wait_for_change(1.0):
                render(board)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        worker.join(timeout=3)
        shutdown(service, receiver)
        print("\nBye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
