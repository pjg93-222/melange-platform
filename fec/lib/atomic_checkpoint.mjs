import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

const RETRYABLE_CODES = new Set(["EBUSY", "EPERM", "EACCES", "EEXIST"]);

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

/**
 * Durably stage a complete file beside its destination, then atomically replace
 * the destination. A crash can leave an ignorable temp file, never a truncated
 * accepted checkpoint.
 */
async function writeValueAtomicWithRetry(
  file,
  value,
  { attempts = 30 } = {},
) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const expected =
    typeof value === "string" ? Buffer.from(value, "utf8") : Buffer.from(value);
  const temporary = path.join(
    path.dirname(file),
    `.${path.basename(file)}.tmp-${process.pid}-${crypto.randomUUID()}`,
  );
  let handle;
  try {
    handle = await fs.open(temporary, "wx");
    await handle.writeFile(
      value,
      typeof value === "string" ? "utf8" : undefined,
    );
    await handle.sync();
    await handle.close();
    handle = null;

    let lastError;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      try {
        await fs.rename(temporary, file);
        const observed = await fs.readFile(file);
        if (!observed.equals(expected)) {
          throw new Error(`Atomic checkpoint readback mismatch: ${file}`);
        }
        return;
      } catch (error) {
        lastError = error;
        if (!RETRYABLE_CODES.has(error.code)) throw error;
        if (attempt < attempts) await sleep(250 * attempt);
      }
    }
    throw new Error(
      `Could not atomically replace checkpoint after ${attempts} attempts: ${file}`,
      { cause: lastError },
    );
  } finally {
    if (handle) await handle.close().catch(() => {});
    await fs.rm(temporary, { force: true }).catch(() => {});
  }
}

export async function writeTextAtomicWithRetry(
  file,
  textValue,
  options = {},
) {
  if (typeof textValue !== "string") {
    throw new TypeError("writeTextAtomicWithRetry requires a string");
  }
  await writeValueAtomicWithRetry(file, textValue, options);
}

export async function writeBufferAtomicWithRetry(
  file,
  bufferValue,
  options = {},
) {
  if (!(bufferValue instanceof Uint8Array)) {
    throw new TypeError("writeBufferAtomicWithRetry requires a Uint8Array");
  }
  await writeValueAtomicWithRetry(file, bufferValue, options);
}

export async function writeJsonAtomicWithRetry(
  file,
  value,
  options = {},
) {
  await writeTextAtomicWithRetry(
    file,
    `${JSON.stringify(value, null, 2)}\n`,
    options,
  );
}
