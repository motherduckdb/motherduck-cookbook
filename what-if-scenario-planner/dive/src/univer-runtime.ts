// Loads the Univer spreadsheet engine inside the Dive sandbox. The Dive
// runtime allows no dynamic import() and no external scripts, so the UMD
// bundles ship inside the Dive as one gzip+base64 blob and are evaluated in
// global scope with whichever exec strategy the host CSP permits.
import * as ReactNS from "react";
import * as ReactDOMNS from "react-dom";
import * as ReactDOMClientNS from "react-dom/client";
import { UNIVER_CSS_B64, UNIVER_SCRIPTS_B64 } from "./univer-vendor.generated";

async function inflate(b64: string): Promise<{ name: string; code: string }[]> {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new (window as any).DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}

type ExecFn = (code: string, sourceUrl?: string) => void;
let execStrategy: ExecFn | null = null;

// Dive hosts enforce different CSPs; probe eval, Function and <script> with a
// canary and keep whichever actually runs.
function pickExec(): ExecFn {
  if (execStrategy) return execStrategy;
  const annotate = (c: string, s?: string) => c + (s ? `\n//# sourceURL=${s}` : "");
  const strategies: Array<[string, ExecFn]> = [
    ["indirect-eval", (c, s) => (0, eval)(annotate(c, s))],
    // eslint-disable-next-line no-new-func
    ["new-function", (c, s) => new Function(annotate(c, s))()],
    ["script-tag", (c, s) => {
      const el = document.createElement("script");
      el.textContent = annotate(c, s);
      document.head.appendChild(el);
      el.remove();
    }],
  ];
  const g: any = window;
  for (const [, fn] of strategies) {
    g.__SP_EXEC_PROBE__ = 0;
    try {
      fn("globalThis.__SP_EXEC_PROBE__ = 1;", "exec-probe");
    } catch {
      continue;
    }
    if (g.__SP_EXEC_PROBE__ === 1) return (execStrategy = fn);
  }
  throw new Error("No script execution strategy is allowed by this page's CSP");
}

const READY_KEY = "__SP_UNIVER_READY__";
let loading: Promise<any> | null = null;

/** Evaluates Univer once per window (module re-evaluation reuses the globals). */
export function loadUniver(): Promise<any> {
  const g: any = window;
  if (g[READY_KEY]) return Promise.resolve(g);
  if (loading) return loading;
  loading = (async () => {
    // The sandbox exposes react, react-dom and react-dom/client but not
    // react/jsx-runtime; Univer's UMDs read both from globalThis.React, so
    // synthesize jsx/jsxs from createElement (jsxs needs keys for array children).
    const React: any = (ReactNS as any).default ?? ReactNS;
    const ReactDOM: any = (ReactDOMNS as any).default ?? ReactDOMNS;
    const ReactDOMClient: any = (ReactDOMClientNS as any).default ?? ReactDOMClientNS;
    const jsx = (type: any, config: any, key?: any) => {
      const props = { ...(config || {}) };
      if (key !== undefined) props.key = key;
      return React.createElement(type, props);
    };
    const jsxs = (type: any, config: any, key?: any) => {
      const props = { ...(config || {}) };
      if (key !== undefined) props.key = key;
      if (Array.isArray(props.children)) {
        props.children = props.children.map((c: any, i: number) =>
          c && typeof c === "object" && c.$$typeof && c.key == null ? { ...c, key: `.${i}` } : c,
        );
      }
      return React.createElement(type, props);
    };
    g.React = Object.assign({}, React, { jsx, jsxs, jsxDEV: jsx, Fragment: React.Fragment });
    g.ReactDOM = Object.assign({}, ReactDOM, ReactDOMClient);

    const exec = pickExec();
    for (const { name, code } of await inflate(UNIVER_SCRIPTS_B64)) {
      // redi refuses to load twice (a host may already have it).
      if (name === "redi.js" && (g.redi || g.REDI_GLOBAL_LOCK)) continue;
      exec(code, `univer/${name}`);
    }
    for (const { code } of await inflate(UNIVER_CSS_B64)) {
      const style = document.createElement("style");
      style.textContent = code;
      document.head.appendChild(style);
    }
    g[READY_KEY] = true;
    return g;
  })();
  loading.catch(() => {
    loading = null;
  });
  return loading;
}
