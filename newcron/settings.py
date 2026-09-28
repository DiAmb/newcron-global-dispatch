"""Configuracion central"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


SOLACE_HOST = _env("SOLACE_HOST")
SOLACE_VPN = _env("SOLACE_VPN")
SOLACE_USERNAME = _env("SOLACE_USERNAME")
SOLACE_PASSWORD = os.getenv("SOLACE_PASSWORD", "")
SOLACE_VALIDATE_CERT = _env("SOLACE_VALIDATE_CERT", "true").lower() not in ("false", "0", "no")
SOLACE_TRUST_STORE = _env("SOLACE_TRUST_STORE")

BUSINESS_TIMEZONE = _env("BUSINESS_TIMEZONE")
CARRIER_NAME = _env("CARRIER_NAME")

QUEUE_REQUESTS = _env("QUEUE_REQUESTS", "q.newcron.validator.requests")
QUEUE_AVAILABLE = _env("QUEUE_AVAILABLE", "q.newcron.carrier.available")
QUEUE_RESULTS = _env("QUEUE_RESULTS", "q.newcron.shipper.results")


def missing_connection_vars() -> List[str]:
    values = {
        "SOLACE_HOST": SOLACE_HOST,
        "SOLACE_VPN": SOLACE_VPN,
        "SOLACE_USERNAME": SOLACE_USERNAME,
        "SOLACE_PASSWORD": SOLACE_PASSWORD,
    }
    return [name for name, value in values.items() if not value]


def now() -> datetime:
    if BUSINESS_TIMEZONE:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(BUSINESS_TIMEZONE))
    return datetime.now()
