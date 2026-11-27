import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { builtinProviders } from '@earendil-works/pi-ai/providers/all';
import { amazonBedrockProvider } from '@earendil-works/pi-ai/providers/amazon-bedrock';
import { cloudflareWorkersAIProvider } from '@earendil-works/pi-ai/providers/cloudflare-workers-ai';
import { deepseekProvider } from '@earendil-works/pi-ai/providers/deepseek';
import { githubCopilotProvider } from '@earendil-works/pi-ai/providers/github-copilot';
import { googleVertexProvider } from '@earendil-works/pi-ai/providers/google-vertex';
import { openaiProvider } from '@earendil-works/pi-ai/providers/openai';
import { installationUuid, runCommand } from '../pi-helper.mjs';

const helperPath = join(import.meta.dirname, '..', 'pi-helper.mjs');

// Real Pi collection and catalog; substitute the interactive provider and Keychain item only.
function fixture(login, provider = openaiProvider()) {
  if (login) provider = { ...provider, auth: { ...provider.auth, oauth: { ...provider.auth.oauth, login } } };
  let saved = null;
  const input = new PassThrough();
  const output = [];
  const options = {
    entry: {
      getPassword: () => saved,
      setPassword: value => { saved = value; },
      deletePassword: () => { saved = null; },
    },
    runSecurity(command, args, { stdio }) {
      assert.equal(command, '/usr/bin/security');
      assert.deepEqual(args, ['find-generic-password', '-s', 'com.second-brain.pi-oauth', '-a', provider.id]);
      assert.equal(stdio, 'ignore');
      return { status: saved === null ? 44 : 0 };
    },
    input,
    send: event => output.push(event),
    getDeviceId: () => 'b7cbde49-3f22-4f07-99df-9b2c873ea445',
  };
  return { input, output, options, run: cmd => runCommand(cmd, provider, options) };
}

const credential = { type: 'oauth', access: 'private-access', refresh: 'private-refresh', expires: 12345 };

test('login, list from a fresh collection, and disconnect without exposing credentials', async () => {
  const f = fixture(async (ui, options) => {
    assert.equal(options.agentName, 'Second Brain');
    ui.notify({ type: 'auth_url', url: 'https://example.test/sign-in' });
    assert.equal(await ui.prompt({ type: 'manual_code', message: 'Paste redirect' }), 'private-answer');
    return credential;
  });
  f.options.send = event => {
    f.output.push(event);
    if (event.type === 'prompt') f.input.write(JSON.stringify({ type: 'answer', id: event.id, value: 'private-answer' }) + '\n');
  };
  await f.run('login');
  assert.equal(f.output.find(e => e.type === 'auth_url')?.url, 'https://example.test/sign-in');
  assert.equal(f.output.at(-1).type, 'done');
  assert.equal(f.output.find(e => e.kind === 'manual_code').callback_listening, false);
  assert.deepEqual(JSON.parse(f.options.entry.getPassword()), credential);
  await f.run('connection-status');
  assert.deepEqual(f.output.at(-1), { connected: true });
  await f.run('list-models');
  assert.ok(f.output.at(-1).some(m => m.id && m.name));
  assert.doesNotMatch(JSON.stringify(f.output), /private-/);
  await f.run('logout');
  await f.run('connection-status');
  assert.deepEqual(f.output.at(-1), { connected: false });
  await f.run('list-models');
  assert.deepEqual(f.output.at(-1), []);
});

test('browser completion cancels the manual prompt without cancelling the login', async () => {
  const f = fixture(async ui => {
    const step = new AbortController();
    const manual = ui.prompt({ type: 'manual_code', message: 'Optional', signal: step.signal });
    step.abort();
    await assert.rejects(manual);
    return credential;
  });
  const listener = createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  try {
    await f.run('login');
    assert.equal(f.output.at(-1).type, 'done');
    assert.equal(f.output.find(e => e.kind === 'manual_code').callback_listening, true);
  } finally {
    await new Promise(resolve => listener.close(resolve));
  }
});

