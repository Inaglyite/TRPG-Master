import { claimByKey, enterSoloLobby, startGame } from "../../../online";
import { useOnlineStore } from "../../../state/online-store";

/**
 * 云端单人开局前的角色卡选择页。
 *
 * 单人房间仍复用 /ws/room，但不再直接跳过角色认领。已有角色的存档会
 * 由 OnlineShell 自动恢复；新建存档必须先在这里选定调查员，再开始开场。
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

  const me = members.find((member) => member.user_id === user?.id);
  const selected = me?.investigator?.character_key ?? null;
  const canChoose = roomConnection === "connected" && !roomBusy;

  return (
    <div
      className="online-box online-card online-card--wide solo-character-screen"
      data-testid="solo-character-select"
    >
      <header className="online-header">
        <div>
          <h1 className="online-title online-title--small">
            {roomMetadata?.name || "新的冒险"}
          </h1>
          <p className="online-subtitle">选择你的调查员，开始这段调查</p>
        </div>
        <span
          className={
            roomConnection === "connected"
              ? "online-badge online-badge--online"
              : "online-badge"
          }
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

      <section
        className="online-section"
        aria-labelledby="solo-character-title"
      >
        <div>
          <h2 id="solo-character-title">调查员角色卡</h2>
          <p className="online-section-desc">
            角色卡会决定你的初始能力与守秘人对你的回应方式。
          </p>
        </div>

        {charactersStatus === "loading" && (
          <p className="online-loading" role="status">
            正在读取角色卡……
          </p>
        )}
        {charactersStatus === "error" && (
          <p className="online-notice online-notice--error" role="alert">
            无法读取角色卡，请返回冒险列表后重试。
          </p>
        )}
        {charactersStatus === "unsupported" && (
          <p className="online-empty">当前服务器暂不支持角色卡列表。</p>
        )}
        {charactersStatus === "ready" && characterOptions.length === 0 && (
          <p className="online-empty">该模组暂无可选角色卡。</p>
        )}
        {characterOptions.length > 0 && (
          <div className="solo-character-grid">
            {characterOptions.map((option) => {
              const isSelected = selected === option.id;
              const holder = members.find(
                (member) => member.investigator?.character_key === option.id,
              );
              const occupiedByOther = holder != null && !isSelected;
              return (
                <button
                  key={option.id}
                  type="button"
                  className={`solo-character-card${
                    isSelected ? " solo-character-card--selected" : ""
                  }`}
                  disabled={!canChoose || occupiedByOther}
                  onClick={() => void claimByKey(option.id)}
                  aria-pressed={isSelected}
                >
                  <span className="solo-character-card-name">
                    {option.name}
                  </span>
                  {option.occupation && (
                    <span className="solo-character-card-meta">
                      {option.occupation}
                    </span>
                  )}
                  {option.era && (
                    <span className="solo-character-card-meta">
                      {option.era}
                    </span>
                  )}
                  <span className="solo-character-card-action">
                    {isSelected
                      ? "已选择"
                      : occupiedByOther
                        ? "已被占用"
                        : "选择角色"}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </section>

      <div className="online-actions solo-character-actions">
        <button
          type="button"
          className="btn-primary"
          disabled={!selected || roomBusy || roomConnection !== "connected"}
          onClick={() => void startGame()}
        >
          开始调查
        </button>
        <button
          type="button"
          className="btn-ghost"
          disabled={roomBusy}
          onClick={() => void enterSoloLobby()}
        >
          ← 返回我的冒险
        </button>
      </div>
    </div>
  );
}
