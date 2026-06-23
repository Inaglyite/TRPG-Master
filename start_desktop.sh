#!/usr/bin/env bash
# TRPG Agent 桌面版启动脚本
# 优先用 Electron，不可用时自动回退到浏览器
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  🎲 疯狂宅邸 — TRPG Agent 桌面版"
echo "========================================"
echo ""

# venv
if [ -f venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

# ---- 前端依赖 & 构建 ----
if [ ! -d frontend/node_modules ]; then
    echo "安装前端依赖..."
    (cd frontend && npm install) || { echo "❌ 前端依赖安装失败"; exit 1; }
fi

if [ ! -d frontend/dist ]; then
    echo "构建前端..."
    (cd frontend && npm run build) || { echo "❌ 前端构建失败"; exit 1; }
fi

# ---- 确保 Electron 二进制存在 ----
# npm 包已安装但二进制需要单独下载；GitHub 在国内常失败，用淘宝镜像兜底。
ensure_electron_binary() {
    local el_dir="frontend/node_modules/electron"
    if [ -f "$el_dir/path.txt" ] && [ -f "$el_dir/dist/electron" ]; then
        return 0
    fi
    echo "Electron 二进制缺失，正在通过镜像下载..."
    (
        cd frontend
        export ELECTRON_MIRROR="https://registry.npmmirror.com/-/binary/electron/"
        node node_modules/electron/install.js
    ) >/dev/null 2>&1
    if [ -f "$el_dir/path.txt" ] && [ -f "$el_dir/dist/electron" ]; then
        echo "✅ Electron 二进制安装完成"
        return 0
    fi
    echo "⚠️  Electron 二进制下载失败（网络问题）"
    return 1
}

# ---- 启动后端服务器 ----
echo "启动后端服务器 (localhost:8765)..."
python3 server.py >/tmp/trpg-server.log 2>&1 &
SERVER_PID=$!
sleep 2

# 确认后端真的起来了
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "❌ 后端启动失败，日志："
    tail -20 /tmp/trpg-server.log 2>/dev/null
    exit 1
fi

cleanup() {
    kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
    echo ""
    echo "游戏结束。"
}
trap cleanup EXIT INT TERM

# ---- 尝试 Electron ----
# 关键：前台等待窗口创建信号，根据真实结果判断成功与否（不再用 & 吞错误）
ELECTRON_OK=false

if ensure_electron_binary; then
    echo "启动 Electron..."
    (
        cd frontend
        # --no-sandbox: 容器/无 setuid 沙箱环境兼容
        node_modules/.bin/electron --no-sandbox .
    ) &
    ELECTRON_PID=$!

    # 给 electron 4 秒确认它没立刻死掉
    sleep 4
    if kill -0 "$ELECTRON_PID" 2>/dev/null; then
        ELECTRON_OK=true
        echo "✅ Electron 已启动"
    else
        echo "⚠️  Electron 启动后立即退出"
    fi
fi

# ---- 回退到浏览器 ----
if [ "$ELECTRON_OK" = false ]; then
    echo ""
    echo "Electron 不可用，改用浏览器打开..."
    URL="http://localhost:8765"
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 &
    elif command -v open >/dev/null 2>&1; then
        open "$URL" &
    fi
    echo "  浏览器地址: $URL"
    echo ""
fi

echo "后端运行中 (PID $SERVER_PID)"
echo "按 Ctrl+C 退出"
echo ""

# 等待后端（electron 退出后由 trap 清理）
wait "$SERVER_PID" 2>/dev/null
