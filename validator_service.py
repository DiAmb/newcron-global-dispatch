"""Servicio validador de NewCron.

Consume las solicitudes de la cola de entrada, aplica las reglas de negocio y publica
"""
from __future__ import annotations

import sys

from newcron import settings
from newcron.broker import (
    GuaranteedPublisher,
    connect_or_exit,
    open_queue_receiver_or_exit,
    payload_text,
    shutdown,
)
from newcron.console import log
from newcron.topics import SUBSCRIPTION_REQUESTS
from newcron.validator import process_request


def handle_message(message, receiver, publisher, now_fn=settings.now):
    raw = payload_text(message)
    decision = process_request(raw, now_fn())

    for publication in decision.publications:
        publisher.publish(publication.topic, publication.payload, message_id=decision.order_id)

    receiver.ack(message)

    label = decision.order_id or "(unknown id)"
    if decision.valid:
        log(f"Order {label}: ACCEPTED -> sent to carriers queue")
    else:
        log(f"Order {label}: CANCELLED -> {decision.notes}")
    return decision


def main() -> int:
    service = connect_or_exit()
    publisher = GuaranteedPublisher(service)
    receiver = open_queue_receiver_or_exit(service, settings.QUEUE_REQUESTS, SUBSCRIPTION_REQUESTS)

    zone = settings.BUSINESS_TIMEZONE or "hora local de esta maquina"
    log(f"Validator listo. Cola: {settings.QUEUE_REQUESTS} | Zona horaria: {zone}")
    log("Esperando solicitudes... (Ctrl+C para salir)")

    try:
        while True:
            message = receiver.receive_message(1000)
            if message is None:
                continue
            try:
                handle_message(message, receiver, publisher)
            except Exception as exc:
                log(f"ERROR procesando mensaje (no se hizo ACK, se reintentara): {exc}")
    except KeyboardInterrupt:
        log("Cerrando validator...")
    finally:
        shutdown(service, receiver, publisher)
    return 0


if __name__ == "__main__":
    sys.exit(main())
