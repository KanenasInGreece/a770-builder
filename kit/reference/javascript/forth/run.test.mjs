// run.test.mjs — the grader entry point for the forth reference exercise: `node --test
// kit/reference/javascript/forth/run.test.mjs`. Installs the Jest-compatible shim as globals, then imports
// the exercise's own forth.spec.js completely unmodified, except for the one import specifier node's ESM
// resolver requires an extension for. That one line is rewritten into a generated copy at run time
// (forth.spec.node.js, deleted again once the import resolves) — the verbatim forth.spec.js is never edited.
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import '../../js/expect-shim.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const specPath = join(here, 'forth.spec.js');
const genPath = join(here, 'forth.spec.node.js');

const specSource = readFileSync(specPath, 'utf8');
const needle = "from './forth';";
const replacement = "from './forth.js';";
if (!specSource.includes(needle)) {
  throw new Error(
    `run.test.mjs: expected to find ${JSON.stringify(needle)} in forth.spec.js — the rewrite is stale`,
  );
}
writeFileSync(genPath, specSource.replace(needle, replacement), 'utf8');

try {
  await import('./forth.spec.node.js');
} finally {
  unlinkSync(genPath);
}
