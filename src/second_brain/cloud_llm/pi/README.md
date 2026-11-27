# Pi provider helper

Connects Second Brain to model providers through Pi. Credentials are stored in macOS Keychain.

With Node.js 22.19 or later, run from this directory:

```sh
npm ci
node --test
```

The tests do not require a provider account. To use the helper from the desktop backend, `node` must be beside the backend’s Python executable (`.venv/bin/node` in development).
