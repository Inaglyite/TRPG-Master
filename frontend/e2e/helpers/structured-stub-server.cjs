/**
 * 结构化操作协议 v1 的**测试替身**后端（不是产品后端）。
 *
 * 用途：在后端 M0/M1 尚未提供结构化服务时，用同一份 M0 schema 驱动的
 * WS/HTTP 替身完成前端联调与按钮 payload 取证。它由
 * `e2e/structured-play.spec.ts` 在本地临时目录里启动，**不出现在生产路径**。
 *
 * 替身只做三件事：按 M0 信封回固定响应、按请求逐字记录收到的帧、
 * 给每个连接分配一个角色（keeper / player-a / player-b）。
 */

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const { WebSocketServer } = require("ws");

const DIST = path.resolve(__dirname, "../../dist");
const PROTOCOL_VERSION = 1;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".webp": "image/webp",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".mp3": "audio/mpeg",
};

/** 收到的 outgoing 帧，按连接与类型记录（取证用）。 */
const received = [];

function structuredCapabilities() {
  return {
    protocol_version: PROTOCOL_VERSION,
    structured_protocol: true,
    execution_profile: "structured_v1",
    keeper_modes: ["human"],
    keeper_console: true,
    free_roll: true,
    check_request: true,
    move_action: true,
    present_clue: true,
    use_item: true,
    commands: [
      "move_party",
      "request_check",
      "resolve_check",
      "present_information",
      "grant_clue",
      "use_item",
      "transfer_item",
      "adjust_stat",
      "advance_time",
      "publish_message",
      "resolve_intent",
      "present_handout",
      "set_npc_presence",
      "record_fact",
    ],
  };
}

const PUBLIC_TARGETS = [
  { kind: "npc", id: "bryce_fallon", name: "布莱斯·法伦" },
  { kind: "npc", id: "john_whitcroft", name: "约翰·惠特克罗夫特" },
  { kind: "investigator", id: "inv-alice", name: "爱丽丝" },
  { kind: "investigator", id: "inv-bob", name: "鲍勃" },
];

const DESTINATIONS = [
  { id: "miskatonic_university", name: "密斯卡托尼克大学" },
  { id: "miskatonic_medical", name: "密斯卡托尼克大学医学院" },
];

const CLUES = [
  {
    id: "clue_death_certificate",
    category: "investigation",
    text: "莱特的死亡证明由惠特克罗夫特医生签署。",
    presentation: ["describe", "image"],
    allowed_physical_item_ids: ["item_certificate"],
  },
];

const ITEMS = [
  { id: "item_certificate", label: "死亡证明", quantity: 1, operations: [] },
  {
    id: "item_bandage",
    label: "绷带",
    quantity: 3,
    operations: ["apply", "give"],
  },
];

/** 每个连接的角色：第一条 room/世界身份由查询参数决定，默认 player-a。 */
function roleFor(request) {
  const url = new URL(request.url || "/", "http://127.0.0.1");
  return url.searchParams.get("role") || "player-a";
}

