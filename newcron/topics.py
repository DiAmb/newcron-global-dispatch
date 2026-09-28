"""Jerarquia de topicos y suscripciones de las colas
"""
from __future__ import annotations

import re

ROOT = "newcron/dispatch"

# El cliente publica aqui su solicitud sin validar
REQUEST_SUBMITTED = f"{ROOT}/request/submitted/v1"

# El validador publica aqui los pedidos correctos listos para los transportistas
ORDER_AVAILABLE = f"{ROOT}/order/available/v1"

# El dashboard de transportistas publica aqui cuando un transportista agarra un pedido
ORDER_ASSIGNED = f"{ROOT}/order/assigned/v1"

# Resultados que ve el cliente
RESULT_ACCEPTED = f"{ROOT}/result/accepted/v1"
RESULT_CANCELLED = f"{ROOT}/result/cancelled/v1"
RESULT_CARRIER_ASSIGNED = f"{ROOT}/result/carrier-assigned/v1"

SUBSCRIPTION_REQUESTS = f"{REQUEST_SUBMITTED}/>"
SUBSCRIPTION_AVAILABLE = f"{ORDER_AVAILABLE}/>"
SUBSCRIPTION_RESULTS = f"{ROOT}/result/>"

_UNSAFE = re.compile(r"[^A-Za-z0-9_\-]")


def safe_segment(value: object) -> str:
    text = _UNSAFE.sub("_", str(value).strip()) if value is not None else ""
    return text or "unknown"


def for_order(base: str, order_id: object) -> str:
    return f"{base}/{safe_segment(order_id)}"
