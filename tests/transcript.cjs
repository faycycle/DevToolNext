const assert = require('node:assert/strict');
const { createTerminal } = require('../devtoolnext/web/transcript.js');

const rec = (id, body, direction) => ({
  id,
  direction: direction || 'rx',
  wall: 1,
  data_base64: Buffer.from(body, 'utf8').toString('base64'),
});
const hexRec = (id, hex) => ({
  id,
  direction: 'rx',
  wall: 1,
  data_base64: Buffer.from(hex.replace(/\s+/g, ''), 'hex').toString('base64'),
});
const texts = terminal => terminal.lines.map(line => line.text);

assert.deepEqual(texts(createTerminal('utf-8', true)), ['']);

// CRLF ends a line exactly once.
const crlf = createTerminal('utf-8', true);
crlf.push([rec(1, 'a\r\nb\r\n')]);
assert.deepEqual(texts(crlf), ['a', 'b', '']);

// A bare CR is a carriage return, not a line break: it overwrites in place.
const crOnly = createTerminal('utf-8', true);
crOnly.push([rec(1, 'OK\rREADY\r')]);
assert.deepEqual(texts(crOnly), ['READY']);

// CRLF split across two records still counts as one break.
const split = createTerminal('utf-8', true);
split.push([rec(1, 'first\r'), rec(2, '\nsecond')]);
assert.deepEqual(texts(split), ['first', 'second']);

// Repeated CRs must not add blank lines.
const repeated = createTerminal('utf-8', true);
repeated.push([rec(1, 'a\r\r\nb\r\r\n')]);
assert.deepEqual(texts(repeated), ['a', 'b', '']);

const blank = createTerminal('utf-8', true);
blank.push([rec(1, 'a\r\n\r\nb')]);
assert.deepEqual(texts(blank), ['a', '', 'b']);

// Real capture from a Linux console: readline repaints the prompt with CR after CRLF.
const consolePrompt = createTerminal('utf-8', false);
consolePrompt.push([hexRec(1, '0d 0a 1b5b3f323030346c 0d 1b5b3f3230303468 6b69636b7069406b69636b70693a7e2420')]);
assert.deepEqual(texts(consolePrompt), ['', 'kickpi@kickpi:~$ ']);

const edit = createTerminal('utf-8', true);
edit.push([rec(1, 'abc\bX')]);
assert.equal(texts(edit)[0], 'abX');

const colour = createTerminal('utf-8', true);
colour.push([rec(1, '\u001b[31mred\u001b[0m ok')]);
assert.equal(texts(colour)[0], 'red ok');
assert.equal(colour.lines[0].styles[0], 'ansi-c1');

const cursor = createTerminal('utf-8', true);
cursor.push([rec(1, 'ab')]);
assert.equal(cursor.col, 2);

// Typed keys can be hidden so a shell echo is not duplicated on screen.
const noTx = createTerminal('utf-8', false);
noTx.push([rec(1, 'typed', 'tx'), rec(2, 'echo')]);
assert.equal(texts(noTx)[0], 'echo');

const utf8 = createTerminal('utf-8', true);
const bytes = Buffer.from('设备', 'utf8');
utf8.push([
  { id: 1, direction: 'rx', wall: 1, data_base64: bytes.subarray(0, 2).toString('base64') },
  { id: 2, direction: 'rx', wall: 1, data_base64: bytes.subarray(2).toString('base64') },
]);
assert.equal(texts(utf8)[0], '设备');

console.log('PASS: terminal projection (CR/LF/CRLF, CR overwrite, console prompt repaint, backspace, ANSI, TX filter, UTF-8 split)');
