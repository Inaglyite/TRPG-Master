import { useEffect, useMemo, useState } from "react";
import {
  loadKeeperGuide,
  loadStructuredAsset,
  type KeeperGuide,
} from "../../../api/structuredAssets";
import { sendKeeperCommand } from "../../../structured-transport";
import { useAppStore } from "../../../state/app-store";
import { useStructuredStore } from "../../../state/structured-store";
import type { FieldValues } from "../../../protocol/keeper-commands";
import {
  CHARACTER_ATTRIBUTE_LABELS,
  CHARACTER_SKILL_LABELS,
} from "../CharacterDossier";

type Category = "scene" | "npc" | "clue" | "asset" | "guide" | "investigator";
const CATEGORIES: [Category, string][] = [
  ["scene", "场景"],
  ["npc", "人物"],
  ["investigator", "队员"],
  ["clue", "线索"],
  ["asset", "图片"],
  ["guide", "模组手册"],
];

export function KeeperLibrary({
  investigators,
  blocked,
  onPrepare,
}: {
  investigators: { id: string; name: string }[];
  blocked: string | null;
  onPrepare: (kind: string, fields: FieldValues) => void;
}) {
  const worldId = useStructuredStore((state) => state.identity.worldId);
  const currentSceneId = useStructuredStore((state) => state.currentSceneId);
  const material = useStructuredStore((state) => state.keeperMaterial);
  const assets = useStructuredStore((state) => state.keeperAssets);
  const party = useStructuredStore((state) => state.keeperInvestigators);
  const clues = useStructuredStore((state) => state.clues);
  const capabilities = useStructuredStore((state) => state.capabilities);
  const local = useAppStore((state) => state.mode === "local");
  const [expanded, setExpanded] = useState(false);
  const [category, setCategory] = useState<Category>("scene");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const [recipients, setRecipients] = useState<string[]>([]);
  const [image, setImage] = useState("");
  const [previewStatus, setPreviewStatus] = useState("");
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const [sentLabel, setSentLabel] = useState("");
  const [commandId, setCommandId] = useState("");
  const [error, setError] = useState("");
  const [guide, setGuide] = useState<KeeperGuide | null>(null);
  const [guideStatus, setGuideStatus] = useState("");
  const [guideAttempt, setGuideAttempt] = useState(0);
  const command = useStructuredStore((state) => state.requests[commandId]);
  const pending =
    !command?.errorCode &&
    (command?.status === "queued" || command?.status === "processing");
  const entries = useMemo(() => {
    const all =
      category === "investigator"
        ? party.map((sheet) => ({
            id: sheet.investigatorId,
            title: sheet.name,
            current: false,
            text: [
              sheet.occupation,
              `HP ${sheet.hp ?? "--"}/${sheet.maxHp ?? "--"} · SAN ${sheet.san ?? "--"}/${sheet.maxSan ?? "--"}`,
              ...Object.entries(sheet.attributes).map(
                ([key, value]) =>
                  `${CHARACTER_ATTRIBUTE_LABELS[key] || key} ${key} ${value}`,
              ),
              ...Object.entries(sheet.skills).map(
                ([key, value]) =>
                  `${CHARACTER_SKILL_LABELS[key] || key} ${key} ${value}%`,
              ),
              ...sheet.conditions,
              ...sheet.inventory.map(
                (item) => `${item.label} ×${item.quantity}`,
              ),
            ]
              .filter(Boolean)
              .join("\n"),
          }))
        : category === "guide"
          ? (guide?.documents ?? []).map((document) => ({
              ...document,
              current: false,
            }))
          : category === "asset"
            ? assets.map((entry) => ({
                id: entry.id,
                title: entry.label,
                text: "",
                current: false,
              }))
            : category === "clue"
              ? clues.map((entry) => ({
                  id: entry.id,
                  title: entry.text.slice(0, 32) || entry.id,
                  text: entry.text,
                  current: false,
                }))
              : material
                  .filter((entry) => (entry.kind ?? "scene") === category)
                  .map((entry, index) => ({
                    ...entry,
                    id: entry.id || `legacy-${index}`,
                    current:
                      entry.kind === "scene" && currentSceneId
                        ? entry.id === currentSceneId
                        : entry.current === true,
                  }));
    const search = query.trim().toLocaleLowerCase();
    return all.filter((entry) =>
      `${entry.title}\n${entry.text}`.toLocaleLowerCase().includes(search),
    );
  }, [assets, clues, material, category, query, currentSceneId, guide, party]);
  const entry = entries.find((item) => item.id === selected) ?? entries[0];
  const person =
    category === "investigator"
      ? party.find((sheet) => sheet.investigatorId === entry?.id)
      : undefined;

  useEffect(() => {
    setExpanded(false);
    setQuery("");
    setSelected("");
    setRecipients([]);
    setCommandId("");
    setSentLabel("");
    setError("");
    setGuide(null);
    setGuideStatus("");
  }, [worldId]);
  useEffect(() => {
    let cancelled = false;
    if (!expanded || category !== "guide" || !worldId) return;
    setGuide(null);
    setGuideStatus("正在读取作者资料…");
    void loadKeeperGuide(worldId, local)
      .then((result) => {
        if (!cancelled) {
          setGuide(result);
          setGuideStatus("");
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled)
          setGuideStatus(
            reason instanceof Error ? reason.message : "资料读取失败，请重试。",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [expanded, category, worldId, local, guideAttempt]);
  // A previous world's/category's response cannot populate this preview.
  useEffect(() => {
    let cancelled = false;
    setImage("");
    setPreviewStatus("");
    if (!expanded || category !== "asset" || !entry || !worldId) return;
    setPreviewStatus("正在读取图片…");
    void loadStructuredAsset(worldId, entry.id, local)
      .then((result) => {
        if (cancelled) return;
        setImage(result.asset_data_uri);
        setPreviewStatus("");
      })
      .catch((reason: unknown) => {
        if (!cancelled)
          setPreviewStatus(
            reason instanceof Error
              ? reason.message
              : "图片读取失败，请重新选择后重试。",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [expanded, category, entry?.id, worldId, local, previewAttempt]);

  const present = () => {
    if (!entry || !recipients.length || pending || blocked) return;
    const result = sendKeeperCommand("present_handout", {
      asset_id: entry.id,
      recipient_investigator_ids: recipients,
      caption: entry.title.slice(0, 200),
    });
    setError(result.ok ? "" : result.reason);
    if (result.ok) {
      setCommandId(result.requestId);
      setSentLabel(entry.title);
    }
  };

  return (
    <section
      className="keeper-library"
      data-testid="keeper-library"
      aria-label="主持资料库"
    >
      <button
        type="button"
        className="btn-ghost keeper-library-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? "收起资料库" : "打开资料库"}
        <span>场景 · 人物 · 队员 · 线索 · 图片 · 手册</span>
      </button>
      {expanded && (
        <div className="keeper-library-content">
          <div className="keeper-library-heading">
            <h4>主持资料库</h4>
            <span>仅主持可见</span>
          </div>
          <p className="keeper-note">
            查阅不等于揭示。这里的设定和秘密不会自动出现在玩家聊天中。
          </p>
          <div className="keeper-library-tools">
            <label>
              <span className="sr-only">搜索资料</span>
              <input
                type="search"
                placeholder="搜索标题或正文"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <div
              className="keeper-library-filters"
              role="group"
              aria-label="资料分类"
            >
              {CATEGORIES.map(([kind, label]) => (
                <button
                  key={kind}
                  type="button"
                  aria-pressed={category === kind}
                  className="btn-ghost"
                  onClick={() => {
                    setCategory(kind);
                    setSelected("");
                    setQuery("");
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {category === "guide" && (
            <div className="keeper-guide-note">
              <p className="keeper-note">
                当前安装模组的作者资料，不是存档内的历史快照。查阅不会改变世界，也不会把秘密发给玩家。
              </p>
              {guide && (
                <p className="keeper-note">
                  {guide.module_title} · 资料版本 {guide.source_version} ·{" "}
                  {guide.documents.length} 篇
                </p>
              )}
              {guideStatus && <p role="status">{guideStatus}</p>}
              {guideStatus && guideStatus !== "正在读取作者资料…" && (
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => setGuideAttempt((value) => value + 1)}
                >
                  重新读取资料
                </button>
              )}
              {guide?.warnings.map((warning, index) => (
                <p className="keeper-note" role="alert" key={index}>
                  {warning}
                </p>
              ))}
            </div>
          )}
          <div className="keeper-library-columns">
            <nav className="keeper-library-index" aria-label="资料索引">
              {entries.length === 0 && (
                <p className="keeper-note">
                  {query
                    ? "没有匹配的资料，请调整搜索条件。"
                    : "本模组未提供这一类资料。"}
                </p>
              )}
              {entries.map((item) => (
                <button
                  type="button"
                  className="keeper-library-entry"
                  key={item.id}
                  aria-pressed={entry?.id === item.id}
                  onClick={() => setSelected(item.id)}
                >
                  <strong>{item.title}</strong>
                  {item.current && (
                    <span className="keeper-library-current">当前场景</span>
                  )}
                  {item.text && <span>{item.text.slice(0, 80)}</span>}
                </button>
              ))}
            </nav>
            <article
              className="keeper-library-preview"
              aria-label="资料预览"
              aria-live="polite"
            >
              {!entry ? (
                <p className="keeper-note">选择一份资料查看内容。</p>
              ) : (
                <>
                  <h5>{entry.title}</h5>
                  {person ? (
                    <div
                      className="keeper-party-sheet"
                      data-testid="keeper-party-sheet"
                    >
                      <p className="keeper-note">
                        {person.occupation || "未提供职业"} · 只读角色卡
                      </p>
                      <dl className="keeper-party-stats">
                        <div>
                          <dt>生命值</dt>
                          <dd>
                            HP {person.hp ?? "--"}/{person.maxHp ?? "--"}
                          </dd>
                        </div>
                        <div>
                          <dt>理智值</dt>
                          <dd>
                            SAN {person.san ?? "--"}/{person.maxSan ?? "--"}
                          </dd>
                        </div>
                      </dl>
                      {person.conditions.length > 0 && (
                        <p>当前状态：{person.conditions.join("、")}</p>
                      )}
                      <h6>属性</h6>
                      <dl className="keeper-party-stats">
                        {Object.entries(person.attributes).map(
                          ([key, value]) => (
                            <div key={key}>
                              <dt>{CHARACTER_ATTRIBUTE_LABELS[key] || key}</dt>
                              <dd>{value}</dd>
                            </div>
                          ),
                        )}
                      </dl>
                      <h6>完整技能</h6>
                      <p className="keeper-note">
                        选择技能只准备检定。请在表单中写明尝试、难度和已知代价，再提交给玩家。
                      </p>
                      <ul className="keeper-party-skills">
                        {Object.entries(person.skills).map(([key, value]) => (
                          <li key={key}>
                            <span>
                              {CHARACTER_SKILL_LABELS[key] || key}
                              {CHARACTER_SKILL_LABELS[key] && (
                                <small>{key}</small>
                              )}
                            </span>
                            <strong>{value}%</strong>
                            <button
                              type="button"
                              className="btn-ghost"
                              aria-label={`准备 ${key} 检定`}
                              disabled={
                                blocked !== null ||
                                !capabilities.commands.includes("request_check")
                              }
                              onClick={() => {
                                onPrepare("request_check", {
                                  investigator_id: person.investigatorId,
                                  skill: key,
                                });
                                setExpanded(false);
                              }}
                            >
                              准备检定
                            </button>
                          </li>
                        ))}
                      </ul>
                      {Object.keys(person.skills).length === 0 && (
                        <p className="keeper-note">
                          角色卡未提供技能，请确认角色资料。
                        </p>
                      )}
                      <h6>持有物</h6>
                      <ul className="keeper-party-skills">
                        {person.inventory.map((item) => (
                          <li key={item.id}>
                            <span>{item.label}</span>
                            <strong>×{item.quantity}</strong>
                            <button
                              type="button"
                              className="btn-ghost"
                              aria-label={`准备使用 ${item.label}`}
                              disabled={
                                item.quantity <= 0 ||
                                blocked !== null ||
                                !capabilities.commands.includes("use_item")
                              }
                              onClick={() => {
                                onPrepare("use_item", {
                                  investigator_id: person.investigatorId,
                                  item_id: item.id,
                                  quantity: 1,
                                  consume: false,
                                });
                                setExpanded(false);
                              }}
                            >
                              准备使用
                            </button>
                          </li>
                        ))}
                      </ul>
                      {!person.inventory.length && (
                        <p className="keeper-note">当前没有持有物。</p>
                      )}
                    </div>
                  ) : category === "asset" ? (
                    <>
                      {image && (
                        <img
                          src={image}
                          alt={entry.title}
                          onError={() =>
                            setPreviewStatus(
                              "图片格式无法显示，尚未分发。请检查模组素材。",
                            )
                          }
                        />
                      )}
                      {previewStatus && (
                        <p role="status" className="keeper-note">
                          {previewStatus}
                        </p>
                      )}
                      {previewStatus && previewStatus !== "正在读取图片…" && (
                        <button
                          type="button"
                          className="btn-ghost"
                          onClick={() => setPreviewAttempt((old) => old + 1)}
                        >
                          重新读取图片
                        </button>
                      )}
                      <p className="keeper-note">
                        预览不会自动分发。请选择接收的调查员，点击展示后等待服务端确认。
                      </p>
                      <fieldset
                        className="keeper-library-recipients"
                        disabled={pending || blocked !== null}
                      >
                        <legend>接收调查员</legend>
                        {investigators.map((person) => (
                          <label key={person.id}>
                            <input
                              type="checkbox"
                              value={person.id}
                              checked={recipients.includes(person.id)}
                              onChange={(event) =>
                                setRecipients((old) =>
                                  event.target.checked
                                    ? [...old, person.id]
                                    : old.filter((id) => id !== person.id),
                                )
                              }
                            />
                            {person.name}
                          </label>
                        ))}
                        {!investigators.length && (
                          <p className="keeper-note">
                            暂无可接收图片的调查员，请先完成选角。
                          </p>
                        )}
                      </fieldset>
                      <button
                        type="button"
                        className="btn-primary keeper-library-present"
                        onClick={present}
                        disabled={
                          !image ||
                          !!previewStatus ||
                          !recipients.length ||
                          pending ||
                          blocked !== null ||
                          !capabilities.commands.includes("present_handout")
                        }
                      >
                        {pending ? "正在提交…" : "展示给所选调查员"}
                      </button>
                      {blocked && <p role="alert">{blocked}</p>}
                      {error && <p role="alert">{error}</p>}
                      {command && (
                        <p role="status">
                          {command.status === "completed"
                            ? `服务端已确认图片分发：${sentLabel}`
                            : command.errorMessage ||
                              command.detail ||
                              "请求已发出，等待服务端确认。"}
                        </p>
                      )}
                    </>
                  ) : (
                    <>
                      <p className="keeper-library-text">{entry.text}</p>
                      {category === "clue" &&
                        capabilities.commands.includes("grant_clue") && (
                          <button
                            type="button"
                            className="btn-ghost"
                            disabled={blocked !== null}
                            onClick={() => {
                              onPrepare("grant_clue", { clue_id: entry.id });
                              setExpanded(false);
                            }}
                          >
                            准备分发这条线索
                          </button>
                        )}
                    </>
                  )}
                  <details className="keeper-library-id">
                    <summary>资料标识</summary>
                    <code>{entry.id}</code>
                  </details>
                </>
              )}
            </article>
          </div>
        </div>
      )}
    </section>
  );
}
