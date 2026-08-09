#!/usr/bin/env bash
set -Eeuo pipefail

# monitor-trpg-master.sh -- TRPG Master 健康监控（阶段 3）
#
# 检查项：应用 readiness、systemd 服务、备份新鲜度、TLS 证书剩余天数、磁盘占用。
# 全部通过退出 0；任一检查失败退出 1；用法/配置错误退出 2。
# 失败时可向可选 webhook 发送 JSON 摘要（TRPG_MONITOR_WEBHOOK_URL）。
#
# 全部参数经环境变量覆盖（与 backup-trpg-master.sh 风格一致），便于 systemd
# 单元按环境注入不同值：
#   TRPG_MONITOR_ENV                环境标签（默认 production）
#   TRPG_MONITOR_HEALTH_URL         readiness URL（默认 http://127.0.0.1:8765/api/ready）
#   TRPG_MONITOR_SERVICE            systemd 服务名（默认 trpg-master.service）
#   TRPG_MONITOR_BACKUP_ROOT        备份目录（默认 /var/backups/trpg-master）
#   TRPG_MONITOR_BACKUP_PREFIX      备份文件名前缀（默认 trpg-master）
#   TRPG_MONITOR_BACKUP_MAX_AGE_HOURS  备份最大允许年龄小时（默认 26）
#   TRPG_MONITOR_CERT_PATH          fullchain.pem 路径（默认生产 Let's Encrypt 路径）
#   TRPG_MONITOR_CERT_MIN_DAYS      证书剩余天数阈值（默认 14）
#   TRPG_MONITOR_DISK_PATHS         磁盘检查路径，空格分隔、路径不含空格（默认 "/ /var/backups/trpg-master"）
#   TRPG_MONITOR_DISK_MAX_PERCENT   使用率上限（默认 90）
#   TRPG_MONITOR_DISK_MIN_FREE_MB   可用空间下限 MB（默认 1024）
#   TRPG_MONITOR_WEBHOOK_URL        可选；仅失败时 POST JSON 摘要
#
# 用法：
#   TRPG_MONITOR_WEBHOOK_URL=https://hooks.example.com/trpg ./deploy/monitor-trpg-master.sh

env_label="${TRPG_MONITOR_ENV:-production}"
health_url="${TRPG_MONITOR_HEALTH_URL:-http://127.0.0.1:8765/api/ready}"
service_name="${TRPG_MONITOR_SERVICE:-trpg-master.service}"
backup_root="${TRPG_MONITOR_BACKUP_ROOT:-/var/backups/trpg-master}"
backup_prefix="${TRPG_MONITOR_BACKUP_PREFIX:-trpg-master}"
backup_max_age_hours="${TRPG_MONITOR_BACKUP_MAX_AGE_HOURS:-26}"
cert_path="${TRPG_MONITOR_CERT_PATH:-/etc/letsencrypt/live/trpggame.xyz/fullchain.pem}"
cert_min_days="${TRPG_MONITOR_CERT_MIN_DAYS:-14}"
disk_paths="${TRPG_MONITOR_DISK_PATHS:-/ /var/backups/trpg-master}"
disk_max_percent="${TRPG_MONITOR_DISK_MAX_PERCENT:-90}"
disk_min_free_mb="${TRPG_MONITOR_DISK_MIN_FREE_MB:-1024}"
webhook_url="${TRPG_MONITOR_WEBHOOK_URL:-}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    grep -E '^#( |$)' -- "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi
if [[ "$#" -gt 0 ]]; then
    echo "unknown option: $1" >&2
    exit 2
fi

# ---- 参数/路径校验（错误即 exit 2，不触发 webhook）----
if [[ ! "$backup_max_age_hours" =~ ^[1-9][0-9]{0,2}$ ]]; then
    echo "invalid TRPG_MONITOR_BACKUP_MAX_AGE_HOURS: $backup_max_age_hours" >&2
    exit 2
fi
if [[ ! "$cert_min_days" =~ ^[0-9]{1,4}$ ]]; then
    echo "invalid TRPG_MONITOR_CERT_MIN_DAYS: $cert_min_days" >&2
    exit 2
