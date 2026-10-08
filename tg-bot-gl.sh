#!/usr/bin/env bash
set -uo pipefail

# Telegram 机器人 安装管理工具
# 功能：获取机器人代码（仓库拉取 / 本地上传）/ 安装环境 / 写入密钥与用户ID / 配置开机自启 / 一键部署 / 卸载
# 说明：没有任何预设值，名称、目录、仓库地址、变量名、Token、ID 全部交互式输入；
#       不使用虚拟环境，依赖直接装到系统 python3。
# 用法：sudo bash tg-bot-gl.sh [仓库地址]

case "$(locale charmap 2>/dev/null)" in
    UTF-8) ;;
    *) export LC_ALL=C.UTF-8 ;;
esac

# ---------------------------------------------------------------- 颜色与界面

RED=$'\033[31m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
CYAN=$'\033[36m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
RESET=$'\033[0m'

BOX_W=54

log()  { echo "  ${GREEN}✔${RESET} $*"; }
warn() { echo "  ${YELLOW}!${RESET} $*"; }
err()  { echo "  ${RED}✘${RESET} $*" >&2; }

# 显示宽度（中文按 2 列）
dw() {
    local s="$1" b c
    b=$(printf '%s' "$s" | wc -c)
    c=${#s}
    echo $(( (b + c) / 2 ))
}

# 左对齐并补空格到指定显示宽度
padr() {
    local s="$1" w="$2" n
    n=$(dw "$s")
    printf '%s' "$s"
    (( w > n )) && printf '%*s' $((w - n)) ''
    return 0
}

rep() {
    local n=$1 ch=$2 out="" i
    for ((i = 0; i < n; i++)); do out+="$ch"; done
    printf '%s' "$out"
}

cls() {
    if [[ -t 1 ]] && command -v clear >/dev/null 2>&1; then
        clear 2>/dev/null || true
    fi
    return 0
}

# banner "左侧标题" ["右侧文字"]
banner() {
    local left="$1" right="${2:-}" gap
    gap=$(( BOX_W - $(dw "$left") - $(dw "$right") - 4 ))
    (( gap < 1 )) && gap=1
    echo "${CYAN}╔$(rep "$BOX_W" ═)╗${RESET}"
    echo "${CYAN}║${RESET}  ${BOLD}${left}${RESET}$(rep "$gap" ' ')${DIM}${right}${RESET}  ${CYAN}║${RESET}"
    echo "${CYAN}╚$(rep "$BOX_W" ═)╝${RESET}"
}

heading() {
    echo
    echo "  ${BOLD}${CYAN}$1${RESET}"
    echo "  ${DIM}$(rep 46 ─)${RESET}"
}

lab() {
    printf '%s' "${DIM}$(padr "$1" 8)${RESET}"
}

# menu_item 键 标题 说明
menu_item() {
    printf '  %s) %s  %s\n' "${YELLOW}$1${RESET}" "$(padr "$2" 22)" "${DIM}${3:-}${RESET}"
}

# rd 变量名 "提示" [默认值]
rd() {
    local __v="$1" text="$2" def="${3:-}" ans
    if [[ -n "$def" ]]; then
        read -r -p "  ${CYAN}▸${RESET} ${text} ${DIM}[${def}]${RESET}: " ans
        ans="${ans:-$def}"
    else
        read -r -p "  ${CYAN}▸${RESET} ${text}: " ans
    fi
    printf -v "$__v" '%s' "$ans"
}

# 隐藏输入
rds() {
    local __v="$1" text="$2" ans
    read -r -s -p "  ${CYAN}▸${RESET} ${text}: " ans
    echo
    printf -v "$__v" '%s' "$ans"
}

ask() { rd "$@"; }

confirm() {
    local a
    read -r -p "  ${CYAN}▸${RESET} $1 ${DIM}[y/N]${RESET}: " a
    [[ "$a" =~ ^[Yy]$ ]]
}

confirm_y() {
    local a
    read -r -p "  ${CYAN}▸${RESET} $1 ${DIM}[Y/n]${RESET}: " a
    [[ -z "$a" || "$a" =~ ^[Yy]$ ]]
}

pause() {
    local _
    read -r -s -p $'\n  '"${DIM}按回车键继续...${RESET}" _
    echo
}

mask() {
    local s="$1"
    if (( ${#s} <= 10 )); then
        echo "******"
    else
        echo "${s:0:6}…${s: -4}"
    fi
}

[[ $EUID -eq 0 ]] || { err "请使用 root 运行：sudo bash $0"; exit 1; }

# ---------------------------------------------------------------- 配置与状态

CONF_DIR="${TGBOT_CONF_DIR:-/etc/tgbot-installer}"
CONF_FILE=""
BOT_NAME=""
NEW_URL="${1:-}"   # 可选：命令行传入的仓库地址，新增机器人时直接使用
SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

# 当前机器人的配置（每个机器人单独一份，保存在 CONF_DIR/<名称>.conf）
SOURCE=""          # repo = 从仓库拉取；local = 本地上传
REPO_URL=""
BRANCH=""
INSTALL_DIR=""
MAIN_PY=""
SERVICE_NAME=""
TOKEN_VAR=""
ID_VAR=""

CHOSEN_PY=""
PYFILES=()

load_conf() {
    if [[ -f "$CONF_FILE" ]]; then
        # shellcheck disable=SC1090
        source "$CONF_FILE" || true
    fi
}

save_conf() {
    [[ -n "$CONF_FILE" ]] || return 0
    mkdir -p "$CONF_DIR" && chmod 700 "$CONF_DIR"
    (
        umask 077
        cat >"$CONF_FILE" <<EOF
SOURCE=$(printf '%q' "$SOURCE")
REPO_URL=$(printf '%q' "$REPO_URL")
BRANCH=$(printf '%q' "$BRANCH")
INSTALL_DIR=$(printf '%q' "$INSTALL_DIR")
MAIN_PY=$(printf '%q' "$MAIN_PY")
SERVICE_NAME=$(printf '%q' "$SERVICE_NAME")
TOKEN_VAR=$(printf '%q' "$TOKEN_VAR")
ID_VAR=$(printf '%q' "$ID_VAR")
EOF
    )
    chmod 600 "$CONF_FILE"
}

reset_defaults() {
    SOURCE=""
    REPO_URL=""
    BRANCH=""
    INSTALL_DIR=""
    MAIN_PY=""
    SERVICE_NAME=""
    TOKEN_VAR=""
    ID_VAR=""
}

has_systemd() {
    command -v systemctl >/dev/null 2>&1
}

# 服务状态（带颜色），参数：服务名
svc_state() {
    local svc="$1"
    if ! has_systemd || [[ ! -f "/etc/systemd/system/${svc}.service" ]]; then
        echo "${DIM}○ 未创建${RESET}"
        return
    fi
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo "${GREEN}● 运行中${RESET}"
    else
        echo "${RED}● 已停止${RESET}"
    fi
}

# 是否已设置开机自启，参数：服务名
svc_enabled() {
    local svc="$1"
    if has_systemd && [[ -f "/etc/systemd/system/${svc}.service" ]] &&
        systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        echo "已设置"
    else
        echo "未设置"
    fi
}

# 危险目录不允许作为安装目录（避免卸载时误删）
is_unsafe_dir() {
    case "$1" in
        /|/root|/home|/etc|/usr|/var|/opt|/bin|/sbin|/lib|/lib64|/boot|/dev|/proc|/sys|/tmp|/mnt|/srv|/run)
            return 0 ;;
    esac
    return 1
}

# ---------------------------------------------------------------- 基础函数

pkg_install() {
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -y && apt-get install -y "$@"
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y "$@"
    elif command -v yum >/dev/null 2>&1; then
        yum install -y "$@"
    else
        err "无法识别包管理器，请手动安装：$*"
        return 1
    fi
}

ensure_git() {
    command -v git >/dev/null 2>&1 && return 0
    log "正在安装 git..."
    pkg_install git
}

ensure_python() {
    if command -v python3 >/dev/null 2>&1 && python3 -m pip --version >/dev/null 2>&1; then
        return 0
    fi
    log "正在安装 python3 / pip..."
    pkg_install python3 python3-pip
}

# 安装 pip 包（系统 Python）；遇到 PEP 668 限制自动加 --break-system-packages
pip_install() {
    local tmp rc
    tmp="$(mktemp)"
    python3 -m pip install "$@" 2>&1 | tee "$tmp"
    rc=${PIPESTATUS[0]}
    if (( rc != 0 )) && grep -q 'externally-managed-environment' "$tmp"; then
        warn "系统 Python 受 PEP 668 限制，改用 --break-system-packages 安装。"
        python3 -m pip install --break-system-packages "$@"
        rc=$?
    fi
    rm -f "$tmp"
    return $rc
}

list_py_files() {
    PYFILES=()
    [[ -d "$INSTALL_DIR" ]] || return 0
    mapfile -t PYFILES < <(cd "$INSTALL_DIR" && find . -maxdepth 2 -name '*.py' \
        -not -path './.git/*' | sed 's|^\./||' | sort)
}

# 选择一个 py 文件，结果放到 CHOSEN_PY（相对路径），参数为标题
choose_py() {
    CHOSEN_PY=""
    if [[ ! -d "$INSTALL_DIR" ]]; then
        warn "目录不存在：$INSTALL_DIR，请先获取机器人代码。"
        return 1
    fi
    list_py_files
    if (( ${#PYFILES[@]} == 0 )); then
        warn "$INSTALL_DIR 下没有找到 .py 文件。"
        return 1
    fi

    heading "$1"
    local i def="" mark sel
    for i in "${!PYFILES[@]}"; do
        mark=""
        if [[ -n "$MAIN_PY" && "${PYFILES[i]}" == "$MAIN_PY" ]]; then
            mark="当前主程序"
            def=$((i + 1))
        fi
        menu_item $((i + 1)) "${PYFILES[i]}" "$mark"
    done
    echo
    rd sel "请选择编号" "$def"
    if [[ ! "$sel" =~ ^[0-9]+$ ]] || (( sel < 1 || sel > ${#PYFILES[@]} )); then
        err "无效编号：$sel"
        return 1
    fi
    CHOSEN_PY="${PYFILES[sel-1]}"
}

# 确保已确定主程序
ensure_main_py() {
    if [[ -n "$MAIN_PY" && -f "$INSTALL_DIR/$MAIN_PY" ]]; then
        return 0
    fi
    MAIN_PY=""
    list_py_files
    if (( ${#PYFILES[@]} == 0 )); then
        warn "$INSTALL_DIR 里没有 .py 文件，请先获取机器人代码。"
        return 1
    fi
    if (( ${#PYFILES[@]} == 1 )); then
        MAIN_PY="${PYFILES[0]}"
        log "主程序：$MAIN_PY"
    else
        choose_py "请选择机器人主程序（开机自启将运行它）" || return 1
        MAIN_PY="$CHOSEN_PY"
    fi
    save_conf
}

# ---------------------------------------------------------------- 机器人选择 / 新增

# 新增机器人向导：名称、目录、代码来源全部手动输入
new_bot() {
    local name dir src="" src_choice url="" branch=""

    cls
    banner "新增机器人" "共 3 步"

    # 步骤 1
    heading "[1/3] 机器人名称"
    echo "  ${DIM}用作 systemd 服务名；仅限小写字母、数字、- 和 _，例如 navbot${RESET}"
    while true; do
        rd name "名称（直接回车取消）"
        if [[ -z "$name" ]]; then warn "已取消。"; return 1; fi
        if [[ ! "$name" =~ ^[a-z0-9_-]+$ ]]; then
            err "名称不合法，请重新输入。"
            continue
        fi
        if [[ -f "$CONF_DIR/$name.conf" ]]; then
            err "已存在同名机器人：$name，请换一个。"
            continue
        fi
        break
    done

    # 步骤 2
    heading "[2/3] 安装目录"
    echo "  ${DIM}绝对路径，例如 /root/$name；不能直接用 /root、/opt 等系统目录${RESET}"
    while true; do
        rd dir "目录（直接回车取消）"
        if [[ -z "$dir" ]]; then warn "已取消。"; return 1; fi
        dir="${dir%/}"
        if [[ "$dir" != /* ]]; then
            err "必须是绝对路径（以 / 开头）。"
            continue
        fi
        if is_unsafe_dir "$dir"; then
            err "不能直接使用系统目录 $dir，请使用其下的子目录，例如 $dir/$name"
            continue
        fi
        break
    done

    # 步骤 3
    heading "[3/3] 机器人代码来源"
    if [[ -n "$NEW_URL" ]]; then
        src="repo"
        url="$NEW_URL"
        echo "  已通过命令行指定仓库：$url"
    else
        menu_item 1 "从 GitHub 仓库拉取" "输入仓库地址，由脚本克隆"
        menu_item 2 "本地上传" "你自己把代码传到安装目录"
        echo
        while true; do
            rd src_choice "请选择（直接回车取消）"
            case "$src_choice" in
                "") warn "已取消。"; return 1 ;;
                1) src="repo"; break ;;
                2) src="local"; break ;;
                *) err "请输入 1 或 2。" ;;
            esac
        done
        if [[ "$src" == "repo" ]]; then
            while true; do
                rd url "仓库地址（如 https://github.com/用户/仓库.git；回车取消）"
                if [[ -z "$url" ]]; then warn "已取消。"; return 1; fi
                break
            done
        fi
    fi
    if [[ "$src" == "repo" ]]; then
        rd branch "分支（留空=默认分支）"
    fi

    # 确认
    heading "请确认"
    echo "  $(lab 名称)$name"
    echo "  $(lab 目录)$dir"
    if [[ "$src" == "repo" ]]; then
        echo "  $(lab 来源)仓库 $url"
        echo "  $(lab 分支)${branch:-默认分支}"
    else
        echo "  $(lab 来源)本地上传"
    fi
    echo
    confirm_y "确认创建？" || { warn "已取消。"; return 1; }

    reset_defaults
    BOT_NAME="$name"
    INSTALL_DIR="$dir"
    SERVICE_NAME="$name"
    SOURCE="$src"
    REPO_URL="$url"
    BRANCH="$branch"
    CONF_FILE="$CONF_DIR/$name.conf"
    NEW_URL=""
    save_conf
    log "已新增机器人「$name」"
    if [[ "$src" == "local" ]]; then
        mkdir -p "$INSTALL_DIR"
        log "请把机器人代码上传到：$INSTALL_DIR"
        sleep 1
    fi
}

# 选择已有机器人，或新增一个
select_bot() {
    local names=() f i sel info
    if [[ -d "$CONF_DIR" ]]; then
        for f in "$CONF_DIR"/*.conf; do
            [[ -f "$f" ]] && names+=("$(basename "$f" .conf)")
        done
    fi

    if (( ${#names[@]} == 0 )); then
        cls
        banner "Telegram Bot Manager" "服务器 IP: ${SERVER_IP:-未知}"
        echo
        echo "  欢迎使用 Telegram 机器人部署工具。"
        echo "  ${DIM}一键完成：获取代码 → 安装环境 → 写入密钥 → 开机自启${RESET}"
        echo "  ${DIM}首次使用，先新增一个机器人。${RESET}"
        echo
        pause
        new_bot
        return $?
    fi

    cls
    banner "Telegram Bot Manager" "服务器 IP: ${SERVER_IP:-未知}"
    heading "选择机器人"
    for i in "${!names[@]}"; do
        info="$( source "$CONF_DIR/${names[i]}.conf" 2>/dev/null
                 printf '%s  %s' "$(svc_state "${SERVICE_NAME:-${names[i]}}")" "${DIM}${INSTALL_DIR}${RESET}" )"
        printf '  %s) %s  %s\n' "${YELLOW}$((i + 1))${RESET}" "$(padr "${names[i]}" 16)" "$info"
    done
    printf '  %s) %s\n' "${YELLOW}n${RESET}" "新增机器人"
    echo

    local def=""
    [[ -n "$NEW_URL" ]] && def="n"
    rd sel "请选择" "$def"

    if [[ "$sel" =~ ^[Nn]$ ]]; then
        new_bot
        return $?
    fi
    if [[ ! "$sel" =~ ^[0-9]+$ ]] || (( sel < 1 || sel > ${#names[@]} )); then
        err "无效选择：$sel"
        return 1
    fi

    reset_defaults
    BOT_NAME="${names[sel-1]}"
    CONF_FILE="$CONF_DIR/$BOT_NAME.conf"
    load_conf
    [[ -n "$SERVICE_NAME" ]] || SERVICE_NAME="$BOT_NAME"
}

# ---------------------------------------------------------------- 1. 拉取 / 更新仓库

do_pull() {
    heading "拉取 / 更新仓库"
    if [[ -z "$REPO_URL" ]]; then
        warn "该机器人目前是本地上传模式，没有仓库地址。"
        rd REPO_URL "请输入仓库地址（直接回车取消）"
        [[ -n "$REPO_URL" ]] || { echo "  已取消。"; return 1; }
        rd BRANCH "分支（留空=默认分支）"
        SOURCE="repo"
        save_conf
    fi
    echo "  $(lab 仓库)$REPO_URL"
    echo "  $(lab 目录)$INSTALL_DIR"
    echo

    ensure_git || return 1

    if [[ -d "$INSTALL_DIR/.git" ]]; then
        log "检测到已有仓库，执行更新..."
        git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL" || true

        local stashed=0
        if [[ -n "$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no)" ]]; then
            warn "检测到本地修改（比如已写入的密钥），先暂存再更新。"
            git -C "$INSTALL_DIR" stash push -m "tgbot-installer-auto" >/dev/null && stashed=1
        fi

        git -C "$INSTALL_DIR" fetch --all --prune || { err "fetch 失败"; return 1; }
        if [[ -n "$BRANCH" ]]; then
            git -C "$INSTALL_DIR" checkout "$BRANCH" || { err "切换分支失败"; return 1; }
        fi
        git -C "$INSTALL_DIR" pull --ff-only || { err "pull 失败（可能有冲突）"; return 1; }

        if (( stashed )); then
            if git -C "$INSTALL_DIR" stash pop >/dev/null 2>&1; then
                log "本地修改已恢复。"
            else
                warn "恢复本地修改时有冲突，修改仍保存在 git stash 中："
                warn "  cd $INSTALL_DIR && git stash list / git stash pop"
                warn "建议改用 .env 方式保存密钥，避免与仓库更新冲突。"
            fi
        fi
    elif [[ -d "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
        err "$INSTALL_DIR 已存在且不是空目录（也不是 git 仓库），请清理后重试。"
        return 1
    else
        log "正在克隆仓库..."
        mkdir -p "$(dirname "$INSTALL_DIR")"
        local args=(clone)
        [[ -n "$BRANCH" ]] && args+=(-b "$BRANCH")
        args+=("$REPO_URL" "$INSTALL_DIR")
        git "${args[@]}" || { err "克隆失败，请检查地址/权限。"; return 1; }
    fi

    log "仓库就绪：$INSTALL_DIR  ($(git -C "$INSTALL_DIR" log -1 --format='%h %s' 2>/dev/null))"
    ensure_main_py
    save_conf
}

# ---------------------------------------------------------------- 2. 安装环境（不使用虚拟环境）

do_deps() {
    heading "安装环境"
    if [[ ! -d "$INSTALL_DIR" ]]; then
        warn "目录不存在：$INSTALL_DIR，请先获取机器人代码。"
        return 1
    fi

    local extra
    rd extra "额外系统软件包（空格分隔，如 traceroute nmap；留空跳过）"
    if [[ -n "$extra" ]]; then
        # shellcheck disable=SC2086
        pkg_install $extra || warn "部分系统包安装失败，继续。"
    fi

    ensure_python || return 1

    if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
        log "正在安装 requirements.txt 依赖（系统 Python）..."
        pip_install -r "$INSTALL_DIR/requirements.txt" || { err "依赖安装失败"; return 1; }
    else
        warn "目录里没有 requirements.txt。"
        local pkgs
        rd pkgs "需要安装的 pip 包（空格分隔，如 python-telegram-bot requests；留空跳过）"
        if [[ -n "$pkgs" ]]; then
            # shellcheck disable=SC2086
            pip_install $pkgs || { err "依赖安装失败"; return 1; }
        fi
    fi
    log "环境安装完成（python3：$(command -v python3)）"
}

# ---------------------------------------------------------------- 3. 写入密钥 / 用户ID

# 写入 py 文件中的变量
# set_py_var 文件 变量名 类型(str|ids|raw) 新增时的ID格式(list|int|strjoin) 值
set_py_var() {
    local file="$1" var="$2" kind="$3" fallback="$4" value="$5" rc

    cp -p "$file" "$file.bak" && chmod 600 "$file.bak"

    TG_VAL="$value" python3 - "$file" "$var" "$kind" "$fallback" <<'PY'
import os, re, sys

path, var, kind, fallback = sys.argv[1:5]
val = os.environ["TG_VAL"]
src = open(path, encoding="utf-8").read()

pat = re.compile(r'^([ \t]*)' + re.escape(var) + r'[ \t]*(?::[^=\n]+)?=(?!=)[ \t]*', re.M)
m = pat.search(src)


def fmt_ids(style, ids):
    if style == "list":
        return "[" + ", ".join(ids) + "]"
    if style == "tuple":
        return "(" + ", ".join(ids) + ("," if len(ids) == 1 else "") + ")"
    if style == "set":
        return "{" + ", ".join(ids) + "}"
    if style == "strjoin":
        return repr(",".join(ids))
    if len(ids) != 1:
        sys.exit(4)
    return ids[0]


def render(style):
    if kind == "str":
        return repr(val)
    if kind == "raw":
        return val
    return fmt_ids(style, val.replace(",", " ").split())


if m:
    start = m.end()
    i, depth = start, 0
    while i < len(src):
        c = src[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "\n" and depth <= 0:
            break
        i += 1
    old = src[start:i].strip()
    if "getenv" in old or "environ" in old:
        sys.exit(3)
    if old.startswith("["):
        style = "list"
    elif old.startswith("("):
        style = "tuple"
    elif old.startswith("{"):
        style = "set"
    elif old[:1] in ("'", '"'):
        style = "strjoin"
    elif re.match(r"-?\d", old):
        style = "int"
    else:
        style = "list"
    out = src[:start] + render(style) + src[i:]
    print("  已替换原有变量 %s" % var)
else:
    lines = src.split("\n")
    idx = 0
    for n, l in enumerate(lines[:80]):
        if re.match(r"(import|from)\s", l):
            idx = n + 1
    if idx == 0:
        while idx < len(lines) and lines[idx].startswith("#") and (
            lines[idx].startswith("#!") or "coding" in lines[idx][:30]
        ):
            idx += 1
    else:
        depth = lines[idx - 1].count("(") - lines[idx - 1].count(")")
        while depth > 0 and idx < len(lines):
            depth += lines[idx].count("(") - lines[idx].count(")")
            idx += 1
    lines.insert(idx, "%s = %s" % (var, render(fallback)))
    out = "\n".join(lines)
    print("  文件中没有找到 %s，已在文件顶部新增" % var)

open(path, "w", encoding="utf-8").write(out)
PY
    rc=$?

    case $rc in
        0) ;;
        3) warn "变量 $var 是从环境变量读取的（getenv/environ），改 py 文件无效。请改用 .env 方式。"
           mv -f "$file.bak" "$file"; return 1 ;;
        4) err "该变量原本是单个数字，但你输入了多个 ID。请改成列表后再试，或只填一个 ID。"
           mv -f "$file.bak" "$file"; return 1 ;;
        *) err "写入失败（代码 $rc），已还原。"
           mv -f "$file.bak" "$file"; return 1 ;;
    esac

    if ! python3 -m py_compile "$file" 2>/dev/null; then
        err "写入后语法检查失败，已还原备份。"
        mv -f "$file.bak" "$file"
        return 1
    fi
    chmod 600 "$file"
    log "已写入 $var（备份：$(basename "$file").bak）"
}

# 写入 .env 文件：set_env_var 文件 KEY 值
set_env_var() {
    local file="$1" key="$2" value="$3"
    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
        err "变量名不合法：$key"
        return 1
    fi
    touch "$file"
    chmod 600 "$file"
    TG_VAL="$value" awk -v k="$key" '
        BEGIN { v = ENVIRON["TG_VAL"] }
        index($0, k "=") == 1 { print k "=" v; f = 1; next }
        { print }
        END { if (!f) print k "=" v }
    ' "$file" > "$file.tmp" && mv "$file.tmp" "$file"
    chmod 600 "$file"
    log "已写入 $key 到 $(basename "$file")"
}

suggest_vars() {
    local file="$1" names
    names="$(grep -iE '^[[:space:]]*[A-Za-z_0-9]*(token|admin|owner|user_?id|uid|chat)[A-Za-z_0-9]*[[:space:]]*(:[^=]*)?=[^=]' "$file" 2>/dev/null |
        sed -E 's/^[[:space:]]*([A-Za-z_0-9]+).*/\1/' | sort -u | tr '\n' ' ')"
    [[ -n "$names" ]] && echo "  ${CYAN}检测到可能相关的变量：${names}${RESET}"
    return 0
}

normalize_ids() {
    echo "$1" | sed 's/[,，;；]/ /g' | xargs
}

valid_name() {
    [[ "$1" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]
}

# write_value 模式(py|env) 文件 变量名 类型 新增格式 值
write_value() {
    local mode="$1" file="$2" name="$3" kind="$4" fallback="$5" value="$6"
    if [[ "$mode" == "py" ]]; then
        set_py_var "$file" "$name" "$kind" "$fallback" "$value"
    else
        if [[ "$kind" == "ids" ]]; then
            value="${value// /,}"
        fi
        set_env_var "$file" "$name" "$value"
    fi
}

cfg_token() {
    local mode="$1" file="$2" token
    heading "写入机器人 Token"
    [[ "$mode" == "py" ]] && suggest_vars "$file"
    ask TOKEN_VAR "Token 变量名" "$TOKEN_VAR"
    valid_name "$TOKEN_VAR" || { err "变量名不合法或为空。"; return; }
    rds token "请输入机器人 Token（输入不显示）"
    [[ -n "$token" ]] || { warn "已取消。"; return; }

    if [[ ! "$token" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]]; then
        warn "这个 Token 看起来不像标准格式（数字:字符串）。"
        confirm "仍然写入？" || return
    fi
    echo "  将写入：$TOKEN_VAR = $(mask "$token")"
    write_value "$mode" "$file" "$TOKEN_VAR" str str "$token" && save_conf
}

cfg_ids() {
    local mode="$1" file="$2" raw ids id
    heading "写入管理员 / 用户 ID"
    [[ "$mode" == "py" ]] && suggest_vars "$file"
    ask ID_VAR "用户ID 变量名" "$ID_VAR"
    valid_name "$ID_VAR" || { err "变量名不合法或为空。"; return; }
    rd raw "用户 ID（多个用空格或逗号分隔）"
    ids="$(normalize_ids "$raw")"
    [[ -n "$ids" ]] || { warn "已取消。"; return; }
    for id in $ids; do
        [[ "$id" =~ ^-?[0-9]+$ ]] || { err "ID 必须是数字：$id"; return; }
    done
    echo "  将写入：$ID_VAR = $ids"

    local fallback="list"
    if [[ "$mode" == "py" ]]; then
        if ! grep -qE "^[[:space:]]*${ID_VAR}[[:space:]]*(:[^=]*)?=" "$file"; then
            local f
            echo "  ${DIM}文件里没有该变量，新增格式：${RESET}"
            menu_item 1 "列表" "[111, 222]"
            menu_item 2 "单个数字" "111"
            menu_item 3 "逗号字符串" "'111,222'"
            rd f "请选择"
            case "$f" in
                1) fallback="list" ;;
                2) fallback="int" ;;
                3) fallback="strjoin" ;;
                *) err "无效选择，已取消。"; return ;;
            esac
        fi
    fi
    write_value "$mode" "$file" "$ID_VAR" ids "$fallback" "$ids" && save_conf
}

cfg_custom() {
    local mode="$1" file="$2" name t value
    heading "写入自定义变量"
    rd name "变量名"
    valid_name "$name" || { err "变量名不合法。"; return; }
    menu_item 1 "字符串" "写成 'xxx'"
    menu_item 2 "数字 / Python 表达式" "原样写入"
    rd t "请选择"
    [[ "$t" == "1" || "$t" == "2" ]] || { err "无效选择，已取消。"; return; }
    rd value "请输入值"
    [[ -n "$value" ]] || { warn "已取消。"; return; }
    if [[ "$t" == "2" ]]; then
        write_value "$mode" "$file" "$name" raw raw "$value"
    else
        write_value "$mode" "$file" "$name" str str "$value"
    fi
}

do_config() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        warn "目录不存在：$INSTALL_DIR，请先获取机器人代码。"
        return 1
    fi
    command -v python3 >/dev/null 2>&1 || ensure_python || return 1

    cls
    banner "写入密钥 / 用户ID" "$BOT_NAME"
    heading "写入位置"
    menu_item 1 "写入 py 脚本变量" "直接改代码，如 BOT_TOKEN = '...'"
    menu_item 2 "写入 .env 文件（推荐）" "代码需用 os.environ 读取"
    echo
    local m mode file
    rd m "请选择"
    case "$m" in
        1)
            mode="py"
            choose_py "请选择要写入的 py 脚本" || return 1
            file="$INSTALL_DIR/$CHOSEN_PY"
            ;;
        2)
            mode="env"
            file="$INSTALL_DIR/.env"
            ;;
        *) err "无效选择：$m"; return 1 ;;
    esac

    local c
    while true; do
        cls
        banner "写入密钥 / 用户ID" "$BOT_NAME"
        echo
        echo "  $(lab 目标)${file}"
        echo "  $(lab 方式)$([[ "$mode" == "py" ]] && echo "写入 py 变量" || echo "写入 .env")"
        echo
        menu_item 1 "机器人 Token" "输入时不显示"
        menu_item 2 "管理员 / 用户 ID" "支持多个"
        menu_item 3 "自定义变量" "任意名称"
        menu_item 0 "返回"
        echo
        rd c "请选择"
        case "$c" in
            1) cfg_token "$mode" "$file"; pause ;;
            2) cfg_ids "$mode" "$file"; pause ;;
            3) cfg_custom "$mode" "$file"; pause ;;
            0) break ;;
            *) warn "无效选择：$c"; sleep 1 ;;
        esac
    done
}

# ---------------------------------------------------------------- 4. 服务管理（开机自启）

service_create() {
    has_systemd || { err "系统没有 systemd。"; return 1; }
    [[ -d "$INSTALL_DIR" ]] || { warn "请先获取机器人代码。"; return 1; }
    ensure_main_py || return 1
    [[ -f "$INSTALL_DIR/$MAIN_PY" ]] || { err "主程序不存在：$INSTALL_DIR/$MAIN_PY"; return 1; }

    local py
    py="$(command -v python3)" || { err "没有找到 python3，请先安装环境。"; return 1; }

    cat >"/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Telegram Bot (${SERVICE_NAME})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=-${INSTALL_DIR}/.env
ExecStart=${py} -u ${MAIN_PY}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl restart "$SERVICE_NAME"
    save_conf
    sleep 2
    log "服务已创建并启动：${SERVICE_NAME}  $(svc_state "$SERVICE_NAME")  已设置开机自启"
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        warn "服务未正常运行，最近日志："
        journalctl -u "$SERVICE_NAME" -n 20 --no-pager 2>/dev/null || true
    fi
}

service_menu() {
    has_systemd || { err "系统没有 systemd。"; return 1; }
    local c
    while true; do
        cls
        banner "服务管理（开机自启）" "$BOT_NAME"
        echo
        echo "  $(lab 服务)${SERVICE_NAME}    $(svc_state "$SERVICE_NAME")"
        echo "  $(lab 主程序)${MAIN_PY:-未选择}"
        echo "  $(lab 自启)$(svc_enabled "$SERVICE_NAME")"
        echo
        menu_item 1 "创建 / 更新服务并启动" "含开机自启"
        menu_item 2 "启动"
        menu_item 3 "停止"
        menu_item 4 "重启"
        menu_item 5 "查看状态"
        menu_item 6 "最近日志" "50 行"
        menu_item 7 "实时日志" "Ctrl+C 退出"
        menu_item 8 "删除服务"
        menu_item 9 "重新选择主程序"
        menu_item 0 "返回"
        echo
        rd c "请选择"
        case "$c" in
            1) service_create; pause ;;
            2) systemctl start "$SERVICE_NAME" && log "已启动"; pause ;;
            3) systemctl stop "$SERVICE_NAME" && log "已停止"; pause ;;
            4) systemctl restart "$SERVICE_NAME" && log "已重启"; pause ;;
            5) systemctl status "$SERVICE_NAME" --no-pager -l || true; pause ;;
            6) journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true; pause ;;
            7) trap ':' INT
               journalctl -u "$SERVICE_NAME" -f -n 20 || true
               trap - INT ;;
            8) if confirm "确认删除服务 ${SERVICE_NAME}？"; then
                   systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
                   rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
                   systemctl daemon-reload
                   log "服务已删除。"
               fi
               pause ;;
            9) if choose_py "请选择机器人主程序"; then
                   MAIN_PY="$CHOSEN_PY"
                   save_conf
                   log "主程序已设为：$MAIN_PY（需在菜单 1 更新服务后生效）"
               fi
               pause ;;
            0) break ;;
            *) warn "无效选择：$c"; sleep 1 ;;
        esac
    done
}

