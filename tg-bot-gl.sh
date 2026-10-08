#!/usr/bin/env bash
set -uo pipefail

# Telegram 机器人 安装管理工具
# 流程：创建安装目录 → 添加机器人（仓库拉取 → 自动装依赖 → 写入密钥/ID → 启动并开机自启）→ 管理（启动/停止/自启/日志/卸载）
# 说明：名称、目录、仓库地址、Token、ID 全部交互式输入；
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
    local __v="$1" text="$2" def="${3:-}" __ans
    if [[ -n "$def" ]]; then
        read -r -p "  ${CYAN}▸${RESET} ${text} ${DIM}[${def}]${RESET}: " __ans
        __ans="${__ans:-$def}"
    else
        read -r -p "  ${CYAN}▸${RESET} ${text}: " __ans
    fi
    printf -v "$__v" '%s' "$__ans"
}

# 隐藏输入
rds() {
    local __v="$1" text="$2" __ans
    read -r -s -p "  ${CYAN}▸${RESET} ${text}: " __ans
    echo
    printf -v "$__v" '%s' "$__ans"
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

# ---------------------------------------------------------------- 1. 拉取 / 更新仓库

do_pull() {
    heading "拉取 / 更新仓库"
    if [[ -z "$REPO_URL" ]]; then
        warn "该机器人还没有设置仓库地址。"
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

# ---------------------------------------------------------------- 依赖自动检测

# Python 侧检测脚本
#   py_detect scan 目录          输出 PIP=缺少的pip包  BIN=缺少的系统工具
#   py_detect pipname 模块 [目录] 输出模块对应的pip包名
py_detect() {
    python3 - "$@" <<'PY'
import ast, importlib.util, os, re, shutil, sys

PIPMAP = {
    "telegram": "python-telegram-bot", "PIL": "Pillow", "yaml": "PyYAML",
    "cv2": "opencv-python-headless", "bs4": "beautifulsoup4", "dotenv": "python-dotenv",
    "sklearn": "scikit-learn", "Crypto": "pycryptodome", "Cryptodome": "pycryptodomex",
    "dateutil": "python-dateutil", "serial": "pyserial", "OpenSSL": "pyOpenSSL",
    "jwt": "PyJWT", "magic": "python-magic", "attr": "attrs", "MySQLdb": "mysqlclient",
    "psycopg2": "psycopg2-binary", "socks": "PySocks", "nacl": "PyNaCl",
    "websocket": "websocket-client", "git": "GitPython", "docx": "python-docx",
    "pptx": "python-pptx", "skimage": "scikit-image", "zmq": "pyzmq", "usb": "pyusb",
    "telebot": "pyTelegramBotAPI", "telethon": "Telethon", "fake_useragent": "fake-useragent",
    "speedtest": "speedtest-cli", "ruamel": "ruamel.yaml", "Levenshtein": "python-Levenshtein",
}
WIN_ONLY = {"msvcrt", "winreg", "_winapi", "nt", "winsound", "_msi", "_winreg"}
EXTRAS = {
    "job-queue": (r"job_queue|JobQueue", "apscheduler"),
    "rate-limiter": (r"AIORateLimiter|rate_limiter", "aiolimiter"),
    "webhooks": (r"run_webhook|webhook_url", "tornado"),
}
KNOWN = {"traceroute", "tracepath", "mtr", "nmap", "ping", "ping6", "dig", "nslookup",
         "host", "curl", "wget", "whois", "ss", "ip", "ffmpeg", "iperf3"}


def gather(root):
    files = []
    base = root.rstrip(os.sep).count(os.sep)
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in {".git", "__pycache__", "venv", ".venv", "node_modules"}]
        if dp.count(os.sep) - base >= 2:
            dn[:] = []
        for f in fn:
            if f.endswith(".py"):
                files.append(os.path.join(dp, f))
    mods, local, text = set(), set(), ""
    for f in files:
        local.add(os.path.splitext(os.path.basename(f))[0])
        try:
            src = open(f, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        text += src + "\n"
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
    try:
        for d in os.listdir(root):
            if os.path.isdir(os.path.join(root, d)):
                local.add(d)
    except OSError:
        pass
    return mods, local, text


def exists(m):
    try:
        return importlib.util.find_spec(m) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def ptb_used(text):
    return [(n, mod) for n, (pat, mod) in EXTRAS.items() if re.search(pat, text)]


def ptb_spec(text):
    names = [n for n, _ in ptb_used(text)]
    return "python-telegram-bot" + ("[" + ",".join(names) + "]" if names else "")


mode = sys.argv[1]

if mode == "pipname":
    name = sys.argv[2]
    root = sys.argv[3] if len(sys.argv) > 3 else ""
    if name == "telegram" and root:
        print(ptb_spec(gather(root)[2]))
    else:
        print(PIPMAP.get(name, name))
    sys.exit(0)

root = sys.argv[2]
mods, local, text = gather(root)
stdlib = getattr(sys, "stdlib_module_names", set())

pip = []
for m in sorted(mods):
    if m in local or m in stdlib or m in WIN_ONLY or m == "__future__":
        continue
    if exists(m):
        continue
    pip.append(PIPMAP.get(m, m))

if "telegram" in mods:
    used = ptb_used(text)
    need = (not exists("telegram")) or any(not exists(mod) for _, mod in used)
    pip = [p for p in pip if p != "python-telegram-bot"]
    if need:
        pip.insert(0, ptb_spec(text))

tools = set(re.findall(
    r"""(?:subprocess\.(?:run|Popen|check_output|check_call|call|getoutput|getstatusoutput)"""
    r"""|create_subprocess_exec|create_subprocess_shell|shutil\.which|os\.system|os\.popen)"""
    r"""\(\s*\[?\s*[fF]?["']\s*([A-Za-z0-9_.+-]+)""", text))
tools |= set(re.findall(
    r"""\[\s*[fF]?["'](traceroute|tracepath|mtr|nmap|ping6?|dig|nslookup|whois|iperf3|ffmpeg|curl|wget)["']\s*,""",
    text))
bins = sorted(t for t in tools if t in KNOWN and shutil.which(t) is None)

print("PIP=" + " ".join(pip))
print("BIN=" + " ".join(bins))
PY
}

# 系统工具名 → 当前系统的软件包名
bin_pkg() {
    local apt="" rpm=""
    case "$1" in
        traceroute) apt=traceroute;      rpm=traceroute ;;
        tracepath)  apt=iputils-tracepath; rpm=iputils ;;
        mtr)        apt=mtr-tiny;        rpm=mtr ;;
        nmap)       apt=nmap;            rpm=nmap ;;
        ping|ping6) apt=iputils-ping;    rpm=iputils ;;
        dig|nslookup|host) apt=dnsutils; rpm=bind-utils ;;
        curl)       apt=curl;            rpm=curl ;;
        wget)       apt=wget;            rpm=wget ;;
        whois)      apt=whois;           rpm=whois ;;
        ss|ip)      apt=iproute2;        rpm=iproute ;;
        ffmpeg)     apt=ffmpeg;          rpm=ffmpeg ;;
        iperf3)     apt=iperf3;          rpm=iperf3 ;;
        *) return 1 ;;
    esac
    if command -v apt-get >/dev/null 2>&1; then echo "$apt"; else echo "$rpm"; fi
}

