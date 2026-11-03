/**
 * Read-only tools for the answer agent.
 *
 * Only `book_grep` is registered here. The agent also gets the built-in `read`
 * tool; bash/edit/write are excluded by the pi `--tools book_grep,read` allowlist.
 * Searches are confined to the md directories of the books in scope, passed via
 * BOOK_MDS (JSON array, one root per book). BOOK_MD keeps the first root for
 * callers that only know the single-book variable.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";

function roots() {
  let list = [];
  const raw = process.env.BOOK_MDS;
  if (raw) {
    try {
      list = JSON.parse(raw);
    } catch (err) {
      list = [];
    }
  }
  if (!Array.isArray(list)) list = [];
  const clean = list.filter((x) => typeof x === "string" && x.trim());
  if (!clean.length && process.env.BOOK_MD) clean.push(process.env.BOOK_MD);
  return clean.map((r) => path.resolve(process.cwd(), r));
}

function grep(target, pattern, limit) {
  try {
    return execFileSync(
      "rg",
      ["-n", "--no-heading", "--color", "never", "-m", String(limit),
       "-e", String(pattern), target],
      { encoding: "utf8", maxBuffer: 8 * 1024 * 1024, cwd: process.cwd() },
    );
  } catch (err) {
    if (err && err.status === 1) return "";
    throw err;
  }
}

export default function (pi) {
  pi.registerTool({
    name: "book_grep",
    label: "book grep",
    description:
      "Read-only regex search inside the md directories of the books in scope. " +
      "Returns matching file:line lines. Use it instead of bash grep to find " +
      "characters, events, or alternate wording; then read the matching chapter.",
    parameters: {
      type: "object",
      properties: {
        pattern: { type: "string", description: "Ripgrep regex, e.g. 南宫婉.*冰" },
        path: { type: "string", description: "Optional path under one of the book md directories" },
        max: { type: "number", description: "Max matching lines, default 40" },
      },
      required: ["pattern"],
    },
    async execute(_id, params) {
      const all = roots();
      if (!all.length) throw new Error("BOOK_MDS is not set");
      const limit = Math.max(1, Math.min(200, Number(params.max) || 40));
      let out = "";
      let searched = all;
      if (params.path) {
        const target = path.resolve(process.cwd(), params.path);
        const inside = all.filter((r) => target === r || target.startsWith(r + path.sep));
        if (!inside.length) throw new Error("path outside the selected books");
        searched = inside;
        out = grep(target, params.pattern, limit);
      } else {
        // 每本书一份配额，否则排在前面的书把限额吃光，后面的书搜不到东西。
        const quota = Math.max(5, Math.floor(limit / all.length));
        out = all.map((r) => grep(r, params.pattern, quota)).filter(Boolean).join("");
      }
      if (!out.trim()) out = "No matches";
      return {
        content: [{ type: "text", text: out.slice(0, 16000) }],
        details: { pattern: params.pattern, roots: searched.length },
      };
    },
  });
}
