#!/usr/bin/env bash
# 把 TRPG Mod Editor 的浏览器版构建产物同步为游戏内 vendored 静态包。
#
# 用法：bash tools/sync_editor_bundle.sh [编辑器仓库路径]
# 默认取与本仓库平级的 ../TRPG_Mod_Editor。
#
# 编辑器以 /editor/ base 构建后由游戏服务器同源挂载（server.py 的 /editor
# mount）；同源让它直接复用本后端的 /api/editor/** 会话契约。同步后请
# 提交 editor/dist 的快照，并在 commit message 里注明编辑器版本。
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
editor_repo="${1:-$(cd "$repo_root/.." && pwd)/TRPG_Mod_Editor}"

if [[ ! -f "$editor_repo/package.json" ]]; then
    echo "editor repo not found: $editor_repo" >&2
    exit 2
fi

cd "$editor_repo"
# 内嵌网页使用 /editor/ base；独立 Electron 使用相对路径。
# 在仓库外单独构建，避免覆盖编辑器 dist 后让桌面启动加载错误的路径。
bundle_work_root="$(dirname "$repo_root")/mid_product/$(basename "$repo_root")/work/editor-bundle-build"
mkdir -p "$bundle_work_root"
chmod 700 "$bundle_work_root"
bundle_work_dir="$(mktemp -d "$bundle_work_root/run-XXXXXXXX")"
npm run typecheck
npx vite build --base=/editor/ --outDir "$bundle_work_dir/dist"
rm -rf "$repo_root/editor/dist"
mkdir -p "$repo_root/editor"
cp -r "$bundle_work_dir/dist" "$repo_root/editor/dist"
version="$(python3 -c 'import json,sys; print(json.load(open("package.json"))["version"])' 2>/dev/null || echo unknown)"
python3 - "$repo_root/editor/dist/editor-build.json" "$version" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"version": sys.argv[2], "editor_api": 1}) + "\n", encoding="utf-8")
PY
echo "editor bundle synced (editor version: $version) -> editor/dist"