fi
if [[ ! "$disk_max_percent" =~ ^[0-9]{1,3}$ ]] || (( disk_max_percent > 100 )); then
    echo "invalid TRPG_MONITOR_DISK_MAX_PERCENT: $disk_max_percent" >&2
    exit 2
fi
if [[ ! "$disk_min_free_mb" =~ ^[0-9]{1,7}$ ]]; then
    echo "invalid TRPG_MONITOR_DISK_MIN_FREE_MB: $disk_min_free_mb" >&2
    exit 2
fi
# 监控脚本只读备份根（不同于 backup 脚本的写路径），因此不硬性限制
# /var/backups 前缀，但要求绝对路径、真实目录且不是符号链接。
# 生产 systemd 单元仍显式配置受管备份根 /var/backups/trpg-master。
if [[ "$backup_root" != /* ]]; then
    echo "backup root must be an absolute path: $backup_root" >&2
    exit 2
fi
if [[ -L "$backup_root" ]]; then
    echo "backup root must not be a symbolic link: $backup_root" >&2
    exit 2
fi
backup_root_real="$(realpath -e -- "$backup_root" 2>/dev/null || true)"
if [[ -z "$backup_root_real" || "$backup_root_real" != "$backup_root" ]]; then
    echo "unsafe backup root: $backup_root" >&2
    exit 2
fi
if [[ ! "$backup_prefix" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]]; then
    echo "invalid backup prefix: $backup_prefix" >&2
    exit 2
fi
# 证书路径不强制限定在 /etc/letsencrypt/live 下（演练/测试可用自定义路径），
# 但必须为绝对路径；读取失败会作为 certificate 检查失败（exit 1）。
if [[ "$cert_path" != /* ]]; then
    echo "certificate path must be absolute: $cert_path" >&2
    exit 2
fi
if [[ ! -d "$backup_root" ]]; then
    echo "backup root does not exist: $backup_root" >&2
    exit 2
fi

# ---- 检查项：累积失败列表，最后统一汇报/告警 ----
failures=()
checks=()

record_check() {
    local name="$1" ok="$2" message="$3"
    checks+=("{\"name\":\"$name\",\"ok\":$ok,\"message\":\"$(json_escape "$message")\"}")
    if [[ "$ok" == "true" ]]; then
        printf '[OK]   %s: %s\n' "$name" "$message"
    else
        failures+=("$name")
        printf '[FAIL] %s: %s\n' "$name" "$message" >&2
    fi
}

json_escape() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\n'/\\n}"
    printf '%s' "$value"
}

check_ready() {
    local message
    if curl --fail --silent --show-error --max-time 5 "$health_url" >/dev/null 2>&1; then
        message="readiness OK: $health_url"
        record_check "ready" true "$message"
    else
        message="readiness failed: $health_url"
        record_check "ready" false "$message"
    fi
}

check_service() {
    local message
    if ! command -v systemctl >/dev/null 2>&1; then
        record_check "service" false "systemctl is unavailable on this host"
        return
    fi
    if systemctl is-active --quiet "$service_name" 2>/dev/null; then
        message="service active: $service_name"
        record_check "service" true "$message"
    else
        message="service is not active: $service_name"
        record_check "service" false "$message"
    fi
}

check_backup_freshness() {
    local minutes fresh latest
    minutes=$((backup_max_age_hours * 60))
    fresh="$(find "$backup_root" -maxdepth 1 -type f \
        -name "$backup_prefix-*.tar.gpg" -mmin "-$minutes" -print -quit 2>/dev/null || true)"
    if [[ -n "$fresh" ]]; then
        record_check "backup" true \
            "backup fresh within ${backup_max_age_hours}h: $(basename -- "$fresh")"
        return
    fi
    # 不用 head/早退读取器:head 提前关闭管道会让 sort 收到 SIGPIPE,
    # pipefail 下命令替换非零使整个监控脚本退出(备份不新鲜时告警静默
    # 丢失)。sed -n '1p' 完整消费排序输出,find/sort 正常结束,真正的
    # 命令失败语义保留。
    latest="$(find "$backup_root" -maxdepth 1 -type f \
        -name "$backup_prefix-*.tar.gpg" -printf '%T@ %f\n' 2>/dev/null \
        | sort -nr | sed -n '1p')"
    if [[ -z "$latest" ]]; then
        record_check "backup" false "no backup found in $backup_root"
    else
        record_check "backup" false \
            "no backup newer than ${backup_max_age_hours}h (latest: ${latest#* })"
    fi
}

check_certificate() {
    local enddate not_after expiry_epoch now_epoch days message
    if [[ ! -f "$cert_path" ]]; then
        record_check "certificate" false "certificate not found: $cert_path"
        return
    fi
    if ! command -v openssl >/dev/null 2>&1; then
        record_check "certificate" false "openssl is unavailable on this host"
        return
    fi
    enddate="$(openssl x509 -enddate -noout -in "$cert_path" 2>/dev/null || true)"
    not_after="${enddate#notAfter=}"
    if [[ -z "$not_after" || "$not_after" == "$enddate" ]]; then
        record_check "certificate" false "cannot read certificate end date: $cert_path"
        return
    fi
    expiry_epoch="$(date -d "$not_after" +%s 2>/dev/null || true)"
    now_epoch="$(date +%s)"
    if [[ -z "$expiry_epoch" ]]; then
        record_check "certificate" false "cannot parse certificate end date: $not_after"
        return
    fi
    days=$(( (expiry_epoch - now_epoch + 86399) / 86400 ))
    if (( days < 0 )); then
        message="certificate expired $((-days)) days ago"
        record_check "certificate" false "$message"
    elif (( days < cert_min_days )); then
        message="certificate expires in ${days}d (minimum ${cert_min_days}d)"
        record_check "certificate" false "$message"
    else
        message="certificate expires in ${days}d"
        record_check "certificate" true "$message"
    fi
}

check_disk() {
    local path available_percent available_mb message
    local -a paths
    # TRPG_MONITOR_DISK_PATHS 是空格分隔的绝对路径列表；
    # 显式 IFS 保证解析确定，路径本身不含空格（不支持带空格的路径）。
    local IFS=' '
    read -r -a paths <<<"$disk_paths"
    for path in "${paths[@]}"; do
        [[ -n "$path" ]] || continue
        if [[ ! -e "$path" ]]; then
            record_check "disk:$path" false "path does not exist: $path"
            continue
        fi
        available_percent="$(df -Pm -- "$path" 2>/dev/null | awk 'NR==2 {print $5}')"
        available_mb="$(df -Pm -- "$path" 2>/dev/null | awk 'NR==2 {print $4}')"
        available_percent="${available_percent%\%}"
        if [[ ! "$available_percent" =~ ^[0-9]+$ ]] \
            || [[ ! "$available_mb" =~ ^[0-9]+$ ]]; then
            record_check "disk:$path" false "cannot stat filesystem: $path"
            continue
        fi
        if (( available_percent > disk_max_percent )) \
            || (( available_mb < disk_min_free_mb )); then
            message="usage ${available_percent}%, free ${available_mb}MB (max ${disk_max_percent}%, min ${disk_min_free_mb}MB)"
            record_check "disk:$path" false "$message"
        else
            message="usage ${available_percent}%, free ${available_mb}MB"
            record_check "disk:$path" true "$message"
        fi
    done
}

check_ready
check_service
check_backup_freshness
check_certificate
check_disk

if [[ "${#failures[@]}" -eq 0 ]]; then
    printf 'monitor %s: all checks passed\n' "$env_label"
    exit 0
fi

printf 'monitor %s: %d check(s) failed: %s\n' \
    "$env_label" "${#failures[@]}" "$(IFS=,; echo "${failures[*]}")" >&2

if [[ -n "$webhook_url" ]]; then
    payload="{\"env\":\"$(json_escape "$env_label")\",\"ok\":false,"
    payload+="\"failed\":${#failures[@]},\"checks\":[$(IFS=,; echo "${checks[*]}")]}"
    if ! curl --fail --silent --show-error --max-time 10 \
        -X POST -H 'Content-Type: application/json' \
        --data-binary "$payload" "$webhook_url" >/dev/null 2>&1; then
        echo "warning: monitor webhook delivery failed: $webhook_url" >&2
    fi
fi
exit 1
