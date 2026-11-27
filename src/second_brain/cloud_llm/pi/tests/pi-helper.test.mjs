import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough, Readable } from 'node:stream';
import test from 'node:test';
import { createAssistantMessageEventStream, createProvider, envApiKeyAuth } from '@earendil-works/pi-ai';
import * as anthropicMessages from '@earendil-works/pi-ai/api/anthropic-messages';
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
  for (const usageError of [run('login', 'not-a-pi-provider'), run('build-turn', 'deepseek')]) {
    assert.equal(usageError.status, 2);
    assert.equal(usageError.stdout, '');
  }
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

const tool = { name: 'read_page', description: 'Read one wiki page', parameters: {
  type: 'object', properties: { title: { type: 'string' } }, required: ['title'],
} };
const firstTurn = { systemPrompt: 'Build a wiki.', tools: [tool], messages: [{ role: 'user', content: 'Read Pi.', timestamp: 1 }] };
const rates = { input: 2, output: 10, cacheRead: 1, cacheWrite: 2.5 };
const turnCost = (10 * rates.input + 8 * rates.output) / 1e6; // completion() reports 10 tokens in, 8 out.
const apiKey = { type: 'api_key', key: 'private-selected-key' };

// build-turn through Pi's real Anthropic adapter: request serialization, SSE parsing, usage and
// replay. Only fetch, the OAuth refresh and the Keychain item are substitutes, so nothing is sent.
// A reply queued as a plain object skips the adapter: it is the message another adapter would
// have produced, for provider stop reasons that Anthropic's stream cannot express.
function buildFixture({ saved = apiKey, cost = rates, refresh } = {}) {
  const requests = [];
  const replies = [];
  const refreshes = [];
  const fetch = async (url, init) => {
    requests.push({ url: new URL(url), headers: new Headers(init.headers), body: JSON.parse(init.body), login: f.options.entry.getPassword() });
    return replies.shift();
  };
  const normalized = ({ status = 200, closed, ...fields }, { onResponse }) => {
    const message = { role: 'assistant', api: 'openai-responses', provider: 'wiki-fixture', model: 'chat', timestamp: 1,
      content: [{ type: 'text', text: 'Done.' }], stopReason: 'stop', rawStopReason: 'completed', ...fields,
      usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 }, ...fields.usage } };
    const stream = createAssistantMessageEventStream();
    if (status) onResponse({ status, headers: {} }); // Google's adapters never report the response.
    // Pi announces each tool call once its arguments are complete; `closed` overrides which ones.
    message.content.forEach((block, contentIndex) => {
      if (closed?.includes(contentIndex) ?? block.type === 'toolCall') stream.push({ type: 'toolcall_end', contentIndex, toolCall: block, partial: message });
    });
    stream.end(message);
    return stream;
  };
  const chat = { provider: 'wiki-fixture', api: 'anthropic-messages', id: 'chat', name: 'Chat',
    baseUrl: 'https://fixture.invalid', reasoning: true, input: ['text'], cost, contextWindow: 200000, maxTokens: 64000,
    compat: { allowedFallbackModels: [{ provider: 'wiki-fixture', model: 'other', cost }] } };
  const provider = createProvider({
    id: 'wiki-fixture',
    models: [{ ...chat, id: 'other', name: 'Other' }, chat],
    auth: { apiKey: envApiKeyAuth('Fixture', ['FIXTURE_API_KEY']), oauth: {
      name: 'Fixture OAuth', login: async () => credential,
      async refresh(stored) {
        refreshes.push(stored);
        return refresh ? refresh(stored) : { ...stored, access: 'private-rotated', refresh: 'private-next', expires: Date.now() + 3600_000 };
      },
      toAuth: stored => ({ apiKey: stored.access, baseUrl: 'https://account.invalid' }),
    } },
    filterModels: (models, stored) => models.filter(m => stored?.availableModelIds?.includes(m.id) ?? true),
    // With nothing queued, a request still reaches fetch, so a test can see that one was sent.
    api: { stream: anthropicMessages.stream, streamSimple: (model, ctx, opts) => replies.length && !(replies[0] instanceof Response)
      ? normalized(replies.shift(), opts) : anthropicMessages.streamSimple(model, ctx, { ...opts, fetch }) },
  });
  const f = fixture(undefined, provider);
  if (saved) f.options.entry.setPassword(JSON.stringify(saved));
  f.options.modelId = 'chat';
  const turn = async (context = firstTurn) => {
    f.options.input = Readable.from([Buffer.from(typeof context === 'string' ? context : JSON.stringify(context))]);
    await f.run('build-turn');
    return f.output.at(-1);
  };
  return { ...f, entry: f.options.entry, requests, replies, refreshes, turn };
}

