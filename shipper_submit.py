"""Simula a un cliente enviando solicitudes de carga a NewCron.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import List, Tuple

from newcron import settings, topics
from newcron.broker import GuaranteedPublisher, connect_or_exit, shutdown
from newcron.console import fail, log
from newcron.validator import extract_order_id


def _base_order(order_id: str, pickup, delivery) -> dict:
    return {
        "shipperOrderId": order_id,
        "pickupDate": pickup.isoformat(),
        "deliveryDate": delivery.isoformat(),
        "price": 900,
        "stops": [
            {"stopNumber": 1, "city": "Milford", "state": "MA", "postalCode": "01757"},
            {"stopNumber": 2, "city": "Shippensburg", "state": "PA", "postalCode": "17257"},
        ],
        "vehicles": [{"year": "2010", "make": "Toyota", "model": "Corolla"}],
        "transportationReleaseNotes": (
            "Verify the pickup date; shipments cannot be delivered after 3:00 p.m. "
            "on the current date or on previous days."
        ),
    }


def demo_orders() -> List[Tuple[str, str]]:
    today = settings.now().date()
    day = timedelta(days=1)
    base = int(time.time()) % 9_000_000 + 1_000_000  

    scenarios = [
        ("valid: pickup tomorrow, delivery in 2 days", _base_order(str(base), today + day, today + 2 * day)),
        ("invalid: pickup date in the past", _base_order(str(base + 1), today - day, today + day)),
        ("invalid: delivery same day as pickup", _base_order(str(base + 2), today + day, today + day)),
        ("depends on the hour: pickup today (valid until 3:00 p.m.)", _base_order(str(base + 3), today, today + day)),
    ]
    out = [(label, json.dumps(payload)) for label, payload in scenarios]
    malformed = (
        '{"shipperOrderId": "%d", "pickupDate": "%s", "deliveryDate": "%s", "vehicles": [] '
        '"transportationReleaseNotes": "missing comma before this key"}'
        % (base + 4, (today + day).isoformat(), (today + 2 * day).isoformat())
    )
    out.append(("invalid: malformed JSON", malformed))
    return out


def load_files(paths: List[str]) -> List[Tuple[str, str]]:
    files: List[Path] = []
    for item in paths:
        path = Path(item)
        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))
        elif path.is_file():
            files.append(path)
        else:
            fail(f"No existe: {item}")
    return [(p.name, p.read_text(encoding="utf-8")) for p in files]


def main() -> int:
    parser = argparse.ArgumentParser(description="Envia solicitudes de carga a NewCron")
    parser.add_argument("paths", nargs="*", help="archivos .json o carpetas")
    parser.add_argument("--demo", action="store_true", help="envia 5 escenarios de ejemplo")
    args = parser.parse_args()

    items: List[Tuple[str, str]] = []
    if args.demo:
        items.extend(demo_orders())
    items.extend(load_files(args.paths))
    if not items:
        parser.print_help()
        return 1

    service = connect_or_exit()
    publisher = GuaranteedPublisher(service)
    try:
        for label, raw in items:
            order_id = extract_order_id(raw)
            topic = topics.for_order(topics.REQUEST_SUBMITTED, order_id)
            publisher.publish(topic, raw, message_id=order_id)
            log(f"Sent [{label}] order={order_id or '?'} -> {topic}")
    finally:
        shutdown(service, publisher)
    log("Listo. Revisa los resultados en shipper_dashboard.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
