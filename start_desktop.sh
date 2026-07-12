#!/usr/bin/env bash
# TRPG Agent desktop launcher.
# Normal invocation keeps logs in the current terminal; --desktop runs quietly.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_MODE=false
DESKTOP_LOG="${TMPDIR:-/tmp}/trpg-desktop.log"
SERVER_LOG="${TMPDIR:-/tmp}/trpg-server.log"

if [ "${1:-}" = "--desktop" ] || [ ! -t 1 ]; then
    DESKTOP_MODE=true
    exec >>"$DESKTOP_LOG" 2>&1
fi

cd "$SCRIPT_DIR"

echo "========================================"
echo "  🎲 TRPG Agent 桌面版"
echo "========================================"
echo ""

# ---- Python environment ----
if [ -f venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

# ---- Frontend dependencies and build ----
if [ ! -d frontend/node_modules ]; then
    echo "安装前端依赖..."
    (cd frontend && npm install) || { echo "❌ 前端依赖安装失败"; exit 1; }
fi

if [ ! -d frontend/dist ]; then
    echo "构建前端..."
    (cd frontend && npm run build) || { echo "❌ 前端构建失败"; exit 1; }
fi

# npm may be installed while the Electron binary is still missing. Use the
# mirror as a fallback because direct GitHub downloads are often unreliable.
ensure_electron_binary() {
    local el_dir="frontend/node_modules/electron"
    if [ -f "$el_dir/path.txt" ] && [ -f "$el_dir/dist/electron" ]; then
        return 0
    fi

    echo "Electron 二进制缺失，正在通过镜像下载..."
    (
        cd frontend || exit 1
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

SERVER_PID=""
ELECTRON_PID=""

terminate_child() {
    local pid="$1"
    local name="$2"
    local i

    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        return
    fi

    echo "正在停止${name} (PID $pid)..."
    kill -TERM "$pid" 2>/dev/null || true
    for ((i = 0; i < 30; i++)); do
        if ! kill -0 "$pid" 2>/dev/null; then
            wait "$pid" 2>/dev/null || true
            return
        fi
        sleep 0.1
    done

    echo "${name}未及时退出，强制结束。"
    kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    terminate_child "$ELECTRON_PID" " Electron"
    terminate_child "$SERVER_PID" "后端服务"
    echo ""
    echo "游戏结束，相关服务已停止。"
    exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

backend_ready() {
    if command -v curl >/dev/null 2>&1; then
        curl --fail --silent --max-time 0.5 http://127.0.0.1:8765/api/health >/dev/null 2>&1
    else
        python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=0.5)' \
            >/dev/null 2>&1
    fi
}

# ---- Backend server ----
echo "启动后端服务器 (localhost:8765)..."
python3 -u server.py > >(tee "$SERVER_LOG") 2>&1 &
SERVER_PID=$!

BACKEND_OK=false
for ((i = 0; i < 50; i++)); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
    fi
    if backend_ready; then
        BACKEND_OK=true
        break
    fi
    sleep 0.2
done

if [ "$BACKEND_OK" = false ]; then
    echo "❌ 后端启动失败，日志："
    tail -20 "$SERVER_LOG" 2>/dev/null || true
    exit 1
fi
echo "✅ 后端已启动 (PID $SERVER_PID)"

# ---- Electron ----
if ensure_electron_binary; then
    echo "启动 Electron..."
    ELECTRON_STARTED_AT=$SECONDS
    (
        cd frontend || exit 1
        exec env -u ELECTRON_RUN_AS_NODE node_modules/.bin/electron --no-sandbox .
    ) &
    ELECTRON_PID=$!

    # Electron is the lifecycle owner: once its last window closes, wait
    # returns and the EXIT trap immediately stops the backend.
    wait "$ELECTRON_PID"
    ELECTRON_STATUS=$?
    ELECTRON_RUNTIME=$((SECONDS - ELECTRON_STARTED_AT))
    ELECTRON_PID=""

    if [ "$ELECTRON_STATUS" -eq 0 ] || [ "$ELECTRON_RUNTIME" -ge 5 ]; then
        exit "$ELECTRON_STATUS"
    fi

    echo "⚠️  Electron 启动后立即退出（状态码 $ELECTRON_STATUS）"
fi

# A hidden browser fallback cannot detect when its tab closes. Only use that
# fallback in an attended terminal, where Ctrl+C can stop the backend.
if [ "$DESKTOP_MODE" = true ]; then
    echo "❌ Electron 不可用，已停止启动，避免后端在后台残留。"
    if command -v notify-send >/dev/null 2>&1; then
        notify-send "疯狂宅邸启动失败" "Electron 不可用，请查看 $DESKTOP_LOG"
    fi
    exit 1
fi

echo ""
echo "Electron 不可用，改用浏览器打开..."
URL="http://localhost:8765"
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 &
elif command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 &
fi
echo "浏览器地址: $URL"
echo "按 Ctrl+C 停止后端"

wait "$SERVER_PID"
