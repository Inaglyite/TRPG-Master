# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: structured-interaction-duals.spec.ts >> PROBE 移动已抵达后：关联请求的「尚未执行」明细必须同时消失（等后端同步请求终态）
- Location: e2e/structured-interaction-duals.spec.ts:650:1

# Error details

```
Error: expect(locator).toBeHidden() failed

Locator:  locator('[data-testid="structured-interaction-card"], [data-testid="structured-awaiting"]').first()
Expected: hidden
Received: visible
Timeout:  30000ms

Call log:
  - Expect "toBeHidden" with timeout 30000ms
  - waiting for locator('[data-testid="structured-interaction-card"], [data-testid="structured-awaiting"]').first()
    64 × locator resolved to <div class="structured-awaiting" data-testid="structured-awaiting">…</div>
       - unexpected value "visible"

```

```yaml
- paragraph: 守秘人正在等你回应。上面是正常叙事，你的位置与行动都还没有变化。
- paragraph: 尚未执行：尚未出发前往密斯卡托尼克大学医学院
- paragraph: 直接说话回应就行：追问、改主意，或让他照办都可以；不需要说“继续”之类的口令。
```

# Test source

```ts
  579 |   const frames = collectFrames(page);
  580 |   const boot = await bootStructuredWorld(page, frames, "human");
  581 |   // 目的地从「前往」对话框实时挑（快照列表在移动后会滞后）
  582 |   const dialogDestinations = await openMoveDialog(page);
  583 |   const target = dialogDestinations[0];
  584 |   await page.getByRole("button", { name: "取消" }).click();
  585 |   const sceneBefore = await page.locator(".header-scene-name").innerText();
  586 |   const movesBefore = eventPayload(frames.received, "scene_changed").length;
  587 | 
  588 |   // 1) 玩家表达意愿 → 主持记录「尚未出发」（线程 open，位置不变）
  589 |   const wishId = await playerTextRequest(
  590 |     page,
  591 |     frames,
  592 |     `我想去${target.name}看看。`,
  593 |   );
  594 |   await keeperPark(page, wishId, target.id, `尚未出发前往${target.name}`);
  595 |   await expect(waitingCard(page)).toBeVisible();
  596 |   const threadBefore = openThreadId(frames);
  597 |   expect(await page.locator(".header-scene-name").innerText()).toBe(
  598 |     sceneBefore,
  599 |   );
  600 | 
  601 |   // 2) 玩家追问细节 —— 这只是普通对话，不是「执行原动作」
  602 |   const followUpId = await playerTextRequest(
  603 |     page,
  604 |     frames,
  605 |     "那边现在有人值班吗？会不会吃闭门羹？",
  606 |   );
  607 |   expect(followUpId).not.toBe(wishId);
  608 | 
  609 |   // 3) 主持正常回答并等待：只给叙事，不移动，也不动线程
  610 |   await keeperResolve(page, followUpId, "completed");
  611 |   await expect(waitingCard(page)).toBeVisible();
  612 |   await expect(waitingCard(page)).toContainText("尚未执行");
  613 |   await expect(waitingCard(page)).toContainText(target.name);
  614 |   // 同一线程仍然存活：位置没变、线程 id 没换、没有新的场景事件
  615 |   expect(await page.locator(".header-scene-name").innerText()).toBe(
  616 |     sceneBefore,
  617 |   );
  618 |   expect(openThreadId(frames)).toBe(threadBefore);
  619 |   expect(
  620 |     eventPayload(frames.received, "scene_changed").length,
  621 |     "回答追问不应改变场景",
  622 |   ).toBe(movesBefore);
  623 |   await page.screenshot({
  624 |     path: `${screenshotsDir}/structured-dual-followup-alive.png`,
  625 |   });
  626 | 
  627 |   // 4) 原动作真的执行（换场景）→ 目标一致的开放线程自动收尾
  628 |   await keeperMove(page, target.id);
  629 |   await expect
  630 |     .poll(() => page.locator(".header-scene-name").innerText(), {
  631 |       timeout: 30_000,
  632 |     })
  633 |     .toBe(target.name);
  634 |   await expect(page.getByTestId("structured-interaction-card")).toHaveCount(0);
  635 | 
  636 |   // 全流程零模型调用（人类主持）
  637 |   expect(modelRequests.length).toBe(boot.modelCallsAfterBoot);
  638 | });
  639 | 
  640 | /**
  641 |  * 已知后端缺陷：移动命令把目标一致的开放线程收尾为 completed（domains.py
  642 |  * `auto_complete_move_threads`），但**没有**同步那条 `awaiting_player` 请求。
  643 |  * 结果是：人已经到达目的地，玩家卡片上仍留着「尚未执行：尚未出发前往X」——
  644 |  * 世界状态与待办自相矛盾（失效待办）。线程卡消失后，这条 awaiting 明细会
  645 |  * 重新以旧卡片形式露出来（见本文件上一用例第 4 步之后）。
  646 |  *
  647 |  * 分类：要么在收尾线程时一并把关联请求置终态，要么让前端不显示「已抵达」的
  648 |  * 待办——前者才是权威侧，所以登记给后端，断言先以 fixme 保留。
  649 |  */
  650 | test("PROBE 移动已抵达后：关联请求的「尚未执行」明细必须同时消失（等后端同步请求终态）", async ({
  651 |   page,
  652 | }) => {
  653 |   test.setTimeout(300_000);
  654 |   page.setDefaultTimeout(30_000);
  655 |   const frames = collectFrames(page);
  656 |   await bootStructuredWorld(page, frames, "human");
  657 |   const dialogDestinations = await openMoveDialog(page);
  658 |   const target = dialogDestinations[0];
  659 |   await page.getByRole("button", { name: "取消" }).click();
  660 | 
  661 |   const wishId = await playerTextRequest(
  662 |     page,
  663 |     frames,
  664 |     `我想去${target.name}看看。`,
  665 |   );
  666 |   await keeperPark(page, wishId, target.id, `尚未出发前往${target.name}`);
  667 |   await expect(waitingCard(page)).toBeVisible();
  668 | 
  669 |   await keeperMove(page, target.id);
  670 |   await expect
  671 |     .poll(() => page.locator(".header-scene-name").innerText(), {
  672 |       timeout: 30_000,
  673 |     })
  674 |     .toBe(target.name);
  675 |   expect(await page.locator(".header-scene-name").innerText()).toBe(
  676 |     target.name,
  677 |   );
  678 |   // 人已经到达：任何「尚未执行：尚未出发」都不该还在（当前会失败）
> 679 |   await expect(waitingCard(page)).toBeHidden({ timeout: 30_000 });
      |                                   ^ Error: expect(locator).toBeHidden() failed
  680 |   await expect(page.getByText(/尚未执行：尚未出发/)).toHaveCount(0);
  681 | });
  682 | 
  683 | test("刷新不丢公开待办，且同一动作只落账一次", async ({ page }) => {
  684 |   test.setTimeout(300_000);
  685 |   page.setDefaultTimeout(30_000);
  686 |   const frames = collectFrames(page);
  687 |   const boot = await bootStructuredWorld(page, frames, "human");
  688 |   const { scene } = boot;
  689 |   // 目的地要从「前往」对话框实时挑（快照列表在移动后会滞后）
  690 |   const dialogDestinations = await openMoveDialog(page);
  691 |   const target = dialogDestinations[0];
  692 |   await page.getByRole("button", { name: "取消" }).click();
  693 | 
  694 |   // 挂一个待办 → 刷新 → 卡片应恢复（快照里的公开待办）
  695 |   const wishId = await playerTextRequest(
  696 |     page,
  697 |     frames,
  698 |     `我想去${target.name}。`,
  699 |   );
  700 |   await keeperPark(page, wishId, target.id, `尚未出发前往${target.name}`);
  701 |   await expect(waitingCard(page)).toBeVisible();
  702 | 
  703 |   await page.reload();
  704 |   await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  705 |   await expect(waitingCard(page)).toBeVisible({
  706 |     timeout: 60_000,
  707 |   });
  708 |   await expect(
  709 |     page.getByText(new RegExp(`尚未执行：尚未出发前往${target.name}`)),
  710 |   ).toBeVisible();
  711 |   expect(await page.locator(".header-scene-name").innerText()).toBe(scene);
  712 | 
  713 |   // 主持执行一次 → 恰好一次 scene_changed；再刷新不会重放
  714 |   await keeperMove(page, target.id);
  715 |   await expect
  716 |     .poll(() => page.locator(".header-scene-name").innerText(), {
  717 |       timeout: 30_000,
  718 |     })
  719 |     .toBe(target.name);
  720 |   const movedEvents = eventPayload(frames.received, "scene_changed").length;
  721 |   expect(movedEvents).toBe(1);
  722 |   await page.reload();
  723 |   await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  724 |   await expect
  725 |     .poll(() => page.locator(".header-scene-name").innerText(), {
  726 |       timeout: 30_000,
  727 |     })
  728 |     .toBe(target.name);
  729 |   expect(eventPayload(frames.received, "scene_changed").length).toBe(
  730 |     movedEvents,
  731 |   );
  732 |   await page.screenshot({
  733 |     path: `${screenshotsDir}/structured-refresh-pending.png`,
  734 |   });
  735 |   expect(modelRequests.length).toBe(boot.modelCallsAfterBoot);
  736 | });
  737 | 
  738 | test("agent 缺 BYOK：请求明确暂停、输入可用、无永久转圈、可接管", async ({
  739 |   page,
  740 | }) => {
  741 |   test.setTimeout(300_000);
  742 |   page.setDefaultTimeout(30_000);
  743 |   const frames = collectFrames(page);
  744 |   const boot = await bootStructuredWorld(page, frames, "agent");
  745 | 
  746 |   await page.locator("#user-input").fill("说实话，我想先看看莱特教授的尸体。");
  747 |   await page.locator("#btn-send").click();
  748 | 
  749 |   // 缺 BYOK 的暂停在 Kimi 的 552672c 之后有实时帧与快照 detail；这条用例负责
  750 |   // 「刷新/重连」那一半：暂停态 + 可操作原因都要能从快照恢复出来，输入仍可用。
  751 |   await expect(page.locator("#user-input")).toBeEnabled();
  752 |   await expect(page.getByTestId("btn-keeper-console")).toBeVisible();
  753 |   await page.reload();
  754 |   await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  755 |   await expect(page.getByText("已暂停（可恢复）")).toBeVisible({
  756 |     timeout: 60_000,
  757 |   });
  758 |   // 刷新后不能只知道「暂停了」，还要知道为什么、能不能接管（快照 detail）
  759 |   await expect(page.getByText(/BYOK|未配置模型服务/)).toBeVisible({
  760 |     timeout: 30_000,
  761 |   });
  762 |   await expect(page.locator("#user-input")).toBeEnabled();
  763 |   await page.screenshot({
  764 |     path: `${screenshotsDir}/structured-agent-paused.png`,
  765 |   });
  766 |   // 缺 BYOK 时是 fail-closed：一次模型调用都不该发生
  767 |   expect(modelRequests.length).toBe(boot.modelCallsAfterBoot);
  768 | });
  769 | 
  770 | // Kimi 在途修复（`agent_runtime._run_keeper_agent` 的 BYOK 分支此前只提交命令、
  771 | // 丢弃 resolve_intent 事件，玩家卡片永久停在「已提交，等待服务端确认」）：
  772 | // 该修复把暂停事件按 deliver/broadcast 投递出去。此用例即原来的 fixme，
  773 | // 后端修复落地后改回真测试；若所在版本尚无该修复，这里会红——那就是回归信号。
  774 | test("agent 缺 BYOK：实时帧必须让请求离开「处理中」", async ({ page }) => {
  775 |   test.setTimeout(300_000);
  776 |   page.setDefaultTimeout(30_000);
  777 |   const frames = collectFrames(page);
  778 |   const boot = await bootStructuredWorld(page, frames, "agent");
  779 |   await page.locator("#user-input").fill("说实话，我想先看看莱特教授的尸体。");
```