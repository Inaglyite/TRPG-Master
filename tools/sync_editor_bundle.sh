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
npx vite build --base=/editor/
rm -rf "$repo_root/editor/dist"
mkdir -p "$repo_root/editor"
cp -r dist "$repo_root/editor/dist"
version="$(python3 -c 'import json,sys; print(json.load(open("package.json"))["version"])' 2>/dev/null || echo unknown)"
echo "editor bundle synced (editor version: $version) -> editor/dist"
