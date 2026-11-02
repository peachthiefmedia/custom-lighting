"""Utility functions for HA core."""

import logging
from collections.abc import Awaitable, Callable

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from homeassistant.util.read_only_dict import ReadOnlyDict

from .adaptation_utils import ServiceData

_LOGGER = logging.getLogger(__name__)


class SplitCallError(HomeAssistantError):
    """Partitions were attempted; run only the narrowed original before raising."""


def target_entities(hass: HomeAssistant, service_data: ServiceData) -> set[str]:
    """Resolve all HA targets, leaving group expansion to the light service."""
    selected = async_extract_referenced_entity_ids(
        hass, TargetSelection(service_data), expand_group=False,
    )
    return selected.referenced | selected.indirectly_referenced


def setup_service_call_interceptor(
    hass: HomeAssistant,
    domain: str,
    service: str,
    intercept_func: Callable[[ServiceCall, ServiceData], Awaitable[None] | None],
) -> Callable[[], None]:
    """Inject a function into a registered service call to preprocess service data.

    The injected interceptor function receives the service call and a writeable data dictionary
    (the data of the service call is read-only) before the service call is executed.
    """
    try:
        # Public registry snapshot; replacement still needs a proxy around HA's handler.
        registered_services = {domain: hass.services.async_services_for_domain(domain)}
    except AttributeError as error:
        msg = (
            "Intercept failed because registered services are no longer"
            " accessible (internal API may have changed)"
        )
        raise RuntimeError(msg) from error

    if domain not in registered_services or service not in registered_services[domain]:
        msg = f"Intercept failed because service {domain}.{service} is not registered"
        raise RuntimeError(msg)

    existing_service = registered_services[domain][service]
    active = True

    async def service_func_proxy(call: ServiceCall):
        deferred_error = None
        try:
            # Convert read-only data to writeable dictionary for modification by interceptor
            data = dict(call.data)

            # Call interceptor
            result = intercept_func(call, data) if active else None
            if result is not None:
                await result

            # Convert data back to read-only
            call.data = ReadOnlyDict(data)
        except SplitCallError as error:
            # Other partitions have already been attempted. Never replay them.
            call.data = ReadOnlyDict(data)
            deferred_error = error
        except Exception:
            # Blindly catch all exceptions to avoid breaking light.turn_on
            _LOGGER.exception(
                "Error for call '%s' in service_func_proxy",
                call.data,
            )
        # Call original service handler with processed data
        import asyncio

        target = existing_service.job.target
        if asyncio.iscoroutinefunction(target):
            response = await target(call)
        else:
            response = target(call)
        if deferred_error is not None:
            raise deferred_error
        return response

    hass.services.async_register(
        domain,
        service,
        service_func_proxy,
        existing_service.schema,
        supports_response=existing_service.supports_response,
        description_placeholders=existing_service.description_placeholders,
    )

    def remove() -> None:
        # Remove the interceptor by reinstalling the original service handler
        nonlocal active
        active = False
        current = hass.services.async_services_for_domain(domain).get(service)
        if current is None or current.job.target is not service_func_proxy:
            return
        hass.services.async_register(
            domain,
            service,
            existing_service.job.target,
            existing_service.schema,
            supports_response=existing_service.supports_response,
            description_placeholders=existing_service.description_placeholders,
        )

    return remove
