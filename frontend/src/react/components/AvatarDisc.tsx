import type { SpeakerAvatar } from "../../state/message-store";

type AvatarFamily = "keeper" | "npc" | "investigator";

/**
 * 发言者头像：优先渲染模组素材（data URI 优先、HTTP 兜底），
 * 没有素材时显示姓名首字的衬线徽章（三族配色）。
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
  const src = avatar?.asset_data_uri || avatar?.asset_url || "";
  const initial = (name || "?").trim().charAt(0) || "?";
  const className = `avatar-disc avatar-${family}`;
  if (src) {
    return (
      <img className={className} src={src} alt={avatar?.alt || `${name}肖像`} />
    );
  }
  return (
    <span className={`${className} avatar-fallback`} aria-hidden="true">
      {initial}
    </span>
  );
}
