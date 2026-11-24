// Convert a JSON array of AsciiDoc snippets to a JSON array of HTML strings.
// Usage: node adoc.mjs in.json out.json   (one process for all snippets)
// Asciidoctor diagnostics go to stderr, tagged with the snippet index.
import Asciidoctor from "@asciidoctor/core";
import { readFileSync, writeFileSync } from "node:fs";

const asciidoctor = Asciidoctor();
const [, , inPath, outPath] = process.argv;
const snippets = JSON.parse(readFileSync(inPath, "utf8"));
const opts = {
  safe: "secure",
  attributes: {
    showtitle: false,
    "attribute-missing": "skip",
    experimental: true,
    "table-caption": false, // no "Table 1." prefixes restarting in every snippet
    leveloffset: "+2", // a description's "= Title" becomes an <h3>
  },
};
const out = snippets.map((s, i) => {
  const logger = asciidoctor.MemoryLogger.create();
  asciidoctor.LoggerManager.setLogger(logger);
  const html = asciidoctor.convert(s, opts);
  for (const m of logger.getMessages()) {
    console.error(`snippet ${i}: ${m.getSeverity()}: ${m.getText()}`);
  }
  return html;
});
writeFileSync(outPath, JSON.stringify(out));
