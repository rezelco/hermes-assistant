"""Configure a connection to Hermes Agent."""

from aiohttp import ClientError, ClientTimeout
from yarl import URL

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
import voluptuous as vol

from . import DOMAIN

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(),
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class HermesTextConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Hermes gateway from the integration UI."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Check the gateway URL and API key before saving them."""
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = user_input[CONF_URL].strip().rstrip("/").removesuffix("/v1")
            api_key = user_input[CONF_API_KEY].strip()
            try:
                url = URL(base_url)
                if (
                    url.scheme not in ("http", "https")
                    or not url.host
                    or url.user is not None
                    or url.query_string
                    or url.fragment
                ):
                    raise ValueError
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            if not api_key or not api_key.isprintable():
                errors[CONF_API_KEY] = "invalid_auth"

            if not errors:
                self._async_abort_entries_match({CONF_URL: base_url})
                session = async_get_clientsession(self.hass)
                try:
                    async with session.get(
                        f"{base_url}/v1/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=ClientTimeout(total=5),
                        allow_redirects=False,
                    ) as response:
                        if response.status in (401, 403):
                            errors[CONF_API_KEY] = "invalid_auth"
                        elif response.status != 200:
                            errors["base"] = "cannot_connect"
                except (ClientError, TimeoutError):
                    errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title="Hermes Agent Text",
                    data={CONF_URL: base_url, CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
