// npm run build [-- <out.wasm>]
// Compiles assembly/voice.ts (asconfig.json: -O3, SIMD, stub runtime, no
// imports) to ../static/voice-dsp.wasm and records its sha256 in
// voice-dsp.wasm.sha256, which tests/studio/test_voice_wasm.py checks the
// served file against. With an output path (the freshness check) it builds
// there and records nothing. The build is deterministic: the same sources
// and locked toolchain give the same bytes.
import asc from "assemblyscript/asc";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const target = process.argv[2] || join(here, "..", "static", "voice-dsp.wasm");
const { error } = await asc.main(
  ["assembly/voice.ts", "--config", "asconfig.json", "--outFile", target],
  { stdout: process.stdout, stderr: process.stderr, baseDir: here },
);
if (error) {
  console.error(`build failed: ${error.message}`);
  process.exit(1);
}
const hash = createHash("sha256").update(readFileSync(target)).digest("hex");
if (!process.argv[2]) writeFileSync(join(here, "voice-dsp.wasm.sha256"), hash + "\n");
console.log(`${target}: sha256 ${hash}`);
