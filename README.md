# Hermes Assistant

Hermes Assistant is a Home Assistant conversation agent. It forwards the text it receives from Assist to your Hermes Agent gateway and returns the gateway's reply. Each request is a single turn with a 90-second limit and a 5-second connection timeout.

## Install in Home Assistant

1. Enable the API server on your Hermes gateway and set `API_SERVER_KEY`. Home Assistant needs to be able to reach the gateway URL from wherever it runs. [Hermes API setup](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server#quick-start)
2. In Home Assistant 2026.11 or later, open **Settings → Marketplace → Add from link**. Add `https://github.com/rezelco/hermes-assistant` and install **Hermes Assistant**. [Marketplace instructions](https://www.home-assistant.io/integrations/marketplace/#custom-repositories)
3. Open **Settings → Devices & services → Add integration → Hermes Assistant**. Enter your gateway URL, such as `http://hermes-host:8642`, and its API server key.

For a manual install, copy `custom_components/hermes_text` into the `custom_components` folder in your Home Assistant config directory and restart Home Assistant before step 3.

## Try it

Open **Developer Tools → Actions → Process conversation**, select **Hermes Assistant** as the agent, and enter `Hi` or any other text. The result contains the Hermes reply under `response.speech.plain.speech`. [Home Assistant action reference](https://www.home-assistant.io/actions/conversation.process/#try-it-yourself)

Select **Hermes Assistant** as the conversation agent in an Assist pipeline to use it with typed Assist messages or voice transcripts.
