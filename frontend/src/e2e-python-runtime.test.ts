// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { e2ePython } from "../e2e/python-runtime";

const root = resolve("isolated-fixture-project");

describe("E2E fixture Python selection", () => {
  beforeEach(() => vi.stubEnv("TRPG_E2E_PYTHON", undefined));
  afterEach(() => vi.unstubAllEnvs());

  it("respects an explicit override without silently selecting another runtime", () => {
    const exists = vi.fn(() => false);
    expect(e2ePython(root, "/explicit/python", exists)).toBe(
      "/explicit/python",
    );
    expect(exists).not.toHaveBeenCalled();
  });

  it("prefers the installed .venv interpreter", () => {
    expect(e2ePython(root, undefined, () => true)).toBe(
      resolve(root, ".venv/bin/python"),
    );
  });

  it("also supports a local venv directory", () => {
    const local = resolve(root, "venv/bin/python");
    expect(e2ePython(root, undefined, (path) => path === local)).toBe(local);
  });

  it("uses PATH Python when neither virtualenv exists, as on setup-python CI", () => {
    expect(e2ePython(root, undefined, () => false)).toBe("python");
  });

  it("does not hide an explicitly empty invalid override", () => {
    expect(e2ePython(root, "", () => true)).toBe("");
  });

  it("both failing CI specs use the shared selection, not a hardcoded virtualenv", () => {
    for (const name of [
      "server-session-boundaries.spec.ts",
      "structured-solo-online.spec.ts",
    ]) {
      const source = readFileSync(
        resolve(import.meta.dirname, "../e2e", name),
        "utf8",
      );
      expect(source).toContain("const python = e2ePython(repositoryRoot)");
      expect(source).not.toMatch(/\.venv\/bin\/python/);
    }
  });
});