# ---------------------------------------------------------------- 5. 状态

do_show() {
    heading "当前状态"
    echo "  $(lab 机器人)$BOT_NAME"
    if [[ "$SOURCE" == "repo" ]]; then
        echo "  $(lab 来源)仓库 $REPO_URL"
        echo "  $(lab 分支)${BRANCH:-（默认）}"
    else
        echo "  $(lab 来源)本地上传"
    fi
    echo "  $(lab 目录)$INSTALL_DIR $([[ -d "$INSTALL_DIR" ]] || echo '（不存在）')"
    echo "  $(lab 主程序)${MAIN_PY:-（未选择）}"
    echo "  $(lab Python)$(command -v python3 || echo 未安装)"
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        echo "  $(lab 版本)$(git -C "$INSTALL_DIR" log -1 --format='%h %cd %s' --date=short 2>/dev/null)"
    fi
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        echo "  $(lab .env)$(cut -d= -f1 "$INSTALL_DIR/.env" | tr '\n' ' ')"
    fi
    if has_systemd; then
        echo "  $(lab 服务)${SERVICE_NAME}  $(svc_state "$SERVICE_NAME")  开机自启: $(svc_enabled "$SERVICE_NAME")"
    fi
}

# ---------------------------------------------------------------- 6/7. 一键部署与卸载

