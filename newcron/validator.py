"""Reglas de validacion de NewCron .
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import topics

PICKUP_CUTOFF = time(15, 0) 
ACCEPTED_NOTE = "You will receive an email when a carrier accepts this dispatch request"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID_RE = re.compile(r'"shipperOrderId"\s*:\s*"?([^",}\s]+)')


@dataclass
class Publication:
    topic: str
    payload: str


@dataclass
class Decision:
    valid: bool
    order_id: Optional[str]
    notes: str
    publications: List[Publication] = field(default_factory=list)


def parse_payload(raw: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return None, f"Payload is not valid JSON ({exc})."
    if not isinstance(data, dict):
        return None, "Payload must be a JSON object."
    return data, None


def extract_order_id(raw: str) -> Optional[str]:
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("shipperOrderId") not in (None, ""):
            return str(data["shipperOrderId"])
    except (TypeError, ValueError):
        pass
    match = _ID_RE.search(raw or "")
    return match.group(1) if match else None


def parse_date(value: Any) -> Optional[date]:
    if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _validate_dates(payload: Dict[str, Any], now: datetime) -> List[str]:
    errors: List[str] = []
    pickup = delivery = None

    raw_pickup, raw_delivery = payload.get("pickupDate"), payload.get("deliveryDate")
    if raw_pickup in (None, ""):
        errors.append("Pickup date is required.")
    else:
        pickup = parse_date(raw_pickup)
        if pickup is None:
            errors.append("Pickup date must be a valid date in YYYY-MM-DD format.")

    if raw_delivery in (None, ""):
        errors.append("Delivery date is required.")
    else:
        delivery = parse_date(raw_delivery)
        if delivery is None:
            errors.append("Delivery date must be a valid date in YYYY-MM-DD format.")

    if pickup is not None:
        today = now.date()
        if pickup < today:
            errors.append("Pickup date cannot be earlier than the current date.")
        elif pickup == today and now.time() > PICKUP_CUTOFF:
            errors.append(
                "Pickup date cannot be the current date when the request is received after 3:00 p.m."
            )

    if pickup is not None and delivery is not None and delivery < pickup + timedelta(days=1):
        errors.append("Delivery date must be at least one day after the pickup date.")

    return errors


def _missing_keys(item: Any, keys: Tuple[str, ...]) -> List[str]:
    if not isinstance(item, dict):
        return list(keys)
    return [k for k in keys if item.get(k) in (None, "") or not str(item.get(k)).strip()]


def _validate_structure(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    if payload.get("shipperOrderId") in (None, "") or isinstance(payload.get("shipperOrderId"), bool):
        errors.append("shipperOrderId is required.")

    price = payload.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
        errors.append("Price must be a number greater than 0.")

    stops = payload.get("stops")
    if not isinstance(stops, list) or len(stops) < 2:
        errors.append("At least two stops (pickup and delivery) are required.")
    else:
        for index, stop in enumerate(stops, start=1):
            missing = _missing_keys(stop, ("city", "state", "postalCode"))
            if missing:
                errors.append(f"Stop {index} is missing: {', '.join(missing)}.")

    vehicles = payload.get("vehicles")
    if not isinstance(vehicles, list) or len(vehicles) < 1:
        errors.append("At least one vehicle is required.")
    else:
        for index, vehicle in enumerate(vehicles, start=1):
            missing = _missing_keys(vehicle, ("year", "make", "model"))
            if missing:
                errors.append(f"Vehicle {index} is missing: {', '.join(missing)}.")

    return errors


def validate_order(payload: Dict[str, Any], now: datetime) -> List[str]:
    return _validate_dates(payload, now) + _validate_structure(payload)


def build_result(order_id: Optional[str], status: str, notes: str) -> Dict[str, Any]:
    return {"shipperOrderId": order_id, "status": status, "notes": notes}


def process_request(raw: str, now: datetime) -> Decision:
    payload, parse_error = parse_payload(raw)

    if payload is None:
        order_id = extract_order_id(raw)
        result = build_result(order_id, "Cancelled", parse_error or "Invalid payload.")
        return Decision(
            valid=False,
            order_id=order_id,
            notes=result["notes"],
            publications=[
                Publication(topics.for_order(topics.RESULT_CANCELLED, order_id), json.dumps(result))
            ],
        )

    errors = validate_order(payload, now)
    order_id = str(payload["shipperOrderId"]) if payload.get("shipperOrderId") not in (None, "") else None

    if errors:
        result = build_result(order_id, "Cancelled", " ".join(errors))
        return Decision(
            valid=False,
            order_id=order_id,
            notes=result["notes"],
            publications=[
                Publication(topics.for_order(topics.RESULT_CANCELLED, order_id), json.dumps(result))
            ],
        )

    result = build_result(order_id, "Accepted", ACCEPTED_NOTE)
    return Decision(
        valid=True,
        order_id=order_id,
        notes=ACCEPTED_NOTE,
        publications=[
            Publication(
                topics.for_order(topics.ORDER_AVAILABLE, order_id),
                json.dumps(payload, ensure_ascii=False),
            ),
            Publication(topics.for_order(topics.RESULT_ACCEPTED, order_id), json.dumps(result)),
        ],
    )