for (const [reason, reply] of [['cancel', '{"type":"cancel"}\n'], ['malformed input', 'private-invalid-line\n'], ['closed pipe', null]]) {
  test(`pending login stops on ${reason} without overwriting the previous credential`, async () => {
    const f = fixture(async ui => {
      await ui.prompt({ type: 'text', message: 'Waiting' });
      return credential;
    });
    f.options.entry.setPassword('previous');
    f.options.send = () => { if (reply === null) f.input.end(); else f.input.write(reply); };
    await assert.rejects(f.run('login'), error => {
      assert.doesNotMatch(error.message, /private-invalid-line/);
      return true;
    });
    assert.equal(f.options.entry.getPassword(), 'previous');
  });
}

test('CLI distinguishes a cancelled login from a failed command', { skip: process.platform !== 'darwin' }, () => {
  const result = spawnSync(process.execPath, [helperPath, 'login', 'deepseek'], {
    encoding: 'utf8', input: '{"type":"cancel"}\n', timeout: 5000,
  });
  assert.equal(result.status, 3, result.stderr);
});

test('list-providers exposes the installed Pi registry and its login capabilities', () => {
  const run = (...args) => spawnSync(process.execPath, [helperPath, ...args], { encoding: 'utf8' });
  const result = run('list-providers');
  assert.equal(result.status, 0, result.stderr);
  const providers = JSON.parse(result.stdout);
  const registry = builtinProviders();
  assert.deepEqual(providers.map(p => p.id).sort(), registry.map(p => p.id).sort());
  for (const { id, name, auth } of registry) {
    const listed = providers.find(p => p.id === id);
    assert.equal(listed.name, name);
    assert.equal(listed.loginMethods.includes('api_key'), Boolean(auth.apiKey?.login));
    assert.equal(listed.loginMethods.includes('oauth'), Boolean(auth.oauth?.login));
  }
  const unknown = run('login', 'not-a-pi-provider');
  assert.equal(unknown.status, 2);
  assert.equal(unknown.stdout, '');
});

for (const [provider, method] of [[deepseekProvider(), undefined], [openaiProvider(), 'api_key']]) {
  test(`${provider.id}: ${method ? 'explicit' : 'default'} API-key login lists this provider's models`, async () => {
    const f = fixture(undefined, provider);
    f.options.authMethod = method;
    f.options.send = event => {
      f.output.push(event);
      if (event.type === 'prompt') {
        assert.equal(event.kind, 'secret');
        f.input.write(JSON.stringify({ type: 'answer', id: event.id, value: 'private-api-key' }) + '\n');
      }
    };
    await f.run('login');
    assert.deepEqual(JSON.parse(f.options.entry.getPassword()), { type: 'api_key', key: 'private-api-key' });
    await f.run('list-models');
    const choices = f.output.at(-1);
    const catalog = new Set(provider.getModels().map(m => m.id));
    assert.ok(choices.length > 0 && choices.every(m => catalog.has(m.id)));
    assert.doesNotMatch(JSON.stringify(f.output), /private-api-key/);
  });
}

test('unsupported login method does not prompt, replace a credential, or fall back', async () => {
  const f = fixture(undefined, deepseekProvider());
  f.options.entry.setPassword('previous');
  f.options.authMethod = 'oauth';
  await assert.rejects(f.run('login'));
  assert.deepEqual(f.output, []);
  assert.equal(f.options.entry.getPassword(), 'previous');
});

