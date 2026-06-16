import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { parse as parseYaml } from "yaml";

export type AiCategory = "crawler" | "agent" | "human_via_ai";

export interface Classification {
  category: AiCategory | null;
  name: string | null;
}

interface Rule {
  pattern: string;
  name: string;
  category: AiCategory;
}

interface RulesFile {
  user_agent_patterns: Rule[];
  referer_patterns: Rule[];
}

function loadRules(): RulesFile {
  // bots.yaml is bundled at the project root via vercel.json includeFiles.
  // At runtime on Vercel this file sits next to the compiled handler.
  const here = dirname(fileURLToPath(import.meta.url));
  const candidates = [
    join(here, "bots.yaml"),
    join(here, "..", "bots.yaml"),
    join(process.cwd(), "bots.yaml"),
  ];
  for (const p of candidates) {
    try {
      return parseYaml(readFileSync(p, "utf8")) as RulesFile;
    } catch {
      // try next
    }
  }
  throw new Error("bots.yaml not found; checked: " + candidates.join(", "));
}

const rules = loadRules();

export function classify(
  userAgent: string | null | undefined,
  referer: string | null | undefined
): Classification {
  if (userAgent) {
    for (const rule of rules.user_agent_patterns) {
      if (userAgent.includes(rule.pattern)) {
        return { category: rule.category, name: rule.name };
      }
    }
  }

  if (referer) {
    for (const rule of rules.referer_patterns) {
      if (referer.includes(rule.pattern)) {
        return { category: rule.category, name: rule.name };
      }
    }
  }

  return { category: null, name: null };
}
