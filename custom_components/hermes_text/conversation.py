"""Pass one Assist text message to Hermes and return its answer."""

import asyncio
from dataclasses import dataclass, field
from functools import partial
from typing import Literal
from uuid import uuid4

from aiohttp import ClientError, ClientTimeout, ConnectionTimeoutError

from homeassistant.components import conversation, persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_API_KEY, CONF_URL, MATCH_ALL
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.chat_session import current_session
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

REPLY_TIMEOUT = 90
BACKGROUND_REPLY_TIMEOUT = 240
HANDOFF_AFTER = 15
HTTP_TIMEOUT = ClientTimeout(total=None, connect=5)


@dataclass
class HermesConversation:
    """Track the gateway conversation for one active Home Assistant chat."""

    name: str = field(default_factory=lambda: f"homeassistant-{uuid4().hex}")
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True)
class HermesReply:
    """A reply or a message describing why the turn failed."""

    text: str
    failed: bool = False


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
        self._entry = entry
        self._attr_unique_id = entry.entry_id
        self._url = f"{entry.data[CONF_URL]}/v1/responses"
        self._api_key = entry.data[CONF_API_KEY]
        self._conversations: dict[tuple[str, str | None], HermesConversation] = {}

    @property
    def supported_languages(self) -> Literal["*"]:
        """Accept any language Hermes can process."""
        return MATCH_ALL

    async def _async_handle_message(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> conversation.ConversationResult:
        """Return the gateway reply as the Assist response."""
        reset = (
            " ".join(user_input.text.casefold().strip().rstrip(".!?").split())
            == "start over"
        )
        session = current_session.get()
        if session is None:
            raise RuntimeError("Home Assistant did not open a chat session")

        chat_key = (session.conversation_id, user_input.context.user_id)
        session_state = self._conversations.get(chat_key)
        if session_state is None:
            session.async_on_cleanup(partial(self._conversations.pop, chat_key, None))
        if reset or session_state is None:
            self._conversations[chat_key] = session_state = HermesConversation()

        if reset:
            reply = HermesReply("Started a fresh conversation.")
        elif user_input.satellite_id or chat_log.delta_listener is not None:
            reply = await self._async_assist_reply(user_input, session_state)
        else:
            reply = await self._async_run_turn(
                user_input.text, session_state, REPLY_TIMEOUT
            )

        if not reply.failed:
            chat_log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(agent_id=user_input.agent_id, content=reply.text)
            )
            return conversation.async_get_result_from_chat_log(user_input, chat_log)

        response = intent.IntentResponse(language=user_input.language)
        response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, reply.text)
        return conversation.ConversationResult(
            response=response, conversation_id=chat_log.conversation_id
        )

    async def _async_assist_reply(
        self, user_input: conversation.ConversationInput, session: HermesConversation
    ) -> HermesReply:
        """Wait briefly in Assist, then deliver a slow reply separately."""
        request = self._entry.async_create_background_task(
            self.hass,
            self._async_run_turn(user_input.text, session, BACKGROUND_REPLY_TIMEOUT),
            "Hermes Assistant request",
        )
        try:
            done, _ = await asyncio.wait({request}, timeout=HANDOFF_AFTER)
        except asyncio.CancelledError:
            request.cancel()
            raise
        if done:
            return request.result()

        self._entry.async_create_background_task(
            self.hass,
            self._async_deliver_reply(request, user_input.satellite_id, user_input.context),
            "Hermes Assistant delayed reply",
        )
        if user_input.satellite_id:
            return HermesReply("I'm working on that. I'll let you know when it's ready.")
        return HermesReply(
            "I'm working on that. Check Home Assistant notifications for the answer."
        )

    async def _async_deliver_reply(
        self,
        request: asyncio.Task[HermesReply],
        satellite_id: str | None,
        context: Context,
    ) -> None:
        """Announce a delayed reply or show it as a Home Assistant notification."""
        reply = await request
        if satellite_id and self.hass.services.has_service("assist_satellite", "announce"):
            try:
                await self.hass.services.async_call(
                    "assist_satellite",
                    "announce",
                    {ATTR_ENTITY_ID: satellite_id, "message": reply.text},
                    blocking=True,
                    context=context,
                )
            except HomeAssistantError:
                pass  # The satellite may be unavailable or lack an announcement feature.
            else:
                return
        persistent_notification.async_create(self.hass, reply.text, title="Hermes Assistant")

    async def _async_run_turn(
        self, text: str, session: HermesConversation, timeout: float
    ) -> HermesReply:
        """Serialize turns in one conversation and return a user-facing result."""
        try:
            async with asyncio.timeout(timeout), session.lock:
                return HermesReply(await self._async_get_reply(text, session.name))
        except ConnectionTimeoutError:
            error = "Home Assistant timed out connecting to Hermes."
        except TimeoutError:
            error = "Hermes took too long to reply."
        except ClientError:
            error = "Home Assistant could not reach Hermes."
        except ValueError as err:
            error = str(err)
        return HermesReply(error, failed=True)

    async def _async_get_reply(self, text: str, session_name: str) -> str:
        """Send a turn to the gateway conversation and read its final message."""
        session = async_get_clientsession(self.hass)
        async with session.post(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"input": text, "conversation": session_name, "store": True},
            timeout=HTTP_TIMEOUT,
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
