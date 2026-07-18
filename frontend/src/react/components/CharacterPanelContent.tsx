import { useState } from "react";

import { useAppStore, type ClueItem } from "../../state/app-store";

const clueCategories = ["investigation", "event", "task", "npc"] as const;
const clueNames: Record<string, string> = {
  investigation: "探案线索",
  event: "事件线索",
  task: "任务线索",
  npc: "人物线索",
};

function percentage(value?: number, maximum?: number) {
  if (!Number.isFinite(value) || !Number.isFinite(maximum) || !maximum)
    return 0;
  return Math.max(0, Math.min(100, (Number(value) / Number(maximum)) * 100));
}

function ClueRow({
  item,
  onImage,
}: {
  item: ClueItem;
  onImage: (src: string, alt: string) => void;
}) {
  const src = item.asset?.asset_data_uri || item.asset?.asset_url || "";
  const alt = item.asset?.label || item.asset?.file || "线索图片";
  return (
    <div
      className={`clue-item${item.type === "profile" ? " profile" : ""}${item.asset?.file ? " has-asset" : ""}`}
    >
      <div className="clue-main">
        <div className="clue-text">{item.text || "未命名线索"}</div>
        <div className="clue-meta">
          {item.type === "profile" && (
            <span className="clue-tier profile">人物</span>
          )}
          {item.tier === 2 && <span className="clue-tier">TIER2</span>}
          {item.type === "inferred" && (
            <span className="clue-tier inferred">推理</span>
          )}
          {item.asset?.file && <span className="clue-tier asset">图像</span>}
        </div>
      </div>
      {src ? (
        <button
          className="clue-thumb-btn"
          title={alt}
          onClick={() => onImage(src, alt)}
        >
          <img className="clue-thumb" src={src} alt={alt} loading="lazy" />
        </button>
      ) : item.asset?.file ? (
        <div className="clue-thumb-missing" title={item.asset.file}>
          图像不可用
        </div>
      ) : null}
    </div>
  );
}

export function CharacterPanelContent() {
  const character = useAppStore((state) => state.character);
  const clues = useAppStore((state) => state.clues);
  const [image, setImage] = useState<{ src: string; alt: string } | null>(null);
  const clueCount = clueCategories.reduce(
    (count, category) => count + (clues[category]?.length || 0),
    0,
  );

  return (
    <>
      <div className="dossier-eyebrow">
        调查员档案<span>INVESTIGATOR</span>
      </div>
      <h3 id="char-name">{character?.name || "调查员"}</h3>
      <p className="sub" id="char-occupation">
        {character?.occupation || "档案载入中…"}
      </p>
      <div className="stat-row">
        <span>HP</span>
        <span id="hp-bar">
          {character ? `${character.hp} / ${character.max_hp}` : "-- / --"}
        </span>
      </div>
      <div className="stat-bar">
        <div
          id="hp-fill"
          className="stat-bar-fill hp"
          style={{ width: `${percentage(character?.hp, character?.max_hp)}%` }}
        />
      </div>
      <div className="stat-row">
        <span>SAN</span>
        <span id="san-bar">
          {character ? `${character.san} / ${character.max_san}` : "-- / --"}
        </span>
      </div>
      <div className="stat-bar">
        <div
          id="san-fill"
          className="stat-bar-fill san"
          style={{
            width: `${percentage(character?.san, character?.max_san)}%`,
          }}
        />
      </div>
      <hr />
      <div id="attr-list">
        {Object.entries(character?.attributes || {}).map(([key, value]) => (
          <div className="stat-row" key={key}>
            <span>{key}</span>
            <span>{String(value)}</span>
          </div>
        ))}
      </div>
      <hr />
      <h4>物品</h4>
      <div id="inv-list">
        {(character?.inventory || []).map((item, index) => (
          <div key={`${item}-${index}`}>{item}</div>
        ))}
      </div>
      <hr />
      <h4>线索册</h4>
      <div id="clues-list">
        {clueCategories.map((category) =>
          clues[category]?.length ? (
            <section key={category}>
              <div className="clue-cat">{clueNames[category]}</div>
              {clues[category].map((item, index) => (
                <ClueRow
                  key={item.id || `${category}-${index}`}
                  item={item}
                  onImage={(src, alt) => setImage({ src, alt })}
                />
              ))}
            </section>
          ) : null,
        )}
        {clueCount === 0 && <div className="clue-empty">暂无记录</div>}
      </div>
      {image && (
        <div
          className="handout-overlay"
          onClick={() => setImage(null)}
          role="presentation"
        >
          <img src={image.src} alt={image.alt} />
        </div>
      )}
    </>
  );
}
