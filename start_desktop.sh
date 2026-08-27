#!/usr/bin/env bash
# TRPG Game desktop launcher.
# Normal invocation keeps logs in the current terminal; --desktop runs quietly.
# Electron calls --backend-only after the user explicitly chooses local mode.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_MODE=false
BACKEND_ONLY=false
DESKTOP_LOG="${TMPDIR:-/tmp}/trpg-desktop.log"
SERVER_LOG="${TMPDIR:-/tmp}/trpg-server.log"

case "${1:-}" in
    "")
        ;;
    --desktop)
        DESKTOP_MODE=true
        ;;
    --backend-only)
        BACKEND_ONLY=true
        ;;
    *)
        echo "用法: $0 [--desktop | --backend-only]" >&2
        exit 2
        ;;
esac

if [ "$BACKEND_ONLY" = false ] && { [ "$DESKTOP_MODE" = true ] || [ ! -t 1 ]; }; then
    DESKTOP_MODE=true
    exec >>"$DESKTOP_LOG" 2>&1
fi

cd "$SCRIPT_DIR"

ensure_backend_dependencies() {
    if python3 -c 'import alembic, argon2, fastapi, psycopg, sqlalchemy, uvicorn' \
        >/dev/null 2>&1; then
        return 0
    fi

    echo "安装/更新后端依赖..."
    python3 -m pip install --disable-pip-version-check -r requirements.txt || {
        echo "❌ 后端依赖安装失败"
        return 1
    }
}

run_backend() {
    # Backend setup is intentionally inside this function. Merely opening the
    # desktop launcher or choosing cloud multiplayer must not touch local
    # Python dependencies, the SQLite database, or legacy world files.
    if [ -f venv/bin/activate ]; then
        # shellcheck disable=SC1091
        source venv/bin/activate
    elif [ -f .venv/bin/activate ]; then
        # shellcheck disable=SC1091
        source .venv/bin/activate
    else
        if ! command -v python3 >/dev/null 2>&1; then
            echo "❌ 未找到 Python 3.12+，无法启动本地单机后端"
            return 1
        fi
        echo "首次启动：创建 Python 虚拟环境..."
        python3 -m venv venv || {
            echo "❌ 虚拟环境创建失败；Debian/Ubuntu 请先安装 python3-venv"
            return 1
        }
        # shellcheck disable=SC1091
        source venv/bin/activate
    fi

    ensure_backend_dependencies || return 1

    local runtime_root="${TRPG_RUNTIME_ROOT:-$SCRIPT_DIR}"
    if [ -z "${TRPG_DATABASE_URL:-}" ]; then
        export TRPG_DATABASE_URL="sqlite:///$runtime_root/trpg-master.db"
    fi

    # Apply schema changes before importing old file-backed worlds. --once
    # stores its completion marker in the database, so later launches never
    # overwrite database state with stale compatibility exports.
    echo "检查数据库迁移..."
    python3 -m alembic upgrade head || {
        echo "❌ 数据库迁移失败"
        return 1
    }
    python3 tools/import_worlds_to_database.py \
        --runtime-root "$runtime_root" --once --replace \
        || {
            echo "❌ 旧世界数据导入失败"
            return 1
        }

    echo "启动后端服务器 (localhost:8765)..."
    exec python3 -u server.py
}

if [ "$BACKEND_ONLY" = true ]; then
    run_backend
    exit $?
fi

echo "========================================"
echo "  🎲 TRPG Game 桌面版"
echo "========================================"
echo ""

# ---- Frontend dependencies and build ----
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "❌ 未找到 Node.js 20+ 与 npm，请安装后重试"
    exit 1
fi

if [ ! -d frontend/node_modules ]; then
    echo "安装前端依赖..."
    (cd frontend && npm ci) || { echo "❌ 前端依赖安装失败"; exit 1; }
fi

FRONTEND_BUILD_STAMP="frontend/dist/index.html"
FRONTEND_BUILD_NEEDED=false
if [ ! -f "$FRONTEND_BUILD_STAMP" ]; then
    FRONTEND_BUILD_NEEDED=true
