#!/usr/bin/env bash
set -uo pipefail

# Telegram 机器人 安装管理工具
# 功能：获取机器人代码（仓库拉取 / 本地上传）/ 安装环境 / 写入密钥与用户ID / 配置开机自启 / 一键部署 / 卸载
# 说明：没有任何预设值，名称、目录、仓库地址、变量名、Token、ID 全部交互式输入；
#       不使用虚拟环境，依赖直接装到系统 python3。
# 用法：sudo bash tg-bot-installer.sh [仓库地址]

RED='\033[31m'
GREEN='\033[32m'
YELLOW='\033[33m'
CYAN='\033[36m'
RESET='\033[0m'

log()  { echo -e "${GREEN}[+]${RESET} $*"; }
warn() { echo -e "${YELLOW}[!]${RESET} $*"; }
err()  { echo -e "${RED}[x]${RESET} $*" >&2; }

[[ $EUID -eq 0 ]] || { err "请使用 root 运行：sudo bash $0"; exit 1; }

CONF_DIR="/etc/tgbot-installer"
CONF_FILE=""
BOT_NAME=""
NEW_URL="${1:-}"   # 可选：命令行传入的仓库地址，新增机器人时直接使用

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

# ---------------------------------------------------------------- 基础函数

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

# ask 变量名 "提示" ["已有值（作为回车默认）"]
ask() {
    local __v="$1" prompt="$2" def="${3:-}" ans
    if [[ -n "$def" ]]; then
        read -r -p "$prompt [$def]: " ans
        ans="${ans:-$def}"
    else
        read -r -p "$prompt: " ans
    fi
    printf -v "$__v" '%s' "$ans"
}

confirm() {
    local a
    read -r -p "$1 [y/N]: " a
    [[ "$a" =~ ^[Yy]$ ]]
}

