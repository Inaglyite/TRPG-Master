import { FormEvent, useEffect, useRef, useState } from "react";

import {
  apiHttpOrigin,
  getCloudOrigin,
  normalizeOrigin,
  setCloudOrigin,
} from "../../../api/client";
import { desktopBridge } from "../../../desktop";
import {
  checkSession,
  enterLobby,
  enterSoloLobby,
  login,
  register,
} from "../../../online";
import { useAppStore } from "../../../state/app-store";
import { resetOnlineState, useOnlineStore } from "../../../state/online-store";
import { ArchiveFolderPanel } from "../ArchiveFolderPanel";
import printedCompass from "../../../assets/ui/printed-compass-v1.webp";
import printedCompass2x from "../../../assets/ui/printed-compass-v1@2x.webp";

/** 联机认证页：登录/注册、会话恢复与过期提示、云端服务器地址配置。 */
export function AuthScreen() {
  const authStatus = useOnlineStore((state) => state.authStatus);
  const authBusy = useOnlineStore((state) => state.authBusy);
  const authError = useOnlineStore((state) => state.authError);
  const authErrorCode = useOnlineStore((state) => state.authErrorCode);
  const sessionExpired = useOnlineStore((state) => state.sessionExpired);
  const pendingIntent = useOnlineStore((state) => state.pendingIntent);
  const setMode = useAppStore((state) => state.setMode);
  const bridge = desktopBridge();

  const [tab, setTab] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [originEditing, setOriginEditing] = useState(false);
  const [originDraft, setOriginDraft] = useState(getCloudOrigin() ?? "");
  const [originError, setOriginError] = useState<string | null>(null);
  const actionActive = useRef<number | null>(null);
  const actionSequence = useRef(0);
  const checking = authStatus === "checking";
  const busy = checking || authBusy;
  const origin = apiHttpOrigin();
  const previousOrigin = useRef(origin);
  useEffect(() => {
    if (previousOrigin.current !== origin) {
      previousOrigin.current = origin;
      actionActive.current = null;
      actionSequence.current += 1;
      setUsername("");
      setPassword("");
      setConfirm("");
      setFormError(null);
      setOriginEditing(false);
      setOriginDraft(getCloudOrigin() ?? "");
    }
  }, [origin]);

  async function enterVerifiedLobby() {
    if (useOnlineStore.getState().pendingIntent === "solo") {
      await enterSoloLobby();
    } else {
      await enterLobby();
    }
  }

  async function recheckSession() {
    if (actionActive.current) return;
    const actionId = ++actionSequence.current;
    actionActive.current = actionId;
    setFormError(null);
    try {
      if (await checkSession()) await enterVerifiedLobby();
    } finally {
      if (actionActive.current === actionId) actionActive.current = null;
    }
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (busy || actionActive.current || originEditing) return;
    setFormError(null);
    const name = username.trim();
    if (!name || !password) {
      setFormError("请输入用户名和密码");
      return;
    }
    if (tab === "register" && password !== confirm) {
      setFormError("两次输入的密码不一致");
      return;
    }
    const actionId = ++actionSequence.current;
    actionActive.current = actionId;
    try {
      const ok =
        tab === "login"
          ? await login(name, password)
          : await register(name, password);
      if (ok) await enterVerifiedLobby();
    } finally {
      if (actionActive.current === actionId) actionActive.current = null;
    }
  }

  function onSaveOrigin() {
    if (busy || actionActive.current) return;
    setOriginError(null);
    if (originDraft.trim() && !normalizeOrigin(originDraft)) {
      setOriginError("地址无效，请输入不含用户名或密码的 http(s) 服务器地址");
      return;
    }
    if (!setCloudOrigin(originDraft)) {
      setOriginError(
        "浏览器未能保存服务器地址，请检查存储权限；当前连接未变更",
      );
      return;
    }
    if (origin !== apiHttpOrigin()) {
      setUsername("");
      setPassword("");
      setConfirm("");
    }
    setOriginEditing(false);
    void recheckSession();
  }

  async function backToModeSelect() {
    if (bridge) {
      const result = await bridge.returnToLauncher();
      if (!result.ok) {
        setFormError(result.error ?? "无法返回模式选择");
      }
      return;
    }
    resetOnlineState();
    setMode("select");
  }

  const error = formError ?? authError;
  const networkRecovery =
    !!authError &&
    ["network_error", "request_timeout"].includes(authErrorCode ?? "") &&
    !checking &&
    !originEditing &&
    !formError;
  const Container = networkRecovery ? ArchiveFolderPanel : "div";

  function editOrigin() {
    setOriginDraft(getCloudOrigin() ?? "");
    setOriginError(null);
    setOriginEditing(true);
  }

  return (
    <Container
      aria-labelledby="auth-screen-title"
      className={`online-box online-auth-screen ${networkRecovery ? "online-connection-recovery" : "online-card"}`}
    >
      <div className="online-auth-heading">
        <div className="start-brand">
          <h1
            className="online-title online-title--small"
            id="auth-screen-title"
          >
            {networkRecovery
              ? "无法连接服务器"
              : pendingIntent === "solo"
                ? "云端单人"
                : "多人游戏"}
          </h1>
          <p className="online-subtitle">
            {networkRecovery
              ? pendingIntent === "solo"
                ? "云端单人 · 会话检查"
                : "多人游戏 · 会话检查"
              : "登录云端守秘人"}
          </p>
        </div>
        {!networkRecovery && (
          <button
            type="button"
            className="start-menu-button"
            onClick={() => void backToModeSelect()}
          >
            返回模式选择
          </button>
        )}
      </div>

      {networkRecovery && (
        <div className="online-connection-art" aria-hidden="true">
          <img
            src={printedCompass}
            srcSet={`${printedCompass} 1x, ${printedCompass2x} 2x`}
            width={768}
            height={512}
            alt=""
          />
        </div>
      )}

      {!networkRecovery && (
        <ArchiveFolderPanel
          variant="wide"
          className="online-server"
          aria-label="连接服务器"
        >
          <div className="online-server-row">
            <span className="online-server-label">当前连接</span>
            <span className="online-server-origin">{origin}</span>
            {!bridge && !originEditing && (
              <button
                type="button"
                className="btn-ghost online-server-edit"
                disabled={busy}
                onClick={editOrigin}
              >
                修改服务器
              </button>
            )}
          </div>
          <p className="online-server-note">
            账号、存档和权限属于此服务器，不会随地址自动迁移。
          </p>
          {origin.startsWith("http:") && (
            <p className="online-server-warning">
              HTTP 未加密。请勿在不可信网络输入密码；互联网服务器应使用 HTTPS。
            </p>
          )}
          {bridge && (
            <p className="online-server-note">
              更换服务器请返回桌面启动器操作。
            </p>
          )}
          {!bridge && originEditing && (
            <div className="online-server-form">
              <label className="online-field">
                <span>服务器地址</span>
                <input
                  value={originDraft}
                  disabled={busy}
                  onChange={(event) => setOriginDraft(event.target.value)}
                  placeholder="https://trpg.example.com"
                  autoComplete="off"
                  spellCheck={false}
                  autoFocus
                  aria-invalid={!!originError}
                  aria-describedby="cloud-server-help"
                />
              </label>
              <p className="online-server-note" id="cloud-server-help">
                留空使用默认服务器。更换地址会清空本机旧会话视图和未提交的登录信息，不会删除服务器上的存档。
              </p>
              <div className="online-server-actions">
                <button
                  type="button"
                  className="btn-primary"
                  disabled={busy}
                  onClick={onSaveOrigin}
                >
                  保存并重新检查
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={busy}
                  onClick={() => {
                    setOriginEditing(false);
                    setOriginError(null);
                  }}
                >
                  取消
                </button>
              </div>
              {originError && (
                <p className="online-notice online-notice--error" role="alert">
                  {originError}
                </p>
              )}
            </div>
          )}
        </ArchiveFolderPanel>
      )}

      {checking && (
        <p className="online-connection-status" role="status">
          正在检查登录状态……你可以返回模式选择，不必一直等待。
        </p>
      )}
      {!checking && !originEditing && authError && !formError && (
        <div
          className={`online-auth-recovery${networkRecovery ? " online-auth-recovery--folder" : ""}`}
        >
          <p className="online-notice online-notice--error" role="alert">
            {authError}
          </p>
          {networkRecovery && (
            <p className="online-recovery-origin">
              服务器地址：<span>{origin}</span>
            </p>
          )}
          <button
            type="button"
            className={
              networkRecovery
                ? "btn-primary online-recovery-retry"
                : "btn-ghost"
            }
            disabled={busy}
            onClick={() => void recheckSession()}
          >
            重新检查
          </button>
          {networkRecovery && (
            <div className="online-recovery-secondary">
              {!bridge && (
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={editOrigin}
                >
                  修改服务器
                </button>
              )}
              <button
                type="button"
                className="btn-ghost"
                onClick={() => void backToModeSelect()}
              >
                返回模式选择
              </button>
            </div>
          )}
        </div>
      )}

      {sessionExpired && (
        <p className="online-notice online-notice--warn" role="alert">
          登录已过期，请重新登录
        </p>
      )}

      <div
        className="online-tabs"
        role="tablist"
        hidden={originEditing || checking || networkRecovery}
      >
        <button
          type="button"
          role="tab"
          disabled={busy}
          aria-selected={tab === "login"}
          className={
            tab === "login" ? "online-tab online-tab--active" : "online-tab"
          }
          onClick={() => {
            setTab("login");
            setFormError(null);
          }}
        >
          登录
        </button>
        <button
          type="button"
          role="tab"
          disabled={busy}
          aria-selected={tab === "register"}
          className={
            tab === "register" ? "online-tab online-tab--active" : "online-tab"
          }
          onClick={() => {
            setTab("register");
            setFormError(null);
          }}
        >
          注册
        </button>
        {/* 滑动指示条：方向与 tab 的空间排布一致（注册在右 → 右移） */}
        <span
          className="online-tab-indicator"
          data-tab={tab}
          aria-hidden="true"
        />
      </div>

      <form
        className="online-form"
        onSubmit={onSubmit}
        hidden={originEditing || checking || networkRecovery}
      >
        <label className="online-field">
          <span>用户名</span>
          <input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            disabled={busy || originEditing}
          />
        </label>
        <label className="online-field">
          <span>密码</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={tab === "login" ? "current-password" : "new-password"}
            disabled={busy || originEditing}
          />
        </label>
        {/* 确认密码常挂载：登录态折叠（grid-rows 补间 + 移出焦点序列），
            切换到注册时展开，而不是整块插拔。 */}
        <div
          className={`online-field-collapse${tab === "register" ? "" : " closed"}`}
          aria-hidden={tab !== "register"}
        >
          <div className="online-field-collapse-clip">
            <label className="online-field">
              <span>确认密码</span>
              <input
                type="password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                autoComplete="new-password"
                disabled={busy || originEditing || tab !== "register"}
              />
            </label>
          </div>
        </div>
        {error && (formError || !authError) && (
          <p className="online-notice online-notice--error" role="alert">
            {error}
          </p>
        )}
        <button
          type="submit"
          className="start-art-button online-submit"
          disabled={busy || originEditing}
          aria-label={
            busy ? "请稍候……" : tab === "login" ? "登录" : "注册并登录"
          }
        >
          {/* key 切换触发原地文案 swap（auth-label-in）；布局尺寸不变 */}
          <span
            className="start-art-label online-submit-label"
            key={busy ? "busy" : tab}
          >
            {busy ? "请稍候……" : tab === "login" ? "登录" : "注册并登录"}
          </span>
        </button>
      </form>
    </Container>
  );
}
