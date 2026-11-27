// Private JSON-lines interface between Django Preferences and Pi.
import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { EventEmitter, once } from 'node:events';
import { linkSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { createModels, defaultProviderAuthContext, InMemoryCredentialStore } from '@earendil-works/pi-ai';
import { builtinProviders } from '@earendil-works/pi-ai/providers/all';
import { Entry } from '@napi-rs/keyring';

// Keep the original service name so existing Pi logins still work after upgrading.
const keychainService = 'com.second-brain.pi-oauth';

// Only display our own errors. Provider errors may contain the callback URL or tokens.
class PreferencesError extends Error {}
class UsageError extends PreferencesError {}
class LoginCancelled extends PreferencesError {}

function loginMethods(provider) {
  const methods = [];
  if (provider.auth.oauth?.login) methods.push('oauth');
  if (provider.auth.apiKey?.login) methods.push('api_key');
  return methods;
}

export function installationUuid(directory) {
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const path = join(directory, 'installation-id');
  try {
    return readFileSync(path, 'utf8');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  // Publish one complete UUID even if two helpers start for the first time together.
  const uuid = randomUUID();
  const temporary = join(directory, `${uuid}.tmp`);
  writeFileSync(temporary, uuid, { flag: 'wx', mode: 0o600 });
  try {
    linkSync(temporary, path);
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
  } finally {
    unlinkSync(temporary);
  }
  return readFileSync(path, 'utf8');
}

// Stdin belongs to this login only. Answers resume a Pi prompt; they are not CLI commands.
async function requestCredential(models, providerId, method, { input, send, getDeviceId }) {
  const controller = new AbortController();
  const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(5 * 60_000)]);
  const replies = new EventEmitter();
  const lines = createInterface({ input, terminal: false, crlfDelay: Infinity });
  const cancel = () => controller.abort(new LoginCancelled('Login cancelled.'));
  lines.on('close', cancel);
  lines.on('line', (line) => {
    try {
      const message = JSON.parse(line);
      if (message.type === 'cancel') return cancel();
      if (message.type === 'answer' && Number.isSafeInteger(message.id) && typeof message.value === 'string') {
        replies.emit(`answer:${message.id}`, message.value);
        return;
      }
    } catch { /* Never echo the input line; it may contain a secret. */ }
    controller.abort(new PreferencesError('Invalid login input. Expected an answer or cancel message.'));
  });

  let nextId = 0;
  /** @type {import('@earendil-works/pi-ai').AuthInteraction} */
  const interaction = {
    signal,
    notify: send, // auth_url, device_code, info, progress: display to the user, don't log.
    async prompt({ type: kind, signal: promptSignal, ...fields }) {
      const id = ++nextId;
      const until = promptSignal ? AbortSignal.any([signal, promptSignal]) : signal;
      until.throwIfAborted();
      const answer = once(replies, `answer:${id}`, { signal: until });
      // Pi's ADC/credential-chain choices require ambient credentials, which this app never resolves.
      if (kind === 'select') {
        const ambient = { 'google-vertex': 'adc', 'amazon-bedrock': 'credential-chain' }[providerId];
        fields.options = fields.options.filter(option => option.id !== ambient);
      }
      // Each login owns its Node process. A loopback redirect alone is not proof Pi
      // acquired the callback port: without a listener, pasting is the *only* path.
      if (kind === 'manual_code') fields.callback_listening = process.getActiveResourcesInfo().includes('TCPServerWrap');
      send({ type: 'prompt', id, kind, ...fields });

      // If the browser callback wins, Pi aborts just this pending manual-code prompt.
      const [value] = await answer;
      if (kind === 'select' && !fields.options.some(option => option.id === value)) {
        throw new PreferencesError('Choose one of the offered sign-in methods.');
      }
      if (kind === 'secret' && !value.trim()) {
        throw new PreferencesError('A credential is required.');
      }
      // An optional text field may be left empty; whitespace alone is not an account ID.
      return kind === 'text' && !value.trim() ? '' : value;
    },
  };

  try {
    return await models.login(providerId, method, interaction, { agentName: 'Second Brain', getDeviceId });
  } catch (error) {
    if (controller.signal.aborted) throw controller.signal.reason;
    if (signal.aborted) throw new PreferencesError('Login timed out. Start a new login.');
    if (error instanceof PreferencesError) throw error;
    if (error?.code === 'EADDRINUSE' || /port \d+ is in use/i.test(error?.message)) {
      throw new PreferencesError('The sign-in callback port is busy. Finish or cancel other logins first.');
    }
    throw new PreferencesError('Provider sign-in failed. Start a new login.');
  } finally {
    lines.removeListener('close', cancel);
    lines.close();
  }
}

