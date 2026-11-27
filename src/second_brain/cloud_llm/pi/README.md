# Pi provider helper

`pi-helper.mjs` is the only code that talks to a model provider. The backend runs it as a
subprocess, one command per run, with JSON on stdin and stdout:

- Settings uses it to sign in to a provider and sign out. The Build tab and cost estimates
  use it to list the models a login offers, with their context window and price.
- The build worker uses `build-turn` to send one turn of a build conversation to the chosen
  model. Python keeps the conversation, runs the tools the model asks for, and enforces
  Stop and the spend cap between turns.

A login is saved in macOS Keychain under the service `com.second-brain.pi-oauth`. A request
uses that login only. The helper starts with a fixed, minimal environment
(`pi_helper.HELPER_ENV`), so no API key, proxy or SDK setting from the shell reaches it.

To test the helper from the repository root:

```sh
.venv/bin/node --test src/second_brain/cloud_llm/pi/tests/pi-helper.test.mjs
```