elif find frontend/src frontend/public \
    -type f -newer "$FRONTEND_BUILD_STAMP" -print -quit 2>/dev/null \
    | grep -q .; then
    FRONTEND_BUILD_NEEDED=true
elif [ frontend/index.html -nt "$FRONTEND_BUILD_STAMP" ] \
    || [ frontend/package.json -nt "$FRONTEND_BUILD_STAMP" ] \
    || [ frontend/package-lock.json -nt "$FRONTEND_BUILD_STAMP" ]; then
    FRONTEND_BUILD_NEEDED=true
fi

if [ "$FRONTEND_BUILD_NEEDED" = true ]; then
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
SERVER_PROCESS_GROUP=false
ELECTRON_PID=""

terminate_child() {
    local pid="$1"
    local name="$2"
    local process_group="${3:-false}"
    local i
    local target="$pid"

    if [ "$process_group" = true ]; then
        target="-$pid"
    fi
    if [ -z "$pid" ] || ! kill -0 -- "$target" 2>/dev/null; then
        return
    fi

    echo "正在停止${name} (PID $pid)..."
    kill -TERM -- "$target" 2>/dev/null || true
    for ((i = 0; i < 30; i++)); do
        if ! kill -0 -- "$target" 2>/dev/null; then
            wait "$pid" 2>/dev/null || true
            return
        fi
        sleep 0.1
    done

    echo "${name}未及时退出，强制结束。"
    kill -KILL -- "$target" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    terminate_child "$ELECTRON_PID" " Electron" false
    terminate_child "$SERVER_PID" "后端服务" "$SERVER_PROCESS_GROUP"
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

start_browser_backend() {
    echo "正在为浏览器单机模式准备本地后端..."
    # Job control gives the helper and every pip/Alembic/Python descendant a
    # dedicated process group. Cleanup can then stop the whole setup chain if
    # the user presses Ctrl+C before the helper execs the final server.
    set -m
    "$SCRIPT_DIR/start_desktop.sh" --backend-only > >(tee "$SERVER_LOG") 2>&1 &
    SERVER_PID=$!
    SERVER_PROCESS_GROUP=true
    set +m

    local ready=false
    local i
    # First dependency installation can take noticeably longer than a normal
    # restart, so keep the UI launch timeout generous.
    for ((i = 0; i < 900; i++)); do
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            break
        fi
        if backend_ready; then
            sleep 0.25
            if kill -0 "$SERVER_PID" 2>/dev/null; then
                ready=true
            fi
            break
        fi
        sleep 0.2
    done

    if [ "$ready" = false ]; then
        echo "❌ 后端启动失败，日志："
        tail -20 "$SERVER_LOG" 2>/dev/null || true
        return 1
    fi
    echo "✅ 后端已启动 (PID $SERVER_PID)"
}

# ---- Electron ----
if ensure_electron_binary; then
    echo "启动 Electron..."
    ELECTRON_STARTED_AT=$SECONDS
    (
        cd frontend || exit 1
        exec env -u ELECTRON_RUN_AS_NODE \
            TRPG_SOURCE_BACKEND_LAUNCHER="$SCRIPT_DIR/start_desktop.sh" \
            node_modules/.bin/electron .
    ) &
    ELECTRON_PID=$!

    # Electron owns any source backend it starts after local-mode selection.
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
        notify-send "TRPG Game 启动失败" "Electron 不可用，请查看 $DESKTOP_LOG"
    fi
    exit 1
fi

echo ""
echo "Electron 不可用，改用浏览器单机模式..."
start_browser_backend || exit 1
URL="http://localhost:8765/?mode=local"
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 &
elif command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 &
fi
echo "浏览器单机地址: $URL"
echo "多人网页版请直接打开部署服务器的 HTTPS 地址。"
echo "按 Ctrl+C 停止后端"

wait "$SERVER_PID"
