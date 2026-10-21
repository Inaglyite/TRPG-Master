import { useEffect, useRef, useState } from "react";

import type { SpeakerAvatar } from "../../state/message-store";

type AvatarFamily = "keeper" | "npc" | "investigator";

/**
 * 发言者头像：data URI 优先、HTTP 地址兜底，两者都失败时退回姓名首字徽章。
 * 失败记录有界（每个候选地址只记一次），身份或地址变更时重置，
 * 不会因坏图无限重试；姓名与气泡始终保留。
 */
export function AvatarDisc({
  name,
  avatar,
  family,
}: {
  name: string;
  avatar?: SpeakerAvatar | null;
  family: AvatarFamily;
}) {
  const candidates = [avatar?.asset_data_uri, avatar?.asset_url].filter(
    (src): src is string => Boolean(src),
  );
  const identityKey = `${family}|${name}|${candidates.join("")}`;
  const [failed, setFailed] = useState<readonly string[]>([]);
  const previousKey = useRef(identityKey);
  useEffect(() => {
    if (previousKey.current !== identityKey) {
      previousKey.current = identityKey;
      setFailed([]);
    }
  }, [identityKey]);
  if (previousKey.current !== identityKey) {
    // 同一渲染轮内同步重置，避免身份切换后仍展示旧失败态。
    previousKey.current = identityKey;
    if (failed.length) setFailed([]);
  }
  const src = candidates.find((candidate) => !failed.includes(candidate)) || "";
  const initial = (name || "?").trim().charAt(0) || "?";
  const className = `avatar-disc avatar-${family}`;
  if (src) {
    return (
      <img
        className={className}
        src={src}
        alt={avatar?.alt || `${name}肖像`}
        onError={() =>
          setFailed((current) =>
            current.includes(src) ? current : [...current, src],
          )
        }
      />
    );
  }
  return (
    <span className={`${className} avatar-fallback`} aria-hidden="true">
      {initial}
    </span>
  );
}
