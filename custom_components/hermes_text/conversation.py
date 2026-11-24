"""Pass one Assist text message to Hermes and return its answer."""

from typing import Literal

from aiohttp import ClientError, ClientTimeout

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_URL, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

REPLY_TIMEOUT = ClientTimeout(total=15)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the Hermes conversation entity."""
    async_add_entities([HermesTextAgent(entry)])


class HermesTextAgent(conversation.ConversationEntity):
    """Forward Assist text to the Hermes gateway."""

    _attr_name = "Hermes Assistant"

    def __init__(self, entry: ConfigEntry) -> None:
        """Keep the connection settings for this agent."""
        self._attr_unique_id = entry.entry_id
        self._url = f"{entry.data[CONF_URL]}/v1/responses"
        self._api_key = entry.data[CONF_API_KEY]

    @property
    def supported_languages(self) -> Literal["*"]:
        """Accept any language Hermes can process."""
        return MATCH_ALL

    async def _async_handle_message(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> conversation.ConversationResult:
        """Return the gateway reply as the Assist response."""
        response = intent.IntentResponse(language=user_input.language)
        try:
            reply = await self._async_get_reply(user_input.text)
        except TimeoutError:
            error = "Hermes took too long to reply."
        except ClientError:
            error = "Home Assistant could not reach Hermes."
        except ValueError as err:
            error = str(err)
        else:
            chat_log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(agent_id=user_input.agent_id, content=reply)
            )
            response.async_set_speech(reply)
            return conversation.ConversationResult(
                response=response, conversation_id=chat_log.conversation_id
            )

        response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, error)
        return conversation.ConversationResult(
            response=response, conversation_id=chat_log.conversation_id
        )

    async def _async_get_reply(self, text: str) -> str:
        """Send the text as a single Hermes turn and read its final message."""
        session = async_get_clientsession(self.hass)
        async with session.post(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"input": text, "store": False},
            timeout=REPLY_TIMEOUT,
            allow_redirects=False,
        ) as response:
            if response.status in (401, 403):
                raise ValueError("Hermes rejected the API key.")
            if response.status != 200:
                raise ValueError(f"Hermes returned HTTP {response.status}.")
            try:
                data = await response.json(content_type=None)
            except ValueError as err:
                raise ValueError("Hermes returned an invalid reply.") from err

        try:
            if data.get("status") in ("failed", "cancelled"):
                raise ValueError("Hermes could not complete the request.")
            for item in reversed(data["output"]):
                if item.get("type") != "message" or item.get("phase") == "commentary":
                    continue
                reply = "".join(
                    part["text"]
                    for part in item["content"]
                    if part.get("type") == "output_text"
                ).strip()
                if reply:
                    return reply
        except (AttributeError, KeyError, TypeError) as err:
            raise ValueError("Hermes returned an invalid reply.") from err
        raise ValueError("Hermes did not return a text reply.")
