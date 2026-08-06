import { FormEvent, useState } from "react";

import {
  apiHttpOrigin,
  getCloudOrigin,
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

/** 联机认证页：登录/注册、会话恢复与过期提示、云端服务器地址配置。 */
export function AuthScreen() {
  const authStatus = useOnlineStore((state) => state.authStatus);
  const authBusy = useOnlineStore((state) => state.authBusy);
  const authError = useOnlineStore((state) => state.authError);
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

  if (authStatus === "checking") {
    return (
      <div className="online-box online-card online-auth-screen">
        <p className="online-loading" role="status">
          正在检查登录状态……
        </p>
      </div>
    );
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
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
    const ok =
      tab === "login"
        ? await login(name, password)
        : await register(name, password);
    // 认证成功后按入口意图落点：云端单人 → 我的冒险；多人 → 联机大厅。
    if (ok) {
      if (useOnlineStore.getState().pendingIntent === "solo") {
        await enterSoloLobby();
      } else {
        await enterLobby();
      }
    }
  }

  function onSaveOrigin() {
    setOriginError(null);
    if (!setCloudOrigin(originDraft)) {
      setOriginError(
        "地址无效，请输入 http(s) 服务器地址，例如 https://trpg.example.com",
      );
      return;
    }
    setOriginEditing(false);
    void checkSession();
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

  return (
    <div className="online-box online-card online-auth-screen">
      <div className="start-brand">
        <h1 className="online-title online-title--small">
          {pendingIntent === "solo" ? "云端单人" : "多人游戏"}
        </h1>
        <p className="online-subtitle">登录云端守秘人</p>
      </div>

      {sessionExpired && (
        <p className="online-notice online-notice--warn" role="alert">
          登录已过期，请重新登录
        </p>
      )}

      <div className="online-tabs" role="tablist">
        <button
          type="button"
          role="tab"
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
      </div>

      <form className="online-form" onSubmit={onSubmit}>
        <label className="online-field">
          <span>用户名</span>
          <input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            disabled={authBusy}
          />
        </label>
        <label className="online-field">
          <span>密码</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={tab === "login" ? "current-password" : "new-password"}
            disabled={authBusy}
          />
        </label>
        {tab === "register" && (
          <label className="online-field">
            <span>确认密码</span>
            <input
              type="password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              autoComplete="new-password"
              disabled={authBusy}
            />
          </label>
        )}
        {error && (
          <p className="online-notice online-notice--error" role="alert">
            {error}
          </p>
        )}
        <button
          type="submit"
          className="btn-primary online-submit"
          disabled={authBusy}
        >
          {authBusy ? "请稍候……" : tab === "login" ? "登录" : "注册并登录"}
        </button>
      </form>

      <div className="online-server">
        <div className="online-server-row">
          <span className="online-server-label">服务器</span>
          <span className="online-server-origin" title={apiHttpOrigin()}>
            {apiHttpOrigin()}
          </span>
          {!bridge && !originEditing && (
            <button
              type="button"
              className="btn-ghost online-server-edit"
              onClick={() => {
                setOriginDraft(getCloudOrigin() ?? "");
                setOriginError(null);
                setOriginEditing(true);
              }}
            >
              修改
            </button>
          )}
        </div>
        {!bridge && originEditing && (
          <div className="online-server-form">
            <input
              value={originDraft}
              onChange={(event) => setOriginDraft(event.target.value)}
              placeholder="https://trpg.example.com（留空使用默认）"
              aria-label="服务器地址"
            />
            <div className="online-server-actions">
              <button
                type="button"
                className="btn-primary"
                onClick={onSaveOrigin}
              >
                保存
              </button>
              <button
                type="button"
                className="btn-ghost"
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
      </div>

      <button
        type="button"
        className="start-menu-button"
        onClick={() => void backToModeSelect()}
      >
        ← 返回模式选择
      </button>
    </div>
  );
}
