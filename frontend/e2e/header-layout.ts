import { expect, type Page } from "@playwright/test";

/** Real rendered hit areas, not only computed display/visibility properties. */
export async function assertGameHeaderFits(page: Page): Promise<void> {
  const header = page.locator("#header");
  await expect(header.locator("h1")).toBeVisible();
  await expect(header.locator("#conn-status")).toBeVisible();
  expect(
    await header
      .locator("#conn-status")
      .evaluate((node) => Number(getComputedStyle(node).opacity)),
  ).toBe(1);
  const bounds = await header.evaluate((node) => {
    const box = node.getBoundingClientRect();
    const children = Array.from(
      node.querySelectorAll(
        ".header-title, #conn-status, #toolbar button, .header-scene, #btn-solo-adventure-exit",
      ),
    );
    return children.map((child) => {
      const rect = child.getBoundingClientRect();
      return {
        id: child.id || child.className,
        width: rect.width,
        height: rect.height,
        inside:
          rect.left >= box.left &&
          rect.right <= box.right + 1 &&
          rect.top >= box.top &&
          rect.bottom <= box.bottom + 1,
      };
    });
  });
  for (const item of bounds) {
    expect(item.width, `${item.id} collapsed`).toBeGreaterThan(0);
    expect(item.inside, `${item.id} escapes the header`).toBe(true);
    if (item.id.startsWith("btn-") && item.id !== "btn-solo-adventure-exit") {
      expect(item.width).toBeGreaterThan(30);
    }
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
}
