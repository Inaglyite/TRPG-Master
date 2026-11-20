import { useEffect, useRef, useState } from "react";
import type { CSSProperties, FormEvent } from "react";

import { menuThemeVariables } from "../../../theme";
import { ModuleImporter } from "../ModuleImporter";
import { CloudModuleManager } from "./CloudModuleManager";
import { apiHttpOrigin } from "../../../api/client";
import { changePassword, updateProfile } from "../../../api/auth";
import { ModelSettingsTrigger } from "../ModelSettingsPanel";
import { ensureModules, logout } from "../../../online";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import { SoloStartMenu } from "../SoloStartMenu";
import { StartMenuIcon } from "../StartMenuIcon";
import { useCloudModuleTransition } from "../module-transition";

function accountErrorMessage(reason: unknown): string {
  return reason instanceof Error && reason.message
    ? reason.message
    : "操作失败，请稍后重试。";
}

export function SoloHome({
  selectedId,
  onSelect,
  onCreate,
  onSaves,
  hasSaves,
  savesBusy,
  savesError,
  onBack,
}: {
  selectedId: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onSaves: () => void;
  hasSaves: boolean;
  savesBusy: boolean;
  savesError: string;
  onBack: () => void;
}) {
  const modules = useOnlineStore((state) => state.modules);
  const modulesStatus = useOnlineStore((state) => state.modulesStatus);
  const modulesError = useOnlineStore((state) => state.modulesError);
  const user = useOnlineStore((state) => state.user);
  const authBusy = useOnlineStore((state) => state.authBusy);
  const authError = useOnlineStore((state) => state.authError);

  const createBusy = useOnlineStore((state) => state.createBusy);
  const createError = useOnlineStore((state) => state.createError);
  const [workshopOpen, setWorkshopOpen] = useState(false);
  const workshopDialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (workshopOpen) workshopDialog.current?.showModal();
  }, [workshopOpen]);
  const [accountOpen, setAccountOpen] = useState(false);
  const accountTriggerRef = useRef<HTMLButtonElement>(null);
  const accountDialogRef = useRef<HTMLDialogElement>(null);
  const restoreAccountFocusRef = useRef(true);
  const [nickname, setNickname] = useState("");
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [profileStatus, setProfileStatus] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordError, setPasswordError] = useState("");
  const [passwordStatus, setPasswordStatus] = useState("");
  const [showPasswords, setShowPasswords] = useState(false);
  const accountBusy = authBusy || profileBusy || passwordBusy;

  const selectedModule = modules.find((module) => module.id === selectedId);
  const backgrounds = Object.fromEntries(
    modules.map((module) => [
      module.id,
      module.has_cover
        ? `url(${JSON.stringify(`${apiHttpOrigin()}/api/modules/${encodeURIComponent(module.id)}/cover`)})`
        : null,
    ]),
  ) as Record<string, string | null>;
  const transition = useCloudModuleTransition(selectedId, backgrounds);
  const activeModule =
    modules.find((module) => module.id === transition.displayedId) ??
    selectedModule;
  const modulesLoading =
    modulesStatus === "idle" || modulesStatus === "loading";
  const selectedBackground = backgrounds[selectedId] ?? null;
  const overlayStyle = {
    ...menuThemeVariables(selectedModule ?? {}),
    "--module-bg-image":
      transition.phase === "idle" && selectedBackground
        ? selectedBackground
        : "var(--ui-start-bg)",
  } as CSSProperties;
  const layerImage = (value: string | null) =>
    ({ "--layer-image": value || "var(--ui-start-bg)" }) as CSSProperties;

  useEffect(() => {
    if (!accountOpen) return;
    setNickname(user?.display_name ?? "");
    setProfileError("");
    setProfileStatus("");
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setPasswordError("");
    setPasswordStatus("");
    setShowPasswords(false);
  }, [accountOpen]);

  useEffect(() => {
    if (!accountOpen) return;
    const dialog = accountDialogRef.current;
    if (!dialog) return;

    const returnFocusTo =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : accountTriggerRef.current;

    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }

    if (!dialog.contains(document.activeElement)) {
      const firstControl = dialog.querySelector<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      (firstControl ?? dialog).focus();
    }

    return () => {
      if (dialog.open && typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
      if (restoreAccountFocusRef.current && returnFocusTo?.isConnected) {
        returnFocusTo.focus();
      }
    };
  }, [accountOpen]);

  const openAccount = () => {
    restoreAccountFocusRef.current = true;
    setAccountOpen(true);
  };

  const closeAccount = () => {
    restoreAccountFocusRef.current = true;
    setAccountOpen(false);
  };

  const leaveAccount = (action: () => void) => {
    restoreAccountFocusRef.current = false;
    setAccountOpen(false);
    action();
  };

  const saveNickname = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (profileBusy) return;
    setProfileBusy(true);
    setProfileError("");
    setProfileStatus("");
    try {
      const updated = await updateProfile(nickname.trim());
      useOnlineStore.setState({ user: updated });
      setProfileStatus("昵称已保存。");
    } catch (reason) {
      setProfileError(accountErrorMessage(reason));
    } finally {
      setProfileBusy(false);
    }
  };

  const savePassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (passwordBusy) return;
    setPasswordError("");
    setPasswordStatus("");
    if (newPassword !== confirmPassword) {
      setPasswordError("两次输入的新密码不一致。");
      return;
    }
    setPasswordBusy(true);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setShowPasswords(false);
      setPasswordStatus("密码已修改，其他设备的登录会话已退出。");
    } catch (reason) {
      setPasswordError(accountErrorMessage(reason));
    } finally {
      setPasswordBusy(false);
    }
  };

  const moduleError = (
    <>
      <span>{modulesError || "无法读取模组列表"}</span>
      <button
        type="button"
        className="btn-ghost lobby-refresh"
        onClick={() => void ensureModules()}
      >
        重试
      </button>
    </>
  );

  const startBox = (
    <div
      id="start-box"
      className={
        transition.phase === "entering" ? "module-entering" : undefined
      }
    >
      <div id="start-view-stack">
        <section
          id="start-menu-view"
          className="start-view"
          aria-label="主菜单"
        >
          <header className="cloud-home-header">
            <button
              type="button"
              ref={accountTriggerRef}
              className="btn-ghost cloud-account-trigger"
              aria-label="个人中心"
              title="个人中心"
              onClick={openAccount}
            >
              个人中心
            </button>
            <nav className="start-module-tools" aria-label="游戏工具">
              <ModuleImporter
                onCloudImported={async (id) => {
                  onSelect(id);
                  await ensureModules(true);
                }}
              />
              <button
                type="button"
                className="start-menu-button"
                data-tooltip="模组工坊"
                onClick={() => setWorkshopOpen(true)}
              >
                <StartMenuIcon kind="workshop" />
                <span>模组工坊</span>
              </button>
              <CloudModuleManager onRefresh={() => ensureModules(true)} />
              <button
                type="button"
                className="start-menu-button"
                data-tooltip="角色库"
                onClick={() =>
                  useAppStore.getState().setCharacterLibraryOpen(true)
                }
              >
                <StartMenuIcon kind="characters" />
                <span>角色库</span>
              </button>
              <ModelSettingsTrigger />
              <button
                type="button"
                className="start-menu-button start-menu-exit"
                data-tooltip="退出游戏"
                onClick={() =>
                  /\bElectron\//.test(navigator.userAgent)
                    ? window.close()
                    : onBack()
                }
              >
                <StartMenuIcon kind="exit" />
                <span>退出游戏</span>
              </button>
            </nav>
          </header>
          <SoloStartMenu
            title={
              activeModule?.menu_title || activeModule?.title || "云端单人"
            }
            subtitle={activeModule?.subtitle}
            description={
              activeModule?.menu_description ?? activeModule?.description
            }
            modules={modules}
            activeModule={selectedId}
            selectingDisabled={
              createBusy || modulesStatus !== "ready" || !modules.length
            }
            onSelect={onSelect}
            startLabel={
              createBusy
                ? "正在准备调查员…"
                : activeModule?.start_button_text || "开始新冒险"
            }
            startDisabled={
              createBusy ||
              modulesStatus !== "ready" ||
              !activeModule ||
              transition.phase === "flipping"
            }
            onStart={onCreate}
            continueDisabled={createBusy || savesBusy || !hasSaves}
            onContinue={onSaves}
            hint={
              savesBusy ? (
                "正在读取存档…"
              ) : savesError ? (
                <span role="alert">{savesError}</span>
              ) : createError ? (
                <span role="alert">{createError}</span>
              ) : !accountOpen && authError ? (
                <span role="alert">{authError}</span>
              ) : modulesStatus === "error" ? (
                <span role="alert">{moduleError}</span>
              ) : modulesLoading ? (
                "正在读取模组…"
              ) : !modules.length ? (
                "暂无可用模组"
              ) : (
                ""
              )
            }
          />
        </section>
      </div>
    </div>
  );

  return (
    <div
      id="start-overlay"
      className={`solo-home${
        transition.phase === "flipping"
          ? " module-flipping"
          : transition.phase === "entering"
            ? " module-entering"
            : ""
      }`}
      style={overlayStyle}
      data-testid="solo-home"
    >
      {transition.incomingBg !== undefined && (
        <div
          className="start-bg-layer incoming"
          aria-hidden="true"
          style={layerImage(transition.incomingBg)}
        />
      )}
      {transition.phase === "flipping" && (
        <>
          <div
            className="start-bg-layer outgoing"
            aria-hidden="true"
            style={layerImage(transition.outgoingBg)}
          />
          <div className="module-page-flip-shade" aria-hidden="true" />
        </>
      )}
      <div
        className={
          transition.phase === "flipping"
            ? "module-page-flip"
            : "module-page-frame"
        }
      >
        {startBox}
      </div>
      {transition.phase === "flipping" && (
        <div className="module-page-edge" aria-hidden="true" />
      )}

      {workshopOpen && (
        <dialog
          ref={workshopDialog}
          className="cloud-workshop-dialog"
          aria-label="模组工坊"
          onClose={() => setWorkshopOpen(false)}
        >
          <header>
            <h2>模组工坊</h2>
            <button
              className="btn-ghost"
              type="button"
              onClick={() => workshopDialog.current?.close()}
            >
              返回游戏
            </button>
          </header>
          <iframe title="模组编辑器" src={apiHttpOrigin() + "/editor/"} />
        </dialog>
      )}
      {accountOpen && (
        <dialog
          ref={accountDialogRef}
          className="personal-center-dialog"
          aria-labelledby="personal-center-title"
          aria-modal="true"
          tabIndex={-1}
          onCancel={(event) => {
            event.preventDefault();
            closeAccount();
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              closeAccount();
            }
          }}
        >
          <div className="personal-center-panel">
            <header className="personal-center-header">
              <h2 id="personal-center-title">个人中心</h2>
              <button
                type="button"
                className="btn-ghost personal-center-close"
                aria-label="关闭个人中心"
                onClick={closeAccount}
              >
                ×
              </button>
            </header>

            <section className="personal-center-account" aria-label="当前账号">
              <span>当前账号</span>
              <div className="personal-center-identity">
                <strong
                  className="online-user"
                  title={user?.display_name || user?.username}
                >
                  {user?.display_name || user?.username}
                </strong>
                {user?.display_name && (
                  <small>
                    登录名：<span>{user.username}</span>
                  </small>
                )}
              </div>
            </section>

            {authError && (
              <p className="online-notice online-notice--error" role="alert">
                {authError}
              </p>
            )}

            <form
              className="personal-center-form"
              aria-labelledby="nickname-form-title"
              onSubmit={(event) => void saveNickname(event)}
            >
              <h3 id="nickname-form-title">昵称设置</h3>
              <label
                className="personal-center-field"
                htmlFor="account-nickname"
              >
                <span>昵称</span>
                <input
                  id="account-nickname"
                  name="nickname"
                  value={nickname}
                  maxLength={40}
                  autoComplete="nickname"
                  aria-describedby="account-nickname-help"
                  disabled={profileBusy}
                  onChange={(event) => {
                    setNickname(event.target.value);
                    setProfileError("");
                    setProfileStatus("");
                  }}
                />
              </label>
              <p className="personal-center-help" id="account-nickname-help">
                最多 40 个字符；保存空白昵称则恢复显示登录名。
              </p>
              {profileError && <p role="alert">{profileError}</p>}
              {profileStatus && <p role="status">{profileStatus}</p>}
              <div className="personal-center-form-actions">
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={profileBusy}
                >
                  {profileBusy ? "正在保存…" : "保存昵称"}
                </button>
              </div>
            </form>

            <form
              className="personal-center-form"
              aria-labelledby="password-form-title"
              onSubmit={(event) => void savePassword(event)}
            >
              <h3 id="password-form-title">修改密码</h3>
              <p className="personal-center-help" id="account-password-help">
                密码长度为 10-256 个字符。修改后，其他设备的登录会话将退出。
              </p>
              <label
                className="personal-center-field"
                htmlFor="current-password"
              >
                <span>当前密码</span>
                <input
                  id="current-password"
                  name="current_password"
                  type={showPasswords ? "text" : "password"}
                  value={currentPassword}
                  required
                  minLength={10}
                  maxLength={256}
                  autoComplete="current-password"
                  aria-describedby="account-password-help"
                  aria-invalid={passwordError === "当前密码错误"}
                  disabled={passwordBusy}
                  onChange={(event) => {
                    setCurrentPassword(event.target.value);
                    setPasswordError("");
                    setPasswordStatus("");
                  }}
                />
              </label>
              <label className="personal-center-field" htmlFor="new-password">
                <span>新密码</span>
                <input
                  id="new-password"
                  name="new_password"
                  type={showPasswords ? "text" : "password"}
                  value={newPassword}
                  required
                  minLength={10}
                  maxLength={256}
                  autoComplete="new-password"
                  aria-describedby="account-password-help"
                  disabled={passwordBusy}
                  onChange={(event) => {
                    setNewPassword(event.target.value);
                    setPasswordError("");
                    setPasswordStatus("");
                  }}
                />
              </label>
              <label
                className="personal-center-field"
                htmlFor="confirm-password"
              >
                <span>确认新密码</span>
                <input
                  id="confirm-password"
                  name="confirm_password"
                  type={showPasswords ? "text" : "password"}
                  value={confirmPassword}
                  required
                  minLength={10}
                  maxLength={256}
                  autoComplete="new-password"
                  aria-describedby="account-password-help"
                  aria-invalid={passwordError === "两次输入的新密码不一致。"}
                  disabled={passwordBusy}
                  onChange={(event) => {
                    setConfirmPassword(event.target.value);
                    setPasswordError("");
                    setPasswordStatus("");
                  }}
                />
              </label>
              {passwordError && <p role="alert">{passwordError}</p>}
              {passwordStatus && <p role="status">{passwordStatus}</p>}
              <div className="personal-center-form-actions">
                <button
                  type="button"
                  className="btn-ghost"
                  aria-pressed={showPasswords}
                  disabled={passwordBusy}
                  onClick={() => setShowPasswords((visible) => !visible)}
                >
                  {showPasswords ? "隐藏密码" : "显示密码"}
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={passwordBusy}
                >
                  {passwordBusy ? "正在修改…" : "修改密码"}
                </button>
              </div>
            </form>

            <nav className="personal-center-actions" aria-label="账号操作">
              <button
                type="button"
                className="account-logout"
                disabled={accountBusy}
                onClick={() => void logout()}
              >
                {authBusy ? "正在退出…" : "退出登录"}
              </button>
            </nav>

            <footer className="personal-center-footer">
              <button
                type="button"
                className="btn-ghost"
                onClick={() => leaveAccount(onBack)}
              >
                返回模式选择
              </button>
            </footer>
          </div>
        </dialog>
      )}
    </div>
  );
}
