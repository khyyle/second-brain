# Pi preferences CLI

Use this helper to connect a model provider, list its chat models, or disconnect it. On macOS with Node.js 22.19 or later, run `npm ci` in this directory. Provider IDs and login methods come from the installed Pi SDK. Use `list-providers` to discover them; custom endpoint URLs are not provider IDs.

| Command | Result |
| --- | --- |
| `node pi-helper.mjs list-providers` | Lists `{id,name,loginMethods}` for each built-in Pi provider. No login required. |
| `node pi-helper.mjs login <provider-id> [oauth\|api_key]` | Runs the selected login flow, then prints `{"type":"done"}` once the credential is saved in Keychain. Defaults to OAuth if supported, otherwise the provider’s API-key flow. |
| `node pi-helper.mjs list-models <provider-id>` | Prints a JSON array of models with `id` and `name`, or `[]` if disconnected. A catalog entry does not guarantee account access. |
| `node pi-helper.mjs logout <provider-id>` | Removes the local credential and prints `{"type":"done"}`. It does not revoke access at the provider. |

Only use a login method your provider permits for this app. OAuth does not necessarily mean subscription billing. A failed login does not switch billing methods or replace the existing credential. Local validation uses the selected connection, including any explicitly configured credential file. It does not fill missing fields from shell credentials or default credential files.

### One command per process, one conversation per login

The CLI reads `argv` **once**. `list-providers`, `list-models`, and `logout` print a result and exit. `login` stays alive: its stdin accepts **replies**, not more CLI commands. Starting another `node pi-helper.mjs` cannot answer the first one.

```text
Python starts:  node pi-helper.mjs login <provider-id>
Node → stdout:  {"type":"prompt","id":1,"kind":"secret","message":"…"}
Python → stdin: {"type":"answer","id":1,"value":"…"}
Node → stdout:  {"type":"done"}    (credential saved; process exits)
```

### Calling the Python service from Settings

Import the [`agent_provider` module](../../apps/preferences/services/agent_provider/__init__.py). Its async functions are the service interface; a view does not instantiate a class or hold a process between requests.

```python
from second_brain.apps.preferences.services import agent_provider

# Connect button
login_id = await agent_provider.start_login(provider_id)
# Read one event at a time; display instructions or the prompt in Settings.
event = await agent_provider.read_login_event(login_id)
# When the user answers a prompt (possibly in a later request):
await agent_provider.answer_login(login_id, prompt_id, answer)
```

The **login ID** selects the live session; the **prompt ID** is the `id` from the `prompt` event. After answering, keep reading until `done` or failure. A browser callback can finish login without a typed answer. To stop early, call `await agent_provider.cancel_login(login_id)`. The service releases the process when login finishes or expires. Pending IDs do not survive a backend restart; saved credentials do.

`await agent_provider.list_models(provider_id)` and `await agent_provider.logout(provider_id)` each run a fresh one-shot helper. Neither needs a login ID. The model picker uses the returned IDs and names; selecting one does not send an answer to a login session.

Only Python code that speaks the helper protocol imports `pi_helper` from `second_brain.cloud_llm.pi`. The [launcher](pi_helper.py) resolves `pi-helper.mjs` relative to itself, so callers need neither a path nor a particular working directory. Settings owns pending login IDs and its public service errors; neither belongs to the launcher.

The launcher looks for `node` beside the backend's Python executable (`.venv/bin/node` in development), not on the user's `PATH`. Until runtime packaging lands, developers can symlink a compatible Node there to exercise the service. Do not log login traffic: URLs, codes, and answers can contain secrets.

Exit codes: `0` for success, `1` for failure, `2` for invalid usage, `3` for a cancelled login (cancel message or closed stdin). Errors go to stderr.

### Offline checks

Run `node --test pi-helper.test.mjs` in this directory to exercise Pi with an in-memory Keychain stand-in. From the repo root, `uv run pytest -q src/second_brain/apps/preferences/tests/test_provider_auth.py` exercises the Settings service through real subprocess pipes. Neither suite needs an account.
