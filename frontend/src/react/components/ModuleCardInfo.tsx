import type { ReactNode } from "react";

import { apiHttpOrigin } from "../../api/client";
import type { ModuleInfo } from "../../api/modules";
import { backendHttpOrigin } from "../../backend-url";

export function ModuleCardInfo({
  item,
  local = false,
  children,
}: {
  item: ModuleInfo;
  local?: boolean;
  children?: ReactNode;
}) {
  const origin = local ? backendHttpOrigin() : apiHttpOrigin();
  const source = local
    ? item.source === "builtin"
      ? "内置模组"
      : "本地导入"
    : item.source === "account"
      ? "个人上传"
      : "服务器模组";
  return (
    <>
      <div className="module-library-cover" aria-hidden="true">
        {item.has_cover && (
          <img
            src={`${origin}/api/modules/${encodeURIComponent(item.id)}/cover`}
            alt=""
            loading="lazy"
            draggable={false}
            onError={(event) => {
              event.currentTarget.hidden = true;
            }}
          />
        )}
      </div>
      <div className="module-manager-info">
        <h3>{item.title}</h3>
        <p className="module-library-source">
          {source}
          {item.version &&
            (item.version === "legacy" ? " · legacy" : ` · v${item.version}`)}
          {children}
        </p>
        {item.description && (
          <p className="module-library-summary">{item.description}</p>
        )}
      </div>
    </>
  );
}