# 扫描机器人代码，自动安装缺少的系统工具和 Python 包
deps_auto() {
    local out pip_list bin_list t p arr=()
    local pkgs=()
    out="$(py_detect scan "$INSTALL_DIR" 2>/dev/null)" || { warn "依赖检测失败。"; return 1; }
    pip_list="$(sed -n 's/^PIP=//p' <<< "$out")"
    bin_list="$(sed -n 's/^BIN=//p' <<< "$out")"

    if [[ -n "$bin_list" ]]; then
        for t in $bin_list; do
            p="$(bin_pkg "$t")" && pkgs+=("$p")
        done
        if (( ${#pkgs[@]} > 0 )); then
            log "检测到需要系统工具：$bin_list，正在安装..."
            pkg_install "${pkgs[@]}" || warn "部分系统工具安装失败，继续。"
        fi
    fi

    if [[ -n "$pip_list" ]]; then
        log "检测到需要 Python 包：$pip_list，正在安装..."
        read -ra arr <<< "$pip_list"
        pip_install "${arr[@]}" || { err "Python 包安装失败"; return 1; }
    else
        log "Python 依赖已满足。"
    fi
}

do_deps() {
    heading "安装环境"
    if [[ ! -d "$INSTALL_DIR" ]]; then
        warn "目录不存在：$INSTALL_DIR，请先获取机器人代码。"
        return 1
    fi

    ensure_python || return 1

    if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
        log "正在安装 requirements.txt 依赖（系统 Python）..."
        pip_install -r "$INSTALL_DIR/requirements.txt" || { err "依赖安装失败"; return 1; }
    else
        log "没有 requirements.txt，自动扫描代码检测依赖..."
    fi

    deps_auto || return 1
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

# 扫描代码里实际使用的 Token / 用户ID 变量名
#   detect_vars 模式(py|env) 文件 → 输出 TOKEN=xxx  ID=xxx（没找到则为空）
detect_vars() {
    python3 - "$INSTALL_DIR" "$1" "$2" <<'PY' 2>/dev/null
import ast, os, re, sys
root, mode, target = sys.argv[1:4]
files = []
if mode == "py" and os.path.isfile(target):
    files = [target]
else:
    base = root.rstrip(os.sep).count(os.sep)
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in {".git", "__pycache__", "venv", ".venv", "node_modules"}]
        if dp.count(os.sep) - base >= 2:
            dn[:] = []
        files += [os.path.join(dp, f) for f in fn if f.endswith(".py")]

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TOK = re.compile(r"token", re.I)
IDS = re.compile(r"admin|owner|user_?ids?|^uids?$|chat_?ids?|allowed|whitelist", re.I)
PREF_T = ["BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "TOKEN", "API_TOKEN"]
PREF_I = ["ADMIN_IDS", "ADMIN_ID", "OWNER_ID", "OWNER_IDS", "ADMIN_USER_IDS", "ALLOWED_USERS", "USER_IDS", "CHAT_ID"]

found = {}   # name -> count
def add(n):
    found[n] = found.get(n, 0) + 1

def is_env(node):
    try:
        d = ast.dump(node)
    except Exception:
        return False
    return "environ" in d or "getenv" in d

for f in files:
    try:
        tree = ast.parse(open(f, encoding="utf-8", errors="ignore").read())
    except (SyntaxError, OSError):
        continue
    for node in ast.walk(tree):
        if mode == "env":
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
               and isinstance(node.args[0].value, str) and NAME.match(node.args[0].value) \
               and isinstance(node.func, ast.Attribute) and node.func.attr in ("getenv", "get") \
               and is_env(node.func):
                add(node.args[0].value)
            elif isinstance(node, ast.Subscript) and is_env(node.value):
                sl = node.slice
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str) and NAME.match(sl.value):
                    add(sl.value)
        else:
            tgt = val = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                tgt, val = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                tgt, val = node.target.id, node.value
            if tgt and not is_env(val):
                add(tgt)

def pick(rx, pref, bad=None):
    names = [n for n in found if rx.search(n) and not (bad and bad.search(n))]
    for p in pref:
        for n in names:
            if n.upper() == p:
                return n
    names.sort(key=lambda n: (-found[n], n))
    return names[0] if names else ""

print("TOKEN=" + pick(TOK, PREF_T))
print("ID=" + pick(IDS, PREF_I, TOK))
PY
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
    local mode="$1" file="$2" token name
    heading "写入机器人 Token"
    rds token "请输入机器人 Token（输入不显示）"
    [[ -n "$token" ]] || { warn "已取消。"; return; }

    if [[ ! "$token" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]]; then
        warn "这个 Token 看起来不像标准格式（数字:字符串）。"
        confirm "仍然写入？" || return
    fi

    echo
    local det def
    det="$(detect_vars "$mode" "$file" | sed -n 's/^TOKEN=//p')"
    def="${det:-${TOKEN_VAR:-BOT_TOKEN}}"
    if [[ -n "$det" ]]; then
        echo "  ${GREEN}已从代码中扫描到 Token 变量：${det}${RESET}"
    else
        [[ "$mode" == "py" ]] && suggest_vars "$file"
        echo "  ${DIM}没有扫描到明确的变量名，使用默认值${RESET}"
    fi
    echo "  ${DIM}直接回车使用括号内的名称${RESET}"
    ask name "变量名" "$def"
    if ! valid_name "$name"; then
        err "变量名不合法，应形如 BOT_TOKEN（只能含字母、数字、下划线，不能以数字开头）。"
        return
    fi
    TOKEN_VAR="$name"
    echo "  将写入：$TOKEN_VAR = $(mask "$token")"
    write_value "$mode" "$file" "$TOKEN_VAR" str str "$token" && save_conf
}

cfg_ids() {
    local mode="$1" file="$2" raw ids id name
    heading "写入管理员 / 用户 ID"
    rd raw "用户 ID（多个用空格或逗号分隔）"
    ids="$(normalize_ids "$raw")"
    [[ -n "$ids" ]] || { warn "已取消。"; return; }
    for id in $ids; do
        [[ "$id" =~ ^-?[0-9]+$ ]] || { err "ID 必须是数字：$id"; return; }
    done

    echo
    local det def
    det="$(detect_vars "$mode" "$file" | sed -n 's/^ID=//p')"
    def="${det:-${ID_VAR:-ADMIN_IDS}}"
    if [[ -n "$det" ]]; then
        echo "  ${GREEN}已从代码中扫描到用户ID变量：${det}${RESET}"
    else
        [[ "$mode" == "py" ]] && suggest_vars "$file"
        echo "  ${DIM}没有扫描到明确的变量名，使用默认值${RESET}"
    fi
    echo "  ${DIM}直接回车使用括号内的名称${RESET}"
    ask name "变量名" "$def"
    if ! valid_name "$name"; then
        if [[ "$name" =~ ^[0-9,\ -]+$ ]]; then
            err "这看起来是数字 ID，不是变量名。变量名应形如 ADMIN_IDS。"
        else
            err "变量名不合法，应形如 ADMIN_IDS（只能含字母、数字、下划线，不能以数字开头）。"
        fi
        return
    fi
    ID_VAR="$name"
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

    ensure_python || return 1
    local py
    py="$(command -v python3)" || { err "没有找到 python3，请先安装环境。"; return 1; }

    log "检查依赖..."
    deps_auto || warn "部分依赖安装失败，仍会尝试启动。"

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
    save_conf

    # 启动；如果因缺少模块崩溃，自动补装后重试
    local round mod pkg
    for round in 1 2 3 4; do
        systemctl restart "$SERVICE_NAME"
        sleep 4
        systemctl is-active --quiet "$SERVICE_NAME" && break
        mod="$(journalctl -u "$SERVICE_NAME" -n 40 --no-pager 2>/dev/null |
            grep -oE "No module named '[^']+'" | tail -1 | sed -E "s/No module named '([^'.]+).*/\1/")"
        [[ -n "$mod" ]] || break
        pkg="$(py_detect pipname "$mod" "$INSTALL_DIR")"
        warn "启动时缺少模块 $mod，自动安装 $pkg 后重试..."
        pip_install "$pkg" || break
    done

    log "服务已创建：${SERVICE_NAME}  $(svc_state "$SERVICE_NAME")  已设置开机自启"
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        warn "服务未正常运行，最近日志："
        journalctl -u "$SERVICE_NAME" -n 20 --no-pager 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------- 安装目录 / 机器人列表

DIRS_FILE="$CONF_DIR/dirs.list"
DIRS=()
BOTS=()
CREATED_DIR=""
PICKED_DIR=""
BOT_GONE=0

list_dirs() {
    DIRS=()
    [[ -f "$DIRS_FILE" ]] && mapfile -t DIRS < <(awk 'NF && !seen[$0]++' "$DIRS_FILE")
    return 0
}

list_bots() {
    BOTS=()
    local f
    if [[ -d "$CONF_DIR" ]]; then
        for f in "$CONF_DIR"/*.conf; do
            [[ -f "$f" ]] && BOTS+=("$(basename "$f" .conf)")
        done
    fi
    return 0
}

# 哪个机器人在用这个目录（输出机器人名，没有则为空）
dir_user() {
    local f
    [[ -d "$CONF_DIR" ]] || return 0
    for f in "$CONF_DIR"/*.conf; do
        [[ -f "$f" ]] || continue
        if ( INSTALL_DIR=""; source "$f" 2>/dev/null; [[ "$INSTALL_DIR" == "$1" ]] ); then
            basename "$f" .conf
            return 0
        fi
    done
}

dir_is_empty() {
    [[ ! -d "$1" || -z "$(ls -A "$1" 2>/dev/null)" ]]
}

# 创建安装目录，成功后目录路径放到 CREATED_DIR
create_dir() {
    CREATED_DIR=""
    local dir
    heading "创建安装目录"
    echo "  ${DIM}绝对路径，例如 /root/mybot；不能直接用 /root、/opt 等系统目录${RESET}"
    while true; do
        rd dir "目录（直接回车取消）"
        if [[ -z "$dir" ]]; then warn "已取消。"; return 1; fi
        dir="${dir%/}"
        if [[ "$dir" != /* ]]; then
            err "必须是绝对路径（以 / 开头）。"
            continue
        fi
        if is_unsafe_dir "$dir"; then
            err "不能直接使用系统目录 $dir，请使用其下的子目录，例如 $dir/mybot"
            continue
        fi
        break
    done
    if [[ -e "$dir" && ! -d "$dir" ]]; then
        err "$dir 已存在且不是目录。"
        return 1
    fi
    mkdir -p "$dir" || { err "创建失败：$dir"; return 1; }
    mkdir -p "$CONF_DIR" && chmod 700 "$CONF_DIR"
    grep -qxF -- "$dir" "$DIRS_FILE" 2>/dev/null || echo "$dir" >>"$DIRS_FILE"
    CREATED_DIR="$dir"
    log "目录已创建：$dir"
}

# 主菜单 1：创建安装目录
menu_create_dir() {
    cls
    banner "创建安装目录" "机器人代码存放位置"
    list_dirs
    if (( ${#DIRS[@]} > 0 )); then
        heading "已创建的目录"
        local d u state
        for d in "${DIRS[@]}"; do
            u="$(dir_user "$d")"
            if [[ -n "$u" ]]; then state="机器人：$u"
            elif dir_is_empty "$d"; then state="空闲"
            else state="非空"; fi
            printf '  %s  %s\n' "$(padr "$d" 30)" "${DIM}${state}${RESET}"
        done
    fi
    create_dir
}

# 选择一个空闲目录，结果放到 PICKED_DIR
pick_dir() {
    PICKED_DIR=""
    list_dirs
    if (( ${#DIRS[@]} == 0 )); then
        warn "还没有安装目录，先创建一个。"
        create_dir || return 1
        PICKED_DIR="$CREATED_DIR"
    else
        local i d u state free=() sel def=""
        for i in "${!DIRS[@]}"; do
            d="${DIRS[i]}"; u="$(dir_user "$d")"
            if [[ -n "$u" ]]; then state="已被「$u」使用"
            elif dir_is_empty "$d"; then state="空闲"; free+=("$((i + 1))")
            else state="非空"; fi
            menu_item $((i + 1)) "$d" "$state"
        done
        menu_item n "新建目录"
        echo
        (( ${#free[@]} == 1 )) && def="${free[0]}"
        rd sel "请选择（直接回车取消）" "$def"
        [[ -n "$sel" ]] || { warn "已取消。"; return 1; }
        if [[ "$sel" =~ ^[Nn]$ ]]; then
            create_dir || return 1
            PICKED_DIR="$CREATED_DIR"
        elif [[ "$sel" =~ ^[0-9]+$ ]] && (( sel >= 1 && sel <= ${#DIRS[@]} )); then
            PICKED_DIR="${DIRS[sel-1]}"
        else
            err "无效选择：$sel"; return 1
        fi
    fi
    if [[ -n "$(dir_user "$PICKED_DIR")" ]]; then
        err "该目录已被机器人「$(dir_user "$PICKED_DIR")」使用。"
        return 1
    fi
    if ! dir_is_empty "$PICKED_DIR"; then
        err "$PICKED_DIR 不是空目录，请选择空目录或新建一个。"
        return 1
    fi
    mkdir -p "$PICKED_DIR"
}

# ---------------------------------------------------------------- 添加机器人

# 按代码决定写入方式：代码从环境变量读取就写 .env，否则写主程序里的变量
quick_keys() {
    local mode file
    if [[ -n "$(detect_vars env "" | sed -n 's/^TOKEN=//p')" ]]; then
        mode="env"; file="$INSTALL_DIR/.env"
        echo "  ${DIM}代码通过环境变量读取密钥，将写入 .env（systemd 自动加载）${RESET}"
    else
        mode="py"; file="$INSTALL_DIR/$MAIN_PY"
        echo "  ${DIM}代码里没有读取环境变量，将直接写入 ${MAIN_PY} 里的变量${RESET}"
    fi
    cfg_token "$mode" "$file"
    cfg_ids "$mode" "$file"
}

add_bot() {
    local name url branch

    cls
    banner "添加机器人" "仓库拉取 → 密钥 → 启动"

    heading "[1/3] 机器人名称"
    echo "  ${DIM}用作 systemd 服务名；仅限小写字母、数字、- 和 _，例如 navbot${RESET}"
    while true; do
        rd name "名称（直接回车取消）"
        if [[ -z "$name" ]]; then warn "已取消。"; return 1; fi
        if [[ ! "$name" =~ ^[a-z0-9_-]+$ ]]; then err "名称不合法，请重新输入。"; continue; fi
        if [[ -f "$CONF_DIR/$name.conf" ]]; then err "已存在同名机器人：$name，请换一个。"; continue; fi
        break
    done

    heading "[2/3] 安装目录"
    pick_dir || return 1
    local dir="$PICKED_DIR"

    heading "[3/3] 机器人仓库"
    while true; do
        rd url "仓库地址（如 https://github.com/用户/仓库.git；回车取消）" "$NEW_URL"
        if [[ -z "$url" ]]; then warn "已取消。"; return 1; fi
        break
    done
    rd branch "分支（留空=默认分支）"

    heading "请确认"
    echo "  $(lab 名称)$name"
    echo "  $(lab 目录)$dir"
    echo "  $(lab 仓库)$url"
    echo "  $(lab 分支)${branch:-默认分支}"
    echo
    confirm_y "确认添加并开始部署？" || { warn "已取消。"; return 1; }

    reset_defaults
    BOT_NAME="$name"
    INSTALL_DIR="$dir"
    SERVICE_NAME="$name"
    SOURCE="repo"
    REPO_URL="$url"
    BRANCH="$branch"
    CONF_FILE="$CONF_DIR/$name.conf"
    NEW_URL=""
    save_conf
    log "已添加机器人「$name」"

    heading "拉取代码"
    do_pull || { warn "拉取失败。到「管理机器人」里选择它，可重新更新仓库。"; return 1; }

    heading "安装环境（自动）"
    do_deps || warn "环境安装有问题，稍后可在「管理机器人」里选「修复依赖」。"

    heading "写入密钥 / 用户ID"
    quick_keys

    heading "启动机器人"
    if confirm_y "现在启动并设置开机自启？"; then
        service_create
    else
        echo "  已跳过，之后可在「管理机器人」里启动。"
    fi
}

# ---------------------------------------------------------------- 管理机器人

# 选一个已有机器人并载入配置
pick_bot() {
    list_bots
    if (( ${#BOTS[@]} == 0 )); then
        warn "还没有机器人，请先用主菜单 2 添加。"
        return 1
    fi
    cls
    banner "管理机器人" "选择一个"
    heading "机器人列表"
    local i info sel
    for i in "${!BOTS[@]}"; do
        info="$( source "$CONF_DIR/${BOTS[i]}.conf" 2>/dev/null
                 printf '%s  %s' "$(svc_state "${SERVICE_NAME:-${BOTS[i]}}")" "${DIM}${INSTALL_DIR}${RESET}" )"
        printf '  %s) %s  %s\n' "${YELLOW}$((i + 1))${RESET}" "$(padr "${BOTS[i]}" 16)" "$info"
    done
    echo
    local def=""
    (( ${#BOTS[@]} == 1 )) && def="1"
    rd sel "请选择编号（直接回车取消）" "$def"
    [[ -n "$sel" ]] || return 1
    if [[ ! "$sel" =~ ^[0-9]+$ ]] || (( sel < 1 || sel > ${#BOTS[@]} )); then
        err "无效选择：$sel"
        return 1
    fi
    reset_defaults
    BOT_NAME="${BOTS[sel-1]}"
    CONF_FILE="$CONF_DIR/$BOT_NAME.conf"
    load_conf
    [[ -n "$SERVICE_NAME" ]] || SERVICE_NAME="$BOT_NAME"
}

unit_exists() {
    [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]]
}

bot_start() {
    has_systemd || { err "系统没有 systemd。"; return 1; }
    if ! unit_exists; then
        log "服务还没创建，现在创建并启动..."
        service_create
        return
    fi
    systemctl start "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log "已启动：${SERVICE_NAME}"
    else
        err "启动失败，最近日志："
        journalctl -u "$SERVICE_NAME" -n 20 --no-pager 2>/dev/null || true
    fi
}

bot_stop() {
    unit_exists || { warn "服务还没创建。"; return 1; }
    systemctl stop "$SERVICE_NAME" && log "已停止：${SERVICE_NAME}（开机自启设置不变）"
}

bot_restart() {
    unit_exists || { warn "服务还没创建，请先启动。"; return 1; }
    systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log "已重启：${SERVICE_NAME}"
    else
        err "重启后未运行，最近日志："
        journalctl -u "$SERVICE_NAME" -n 20 --no-pager 2>/dev/null || true
    fi
}

bot_autostart() {
    unit_exists || { warn "服务还没创建，请先启动机器人。"; return 1; }
    if [[ "$(svc_enabled "$SERVICE_NAME")" == "已设置" ]]; then
        systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 && log "已关闭开机自启（当前运行状态不变）"
    else
        systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 && log "已开启开机自启"
    fi
}

# 更新仓库，服务在运行就询问是否重启
bot_update() {
    do_pull || return 1
    if unit_exists && systemctl is-active --quiet "$SERVICE_NAME"; then
        confirm_y "代码已更新，重启机器人使其生效？" && bot_restart
    fi
}

do_show() {
    heading "当前状态"
    echo "  $(lab 机器人)$BOT_NAME"
    echo "  $(lab 仓库)${REPO_URL:-（未设置）}"
    echo "  $(lab 分支)${BRANCH:-（默认）}"
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
                if [[ -f "$DIRS_FILE" ]]; then
                    grep -vxF -- "$INSTALL_DIR" "$DIRS_FILE" >"$DIRS_FILE.tmp" || true
                    mv "$DIRS_FILE.tmp" "$DIRS_FILE"
                fi
            fi
        else
            log "目录已保留（可在添加机器人时重新使用，需先清空）。"
        fi
    fi

    if confirm "是否同时从机器人列表中移除「${BOT_NAME}」？"; then
        rm -f "$CONF_FILE"
        CONF_FILE=""
        BOT_GONE=1
        log "已移除。"
    fi
}

bot_menu() {
    local c
    BOT_GONE=0
    while (( ! BOT_GONE )); do
        cls
        banner "管理机器人" "$BOT_NAME"
        echo
        echo "  $(lab 状态)$(svc_state "$SERVICE_NAME")    $(lab 自启)$(svc_enabled "$SERVICE_NAME")"
        echo "  $(lab 目录)${INSTALL_DIR}"
        echo "  $(lab 主程序)${MAIN_PY:-未选择}"
        echo "  $(lab 仓库)${REPO_URL:-未设置}"
        echo
        menu_item 1 "启动机器人" "没有服务时自动创建并设为开机自启"
        menu_item 2 "停止机器人" "停止运行，不会自动重启"
        menu_item 3 "重启机器人"
        menu_item 4 "开机自启 开 / 关" "切换"
        menu_item 5 "写入密钥 / 用户ID" "Token、管理员ID"
        menu_item 6 "更新仓库代码" "有更新后可选重启"
        menu_item 7 "修复依赖" "自动检测并安装缺少的包"
        menu_item 8 "查看状态"
        menu_item 9 "最近日志" "50 行"
        menu_item a "实时日志" "Ctrl+C 退出"
        menu_item m "重新选择主程序"
        menu_item d "卸载此机器人" "删除服务 / 目录"
        menu_item 0 "返回"
        echo
        rd c "请选择"
        case "$c" in
            1) bot_start; pause ;;
            2) bot_stop; pause ;;
            3) bot_restart; pause ;;
            4) bot_autostart; pause ;;
            5) do_config ;;
            6) bot_update; pause ;;
            7) do_deps; pause ;;
            8) do_show; pause ;;
            9) journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true; pause ;;
            a|A) trap ':' INT
               journalctl -u "$SERVICE_NAME" -f -n 20 || true
               trap - INT ;;
            m|M) if choose_py "请选择机器人主程序"; then
                   MAIN_PY="$CHOSEN_PY"
                   save_conf
                   log "主程序已设为：$MAIN_PY（需先停止再启动/重建服务后生效）"
                   unit_exists && confirm_y "现在重建服务使其生效？" && service_create
               fi
               pause ;;
            d|D) do_uninstall; pause ;;
            0) break ;;
            *) warn "无效选择：$c"; sleep 1 ;;
        esac
    done
}

# ---------------------------------------------------------------- 主菜单

while true; do
    list_dirs; list_bots
    cls
    banner "Telegram Bot Manager" "服务器 IP: ${SERVER_IP:-未知}"
    echo
    echo "  $(lab 目录)${#DIRS[@]} 个    $(lab 机器人)${#BOTS[@]} 个"
    echo
    menu_item 1 "创建安装目录" "机器人代码放哪里"
    menu_item 2 "添加机器人" "仓库拉取→装依赖→密钥→启动自启"
    menu_item 3 "管理机器人" "启动 / 停止 / 自启 / 日志 / 卸载"
    menu_item 0 "退出"
    echo
    rd CHOICE "请选择" "$([[ -n "$NEW_URL" ]] && echo 2)"
    case "$CHOICE" in
        1) menu_create_dir; pause ;;
        2) add_bot; pause ;;
        3) if pick_bot; then bot_menu; else pause; fi ;;
        0|q|Q) echo; echo "  已退出。"; exit 0 ;;
        *) warn "无效选择：$CHOICE"; sleep 1 ;;
    esac
done