function completion(rawStopReason, { call = false, closed = true } = {}) {
  const events = [
    { type: 'message_start', message: { id: 'msg_1', model: 'chat', usage: { input_tokens: 10 },
      input_transformations: [{ reason: 'private-server-diagnostic' }] } },
    { type: 'content_block_start', index: 0, content_block: { type: 'thinking', thinking: 'Consider Pi.', signature: 'opaque-signature' } },
    { type: 'content_block_stop', index: 0 },
    { type: 'content_block_start', index: 1, content_block: call ? { type: 'tool_use', id: 'call_1', name: tool.name, input: {} } : { type: 'text', text: 'Done.' } },
    ...(call ? [{ type: 'content_block_delta', index: 1, delta: { type: 'input_json_delta', partial_json: '{"title":"Pi"}' } }] : []),
    ...(closed ? [{ type: 'content_block_stop', index: 1 }] : []),
    { type: 'message_delta', delta: { stop_reason: rawStopReason }, usage: { output_tokens: 8 } },
    { type: 'message_stop' },
  ];
  return new Response(events.map(e => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join(''), {
    headers: { 'content-type': 'text/event-stream' },
  });
}

test('build-turn uses the saved key and exact model, and replays a tool call with its signed thinking', async () => {
  const f = buildFixture();
  f.replies.push(completion('tool_use', { call: true }), completion('end_turn'));
  const first = await f.turn();
  assert.equal(first.outcome, 'toolUse');
  assert.equal(first.cost_usd, turnCost);
  assert.deepEqual(first.message.content, [
    { type: 'thinking', thinking: 'Consider Pi.', thinkingSignature: 'opaque-signature' },
    { type: 'toolCall', id: 'call_1', name: 'read_page', arguments: { title: 'Pi' } },
  ]);
  const [sent] = f.requests;
  assert.equal(sent.headers.get('x-api-key'), apiKey.key);
  assert.equal(sent.body.model, 'chat');
  assert.equal(sent.body.fallbacks, undefined);
  assert.equal(sent.body.max_tokens, 16384);
  assert.equal(sent.body.system[0].text, firstTurn.systemPrompt);
  assert.deepEqual(sent.body.tools[0].input_schema, tool.parameters);

  const result = { role: 'toolResult', toolCallId: 'call_1', toolName: 'read_page', isError: false,
    content: [{ type: 'text', text: 'Here is Pi.' }], timestamp: 2 };
  const final = await f.turn({ ...firstTurn, messages: [...firstTurn.messages, first.message, result] });
  assert.equal(final.outcome, 'stop');
  const [, assistant, reply] = f.requests[1].body.messages;
  assert.deepEqual(assistant.content, [
    { type: 'thinking', thinking: 'Consider Pi.', signature: 'opaque-signature' },
    { type: 'tool_use', id: 'call_1', name: 'read_page', input: { title: 'Pi' } },
  ]);
  assert.equal(reply.content[0].tool_use_id, 'call_1');
  assert.doesNotMatch(JSON.stringify(f.output), /private-/);
});

test('build-turn saves a refreshed OAuth login before sending the request with it', async () => {
  const f = buildFixture({ saved: credential });
  f.replies.push(completion('end_turn'), completion('end_turn'));
  assert.equal((await f.turn()).outcome, 'stop');
  assert.equal((await f.turn()).outcome, 'stop');
  assert.deepEqual(f.refreshes, [credential]); // The second turn finds the saved token still fresh.
  assert.equal(f.requests.length, 2);
  for (const sent of f.requests) {
    assert.equal(JSON.parse(sent.login).refresh, 'private-next');
    assert.equal(sent.headers.get('x-api-key'), 'private-rotated');
    assert.equal(sent.url.host, 'account.invalid');
  }
  assert.doesNotMatch(JSON.stringify(f.output), /private-/);
});

test('build-turn sends nothing without a saved login or for a model the login does not offer', async t => {
  process.env.FIXTURE_API_KEY = 'ambient-test-key';
  t.after(() => { delete process.env.FIXTURE_API_KEY; });
  const f = buildFixture({ saved: null });
  assert.deepEqual(await f.turn(), { outcome: 'not_connected' });
  f.entry.setPassword(JSON.stringify({ ...credential, availableModelIds: [] }));
  assert.deepEqual(await f.turn(), { outcome: 'model_unavailable' });
  assert.equal(f.requests.length, 0);
  assert.equal(f.refreshes.length, 0);
});

test('build-turn reports a failed refresh or Keychain write as an error and sends nothing', async () => {
  const failed = buildFixture({ saved: credential, refresh: () => { throw new Error('private-oauth-error'); } });
  assert.deepEqual(await failed.turn(), { outcome: 'error', cost_usd: 0 });
  assert.deepEqual(JSON.parse(failed.entry.getPassword()), credential);
  const unsaved = buildFixture({ saved: credential });
  unsaved.entry.setPassword = () => { throw new Error('private-keychain-error'); };
  assert.deepEqual(await unsaved.turn(), { outcome: 'error', cost_usd: 0 });
  assert.equal(failed.requests.length + unsaved.requests.length, 0);
});

test('build-turn never passes on a paused or cut-off reply, or tool calls from a turn that was not tool use', async () => {
  const f = buildFixture();
  for (const [reason, options] of [
    ['pause_turn', {}], ['stop_sequence', {}], ['max_tokens', { call: true }], ['end_turn', { call: true }],
    ['tool_use', {}], ['tool_use', { call: true, closed: false }], // The last one never finished its arguments.
  ]) {
    f.replies.push(completion(reason, options));
    assert.deepEqual(await f.turn(), { outcome: 'incomplete', cost_usd: turnCost }, `${reason} ${JSON.stringify(options)}`);
  }
  const call = { type: 'toolCall', id: 'call_1', name: tool.name, arguments: { title: 'Pi' } };
  for (const [why, reply] of [
    ['the provider gave no stop reason', { rawStopReason: undefined }],
    ['one of two tool calls never finished its arguments', { stopReason: 'toolUse', content: [call, { ...call, id: 'call_2' }], closed: [0] }],
    ['the response was still in progress', { rawStopReason: 'in_progress' }],
    ['the model did not end its turn', { endTurn: false }],
    ['the provider recorded a refusal', { errorMessage: 'private-refusal' }],
    ['the gateway cut the reply off', { api: 'pi-messages', rawStopReason: undefined, stopReason: 'length' }],
    ['Pi gained an API this helper has not vetted', { api: 'new-api' }],
  ]) {
    f.replies.push(reply);
    assert.deepEqual(await f.turn(), { outcome: 'incomplete', cost_usd: null }, why);
  }
  // The same shapes are complete where they are normal: Pi's gateway, and a tool-use turn.
  f.replies.push({ api: 'pi-messages', rawStopReason: undefined }, { stopReason: 'toolUse', content: [call], endTurn: false });
  assert.equal((await f.turn()).outcome, 'stop');
  assert.equal((await f.turn()).outcome, 'toolUse');
});

test('build-turn retries a rate-limited request and reports the reply that follows', async () => {
  const f = buildFixture();
  const busy = () => new Response('{}', { status: 429, headers: { 'retry-after-ms': '1' } });
  f.replies.push(busy(), busy(), completion('end_turn'));
  assert.deepEqual([(await f.turn()).outcome, f.requests.length], ['stop', 3]);
  f.replies.push(busy(), busy(), busy(), completion('end_turn'));
  assert.deepEqual(await f.turn(), { outcome: 'error', cost_usd: 0 });
});

test('build-turn keeps provider error text to itself and recognizes a request that is too large', async () => {
  const f = buildFixture();
  for (const [status, outcome] of [[413, 'too_large'], [400, 'error']]) {
    f.replies.push(new Response(JSON.stringify({ type: 'error', error: { message: 'private-provider-body' } }), { status }));
    assert.deepEqual(await f.turn(), { outcome, cost_usd: 0 });
  }
  assert.equal(f.requests.length, 2);
  // Other adapters hand over the rejected response itself. It is still a request nobody pays for.
  f.replies.push({ status: 429, stopReason: 'error', rawStopReason: undefined, content: [], errorMessage: 'private-provider-body' });
  assert.deepEqual(await f.turn(), { outcome: 'error', cost_usd: 0 });
  // Bedrock reports an overflowing request as a stop reason, not as an error.
  f.replies.push({ api: 'bedrock-converse-stream', stopReason: 'length', rawStopReason: 'model_context_window_exceeded', content: [] });
  assert.equal((await f.turn()).outcome, 'too_large');
  // A reply that died after the provider took the request may still be charged for.
  f.replies.push({ stopReason: 'error', rawStopReason: undefined, content: [], errorMessage: 'private-provider-body' });
  assert.deepEqual(await f.turn(), { outcome: 'error', cost_usd: null });
});

test('build-turn reports an unknown cost, not $0, without a price or usage, unless the provider billed $0', async () => {
  const unpriced = buildFixture({ cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } });
  unpriced.replies.push(completion('end_turn'));
  assert.equal((await unpriced.turn()).cost_usd, null);
  const f = buildFixture();
  // No usage reported, with and without the adapter reporting the response; then a free turn.
  f.replies.push({}, { status: null }, { usage: { cost: { total: 0, totalFromProvider: true } } });
  assert.equal((await f.turn()).cost_usd, null);
  assert.equal((await f.turn()).cost_usd, null);
  assert.equal((await f.turn()).cost_usd, 0);
});

test('build-turn rejects anything but one Pi context on stdin before reading the login', async () => {
  const f = buildFixture();
  f.entry.getPassword = () => assert.fail('read the saved login');
  for (const input of ['{}{}', '{"systemPrompt":"private-invalid-json"', '{"tools":[]}']) {
    await assert.rejects(f.turn(input), { message: 'build-turn reads one Pi context as JSON on stdin.' });
  }
});
