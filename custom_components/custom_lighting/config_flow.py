"""Set up profiles and device handling; edit curves in the Lighting panel."""

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
import probatio as vol

from .const import DOMAIN, CONF_LIGHTS, VALIDATION_TUPLES
from .helpers import expand_lights

def lights_selector():
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="light", multiple=True))

def occupied_lights(hass, exclude=None):
    return {light for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.entry_id != exclude
            for light in expand_lights(hass, entry.options.get(CONF_LIGHTS, entry.data.get(CONF_LIGHTS, [])))}

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create a scheduled lighting profile."""
    VERSION = 2

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input[CONF_LIGHTS]:
                errors[CONF_LIGHTS] = "no_lights"
            elif set(expand_lights(self.hass, user_input[CONF_LIGHTS])) & occupied_lights(self.hass):
                errors[CONF_LIGHTS] = "already_assigned"
            else:
                await self.async_set_unique_id(user_input[CONF_NAME])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_LIGHTS): lights_selector(),
        }), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()

class OptionsFlow(config_entries.OptionsFlow):
    """Keep the primary form small, with device options in a separate step."""

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["lights", "device"])

    async def async_step_lights(self, user_input=None):
        entry = self.config_entry
        errors = {}
        if user_input is not None:
            if not user_input[CONF_LIGHTS]:
                errors[CONF_LIGHTS] = "no_lights"
            elif set(expand_lights(self.hass, user_input[CONF_LIGHTS])) & occupied_lights(self.hass, entry.entry_id):
                errors[CONF_LIGHTS] = "already_assigned"
            else:
                return self.async_create_entry(title="", data={**entry.options, **user_input})
        return self.async_show_form(step_id="lights", data_schema=vol.Schema({
            vol.Required(CONF_LIGHTS, default=entry.options.get(CONF_LIGHTS, entry.data[CONF_LIGHTS])): lights_selector(),
        }), errors=errors)

    async def async_step_device(self, user_input=None):
        entry = self.config_entry
        errors = {}
        if user_input is not None:
            if user_input["transition"] >= user_input["interval"]:
                errors["transition"] = "transition_too_long"
            else:
                return self.async_create_entry(title="", data={**entry.options, **user_input})
        schema = {}
        for key, default, validation in VALIDATION_TUPLES:
            if key == CONF_LIGHTS:
                continue
            schema[vol.Required(key, default=entry.options.get(key, default))] = validation
        return self.async_show_form(step_id="device", data_schema=vol.Schema(schema), errors=errors)
