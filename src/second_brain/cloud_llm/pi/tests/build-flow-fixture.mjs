// The helper as the backend runs it, for django_config/tests/test_pi_build_flow.py: real Pi
// login, catalog, request adapter and tool replay. Only the Keychain item (a file beside the
// script that imports this one) and the provider's HTTP endpoint are substitutes.
import assert from 'node:assert/strict';
import { appendFileSync, existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { deepseekProvider } from '@earendil-works/pi-ai/providers/deepseek';
import { runCommand } from '../pi-helper.mjs';

const [command, providerId, choice] = process.argv.slice(2);
const provider = deepseekProvider();
assert.equal(providerId, provider.id);
const directory = dirname(process.argv[1]);
const saved = join(directory, 'fixture-login.json');

// The provider: asks for one page on the first turn, and finishes once the tool has run.
// It records which key and model each request arrived with, for the test to compare.
globalThis.fetch = async (_url, request) => {
  const body = JSON.parse(request.body);
  const { model } = body;
  const authorization = new Headers(request.headers).get('authorization');
  appendFileSync(join(directory, 'fixture-requests.jsonl'), `${JSON.stringify({ authorization, model })}\n`);
  assert.ok(body.tools.some(tool => tool.function.name === 'write_page'));
  assert.ok(JSON.stringify(body.messages).includes('The red notebook'));
  const last = body.messages.at(-1);
  const wrote = last.role === 'tool';
  if (wrote) {
    assert.equal(last.tool_call_id, 'call_fixture');
    assert.match(last.content, /notebook\.md/);
  }
  const page = { type: 'concept', title: 'Notebook', domains: ['testing'], body: 'Notes from the red notebook.' };
  const delta = wrote ? { role: 'assistant', content: 'Done.' } : { role: 'assistant', tool_calls: [
    { index: 0, id: 'call_fixture', type: 'function', function: { name: 'write_page', arguments: JSON.stringify(page) } },
  ] };
  const chunks = [{ delta, finish_reason: null }, { delta: {}, finish_reason: wrote ? 'stop' : 'tool_calls' }].map(choice =>
    `data: ${JSON.stringify({ id: 'turn_fixture', object: 'chat.completion.chunk', created: 1, model,
      choices: [{ index: 0, ...choice }], usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 } })}\n\n`);
  return new Response(`${chunks.join('')}data: [DONE]\n\n`, { headers: { 'Content-Type': 'text/event-stream' } });
};

try {
  await runCommand(command, provider, {
    entry: {
      getPassword: () => existsSync(saved) ? readFileSync(saved, 'utf8') : null,
      setPassword: value => writeFileSync(saved, value, { mode: 0o600 }),
      deletePassword: () => rmSync(saved, { force: true }),
    },
    runSecurity: () => ({ status: existsSync(saved) ? 0 : 44 }),
    input: process.stdin, send: event => console.log(JSON.stringify(event)),
    authMethod: choice, modelId: choice,
  });
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
