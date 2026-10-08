import { existsSync } from "node:fs";
import { resolve } from "node:path";

/** Use the same interpreter for fixture startup and read-only evidence checks. */
export function e2ePython(
  repositoryRoot: string,
  override: string | undefined = process.env.TRPG_E2E_PYTHON,
  fileExists: (path: string) => boolean = existsSync,
): string {
  // An explicit interpreter is authoritative; an invalid override must fail,
  // not silently run another Python with different installed dependencies.
  if (override !== undefined) return override;
  for (const directory of [".venv", "venv"]) {
    const candidate = resolve(repositoryRoot, directory, "bin/python");
    if (fileExists(candidate)) return candidate;
  }
  // setup-python installs onto PATH in CI, without a repository virtualenv.
  return "python";
}