do_all() {
    heading "一键部署"
    echo "  ${DIM}获取代码 → 安装环境 → 写入密钥/ID → 创建服务并开机自启${RESET}"
    echo
    if [[ "$SOURCE" == "repo" ]]; then
        do_pull || return 1
    else
        ensure_main_py || return 1
    fi
    do_deps || return 1
    echo
    log "接下来写入 Token 和用户 ID（完成后选 0 返回继续）"
    pause
    do_config
    cls
    banner "一键部署" "$BOT_NAME"
    echo
    service_create
}

do_uninstall() {
    heading "卸载"
    warn "将停止并删除服务 ${SERVICE_NAME}。"
    confirm "确认继续？" || { echo "  已取消。"; return; }

    if has_systemd; then
        systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
        systemctl daemon-reload
        log "服务已删除。"
    fi

    if [[ -d "$INSTALL_DIR" ]]; then
        warn "安装目录：$INSTALL_DIR（含代码和密钥）"
        local ans
        rd ans "如需同时删除该目录，请输入 yes 确认（其他输入=保留）"
        if [[ "$ans" == "yes" ]]; then
            if is_unsafe_dir "$INSTALL_DIR"; then
                err "拒绝删除系统目录：$INSTALL_DIR"
            else
                rm -rf -- "$INSTALL_DIR" && log "目录已删除。"
            fi
        else
            log "目录已保留。"
        fi
    fi

    if confirm "是否同时从机器人列表中移除「${BOT_NAME}」？"; then
        rm -f "$CONF_FILE"
        CONF_FILE=""
        log "已移除。"
        pause
        select_bot || exit 0
    fi
}

