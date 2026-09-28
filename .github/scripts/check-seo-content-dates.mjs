import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

function field(source, name) {
  const frontmatter = source.match(/^---\r?\n([\s\S]*?)\r?\n---/u)?.[1] ?? "";
  return frontmatter.match(new RegExp(`^${name}:\\s*["']?(\\d{4}-\\d{2}-\\d{2})["']?\\s*$`, "m"))?.[1];
}

export function checkBlogContentDate(before, after, today) {
  const modified = field(after, "dateModified") ?? field(after, "datePublished");
  if (!modified || !Number.isFinite(Date.parse(modified)) ||
      new Date(modified).toISOString().slice(0, 10) !== modified || modified > today) {
    throw new Error("Use a valid, non-future dateModified/datePublished");
  }
  const withoutDate = (value) => value.replace(/^dateModified:.*\r?\n/gm, "").trim();
  const previous = field(before, "dateModified") ?? field(before, "datePublished");
  if (before && withoutDate(before) !== withoutDate(after) &&
      previous && modified <= previous && modified !== today) {
    throw new Error("Content changed: advance dateModified to the date of this edit");
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const base = process.argv[2];
  if (!/^[a-f0-9]{40}$/.test(base ?? "")) throw new Error("Expected an exact base SHA");
  const git = (args) => execFileSync("git", args, { encoding: "utf8" });
  const files = git(["diff", "--name-only", "--diff-filter=ACMR", base, "HEAD", "--", "apps/web/src/content/blog"])
    .trim().split("\n").filter((file) => file.endsWith(".mdx"));
  const today = new Date().toISOString().slice(0, 10);
  for (const file of files) {
    let before = "";
    try { before = git(["show", `${base}:${file}`]); } catch { /* new post */ }
    try { checkBlogContentDate(before, readFileSync(file, "utf8"), today); }
    catch (error) { throw new Error(`${file}: ${error.message}`); }
  }
  console.log(`Checked modification dates for ${files.length} changed blog files`);
}
