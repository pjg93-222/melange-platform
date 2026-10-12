import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  writeBufferAtomicWithRetry,
  writeJsonAtomicWithRetry,
  writeTextAtomicWithRetry,
} from "../lib/atomic_checkpoint.mjs";

async function fixture(run) {
  const directory = await fs.mkdtemp(
    path.join(os.tmpdir(), "legislation-atomic-checkpoint-"),
  );
  try {
    await run(directory);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
}

test("atomic text replacement preserves only complete generations", async () => {
  await fixture(async (directory) => {
    const file = path.join(directory, "checkpoint.txt");
    await writeTextAtomicWithRetry(file, "first\n");
    await writeTextAtomicWithRetry(file, "second\n");
    assert.equal(await fs.readFile(file, "utf8"), "second\n");
    assert.deepEqual(await fs.readdir(directory), ["checkpoint.txt"]);
  });
});

test("atomic JSON replacement remains parseable after overwrite", async () => {
  await fixture(async (directory) => {
    const file = path.join(directory, "checkpoint.json");
    await writeJsonAtomicWithRetry(file, { generation: 1, rows: [1, 2] });
    await writeJsonAtomicWithRetry(file, { generation: 2, rows: [3] });
    assert.deepEqual(JSON.parse(await fs.readFile(file, "utf8")), {
      generation: 2,
      rows: [3],
    });
    assert.deepEqual(await fs.readdir(directory), ["checkpoint.json"]);
  });
});

test("atomic binary replacement preserves exact compressed bytes", async () => {
  await fixture(async (directory) => {
    const file = path.join(directory, "checkpoint.bin");
    const first = new Uint8Array([0, 1, 2, 255]);
    const second = new Uint8Array([31, 139, 8, 0, 7, 6, 5, 4]);
    await writeBufferAtomicWithRetry(file, first);
    await writeBufferAtomicWithRetry(file, second);
    assert.deepEqual(new Uint8Array(await fs.readFile(file)), second);
    assert.deepEqual(await fs.readdir(directory), ["checkpoint.bin"]);
  });
});