function modelsFor(provider, credentials = new InMemoryCredentialStore()) {
  // A saved connection can reference a credential file, but it cannot borrow a shell key
  // or a default credentials file the user didn't select.
  const files = defaultProviderAuthContext();
  const models = createModels({ credentials, authContext: {
    env: async () => undefined,
    async fileExists(path) {
      const saved = await credentials.read(provider.id);
      return Object.values(saved?.env ?? {}).includes(path) && files.fileExists(path);
    },
  } });
  models.setProvider(provider);
  return models;
}

function hasCredentialShape(credential) {
  if (credential?.type === 'api_key') return true; // Required API-key fields vary by provider.
  return credential?.type === 'oauth' && typeof credential.access === 'string' && credential.access.length > 0
    && typeof credential.refresh === 'string' && Number.isFinite(credential.expires);
}

async function listModels(provider, entry) {
  const saved = entry.getPassword();
  if (saved === null) return []; // Never fall back to an ambient API key.

  const credential = JSON.parse(saved);
  if (!hasCredentialShape(credential)) {
    throw new PreferencesError('Invalid saved login. Disconnect and sign in again.');
  }
  // Listing never resolves or refreshes tokens. The build worker owns refresh.
  const credentials = new InMemoryCredentialStore();
  await credentials.modify(provider.id, async () => credential);
  const available = await modelsFor(provider, credentials).getAvailable(provider.id);
  return available.map(({ id, name }) => ({ id, name }));
}

async function connectProvider(provider, { entry, input, send, getDeviceId, authMethod }) {
  const supported = loginMethods(provider);
  if (!supported.length) throw new UsageError(`${provider.id} has no interactive login method.`);
  const method = authMethod ?? supported[0];
  if (!supported.includes(method)) {
    throw new UsageError(`Login methods for ${provider.id}: ${supported.join(', ')}.`);
  }

  const models = modelsFor(provider);
  const credential = await requestCredential(models, provider.id, method, { input, send, getDeviceId });
  // Pi has staged the candidate in memory. Validate it without a network call or token refresh
  // before replacing the working Keychain entry (some API keys also need an account ID).
  if (credential.type !== method || !hasCredentialShape(credential) || !(await models.checkAuth(provider.id))) {
    throw new PreferencesError('Sign-in details are incomplete. The saved credential was not changed.');
  }
  entry.setPassword(JSON.stringify(credential));
}

/** Dispatch argv once at startup. Only connectProvider keeps the process open for replies. */
export async function runCommand(command, provider, options) {
  switch (command) {
    case 'connection-status': {
      // Without -w or -g, security queries item metadata, not the password. A saved
      // connection can still have no available models. Only errSecItemNotFound is absent.
      const { status } = options.runSecurity('/usr/bin/security',
        ['find-generic-password', '-s', keychainService, '-a', provider.id],
        { stdio: 'ignore', timeout: 10_000 });
      if (status !== 0 && status !== 44) throw new PreferencesError('Cannot check the saved login. Check macOS Keychain.');
      return options.send({ connected: status === 0 });
    }
    case 'list-models':
      return options.send(await listModels(provider, options.entry));
    case 'login':
      await connectProvider(provider, options);
      return options.send({ type: 'done' });
    case 'logout':
      options.entry.deletePassword(); // Local disconnect, not provider-side revocation.
      return options.send({ type: 'done' });
    default:
      throw new UsageError('Unknown helper command.');
  }
}

async function main(args) {
  const [command, providerId, authMethod] = args;
  const usage = 'Usage: node pi-helper.mjs list-providers | login <provider-id> [oauth|api_key] | <connection-status|list-models|logout> <provider-id>';

  if (command === '--help' && args.length === 1) {
    return console.log(usage);
  }

  const providers = builtinProviders();
  const send = message => process.stdout.write(`${JSON.stringify(message)}\n`);
  if (command === 'list-providers' && args.length === 1) {
    return send(providers.map(p => ({ id: p.id, name: p.name, loginMethods: loginMethods(p) })));
  }
  if (!['connection-status', 'list-models', 'login', 'logout'].includes(command) || !providerId || args.length > (command === 'login' ? 3 : 2)) {
    throw new UsageError(usage);
  }

  const provider = providers.find(p => p.id === providerId);
  if (!provider) {
    throw new UsageError('Unknown Pi provider. Run list-providers for supported IDs.');
  }
  if (process.platform !== 'darwin') {
    throw new PreferencesError('Second Brain requires macOS Keychain.');
  }
  await runCommand(command, provider, {
    entry: new Entry(keychainService, provider.id),
    runSecurity: spawnSync,
    input: process.stdin, send, authMethod,
    getDeviceId: () => installationUuid(join(homedir(), 'Library', 'Application Support', 'Second Brain')),
  });
}

if (import.meta.main) {
  try {
    await main(process.argv.slice(2));
  } catch (error) {
    console.error(error instanceof PreferencesError ? error.message : 'Cannot access the saved login. Check macOS Keychain.');
    if (error instanceof LoginCancelled) process.exitCode = 3;
    else process.exitCode = error instanceof UsageError ? 2 : 1;
  }
}