# ---------------------------------------------------------------- 主菜单

select_bot || { pause; exit 1; }

while true; do
    cls
    banner "Telegram Bot Manager" "服务器 IP: ${SERVER_IP:-未知}"
    echo
    echo "  $(lab 机器人)${BOLD}${BOT_NAME}${RESET}    $(lab 服务)$(svc_state "$SERVICE_NAME")"
    echo "  $(lab 目录)${INSTALL_DIR}"
    echo "  $(lab 主程序)${MAIN_PY:-未选择}    $(lab 来源)$([[ "$SOURCE" == "repo" ]] && echo 仓库 || echo 本地上传)"
    echo
    menu_item 1 "拉取 / 更新仓库" "克隆或更新机器人代码"
    menu_item 2 "安装环境" "python3 / pip / 依赖包"
    menu_item 3 "写入密钥 / 用户ID" "Token、管理员ID"
    menu_item 4 "服务管理（开机自启）" "创建 / 启停 / 日志"
    menu_item 5 "查看当前状态" "详细信息"
    menu_item 6 "一键部署" "代码→环境→密钥→自启"
    menu_item 7 "卸载" "删除服务 / 目录"
    menu_item 8 "切换 / 新增机器人" "多机器人管理"
    menu_item 0 "退出"
    echo
    rd CHOICE "请选择"
    case "$CHOICE" in
        1) do_pull; pause ;;
        2) do_deps; pause ;;
        3) do_config ;;
        4) service_menu ;;
        5) do_show; pause ;;
        6) do_all; pause ;;
        7) do_uninstall; pause ;;
        8) select_bot || pause ;;
        0|q|Q) echo; echo "  已退出。"; exit 0 ;;
        *) warn "无效选择：$CHOICE"; sleep 1 ;;
    esac
done
