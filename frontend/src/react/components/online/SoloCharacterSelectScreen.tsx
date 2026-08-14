import { useEffect, useRef, useState } from "react";

import { claimByKey, enterSoloLobby, startGame } from "../../../online";
import { useOnlineStore } from "../../../state/online-store";
import { useStartStore } from "../../../state/start-store";
import { CharacterDossier } from "../CharacterDossier";

/**
 * 云端单人开局前的角色选择页：与本地开始页共用同一套视觉与档案卡组件
 * （character-select.css 的裸排版 + CharacterDossier 羊皮纸档案卡）。
 *
 * 与本地的差异只在写通路：点卡 = HTTP claim（claimByKey），确认 = 房间 WS
 * start（startGame，服务端用认领记录覆盖 character_ref）。档案数据来自房间
 * bootstrap 推送的 character_list（useStartStore.characterGroups，与本地同源
 * 同构）；HTTP characterOptions 仅作旧服务器回退（无完整档案时显示简表）。
 */
export function SoloCharacterSelectScreen() {
  const user = useOnlineStore((state) => state.user);
  const members = useOnlineStore((state) => state.members);
  const characterOptions = useOnlineStore((state) => state.characterOptions);
  const charactersStatus = useOnlineStore((state) => state.charactersStatus);
  const roomConnection = useOnlineStore((state) => state.roomConnection);
  const roomBusy = useOnlineStore((state) => state.roomBusy);
  const roomError = useOnlineStore((state) => state.roomError);
  const roomMetadata = useOnlineStore((state) => state.roomMetadata);
  const groups = useStartStore((state) => state.characterGroups);
  const activeModuleTitle = useStartStore((state) => state.activeModuleTitle);

  const me = members.find((member) => member.user_id === user?.id);
  const claimedKey = me?.investigator?.character_key ?? null;
  const canChoose = roomConnection === "connected" && !roomBusy;

  // 完整档案（start-store）优先；回退到 HTTP 简表（无属性/技能/背景）。
  const fullCharacters = groups.flatMap((group) => group.characters || []);
  const fullFor = (id: string) =>
    fullCharacters.find((character) => character.id === id);
  // roster 卡片视图模型：完整档案（CharacterOption）与 HTTP 简表都归一到
  // 这个形状；hp/san 等仅完整档案有，卡片按存在性渲染。
  type RosterCharacter = {
    id: string;
    name: string;
    occupation?: string;
    era?: string;
    source_label?: string;
    hp?: number;
    max_hp?: number;
    san?: number;
    max_san?: number;
  };
  const rosterGroups: {
    id: string;
    title: string;
    characters: RosterCharacter[];
  }[] = groups.length
    ? groups
    : [
        {
          id: "all",
          title: "可用调查员",
          characters: characterOptions.map((option) => ({
            id: option.id,
            name: option.name,
            occupation: option.occupation || "",
            era: option.era,
            source_label: option.source_label || "",
          })),
        },
      ];

  // 预览焦点：默认跟随已认领角色，否则第一个可用候选；点卡即预览 + 认领。
  // 注意房间模式 include_personal=False 时 profile/custom 组为空，必须取
  // 第一个非空组的角色，否则焦点落空、档案卡永远停在加载文案。
  const allCards = rosterGroups.flatMap((group) => group.characters);
  const firstId = allCards[0]?.id ?? null;
  const [focusId, setFocusId] = useState<string | null>(null);
  const focusedId = focusId ?? claimedKey ?? firstId;
  const detailRef = useRef<HTMLElement>(null);
  // 切换预览角色时档案面板回到顶部（与本地同一交互）。
  useEffect(() => {
    if (detailRef.current) detailRef.current.scrollTop = 0;
  }, [focusedId]);
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape" && canChoose) void enterSoloLobby();
    };
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, [canChoose]);

  const focusedOption =
    allCards.find((character) => character.id === focusedId) ?? null;
  const focusedFull = focusedId ? fullFor(focusedId) : undefined;
  const heading = roomMetadata?.name || activeModuleTitle || "新的冒险";

  return (
    <div
      className="online-start-view solo-character-select"
      data-testid="solo-character-select"
    >
      <header className="character-select-header">
        <button
          className="character-back-button"
          disabled={roomBusy}
          onClick={() => void enterSoloLobby()}
        >
          ← 返回我的冒险
        </button>
        <div className="character-select-heading">
          <h2>选择调查员</h2>
          <div id="character-module-name">{heading}</div>
        </div>
        <span
          className={`online-conn-hint${roomConnection === "connected" ? " online-conn-hint--ok" : ""}`}
          role="status"
        >
          {roomConnection === "connected" ? "已连接" : "正在连接……"}
        </span>
      </header>

      {roomError && (
        <p className="online-notice online-notice--error" role="alert">
          {roomError}
        </p>
      )}

      <div className="investigator-layout">
        <section id="character-selector" className="character-roster">
          <div className="character-roster-title">可用调查员</div>
          {charactersStatus === "loading" && (
            <p className="character-detail-empty" role="status">
              正在读取调查员档案…
            </p>
          )}
          {charactersStatus === "error" && (
            <p className="online-notice online-notice--error" role="alert">
              无法读取角色卡，请返回冒险列表后重试。
            </p>
          )}
          {charactersStatus === "unsupported" && (
            <p className="character-detail-empty">
              当前服务器暂不支持角色卡列表。
            </p>
          )}
          {charactersStatus === "ready" &&
            rosterGroups.every((group) => group.characters.length === 0) && (
              <p className="character-detail-empty">该模组暂无可选角色卡。</p>
            )}
          <div id="character-choice-list">
            {rosterGroups.map((group) => (
              <section className="character-group" key={group.id}>
                {groups.length > 0 && (
                  <div className="character-group-title">
                    {group.id === "module"
                      ? `${activeModuleTitle} 特色调查员`
                      : group.title}
                  </div>
                )}
                <div className="character-card-row">
                  {group.characters.map((character) => {
                    const isClaimed = claimedKey === character.id;
                    const holder = members.find(
                      (member) =>
                        member.investigator?.character_key === character.id,
                    );
                    const occupiedByOther = holder != null && !isClaimed;
                    return (
                      <button
                        className={`character-card${focusedId === character.id ? " selected" : ""}`}
                        aria-pressed={focusedId === character.id}
                        key={character.id}
                        disabled={!canChoose || occupiedByOther}
                        onClick={() => {
                          setFocusId(character.id);
                          void claimByKey(character.id);
                        }}
                      >
                        <span className="character-card-name">
                          {character.name}
                        </span>
                        {character.source_label ? (
                          <span className="character-card-source">
                            {character.source_label}
                          </span>
                        ) : null}
                        <span className="character-card-meta">
                          {character.occupation || "调查员"}
                        </span>
                        {typeof character.hp === "number" ? (
                          <span className="character-card-vitals">
                            HP {character.hp}/{character.max_hp} · SAN{" "}
                            {character.san}/{character.max_san}
                          </span>
                        ) : null}
                        {isClaimed && (
                          <span className="character-card-claimed">已选择</span>
                        )}
                        {occupiedByOther && (
                          <span className="character-card-claimed">
                            已被占用
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        </section>
        <aside id="character-detail" ref={detailRef}>
          {focusedFull ? (
            <CharacterDossier key={focusedFull.id} character={focusedFull} />
          ) : (
            <div className="character-detail-empty">
              {focusedOption
                ? `${focusedOption.name} · ${focusedOption.occupation || "调查员"}`
                : "正在读取调查员档案…"}
            </div>
          )}
        </aside>
      </div>

      <footer className="character-select-footer">
        <span id="character-selected-summary">
          {focusedOption
            ? `${focusedOption.name} · ${focusedOption.occupation || "调查员"}${claimedKey === focusedOption.id ? " · 已认领" : ""}`
            : "未选择调查员"}
        </span>
        <button
          id="btn-character-confirm"
          disabled={!claimedKey || !canChoose}
          onClick={() => void startGame()}
        >
          {roomConnection === "connected" ? "以此调查员开始" : "正在连接房间…"}
        </button>
      </footer>
    </div>
  );
}
