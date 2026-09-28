"""Dashboard de transportistas .

Muestra los pedidos disponibles que llegan por la cola de pedidos validados y permite
que un transportista acepte uno. 

Comandos:  Enter = refrescar | a <#|id> = aceptar | v <#|id> = ver detalle | q = salir
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from newcron import settings, topics
from newcron.broker import (
    GuaranteedPublisher,
    connect_or_exit,
    open_queue_receiver_or_exit,
    payload_text,
    shutdown,
)
from newcron.console import clear, log, truncate


class OrderBoard:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._orders: "OrderedDict[str, dict]" = OrderedDict()
        self._messages: Dict[str, object] = {}

    def add(self, order_id: str, order: dict, message: object) -> bool:
        with self._lock:
            if order_id in self._orders:
                return False
            self._orders[order_id] = order
            self._messages[order_id] = message
            return True

    def snapshot(self) -> List[Tuple[str, dict]]:
        with self._lock:
            return list(self._orders.items())

    def resolve(self, ref: str) -> Optional[str]:
        with self._lock:
            if ref in self._orders:
                return ref
            if ref.isdigit() and 1 <= int(ref) <= len(self._orders):
                return list(self._orders.keys())[int(ref) - 1]
        return None

    def get(self, order_id: str) -> Optional[dict]:
        with self._lock:
            return self._orders.get(order_id)

    def take(self, order_id: str):
        with self._lock:
            self._orders.pop(order_id, None)
            return self._messages.pop(order_id, None)


def _flush_acks(receiver, ack_queue: "queue.Queue") -> None:
    while True:
        try:
            message = ack_queue.get_nowait()
        except queue.Empty:
            return
        receiver.ack(message)


def receiver_worker(receiver, board: OrderBoard, ack_queue: "queue.Queue", stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            _flush_acks(receiver, ack_queue)
            message = receiver.receive_message(500)
            if message is None:
                continue
            try:
                order = json.loads(payload_text(message))
                order_id = str(order["shipperOrderId"])
            except (ValueError, KeyError, TypeError):
                log("Mensaje ilegible descartado de la cola de pedidos.")
                receiver.ack(message)
                continue
            if board.add(order_id, order, message):
                log(f">> New order available: {order_id}  (Enter para refrescar)")
            else:
                log(f"Pedido duplicado {order_id} descartado.")
                receiver.ack(message)
        except Exception as exc:
            log(f"Error en el receptor: {exc}")
            time.sleep(1)
    try:
        _flush_acks(receiver, ack_queue)
    except Exception:
        pass


def _route(order: dict) -> str:
    stops = order.get("stops") or []
    if not stops:
        return "-"

    def place(stop: dict) -> str:
        return f"{stop.get('city', '?')}, {stop.get('state', '?')}"

    return f"{place(stops[0])} -> {place(stops[-1])}"


def _vehicles(order: dict) -> str:
    vehicles = order.get("vehicles") or []
    if not vehicles:
        return "-"
    first = vehicles[0]
    text = f"{first.get('year', '')} {first.get('make', '')} {first.get('model', '')}".strip()
    return text if len(vehicles) == 1 else f"{text} (+{len(vehicles) - 1})"


def render(board: OrderBoard, carrier: str) -> None:
    clear()
    rows = board.snapshot()
    print(f"=== NEWCRON - CARRIER DASHBOARD | carrier: {carrier} | available orders: {len(rows)} ===\n")
    header = f"{'#':>3}  {'Order':<10} {'Pickup':<11} {'Delivery':<11} {'Route':<34} {'Vehicles':<26} {'Price':>9}"
    print(header)
    print("-" * len(header))
    if not rows:
        print("  (no available orders yet - waiting for new requests...)")
    for index, (order_id, order) in enumerate(rows, start=1):
        price = order.get("price")
        price_text = f"${price:,.2f}" if isinstance(price, (int, float)) else "-"
        print(
            f"{index:>3}  {truncate(order_id, 10):<10} {order.get('pickupDate', '-'):<11} "
            f"{order.get('deliveryDate', '-'):<11} {truncate(_route(order), 34):<34} "
            f"{truncate(_vehicles(order), 26):<26} {price_text:>9}"
        )
    print("\nEnter = refresh | a <# or id> = accept | v <# or id> = details | q = quit")


def show_details(board: OrderBoard, ref: str) -> None:
    order_id = board.resolve(ref)
    if order_id is None:
        print(f"Order '{ref}' not found.")
        return
    print(json.dumps(board.get(order_id), indent=2))


def accept_order(ref: str, carrier: str, board: OrderBoard, publisher: GuaranteedPublisher, ack_queue: "queue.Queue") -> None:
    order_id = board.resolve(ref)
    if order_id is None:
        print(f"Order '{ref}' not found. Use the # or the order id from the table.")
        return
    order = board.get(order_id)

    assigned_event = {"shipperOrderId": order_id, "carrier": carrier, "order": order}
    shipper_result = {
        "shipperOrderId": order_id,
        "status": "CarrierAssigned",
        "carrier": carrier,
        "notes": f"Carrier {carrier} accepted this dispatch request.",
    }
    try:
        publisher.publish(topics.for_order(topics.ORDER_ASSIGNED, order_id), json.dumps(assigned_event), order_id)
        publisher.publish(topics.for_order(topics.RESULT_CARRIER_ASSIGNED, order_id), json.dumps(shipper_result), order_id)
    except Exception as exc:
        print(f"Could not accept order {order_id}: {exc}. It stays available.")
        return

    message = board.take(order_id)
    if message is not None:
        ack_queue.put(message)
    print(f"Order {order_id} accepted by {carrier}. The shipper has been notified.")


def main() -> int:
    carrier = settings.CARRIER_NAME or input("Carrier name: ").strip() or "carrier"

    service = connect_or_exit()
    publisher = GuaranteedPublisher(service)
    receiver = open_queue_receiver_or_exit(service, settings.QUEUE_AVAILABLE, topics.SUBSCRIPTION_AVAILABLE)

    board = OrderBoard()
    ack_queue: "queue.Queue" = queue.Queue()
    stop = threading.Event()
    worker = threading.Thread(target=receiver_worker, args=(receiver, board, ack_queue, stop), daemon=True)
    worker.start()

    time.sleep(1.0) 
    render(board, carrier)
    try:
        while True:
            try:
                command = input("carrier> ").strip()
            except EOFError:
                break
            if command in ("q", "quit", "exit"):
                break
            if command == "":
                render(board, carrier)
            elif command.startswith("a "):
                accept_order(command[2:].strip(), carrier, board, publisher, ack_queue)
            elif command.startswith("v "):
                show_details(board, command[2:].strip())
            else:
                print("Unknown command. Use: Enter, a <#|id>, v <#|id>, q")
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        worker.join(timeout=3)
        shutdown(service, receiver, publisher)
        print("\nBye. Orders you did not accept remain in the queue for other carriers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
