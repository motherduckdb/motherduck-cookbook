// Bundle the multi-file dive source into a single self-contained .jsx that
// save_dive accepts. Inlines our own ./components and ./lib imports; leaves the
// runtime-provided libraries external (the dive runtime supplies them).
//
//   npm install && npm run build   (or: node bundle.mjs)
//
// Output: dist/dive.jsx  (the string to pass to save_dive / MD_CREATE_DIVE)
import { build } from "esbuild";
import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const outfile = path.resolve(here, "dist/dive.jsx");
mkdirSync(path.dirname(outfile), { recursive: true });

// These are externalized by the dive runtime — must NOT be inlined.
const EXTERNAL = [
  "react",
  "react-dom",
  "recharts",
  "d3",
  "lucide-react",
  "@motherduck/react-sql-query",
];

await build({
  entryPoints: [path.resolve(here, "src/dive.tsx")],
  bundle: true,
  format: "esm",
  jsx: "preserve", // keep JSX; the dive runtime transpiles
  target: "esnext",
  charset: "utf8",
  legalComments: "none",
  external: EXTERNAL,
  outfile,
});

const code = readFileSync(outfile, "utf8");
const sizeKB = (Buffer.byteLength(code, "utf8") / 1024).toFixed(1);

// Sanity checks.
const problems = [];
if (!/export\s*\{[^}]*\bas default\b|export default/.test(code))
  problems.push("no default export found");
if (/from\s*["']\.\.?\//.test(code))
  problems.push("a relative import survived (local file not inlined)");
for (const ext of EXTERNAL) {
  // each used external should appear as a bare import
}

console.log(`bundled -> ${outfile}`);
console.log(`size: ${sizeKB} KB`);
console.log(problems.length ? `PROBLEMS: ${problems.join("; ")}` : "checks: OK");
