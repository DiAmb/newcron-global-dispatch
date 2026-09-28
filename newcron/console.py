"""Utilidades minimas de consola"""
from __future__ import annotations

import os
import sys
from datetime import datetime


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(message: str) -> None:
    print(f"[{stamp()}] {message}", flush=True)


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def fail(message: str) -> "None":
    print(f"\nERROR: {message}\n", file=sys.stderr)
    raise SystemExit(1)


def truncate(text: object, width: int) -> str:
    value = "" if text is None else str(text)
    return value if len(value) <= width else value[: max(width - 3, 0)] + "..."
