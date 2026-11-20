import type { Page, Route } from "@playwright/test";

/** Seed the human-hosted world required by structured-gameplay scenarios. */
export async function startStructuredSoloFixture(page: Page) {
  const url = "**/api/worlds";
  async function seed(route: Route) {
    if (route.request().method() !== "POST") return route.fallback();
    await route.continue({
      postData: JSON.stringify({
        ...route.request().postDataJSON(),
        execution_profile: "structured_v1",
        keeper_mode: "human",
      }),
    });
  }
  await page.route(url, seed);
  try {
    await Promise.all([
      page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === "/api/worlds" &&
          response.request().method() === "POST",
      ),
      page.locator("#btn-start").click(),
    ]);
  } finally {
    await page.unroute(url, seed);
  }
}
