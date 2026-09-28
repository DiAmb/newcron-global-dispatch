"""Conexion con Solace PubSub+ 
"""
from __future__ import annotations

from typing import Optional

from solace.messaging.config.missing_resources_creation_configuration import (
    MissingResourcesCreationStrategy,
)
from solace.messaging.config.solace_properties import (
    authentication_properties as auth_props,
    service_properties as service_props,
    transport_layer_properties as transport_props,
)
from solace.messaging.config.transport_security_strategy import TLS
from solace.messaging.messaging_service import MessagingService, RetryStrategy
from solace.messaging.resources.queue import Queue
from solace.messaging.resources.topic import Topic

from . import settings, topics
from .console import fail

SHUTDOWN_GRACE_MS = 2000  


def _tls_strategy() -> Optional[TLS]:
    if not settings.SOLACE_VALIDATE_CERT:
        return TLS.create().without_certificate_validation()
    if settings.SOLACE_TRUST_STORE:
        return TLS.create().with_certificate_validation(
            ignore_expiration=False,
            validate_server_name=True,
            trust_store_file_path=settings.SOLACE_TRUST_STORE,
        )
    return None


def connect() -> MessagingService:
    properties = {
        transport_props.HOST: settings.SOLACE_HOST,
        service_props.VPN_NAME: settings.SOLACE_VPN,
        auth_props.SCHEME_BASIC_USER_NAME: settings.SOLACE_USERNAME,
        auth_props.SCHEME_BASIC_PASSWORD: settings.SOLACE_PASSWORD,
    }
    builder = (
        MessagingService.builder()
        .from_properties(properties)
        .with_connection_retry_strategy(RetryStrategy.parametrized_retry(3, 2000))
        .with_reconnection_retry_strategy(RetryStrategy.parametrized_retry(20, 3000))
    )
    tls = _tls_strategy()
    if tls is not None:
        builder = builder.with_transport_security_strategy(tls)

    service = builder.build()
    service.connect()
    return service


def connect_or_exit() -> MessagingService:
    missing = settings.missing_connection_vars()
    if missing:
        fail(
            "Faltan variables en el archivo .env: " + ", ".join(missing) + "\n"
        )
    try:
        return connect()
    except Exception as exc:  
        fail(
            f"No se pudo conectar a {settings.SOLACE_HOST} (VPN '{settings.SOLACE_VPN}').\n"
            f"Detalle: {exc}\n\n"
        )
        raise  


class GuaranteedPublisher:

    def __init__(self, service: MessagingService) -> None:
        self._service = service
        self._publisher = service.create_persistent_message_publisher_builder().build()
        self._publisher.start()

    def publish(self, topic: str, payload: str, message_id: Optional[str] = None, timeout_ms: int = 5000) -> None:
        builder = self._service.message_builder()
        if message_id:
            builder = builder.with_application_message_id(str(message_id))
        message = builder.build(payload)
        self._publisher.publish_await_acknowledgement(message, Topic.of(topic), timeout_ms)

    def close(self) -> None:
        try:
            self._publisher.terminate(SHUTDOWN_GRACE_MS)
        except Exception:
            pass


def open_queue_receiver(service: MessagingService, queue_name: str, exclusive: bool = False):
    queue = (
        Queue.durable_exclusive_queue(queue_name)
        if exclusive
        else Queue.durable_non_exclusive_queue(queue_name)
    )
    receiver = (
        service.create_persistent_message_receiver_builder()
        .with_missing_resources_creation_strategy(MissingResourcesCreationStrategy.DO_NOT_CREATE)
        .with_message_client_acknowledgement()
        .build(queue)
    )
    receiver.start()
    return receiver


def open_queue_receiver_or_exit(service: MessagingService, queue_name: str, subscription: str, exclusive: bool = False):
    try:
        return open_queue_receiver(service, queue_name, exclusive)
    except Exception as exc:
        fail(
            f"No se pudo enlazar a la cola '{queue_name}'.\n"
            f"Detalle: {exc}\n\n"
        )
        raise  


def payload_text(message) -> str:
    """Payload del mensaje como texto, sea texto o bytes."""
    try:
        text = message.get_payload_as_string()
    except Exception:
        text = None
    if text is None:
        raw = message.get_payload_as_bytes()
        text = bytes(raw).decode("utf-8", errors="replace") if raw else ""
    return text


def shutdown(service: MessagingService, *closeables) -> None:
    """Cierra receptores/publicadores y desconecta, sin lanzar errores."""
    for item in closeables:
        try:
            if hasattr(item, "close"):
                item.close()
            else:
                item.terminate(SHUTDOWN_GRACE_MS)
        except Exception:
            pass
    try:
        service.disconnect()
    except Exception:
        pass


QUEUE_PLAN = [
    (settings.QUEUE_REQUESTS, topics.SUBSCRIPTION_REQUESTS, "Non-Exclusive", "validator_service.py"),
    (settings.QUEUE_AVAILABLE, topics.SUBSCRIPTION_AVAILABLE, "Non-Exclusive", "carrier_dashboard.py"),
    (settings.QUEUE_RESULTS, topics.SUBSCRIPTION_RESULTS, "Exclusive", "shipper_dashboard.py"),
]
