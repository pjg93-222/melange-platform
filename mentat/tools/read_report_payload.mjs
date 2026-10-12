import fs from "node:fs/promises";
import { createReadStream } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";

export async function readReportPayload(filename, limit = 480 * 1024 * 1024) {
  if ((await fs.stat(filename)).size < limit) {
    return JSON.parse(await fs.readFile(filename, "utf8"));
  }
  const directory = `${filename}.parts`;
  const index = JSON.parse(await fs.readFile(path.join(directory, "index.json"), "utf8"));
  if (index.format !== "mentat-json-parts-v1") throw new Error("Unsupported report parts format");
  const sourceHash = createHash("sha256");
  for await (const chunk of createReadStream(filename)) sourceHash.update(chunk);
  if (sourceHash.digest("hex") !== index.source_sha256) throw new Error("Report parts do not match source payload");
  const result = {};
  for (const [key, field] of Object.entries(index.fields)) {
    let value = field.array ? [] : undefined;
    if (!field.array && field.parts.length !== 1) throw new Error("Invalid scalar report part");
    for (const part of field.parts) {
      if (!/^\d+-\d+\.json$/.test(part.file)) throw new Error("Invalid report part filename");
      const bytes = await fs.readFile(path.join(directory, part.file));
      if (createHash("sha256").update(bytes).digest("hex") !== part.sha256) throw new Error("Report part checksum mismatch");
      const parsed = JSON.parse(bytes.toString("utf8"));
      if (field.array) {
        if (!Array.isArray(parsed)) throw new Error("Invalid array report part");
        for (const row of parsed) value.push(row);
      } else value = parsed;
    }
    Object.defineProperty(result, key, {value, writable: true, enumerable: true, configurable: true});
  }
  return result;
}
