# Model provider service

Second Brain reaches cloud model providers through [Pi](https://github.com/earendil-works/pi), an open-source library that
knows how to sign in to many providers, which models each lists for a login, what they cost,
and how to call them. Pi is a Node library and the backend is Python, so this directory holds a
small Node script, `pi-helper.mjs`, that the backend runs as a subprocess: one command per
run, with JSON on stdin and stdout.

- Settings uses it to sign in to a provider and sign out. The Build tab and cost estimates
  use it to list the models a login offers, with their context window and price.
- The build worker uses its `build-turn` command to send one turn of a build conversation to
  the chosen model. Python keeps the conversation, runs the tools the model asks for, and
  enforces Stop and the spend cap between turns.

A login is saved in macOS Keychain under the service `com.second-brain.pi-oauth`. A request
uses that login only. The script starts with a fixed, minimal environment
(`pi_helper.HELPER_ENV`), so no API key, proxy or SDK setting from the shell reaches it.

`make setup-pi` installs the pinned Node runtime and packages. To test the script from the
repository root:

```sh
.venv/bin/node --test src/second_brain/cloud_llm/pi/tests/pi-helper.test.mjs
```
