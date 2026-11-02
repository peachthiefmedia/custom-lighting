"""The service proxy preserves responses, metadata and later wrappers."""

import logging
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_controller import controller, definitions

_scope = {"HomeAssistantError": Exception}
definitions("hass_utils.py", {"SplitCallError"}, _scope)
SplitCallError = _scope["SplitCallError"]

class CompatibilityTests(unittest.TestCase):
    def test_target_resolution_keeps_direct_and_indirect_targets(self):
        data = {"entity_id": ["light.one"], "device_id": ["device"],
                "area_id": ["area"], "floor_id": ["floor"], "label_id": ["label"]}
        extract = Mock(return_value=SimpleNamespace(
            referenced={"light.one", "light.group"}, indirectly_referenced={"light.two", "switch.other"}))
        scope = {"TargetSelection": lambda config: config,
                 "async_extract_referenced_entity_ids": extract}
        definitions("hass_utils.py", {"target_entities"}, scope)
        manager, _, _, _, _ = controller()
        manager._get_entity_list.__globals__["target_entities"] = scope["target_entities"]
        self.assertEqual(manager._get_entity_list(data), ["light.group", "light.one", "light.two"])
        extract.assert_called_once_with(manager.hass, data, expand_group=False)

    def test_raw_transition_is_normalized_before_timing_calculations(self):
        scope = {"ATTR_SERVICE_DATA": "service_data", "ATTR_TRANSITION": "transition",
                 "VALID_TRANSITION": lambda value: max(0, min(6553, float(value)))}
        definitions("switch.py", {"_turn_off_transition"}, scope)
        event = SimpleNamespace(data={"service_data": {"transition": "1.5"}})
        self.assertEqual(max(scope["_turn_off_transition"](event), 1), 1.5)
        event.data["service_data"] = {}
        self.assertIsNone(scope["_turn_off_transition"](event))


class Registry:
    def __init__(self, target):
        self.schema = object()
        self.services = {"turn_on": SimpleNamespace(
            job=SimpleNamespace(target=target), schema=self.schema,
            supports_response="optional", description_placeholders={"name": "Light"})}

    def async_services_for_domain(self, domain):
        return self.services.copy()

    def async_register(self, domain, service, target, schema, **metadata):
        self.services[service] = SimpleNamespace(job=SimpleNamespace(target=target),
                                                schema=schema, **metadata)


class InterceptorTests(unittest.IsolatedAsyncioTestCase):
    def create(self, interceptor):
        scope = {"_LOGGER": logging.getLogger("interceptor-test"), "ReadOnlyDict": dict,
                 "SplitCallError": SplitCallError}
        definitions("hass_utils.py", {"setup_service_call_interceptor"}, scope)
        calls = []
        async def original(call):
            calls.append(dict(call.data))
            return {"response": "preserved"}
        registry = Registry(original)
        remove = scope["setup_service_call_interceptor"](
            SimpleNamespace(services=registry), "light", "turn_on", interceptor)
        return registry, original, calls, remove

    async def test_response_and_metadata_survive_install_and_restore(self):
        async def intercept(call, data):
            data["brightness"] = 42
        registry, original, calls, remove = self.create(intercept)
        service = registry.services["turn_on"]
        self.assertIs(service.schema, registry.schema)
        self.assertEqual(service.supports_response, "optional")
        response = await service.job.target(SimpleNamespace(data={}))
        self.assertEqual(response, {"response": "preserved"})
        self.assertEqual(calls, [{"brightness": 42}])
        remove()
        self.assertIs(registry.services["turn_on"].job.target, original)
        self.assertEqual(registry.services["turn_on"].description_placeholders, {"name": "Light"})

    async def test_unload_does_not_clobber_a_later_wrapper(self):
        def intercept(call, data):
            data["brightness"] = 42
        registry, original, calls, remove = self.create(intercept)
        proxy = registry.services["turn_on"].job.target
        async def other_wrapper(call):
            return await proxy(call)
        registry.async_register("light", "turn_on", other_wrapper, registry.schema)
        remove()
        self.assertIs(registry.services["turn_on"].job.target, other_wrapper)
        await other_wrapper(SimpleNamespace(data={"brightness": 80}))
        self.assertEqual(calls, [{"brightness": 80}])

    async def test_failed_interceptor_does_not_break_the_original_service(self):
        def intercept(call, data):
            raise RuntimeError("interception failed")
        registry, original, calls, remove = self.create(intercept)
        with self.assertLogs("interceptor-test", level="ERROR"):
            response = await registry.services["turn_on"].job.target(SimpleNamespace(data={"brightness": 80}))
        self.assertEqual(response, {"response": "preserved"})
        self.assertEqual(calls, [{"brightness": 80}])
        remove()

    async def test_split_failure_runs_only_narrowed_original_then_reaches_caller(self):
        async def intercept(call, data):
            data["entity_id"] = ["light.first"]
            raise SplitCallError("forwarded light.second failed")
        registry, original, calls, remove = self.create(intercept)
        with self.assertRaisesRegex(SplitCallError, "light.second"):
            await registry.services["turn_on"].job.target(SimpleNamespace(
                data={"entity_id": ["light.first", "light.second"]}))
        self.assertEqual(calls, [{"entity_id": ["light.first"]}])
        remove()


if __name__ == "__main__":
    unittest.main()
