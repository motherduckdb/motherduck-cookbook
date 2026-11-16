// MotherDuck website design tokens (from the Marketing MCP's motherduck-design-system
// skill: packages/ui/theme). Sand canvas, #383838 ink, 2px borders, 2px radii,
// hard down-left offset shadows, Aeonik Mono UPPERCASE headings, Inter body.
// The Dive runtime cannot load web fonts, so both stacks fall back gracefully.
export const C = {
  ink: "#383838",
  white: "#FFFFFF",
  snow: "#F8F8F7",
  sand: "#F4EFEA",
  darkSnow: "#ECE5DD",
  darkSand: "#E1D6CB",
  lightGrey: "#C0C0C0",
  grey: "#A1A1A1",
  darkGrey: "#818181",
  darkerGrey: "#666666",
  lightestSky: "#CEEBFF",
  lightSky: "#97D4FF",
  sky: "#6FC2FF",
  darkSky: "#2BA5FF",
  darkerSky: "#1276BF",
  lighterGarden: "#53DBC9",
  garden: "#16AA98",
  darkGarden: "#068475",
  lighterSun: "#FAF175",
  sun: "#FFDE00",
  lightWatermelon: "#FF9A94",
  watermelon: "#FF7169",
  darkWatermelon: "#E23F35",
  lighterDuck: "#FFDCBD",
  duck: "#FF9538",
};

export const MONO = '"Aeonik Mono", "Space Mono", Menlo, ui-monospace, SFMono-Regular, monospace';
export const SANS = 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

export const shadow = (n = 4) => `-${n}px ${n}px 0 0 ${C.ink}`;
export const border = `2px solid ${C.ink}`;

export const STATUS_STYLE: Record<string, { bg: string; label: string }> = {
  baseline: { bg: C.darkSand, label: "Baseline" },
  draft: { bg: C.white, label: "Draft" },
  submitted: { bg: C.sun, label: "Submitted" },
  approved: { bg: C.lighterGarden, label: "Approved" },
};