function startServer({ port, scenario = "full" }) {
  const server = http.createServer((req, res) => {
    if (req.url && req.url.startsWith("/api/health")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
      return;
    }
    const urlPath = (req.url || "/").split("?")[0];
    const target = path.join(DIST, urlPath === "/" ? "index.html" : urlPath);
    if (
      !target.startsWith(DIST) ||
      !fs.existsSync(target) ||
      fs.statSync(target).isDirectory()
    ) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, {
      "Content-Type": MIME[path.extname(target)] || "application/octet-stream",
    });
    fs.createReadStream(target).pipe(res);
  });

  const wss = new WebSocketServer({ server, path: "/ws" });

  wss.on("connection", (socket, request) => {
    const role = roleFor(request);
    const worldId = "world-stub-1";
    let eventId = 0;

    const send = (type, payload, revision = 12) => {
      eventId += 1;
      socket.send(
        JSON.stringify({
          protocol_version: PROTOCOL_VERSION,
          event_id: eventId,
          world_id: worldId,
          sequence: eventId,
          revision,
          ...(payload && payload.cause_request_id
            ? { cause_request_id: payload.cause_request_id }
            : {}),
          type,
          payload,
        }),
      );
    };

    // 首连引导：与真实后端一致的五条初始化帧 + 结构化快照。
    // legacy-only 场景：完全不发结构化帧，用于验证旧世界不出现新入口。
    socket.send(
      JSON.stringify({
        type: "module_list",
        modules: [{ id: "猩红文档", title: "猩红文档", version: "legacy" }],
        active: "猩红文档",
        world_id: worldId,
        module_name: "猩红文档",
      }),
    );
    socket.send(
      JSON.stringify({
        type: "character_list",
        module: "猩红文档",
        groups: [
          {
            id: "default",
            title: "默认调查员",
            characters: [
              {
                ref: { source: "default", id: "alice" },
                id: "alice",
                name: "爱丽丝",
                occupation: "记者",
                source_label: "默认",
                hp: 12,
                max_hp: 12,
                san: 65,
                max_san: 65,
                reputation: 40,
                completed_modules: 0,
              },
            ],
          },
        ],
      }),
    );
    socket.send(JSON.stringify({ type: "theme", theme: {} }));
    socket.send(JSON.stringify({ type: "model_settings" }));
    socket.send(JSON.stringify({ type: "save_list", saves: [] }));
    socket.send(JSON.stringify({ type: "adventure_list", adventures: [] }));

    if (scenario === "legacy-only") return;

    // 连接时只做能力协商（capabilities-only 场景）。
    // 常规场景的会话快照在 start 之后下发：真实服务端不会在玩家选角之前
    // 就绑定调查员，前端的“进入游戏中”也因此由开局而非连接触发。
    if (scenario === "capabilities-only") {
      send("session_snapshot", {
        revision: 12,
        execution_profile: "structured_v1",
        keeper_mode: "human",
        server_capabilities: structuredCapabilities(),
        keeper:
          role === "keeper"
            ? { user_id: null, mode: "human" }
            : { mode: "human" },
        scene: { id: "miskatonic_university", name: "密斯卡托尼克大学" },
        destinations: DESTINATIONS,
        investigator_id: role === "player-b" ? "inv-bob" : "inv-alice",
        targets: PUBLIC_TARGETS,
        clues: CLUES,
        items: ITEMS,
        requests: [],
        pending_checks: [],
        cursor: { event_id: eventId, revision: 12 },
      });
    }

    socket.on("message", (raw) => {
      let frame = null;
      try {
        frame = JSON.parse(String(raw));
      } catch {
        return;
      }
      ownReceived.push({ role, frame });

      if (frame.type === "ping") {
        socket.send(JSON.stringify({ type: "pong" }));
        return;
      }
      if (frame.type === "state") {
        socket.send(
          JSON.stringify({
            type: "state_data",
            world_id: worldId,
            data: JSON.stringify({
              name: "爱丽丝",
              hp: 12,
              max_hp: 12,
              san: 65,
              max_san: 65,
              inventory: [],
            }),
            clues: JSON.stringify({}),
            scene: { name: "密斯卡托尼克大学" },
          }),
        );
        return;
      }

      if (frame.type === "action_request") {
        send("action_ack", { request_id: frame.request_id, status: "queued" });
        const action = frame.action || {};

        if (action.kind === "freeform" && scenario === "revisions") {
          // 同一 revision 的两条不同聊天事件：都必须渲染。
          send(
            "message_completed",
            {
              message_id: "m-1",
              speaker: { kind: "npc", id: "bryce_fallon", name: "法伦" },
              text: "第一句：法伦抬起头。",
            },
            12,
          );
          send(
            "message_completed",
            {
              message_id: "m-2",
              speaker: { kind: "npc", id: "bryce_fallon", name: "法伦" },
              text: "第二句：他把档案推到桌边。",
            },
            12,
          );
          return;
        }
        if (action.kind === "move" && scenario === "revisions") {
          // 先切到另一个世界，再发一条属于旧世界的迟到场景事件。
          socket.send(
            JSON.stringify({
              protocol_version: PROTOCOL_VERSION,
              event_id: 500,
              world_id: "world-stub-2",
              sequence: 1,
              revision: 1,
              type: "session_snapshot",
              payload: {
                revision: 1,
                execution_profile: "structured_v1",
                keeper_mode: "human",
                server_capabilities: structuredCapabilities(),
                scene: { id: "hobhouse_mansion", name: "霍布豪斯宅邸" },
                destinations: DESTINATIONS,
                investigator_id: "inv-alice",
                targets: PUBLIC_TARGETS,
                clues: CLUES,
                items: ITEMS,
                requests: [],
                pending_checks: [],
                cursor: { event_id: 500, revision: 1 },
              },
            }),
          );
          socket.send(
            JSON.stringify({
              protocol_version: PROTOCOL_VERSION,
              event_id: 501,
              world_id: "world-stub-1",
              sequence: 99,
              revision: 99,
              type: "scene_changed",
              payload: {
                scene: { id: "miskatonic_medical", name: "旧世界的停尸房" },
                destinations: DESTINATIONS,
              },
            }),
          );
          return;
        }
        if (action.kind === "move") {
          if (scenario === "conflict") {
            send("request_error", {
              request_id: frame.request_id,
              code: "revision_conflict",
              message: "世界版本已从 12 变为 13",
              retryable: true,
            });
            return;
          }
          send("action_status", {
            request_id: frame.request_id,
            status: "processing",
            detail: "守秘人正在处理你的移动。",
          });
          send("scene_changed", {
            scene:
              action.destination_scene_id === "miskatonic_medical"
                ? { id: "miskatonic_medical", name: "密斯卡托尼克大学医学院" }
                : { id: "miskatonic_university", name: "密斯卡托尼克大学" },
            destinations: DESTINATIONS,
          });
          send("action_status", {
            request_id: frame.request_id,
            status: "completed",
            outcome: "success",
            detail: "你们抵达了目的地。抵达不等于调查。",
          });
          return;
        }
        if (action.kind === "present_clue" && scenario === "conflict") {
          send("request_error", {
            request_id: frame.request_id,
            code: "revision_conflict",
            message: "世界版本已从 12 变为 13",
            retryable: true,
          });
          return;
        }
        if (action.kind === "present_clue" && scenario === "conflict") {
          send("request_error", {
            request_id: frame.request_id,
            code: "revision_conflict",
            message: "世界版本已从 12 变为 13",
            retryable: true,
          });
          return;
        }
        if (action.kind === "present_clue") {
          send("action_status", {
            request_id: frame.request_id,
            status: "awaiting_player",
            detail: "守秘人请你先做一次检定。",
          });
          send("check_requested", {
            check_request_id: "chk-stub-1",
            investigator_id: frame.investigator_id,
            skill: "说服",
            difficulty: "regular",
            bonus_penalty: 0,
            attempt: "向医生说明来意。",
            known_cost: "可能需要出示证件",
            visibility: "public",
          });
          return;
        }
        send("action_status", {
          request_id: frame.request_id,
          status: "declined",
          outcome: "not_executed",
          detail: "这次尝试没有被执行。",
        });
        return;
      }

      if (frame.type === "check_response") {
        send("action_ack", { request_id: frame.request_id, status: "queued" });
        if (frame.decision === "decline") {
          send("check_cancelled", {
            check_request_id: frame.check_request_id,
            reason: "玩家放弃了这次检定。",
          });
          send("action_status", {
            request_id: frame.request_id,
            status: "completed",
            outcome: "not_executed",
            detail: "检定已放弃。",
          });
          return;
        }
        send("check_resolved", {
          check_request_id: frame.check_request_id,
          investigator_id: "inv-alice",
          skill: "说服",
          target_value: 55,
          roll: 23,
          outcome: "success",
          detail: "23 ≤ 55，说服成功。",
        });
        send("state_changed", {
          investigator_id: "inv-alice",
          san: 58,
          max_san: 65,
          hp: 12,
          max_hp: 12,
        });
        send("action_status", {
          request_id: frame.request_id,
          status: "completed",
          outcome: "success",
          detail: "医生同意带你们去看遗体。",
        });
        return;
      }

      if (frame.type === "free_roll_request") {
        send("action_ack", { request_id: frame.request_id, status: "queued" });
        send("roll_resolved", {
          request_id: frame.request_id,
          expression: frame.spec,
          dice: [{ sides: 100, values: [42] }],
          total: 42,
          modifier: 0,
        });
        return;
      }

      if (frame.type === "command_request") {
        send("action_ack", { request_id: frame.command_id, status: "queued" });
        send("action_status", {
          request_id: frame.command_id,
          status: "completed",
          outcome: "success",
          detail: `命令 ${frame.kind} 已执行。`,
        });
        if (frame.kind === "grant_clue") {
          send("clue_granted", {
            investigator_id: (frame.payload.recipient_investigator_ids ||
              [])[0],
            clue_id: frame.payload.clue_id,
            category: "investigation",
            text: "定向发放的线索。",
          });
        }
        if (frame.kind === "request_check") {
          send("check_requested", {
            check_request_id: "chk-keeper-1",
            investigator_id: frame.payload.investigator_id,
            skill: frame.payload.skill,
            difficulty: frame.payload.difficulty,
            bonus_penalty: frame.payload.bonus_penalty || 0,
            attempt: frame.payload.attempt,
            known_cost: frame.payload.known_cost || "",
            visibility: frame.payload.visibility,
          });
        }
        return;
      }

      if (frame.type === "start") {
        if (scenario !== "legacy-only") {
          send("session_snapshot", {
            revision: 12,
            execution_profile: "structured_v1",
            keeper_mode: "human",
            server_capabilities: structuredCapabilities(),
            keeper:
              role === "keeper"
                ? { user_id: null, mode: "human" }
                : { mode: "human" },
            scene: { id: "miskatonic_university", name: "密斯卡托尼克大学" },
            destinations: DESTINATIONS,
            investigator_id: role === "player-b" ? "inv-bob" : "inv-alice",
            targets: PUBLIC_TARGETS,
            clues: CLUES,
            items: ITEMS,
            requests: [],
            pending_checks: [],
            cursor: { event_id: eventId, revision: 12 },
          });
        }
        // 真实后端的开局：一个回合承载开场叙述；gameStarted 由 gm_turn_start 触发。
        socket.send(
          JSON.stringify({
            type: "gm_turn_start",
            turn_id: "turn-open-1",
            seq: 1,
            turn_kind: "gameplay",
          }),
        );
        socket.send(
          JSON.stringify({
            type: "narrative_chunk",
            turn_id: "turn-open-1",
            seq: 2,
            text: "雨幕笼罩着阿卡姆，你在约定的办公室里见到了等待已久的委托人。",
          }),
        );
        socket.send(
          JSON.stringify({ type: "done", turn_id: "turn-open-1", seq: 3 }),
        );
        return;
      }

      if (frame.type === "action") {
        // 旧文字通道：替身只回一条普通叙述，用于确认它没有被误用。
        socket.send(
          JSON.stringify({
            type: "gm_turn_start",
            turn_id: "turn-legacy-1",
            seq: 1,
            player_input: frame.content,
          }),
        );
        socket.send(
          JSON.stringify({
            type: "narrative_chunk",
            turn_id: "turn-legacy-1",
            seq: 2,
            text: "旧通道文本。",
          }),
        );
        socket.send(
          JSON.stringify({ type: "done", turn_id: "turn-legacy-1", seq: 3 }),
        );
        return;
      }
    });
  });

  const ownReceived = [];
  return new Promise((resolve) => {
    server.listen(port, "127.0.0.1", () =>
      resolve({
        server,
        close: () =>
          new Promise((done) => {
            for (const client of wss.clients) client.terminate();
            wss.close(() => server.close(() => done()));
          }),
        received: ownReceived,
      }),
    );
  });
}

module.exports = { startServer, received };