mask() {
    local s="$1"
    if (( ${#s} <= 10 )); then
        echo "******"
    else
        echo "${s:0:6}…${s: -4}"
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

# 选择一个 py 文件，结果放到 CHOSEN_PY（相对路径），参数为提示语
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

    echo
    echo "$1"
    local i def="" mark sel
    for i in "${!PYFILES[@]}"; do
        mark=""
        if [[ -n "$MAIN_PY" && "${PYFILES[i]}" == "$MAIN_PY" ]]; then
            mark="  (当前主程序)"
            def=$((i + 1))
        fi
        printf "  %d) %s%s\n" $((i + 1)) "${PYFILES[i]}" "$mark"
    done
    if [[ -n "$def" ]]; then
        read -r -p "请选择编号 [${def}]: " sel
        sel="${sel:-$def}"
    else
        read -r -p "请选择编号: " sel
    fi
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
        choose_py "请选择机器人主程序（开机自启将运行它）：" || return 1
        MAIN_PY="$CHOSEN_PY"
    fi
    save_conf
}

# ---------------------------------------------------------------- 机器人选择 / 新增

# 新增机器人：名称、目录、代码来源全部手动输入
new_bot() {
    local name dir src src_choice url="" branch=""
    echo
    echo "名称同时用作 systemd 服务名，只能包含小写字母、数字、- 和 _"
    read -r -p "机器人名称: " name
    [[ "$name" =~ ^[a-z0-9_-]+$ ]] || { err "名称不合法。"; return 1; }
    if [[ -f "$CONF_DIR/$name.conf" ]]; then
        err "已存在同名机器人：$name"
        return 1
    fi

    read -r -p "安装目录（绝对路径，例如 /root/$name）: " dir
    dir="${dir%/}"
    if [[ "$dir" != /* ]]; then
        err "安装目录必须是绝对路径。"
        return 1
    fi
    if is_unsafe_dir "$dir"; then
        err "不能直接使用系统目录 $dir，请使用其下的子目录，例如 $dir/$name"
        return 1
    fi

    if [[ -n "$NEW_URL" ]]; then
        src="repo"
        url="$NEW_URL"
        echo "代码来源：仓库 $url"
    else
        echo
        echo "机器人代码来源："
        echo "  1) 从 GitHub 仓库拉取"
        echo "  2) 本地上传（我自己把代码传到安装目录）"
        read -r -p "请选择: " src_choice
        case "$src_choice" in
            1) src="repo" ;;
            2) src="local" ;;
            *) err "无效选择：$src_choice"; return 1 ;;
        esac
        if [[ "$src" == "repo" ]]; then
            read -r -p "仓库地址（如 https://github.com/用户/仓库.git）: " url
            [[ -n "$url" ]] || { err "仓库地址不能为空。"; return 1; }
        fi
    fi
    if [[ "$src" == "repo" ]]; then
        read -r -p "分支（留空=默认分支）: " branch
    fi

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
    log "已新增机器人「$name」  目录：$INSTALL_DIR  服务名：$SERVICE_NAME"
    if [[ "$src" == "local" ]]; then
        mkdir -p "$INSTALL_DIR"
        log "请把机器人代码上传到：$INSTALL_DIR"
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
        new_bot
        return $?
    fi

    echo
    echo "已有机器人："
    for i in "${!names[@]}"; do
        info="$( source "$CONF_DIR/${names[i]}.conf" 2>/dev/null
                 echo "${REPO_URL:-本地上传}  [$(systemctl is-active "${SERVICE_NAME:-${names[i]}}" 2>/dev/null || true)]" )"
        printf "  %d) %s   %s\n" $((i + 1)) "${names[i]}" "$info"
    done
    echo "  n) 新增机器人"

    local def=""
    [[ -n "$NEW_URL" ]] && def="n"
    if [[ -n "$def" ]]; then
        read -r -p "请选择 [${def}]: " sel
        sel="${sel:-$def}"
    else
        read -r -p "请选择: " sel
    fi

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
    echo
    if [[ -z "$REPO_URL" ]]; then
        warn "该机器人目前是本地上传模式，没有仓库地址。"
        read -r -p "请输入仓库地址（直接回车取消）: " REPO_URL
        [[ -n "$REPO_URL" ]] || { echo "已取消。"; return 1; }
        read -r -p "分支（留空=默认分支）: " BRANCH
        SOURCE="repo"
        save_conf
    fi
    echo "仓库：$REPO_URL"
    echo "目录：$INSTALL_DIR"

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
    if [[ ! -d "$INSTALL_DIR" ]]; then
        warn "目录不存在：$INSTALL_DIR，请先获取机器人代码。"
        return 1
    fi

    local extra
    echo
    read -r -p "额外系统软件包（空格分隔，如 traceroute nmap；留空跳过）: " extra
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
        read -r -p "请输入需要安装的 pip 包（空格分隔，如 python-telegram-bot requests；留空跳过）: " pkgs
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
    print("已替换原有变量 %s" % var)
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
    print("文件中没有找到 %s，已在文件顶部新增" % var)

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
    [[ -n "$names" ]] && echo -e "  ${CYAN}检测到可能相关的变量：${names}${RESET}"
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
    [[ "$mode" == "py" ]] && suggest_vars "$file"
    ask TOKEN_VAR "Token 变量名" "$TOKEN_VAR"
    valid_name "$TOKEN_VAR" || { err "变量名不合法或为空。"; return; }
    read -r -s -p "请输入机器人 Token（输入不显示）: " token
    echo
    [[ -n "$token" ]] || { warn "已取消。"; return; }

    if [[ ! "$token" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]]; then
        warn "这个 Token 看起来不像标准格式（数字:字符串）。"
        confirm "仍然写入？" || return
    fi
    echo "将写入：$TOKEN_VAR = $(mask "$token")"
    write_value "$mode" "$file" "$TOKEN_VAR" str str "$token" && save_conf
}

cfg_ids() {
    local mode="$1" file="$2" raw ids id
    [[ "$mode" == "py" ]] && suggest_vars "$file"
    ask ID_VAR "用户ID 变量名" "$ID_VAR"
    valid_name "$ID_VAR" || { err "变量名不合法或为空。"; return; }
    read -r -p "请输入用户 ID（多个用空格或逗号分隔）: " raw
    ids="$(normalize_ids "$raw")"
    [[ -n "$ids" ]] || { warn "已取消。"; return; }
    for id in $ids; do
        [[ "$id" =~ ^-?[0-9]+$ ]] || { err "ID 必须是数字：$id"; return; }
    done
    echo "将写入：$ID_VAR = $ids"

    local fallback="list"
    if [[ "$mode" == "py" ]]; then
        if ! grep -qE "^[[:space:]]*${ID_VAR}[[:space:]]*(:[^=]*)?=" "$file"; then
            local f
            read -r -p "文件里没有该变量，新增格式：1) 列表 [..]  2) 单个数字  3) 逗号字符串: " f
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
    read -r -p "变量名: " name
    valid_name "$name" || { err "变量名不合法。"; return; }
    echo "值类型：1) 字符串  2) 数字或 Python 表达式（原样写入）"
    read -r -p "请选择: " t
    [[ "$t" == "1" || "$t" == "2" ]] || { err "无效选择，已取消。"; return; }
    read -r -p "请输入值: " value
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

    echo
    echo "写入位置："
    echo "  1) 写入 py 脚本里的变量（直接改代码，如 BOT_TOKEN = '...'）"
    echo "  2) 写入 .env 文件（脚本需用 os.environ/getenv 读取；更新仓库不冲突，推荐）"
    local m mode file
    read -r -p "请选择: " m
    case "$m" in
        1)
            mode="py"
            choose_py "请选择要写入的 py 脚本：" || return 1
            file="$INSTALL_DIR/$CHOSEN_PY"
            ;;
        2)
            mode="env"
            file="$INSTALL_DIR/.env"
            ;;
        *) err "无效选择：$m"; return 1 ;;
    esac
    echo "目标文件：$file"

    local c
    while true; do
        echo
        echo "  1) 机器人 Token"
        echo "  2) 管理员 / 用户 ID"
        echo "  3) 自定义变量"
        echo "  0) 返回"
        read -r -p "请选择: " c
        case "$c" in
            1) cfg_token "$mode" "$file" ;;
            2) cfg_ids "$mode" "$file" ;;
            3) cfg_custom "$mode" "$file" ;;
            0) break ;;
            *) warn "无效选择：$c" ;;
        esac
    done
}

# ---------------------------------------------------------------- 4. 开机自启（systemd 服务）

has_systemd() {
    command -v systemctl >/dev/null 2>&1
}

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
    log "服务已创建并启动：${SERVICE_NAME}（状态：$(systemctl is-active "$SERVICE_NAME")，已设置开机自启）"
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        warn "服务未正常运行，最近日志："
        journalctl -u "$SERVICE_NAME" -n 20 --no-pager 2>/dev/null || true
    fi
}

service_menu() {
    has_systemd || { err "系统没有 systemd。"; return 1; }
    local c
    while true; do
        echo
        echo "服务：${SERVICE_NAME}  状态：$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo 未创建)  主程序：${MAIN_PY:-未选择}"
        echo "  1) 创建 / 更新服务并启动（含开机自启）"
        echo "  2) 启动"
        echo "  3) 停止"
        echo "  4) 重启"
        echo "  5) 查看状态"
        echo "  6) 查看最近日志（50 行）"
        echo "  7) 实时日志（Ctrl+C 退出）"
        echo "  8) 删除服务"
        echo "  9) 重新选择主程序"
        echo "  0) 返回"
        read -r -p "请选择: " c
        case "$c" in
            1) service_create ;;
            2) systemctl start "$SERVICE_NAME" && log "已启动" ;;
            3) systemctl stop "$SERVICE_NAME" && log "已停止" ;;
            4) systemctl restart "$SERVICE_NAME" && log "已重启" ;;
            5) systemctl status "$SERVICE_NAME" --no-pager -l || true ;;
            6) journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true ;;
            7) journalctl -u "$SERVICE_NAME" -f -n 20 || true ;;
            8) if confirm "确认删除服务 ${SERVICE_NAME}？"; then
                   systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
                   rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
                   systemctl daemon-reload
                   log "服务已删除。"
               fi ;;
            9) if choose_py "请选择机器人主程序："; then
                   MAIN_PY="$CHOSEN_PY"
                   save_conf
                   log "主程序已设为：$MAIN_PY（需在菜单 1 更新服务后生效）"
               fi ;;
            0) break ;;
            *) warn "无效选择：$c" ;;
        esac
    done
}

# ---------------------------------------------------------------- 5. 状态

do_show() {
    echo
    echo "----------------------------------------------"
    echo "机器人   : $BOT_NAME"
    echo "代码来源 : $([[ "$SOURCE" == "repo" ]] && echo "仓库 $REPO_URL" || echo "本地上传")"
    [[ "$SOURCE" == "repo" ]] && echo "分支     : ${BRANCH:-（默认）}"
    echo "安装目录 : $INSTALL_DIR $([[ -d "$INSTALL_DIR" ]] || echo '（不存在）')"
    echo "主程序   : ${MAIN_PY:-（未选择）}"
    echo "Python   : $(command -v python3 || echo 未安装)"
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        echo "当前版本 : $(git -C "$INSTALL_DIR" log -1 --format='%h %cd %s' --date=short 2>/dev/null)"
    fi
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        echo ".env 变量: $(cut -d= -f1 "$INSTALL_DIR/.env" | tr '\n' ' ')"
    fi
    if has_systemd; then
        echo "服务     : ${SERVICE_NAME}  $(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo 未创建)  开机自启: $(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || echo 否)"
    fi
    echo "----------------------------------------------"
}

# ---------------------------------------------------------------- 6/7. 一键部署与卸载

do_all() {
    echo
    echo "一键部署流程：获取代码 → 安装环境 → 写入密钥/ID → 创建服务并开机自启"
    if [[ "$SOURCE" == "repo" ]]; then
        do_pull || return 1
    else
        ensure_main_py || return 1
    fi
    do_deps || return 1
    echo
    log "接下来写入 Token 和用户 ID（完成后选 0 返回继续）"
    do_config
    echo
    service_create
}

do_uninstall() {
    echo
    warn "将停止并删除服务 ${SERVICE_NAME}。"
    confirm "确认继续？" || { echo "已取消。"; return; }

    if has_systemd; then
        systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
        systemctl daemon-reload
        log "服务已删除。"
    fi

    if [[ -d "$INSTALL_DIR" ]]; then
        warn "安装目录：$INSTALL_DIR（含代码和密钥）"
        local ans
        read -r -p "如需同时删除该目录，请输入 yes 确认（其他输入=保留）: " ans
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
        select_bot || exit 0
    fi
}

# ---------------------------------------------------------------- 主菜单

select_bot || exit 1

while true; do
    echo
    echo "=============================================="
    echo "        Telegram 机器人 安装管理工具"
    echo "        当前机器人：${BOT_NAME}"
    echo "=============================================="
    echo "  1) 拉取 / 更新仓库"
    echo "  2) 安装环境"
    echo "  3) 写入密钥 / 用户ID"
    echo "  4) 服务管理（开机自启）"
    echo "  5) 查看当前状态"
    echo "  6) 一键部署（获取代码 → 环境 → 密钥 → 自启）"
    echo "  7) 卸载"
    echo "  8) 切换 / 新增机器人"
    echo "  0) 退出"
    echo "----------------------------------------------"
    read -r -p "请选择: " CHOICE
    case "$CHOICE" in
        1) do_pull ;;
        2) do_deps ;;
        3) do_config ;;
        4) service_menu ;;
        5) do_show ;;
        6) do_all ;;
        7) do_uninstall ;;
        8) select_bot ;;
        0|q|Q) echo "已退出。"; exit 0 ;;
        *) warn "无效选择：$CHOICE" ;;
    esac
done
