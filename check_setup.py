"""Diagnostico del ambiente
"""
from __future__ import annotations

import sys

from newcron import settings
from newcron.broker import (
    GuaranteedPublisher,
    QUEUE_PLAN,
    connect_or_exit,
    open_queue_receiver,
    shutdown,
)


def show_setup() -> None:
    print("Colas a crear en Solace (Broker Manager -> Queues -> + Queue):\n")
    for name, subscription, access, consumer in QUEUE_PLAN:
        print(f"  Cola:        {name}")
        print(f"  Access Type: {access}")
        print("  Permission:  Non-Owner = Consume  (o Owner = tu usuario)")
        print(f"  Suscripcion: {subscription}   (pestana Subscriptions -> + Subscription)")
        print(f"  La consume:  {consumer}\n")


def main() -> int:
    if "--show-setup" in sys.argv:
        show_setup()
        return 0

    print(f"Host: {settings.SOLACE_HOST}\nVPN:  {settings.SOLACE_VPN}\nUser: {settings.SOLACE_USERNAME}\n")
    service = connect_or_exit()
    print("[OK]   Conexion establecida")

    publisher = None
    try:
        publisher = GuaranteedPublisher(service)
        print("[OK]   Publicador persistente iniciado")
    except Exception as exc:
        print(f"[FAIL] Publicador persistente: {exc}")

    failures = 0
    for name, subscription, _access, _consumer in QUEUE_PLAN:
        try:
            receiver = open_queue_receiver(service, name)
            receiver.terminate(2000)
            print(f"[OK]   Cola '{name}' existe y se puede consumir")
            print(f"       -> confirma que tenga la suscripcion {subscription}")
        except Exception as exc:
            failures += 1
            print(f"[FAIL] Cola '{name}': {exc}")
            print(f"       -> crea la cola y agrega la suscripcion {subscription}")

    shutdown(service, *([publisher] if publisher else []))
    print("\nTodo listo." if failures == 0 else f"\n{failures} cola(s) con problemas.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