test('incomplete API-key configuration does not replace a working connection', async () => {
  const f = fixture(undefined, cloudflareWorkersAIProvider());
  const previous = JSON.stringify({ type: 'api_key', key: 'old-key', env: { CLOUDFLARE_ACCOUNT_ID: 'account' } });
  f.options.entry.setPassword(previous);
  f.options.send = event => {
    f.output.push(event);
    if (event.type === 'prompt') {
      f.input.write(JSON.stringify({ type: 'answer', id: event.id, value: event.kind === 'secret' ? 'new-key' : '   ' }) + '\n');
    }
  };
  await assert.rejects(f.run('login'));
  assert.equal(f.output.some(e => e.type === 'done'), false);
  assert.equal(f.options.entry.getPassword(), previous);
});

test('a selected service-account file counts as configuration, not an ambient credential', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'pi-service-account-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = join(dir, 'selected.json');
  writeFileSync(file, '{}'); // Pi checks local configuration, not entitlement or file contents.
  const f = fixture(undefined, googleVertexProvider());
  const answers = ['service-account', 'test-project', 'us-central1', file];
  f.options.send = event => {
    f.output.push(event);
    if (event.type === 'prompt') f.input.write(JSON.stringify({ type: 'answer', id: event.id, value: answers.shift() }) + '\n');
  };
  await f.run('login');
  await f.run('list-models');
  assert.ok(f.output.at(-1).length > 0);
  rmSync(file);
  await f.run('list-models');
  assert.deepEqual(f.output.at(-1), []);
  await f.run('connection-status');
  assert.deepEqual(f.output.at(-1), { connected: true }); // Saved, though no models are available.
});

test('a failed Keychain lookup is not reported as disconnected', async () => {
  const f = fixture();
  f.options.runSecurity = () => ({ status: 1, stderr: 'private diagnostic' });
  await assert.rejects(f.run('connection-status'), { message: 'Cannot check the saved login. Check macOS Keychain.' });
  assert.deepEqual(f.output, []);
});

for (const [provider, ambient] of [[googleVertexProvider(), 'adc'], [amazonBedrockProvider(), 'credential-chain']]) {
  test(`${provider.id}: ambient-only sign-in is neither offered nor accepted`, async () => {
    const f = fixture(undefined, provider);
    f.options.send = event => {
      assert.equal(event.kind, 'select');
      assert.ok(event.options.length > 0 && !event.options.some(option => option.id === ambient));
      f.input.write(JSON.stringify({ type: 'answer', id: event.id, value: ambient }) + '\n');
    };
    await assert.rejects(f.run('login'), /offered sign-in methods/);
    assert.equal(f.options.entry.getPassword(), null);
  });
}

test('model listing never fills in a missing saved key with an ambient key', async t => {
  const previous = process.env.DEEPSEEK_API_KEY;
  t.after(() => {
    if (previous === undefined) delete process.env.DEEPSEEK_API_KEY;
    else process.env.DEEPSEEK_API_KEY = previous;
  });
  process.env.DEEPSEEK_API_KEY = 'ambient-test-key';
  const f = fixture(undefined, deepseekProvider());
  f.options.entry.setPassword(JSON.stringify({ type: 'api_key' }));
  await f.run('list-models');
  assert.deepEqual(f.output.at(-1), []);
});

test('OAuth model choices respect the saved account catalog without refreshing', async () => {
  const provider = githubCopilotProvider();
  const f = fixture(undefined, provider);
  const [allowed] = provider.getModels();
  f.options.entry.setPassword(JSON.stringify({ ...credential, availableModelIds: [allowed.id] }));
  await f.run('list-models');
  assert.deepEqual(f.output.at(-1), [{ id: allowed.id, name: allowed.name }]);
  f.options.entry.setPassword(JSON.stringify({ type: 'oauth' }));
  await assert.rejects(f.run('list-models'));
});

test('the provider receives a stable UUID per installation', t => {
  const directory = mkdtempSync(join(tmpdir(), 'second-brain-pi-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const id = installationUuid(directory);
  assert.match(id, /^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i);
  assert.equal(installationUuid(directory), id);
  assert.notEqual(installationUuid(join(directory, 'other-installation')), id);
});
