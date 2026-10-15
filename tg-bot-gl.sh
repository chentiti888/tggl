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

if [[ ! -t 0 && -z "${TGBOT_NONINTERACTIVE:-}" ]]; then
    err "需要交互式终端。请用：bash <(curl -fsSL 脚本地址)，不要用 curl ... | bash"
    exit 1
fi

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
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in {".git", "__pycache__", "venv", ".venv", "node_modules"}]
        local.update(dn)
        local.update(os.path.splitext(f)[0] for f in fn if f.endswith(".py"))
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

# 扫描代码里通过环境变量读取的变量名（每行一个）
scan_env_names() {
    python3 - "$INSTALL_DIR" <<'PY' 2>/dev/null
import ast, os, re, sys
root = sys.argv[1]
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
names = set()
base = root.rstrip(os.sep).count(os.sep)
for dp, dn, fn in os.walk(root):
    dn[:] = [d for d in dn if d not in {".git", "__pycache__", "venv", ".venv", "node_modules"}]
    if dp.count(os.sep) - base >= 2:
        dn[:] = []
    for f in fn:
        if not f.endswith(".py"):
            continue
        try:
            tree = ast.parse(open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read())
        except (SyntaxError, OSError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) \
               and isinstance(n.args[0].value, str) and NAME.match(n.args[0].value) \
               and isinstance(n.func, ast.Attribute) and n.func.attr in ("getenv", "get") \
               and ("environ" in ast.dump(n.func) or n.func.attr == "getenv"):
                names.add(n.args[0].value)
            elif isinstance(n, ast.Subscript) and "environ" in ast.dump(n.value) \
                 and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str) \
                 and NAME.match(n.slice.value):
                names.add(n.slice.value)
print("\n".join(sorted(names)))
PY
}

# 变量名是否像密钥 / ID（用来筛掉路径、开关之类的普通变量）
is_secret_name() {
    [[ "$1" =~ (KEY|TOKEN|SECRET|PASS|PASSWD|PASSWORD|API|CREDENTIAL|(^|_)IDS?$) ]] || is_port_name "$1"
}

# ---------------------------------------------------------------- 端口
# 代码里从环境变量读取的端口 (变量名以 PORT 结尾): 部署时填写或回车随机分配,
# 和本机正在监听的端口、其他机器人 .env 里的端口都不能重复
is_port_name() { [[ "$1" =~ (^|_)PORT$ ]]; }

port_listening() {  # 本机是否已有程序在监听这个端口
    if command -v ss >/dev/null 2>&1; then
        ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"
    else
        netstat -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"
    fi
}

port_owner() {  # 哪个机器人 (不含当前这个) 的 .env 里用了这个端口
    local p="$1" f n d
    [[ -d "$CONF_DIR" ]] || return 0
    for f in "$CONF_DIR"/*.conf; do
        [[ -f "$f" ]] || continue
        n="$(basename "$f" .conf)"
        d="$( INSTALL_DIR=""; source "$f" 2>/dev/null; echo "$INSTALL_DIR" )"
        [[ -n "$d" && "$d" != "$INSTALL_DIR" && -f "$d/.env" ]] || continue
        if grep -qE "^[A-Za-z0-9_]*PORT=[\"']?${p}[\"']?\$" "$d/.env"; then echo "$n"; return 0; fi
    done
    return 0
}

port_random() {  # 随机挑一个没人用的端口 (20000-59999)
    local p i
    for ((i = 0; i < 200; i++)); do
        p=$(( 20000 + RANDOM % 40000 ))
        port_listening "$p" && continue
        [[ -n "$(port_owner "$p")" ]] && continue
        echo "$p"; return 0
    done
    return 1
}

fill_port_var() {  # fill_port_var 变量名 : 填写端口, 回车随机, 冲突就重新输入
    local name="$1" cur val owner
    cur="$(sed -n "s/^${name}=//p" "$INSTALL_DIR/.env" 2>/dev/null | tr -d "\"'" | head -1)"
    echo
    echo "  ${BOLD}${name}${RESET}"
    echo "  ${DIM}$(var_desc "$name")${RESET}"
    [[ -n "$cur" ]] && echo "  ${DIM}当前: ${cur}（直接回车保持不变）${RESET}"
    while true; do
        rd val "端口（1024-65535；直接回车$([[ -n "$cur" ]] && echo 保持不变 || echo 随机分配)）"
        if [[ -z "$val" ]]; then
            if [[ -n "$cur" ]]; then echo "  保持 ${cur}"; return 0; fi
            val="$(port_random)" || { err "没找到可用的随机端口，请手动输入。"; continue; }
            echo "  已随机分配端口：${GREEN}${val}${RESET}"
        fi
        if [[ ! "$val" =~ ^[0-9]+$ ]] || (( val < 1024 || val > 65535 )); then
            err "端口要是 1024-65535 之间的数字。"; continue
        fi
        [[ "$val" == "$cur" ]] && { echo "  端口没变。"; return 0; }
        owner="$(port_owner "$val")"
        if [[ -n "$owner" ]]; then
            err "端口 ${val} 已被机器人「${owner}」使用，请换一个（或直接回车随机分配）。"; continue
        fi
        if port_listening "$val"; then
            err "端口 ${val} 已被本机其他程序占用（查看: ss -ltnp | grep :${val}），请换一个。"; continue
        fi
        break
    done
    write_value env "$INSTALL_DIR/.env" "$name" str str "$val"
    echo "  ${YELLOW}提示:${RESET} 如果有网页 / 其他程序要连这个端口（比如 Mini App 的 PHP 转发地址），记得改成 ${val}"
}

# 变量说明：这个变量是哪个 API / 服务的密钥
var_desc() {
    case "$1" in
        BOT_TOKEN)         echo "Telegram 机器人 Token（@BotFather 获取）[必填]" ;;
        ADMIN_IDS)         echo "Telegram 管理员用户ID（多个用空格或逗号分隔）[必填]" ;;
        GLOBALPING_TOKEN)  echo "Globalping API Token（globalping.io，提高探测额度）[可选]" ;;
        ABUSEIPDB_KEY)     echo "AbuseIPDB API 密钥（abuseipdb.com，IP 信誉/滥用查询）[可选]" ;;
        IPAPI_IS_KEY)      echo "ipapi.is API 密钥（ipapi.is，IP 信息查询）[可选]" ;;
        PROXYCHECK_KEY)    echo "proxycheck.io API 密钥（代理 / VPN 检测）[可选]" ;;
        MINIAPP_PORT)      echo "Mini App 接口端口（只在本机 127.0.0.1 监听，给网页转发用）[回车随机]" ;;
        CALLBACK_PORT)     echo "回调接收端口（给网页 / 其他程序回调用）[回车随机]" ;;
        *PORT)             echo "${1%_PORT} 监听端口 [回车随机]" ;;
        *)
            local base="$1"
            base="${base%_API_KEY}"; base="${base%_API_TOKEN}"; base="${base%_KEY}"
            base="${base%_TOKEN}"; base="${base%_SECRET}"; base="${base%_PASSWORD}"
            if [[ "$1" =~ IDS?$ ]]; then echo "${base} 相关的数字 ID [自动识别]"
            else echo "${base} 的 API 密钥 / 凭据 [自动识别]"; fi ;;
    esac
}

env_is_set() {
    local f="$INSTALL_DIR/.env"
    [[ -f "$f" ]] && grep -qE "^$1=.+" "$f"
}

# 依次询问并写入一个环境变量
fill_env_var() {
    local name="$1" desc val id ids bad=0
    is_port_name "$name" && { fill_port_var "$name"; return; }
    desc="$(var_desc "$name")"
    echo
    echo "  ${BOLD}${name}${RESET}"
    echo "  ${DIM}${desc}${RESET}"
    if [[ "$name" =~ IDS?$ ]]; then
        rd val "请输入（多个用空格或逗号分隔；回车跳过）"
        [[ -n "$val" ]] || { echo "  已跳过。"; return 0; }
        ids="$(normalize_ids "$val")"
        for id in $ids; do [[ "$id" =~ ^-?[0-9]+$ ]] || bad=1; done
        (( bad )) && { err "ID 必须是数字，已跳过。"; return 1; }
        write_value env "$INSTALL_DIR/.env" "$name" ids ids "$ids"
    else
        rds val "请输入（输入不显示；回车跳过）"
        [[ -n "$val" ]] || { echo "  已跳过。"; return 0; }
        write_value env "$INSTALL_DIR/.env" "$name" str str "$val"
    fi
}

# 扫描出代码里需要密钥的变量，结果放到 SECRET_VARS
scan_secret_vars() {
    SECRET_VARS=()
    local n
    while IFS= read -r n; do
        [[ -n "$n" ]] && is_secret_name "$n" && SECRET_VARS+=("$n")
    done < <(scan_env_names)
}

cfg_custom() {
    local mode="$1" file="$2" c i n st
    SECRET_VARS=()
    heading "自定义变量（自动扫描）"
    scan_secret_vars
    if (( ${#SECRET_VARS[@]} > 0 )); then
        echo "  ${DIM}已从代码中扫描到以下需要填写的变量（统一写入 .env）：${RESET}"
        echo
        for i in "${!SECRET_VARS[@]}"; do
            n="${SECRET_VARS[i]}"
            if env_is_set "$n"; then st="${GREEN}已设置${RESET}"; else st="${YELLOW}未设置${RESET}"; fi
            printf '  %s) %s %s\n' "${YELLOW}$((i + 1))${RESET}" "$(padr "$n" 20)" "$st"
            echo "       ${DIM}$(var_desc "$n")${RESET}"
        done
        echo
        printf "  %s  %s  %s\n" "${YELLOW}↵${RESET}" "$(padr "回车" 22)" "${DIM}依次填写所有「未设置」的变量${RESET}"
        menu_item a "全部重新填写"
        menu_item m "手动输入变量名"
        echo "  ${DIM}或输入编号只填写某一个${RESET}"
        echo
        rd c "请选择" ""
        case "$c" in
            "") for n in "${SECRET_VARS[@]}"; do env_is_set "$n" || fill_env_var "$n"; done; return ;;
            a|A) for n in "${SECRET_VARS[@]}"; do fill_env_var "$n"; done; return ;;
            m|M) ;;
            *) if [[ "$c" =~ ^[0-9]+$ ]] && (( c >= 1 && c <= ${#SECRET_VARS[@]} )); then
                   fill_env_var "${SECRET_VARS[c-1]}"; return
               fi
               err "无效选择：$c"; return ;;
        esac
    else
        warn "没有从代码中扫描到需要填写的变量，改为手动输入。"
    fi

    local name t value
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

do_config_manual() {
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
        menu_item 3 "自定义变量 / API 密钥" "自动扫描代码需要的密钥"
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
    local dir def="${1:-/root/mybot}"
    heading "创建安装目录"
    echo "  ${DIM}绝对路径；不能直接用 /root、/opt 等系统目录；直接回车使用默认值${RESET}"
    while true; do
        rd dir "目录" "$def"
        if [[ -z "$dir" ]]; then warn "已取消。"; return 1; fi
        if [[ "$dir" != /* ]]; then
            err "必须是绝对路径（以 / 开头）。"
            continue
        fi
        dir="$(realpath -m -- "$dir" 2>/dev/null || echo "${dir%/}")"
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

# 选择一个空闲目录，结果放到 PICKED_DIR；参数 1 = 默认目录（直接回车使用，不存在则创建）
pick_dir() {
    PICKED_DIR=""
    local def="${1:-/root/mybot}" i d u state sel
    list_dirs
    echo "  ${DIM}直接回车 = 使用默认目录 ${def}${RESET}"
    if (( ${#DIRS[@]} > 0 )); then
        for i in "${!DIRS[@]}"; do
            d="${DIRS[i]}"; u="$(dir_user "$d")"
            if [[ -n "$u" ]]; then state="已被「$u」使用"
            elif dir_is_empty "$d"; then state="空闲"
            else state="非空"; fi
            menu_item $((i + 1)) "$d" "$state"
        done
    fi
    menu_item n "输入其他路径"
    echo
    rd sel "选择" "默认"
    if [[ "$sel" == "默认" ]]; then
        PICKED_DIR="$(realpath -m -- "$def" 2>/dev/null || echo "${def%/}")"
        mkdir -p "$PICKED_DIR" || { err "创建失败：$PICKED_DIR"; return 1; }
        mkdir -p "$CONF_DIR" && chmod 700 "$CONF_DIR"
        grep -qxF -- "$PICKED_DIR" "$DIRS_FILE" 2>/dev/null || echo "$PICKED_DIR" >>"$DIRS_FILE"
        log "使用目录：$PICKED_DIR"
    elif [[ "$sel" =~ ^[Nn]$ ]]; then
        create_dir "$def" || return 1
        PICKED_DIR="$CREATED_DIR"
    elif [[ "$sel" =~ ^[0-9]+$ ]] && (( sel >= 1 && sel <= ${#DIRS[@]} )); then
        PICKED_DIR="${DIRS[sel-1]}"
    else
        err "无效选择：$sel"; return 1
    fi
    if is_unsafe_dir "$PICKED_DIR"; then
        err "不能使用系统目录 $PICKED_DIR"; return 1
    fi
    if [[ -n "$(dir_user "$PICKED_DIR")" ]]; then
        err "该目录已被机器人「$(dir_user "$PICKED_DIR")」使用。"
        return 1
    fi
    if ! dir_is_empty "$PICKED_DIR"; then
        err "$PICKED_DIR 不是空目录，请换一个目录。"
        return 1
    fi
    mkdir -p "$PICKED_DIR"
}

# ---------------------------------------------------------------- 添加机器人

# 按代码决定写入方式：代码从环境变量读取就写 .env，否则写主程序里的变量
quick_keys_legacy() {
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

    # 代码里还有其他 API 密钥时，提示是否现在填写
    local extra=() ports=() n
    scan_secret_vars
    for n in "${SECRET_VARS[@]}"; do
        [[ "$n" == "$TOKEN_VAR" || "$n" == "$ID_VAR" ]] && continue
        if is_port_name "$n"; then env_is_set "$n" || ports+=("$n"); continue; fi
        env_is_set "$n" || extra+=("$n")
    done
    if [[ "$mode" == "env" ]] && (( ${#ports[@]} > 0 )); then
        echo
        echo "  ${DIM}代码需要监听端口：${ports[*]}（同一台服务器上的机器人不能用同一个端口）${RESET}"
        for n in "${ports[@]}"; do fill_port_var "$n"; done
    fi
    if [[ "$mode" == "env" ]] && (( ${#extra[@]} > 0 )); then
        echo
        echo "  ${DIM}代码里还支持这些可选 API 密钥：${extra[*]}${RESET}"
        if confirm "现在填写？（每项可回车跳过；之后也可在 管理机器人 → 5 里填）"; then
            for n in "${extra[@]}"; do fill_env_var "$n"; done
        fi
    fi
}

# ---------------------------------------------------------------- 自动识别需要填写的配置
# 一次扫描整个项目 (最深 5 层目录), 能识别:
#   环境变量: os.getenv / os.environ.get / os.environ[...] / from os import getenv / env = os.environ
#             python-decouple config() / environs Env() / pydantic BaseSettings / A or B or 默认值
#   示例文件: .env.example / .env.sample / .env.template ... (里面的注释当作说明)
#   代码常量: TOKEN = "YOUR_TOKEN" 这类还没填的占位符 (项目不用环境变量时)
#   JSON 配置: config.example.json 等 (自动复制成 config.json 再逐项填写)
# 结果按 必填 / 可选 / 高级(一般不用改) 分组
VS_SRC=(); VS_NAME=(); VS_GRP=(); VS_KIND=(); VS_DEF=(); VS_DESC=(); VS_FILE=(); VS_ST=()
VARS_CHANGED=0

scan_all() {
    VS_SRC=(); VS_NAME=(); VS_GRP=(); VS_KIND=(); VS_DEF=(); VS_DESC=(); VS_FILE=(); VS_ST=()
    local a b c d e f g h
    while IFS=$'\x1f' read -r a b c d e f g h; do
        [[ -n "$b" ]] || continue
        VS_SRC+=("$a"); VS_NAME+=("$b"); VS_GRP+=("$c"); VS_KIND+=("$d")
        VS_DEF+=("$e"); VS_DESC+=("$f"); VS_FILE+=("$g"); VS_ST+=("$h")
    done < <(python3 - "$INSTALL_DIR" <<'PY' 2>/dev/null
import ast, json, os, re, sys

root = sys.argv[1]
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SKIP_DIRS = {".git", "__pycache__", "venv", ".venv", "env", "node_modules", "site-packages",
             "tests", "test", "docs", "dist", "build", ".github", ".idea", ".vscode"}
MAX_DEPTH = 5

# 系统 / 运行环境自带的变量, 不需要用户填
SYS_ENV = {"HOME", "PATH", "USER", "PWD", "LANG", "LC_ALL", "TERM", "SHELL", "HOSTNAME", "TZ",
           "PYTHONPATH", "PYTHONUNBUFFERED", "VIRTUAL_ENV", "TMPDIR", "TEMP", "TMP", "LOGNAME",
           "SUDO_USER", "SYSTEMD_EXEC_PID", "INVOCATION_ID", "JOURNAL_STREAM", "XDG_RUNTIME_DIR",
           "COLUMNS", "LINES", "NO_COLOR", "FORCE_COLOR", "CI", "DYNO", "PORT_OFFSET"}

RX_TG_TOKEN = re.compile(r"(^|_)BOT_TOKEN$|^TOKEN$|^TG_TOKEN$|^TELEGRAM(_BOT)?_TOKEN$|^TELEGRAM_API_TOKEN$", re.I)
RX_ADMIN = re.compile(r"admin|owner|super_?user|allowed_?users|whitelist|white_list|(^|_)uids?$|(^|_)user_?ids?$", re.I)
RX_IDS = re.compile(r"(^|_)ids?$|admin|owner|chat_?id|group_?id|channel_?id|allowed_?users|whitelist|(^|_)uids?$", re.I)
RX_SECRET = re.compile(r"token|key|secret|pass(wd|word)?$|pwd$|credential|cookie|session|auth", re.I)
RX_PORT = re.compile(r"(^|_)port$", re.I)
RX_URL = re.compile(r"(url|uri|endpoint|host|domain|proxy|base)$", re.I)
RX_ADV = re.compile(r"(dir|path|file|folder|conf|config|log|debug|level|timeout|interval|retries|retry|"
                    r"workers?|threads?|cache|ttl|lang|locale|timezone|tz|mode|env|environment)$", re.I)
RX_PLACEHOLDER = re.compile(r"your|xxx|change_?me|placeholder|replace|在此|填写|替换|请填|待填|示例|example|"
                            r"^<.*>$|^\*+$|^0+$|^123456|abcdef|^todo$|^none$|^null$|^-$", re.I)
RX_PY_CAND = re.compile(r"token|api_?key|secret|password|passwd|admin|owner|(^|_)ids?$|chat_?id|"
                        r"channel|group_?id|webhook|cookie|proxy|(^|_)port$|key$", re.I)


def files_in(root, exts):
    out = []
    base = root.rstrip(os.sep).count(os.sep)
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith("."))
        if dp.count(os.sep) - base >= MAX_DEPTH:
            dn[:] = []
        for f in sorted(fn):
            if f.endswith(exts):
                out.append(os.path.join(dp, f))
    return out


def is_placeholder(v):
    if v is None:
        return True
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return v == 0 or bool(re.match(r"^-?(100)?123456", str(v)))
    if isinstance(v, (list, tuple, set)):
        return len(v) == 0 or all(is_placeholder(x) for x in v)
    if isinstance(v, dict):
        return False
    s = str(v).strip()
    return s == "" or bool(RX_PLACEHOLDER.search(s))


def kind_of(name, hint=""):
    if RX_PORT.search(name):
        return "port"
    if RX_TG_TOKEN.search(name):
        return "token"
    if RX_IDS.search(name) and not re.search(r"token|key|secret|url", name, re.I):
        return "ids"
    if RX_SECRET.search(name):
        return "secret"
    if hint in ("num", "bool", "ids"):
        return hint
    if RX_URL.search(name):
        return "url"
    return "str"


# ------------------------------------------------------------------ 1. 环境变量 (代码)
env = {}   # name -> dict(req, default, has_default, kind_hint, files)


def add_env(name, has_default, default, hint, path, force_req=False):
    if not NAME.match(name) or name in SYS_ENV:
        return
    e = env.setdefault(name, {"req": False, "nodef": False, "default": "", "hint": "", "files": set(),
                              "forced": False, "desc": ""})
    if not has_default or RX_TG_TOKEN.search(name) or RX_ADMIN.search(name) and RX_IDS.search(name):
        e["nodef"] = True
    if isinstance(default, list):
        default = ",".join(str(x) for x in default)
    if has_default and default not in ("", None) and not e["default"]:
        e["default"] = str(default)
    if hint and not e["hint"]:
        e["hint"] = hint
    if force_req:
        e["forced"] = True
    e["files"].add(os.path.relpath(path, root))


def const(n):
    if isinstance(n, ast.Constant):
        return True, n.value
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub) and isinstance(n.operand, ast.Constant):
        return True, -n.operand.value
    if isinstance(n, (ast.List, ast.Tuple, ast.Set)):
        items = [const(e) for e in n.elts]
        if all(o for o, _ in items):
            return True, [x for _, x in items]
    return False, None


py_cands = []   # (name, value, file, kind)
json_refs = set()
# json 文件分类: 配置文件才扫, 数据文件 (用户数据 / 缓存 / 状态) 不扫
RX_CFG_VAR = re.compile(r"conf|config|setting|secret|credential|cfg", re.I)
RX_DATA_VAR = re.compile(r"data|cache|state|backup|recent|history|log|db|store|record|servers?|users?|stats?|geo", re.I)
json_cfg_vars = set()    # 赋值给 CONFIG_FILE 之类变量的 json
json_data_vars = set()   # 赋值给 DATA_FILE 之类变量的 json
cfg_get_keys = set()     # 代码里 X.get("key") 读取的键 (有兜底 = 可选)
cfg_sub_keys = set()     # 代码里 X["key"] 直接读取的键 (没有兜底)

for path in files_in(root, (".py",)):
    try:
        src = open(path, encoding="utf-8", errors="ignore").read()
        tree = ast.parse(src)
    except (SyntaxError, OSError, ValueError):
        continue
    for m in re.findall(r"""["']([\w./-]+\.(?:json|ya?ml|toml|ini))["']""", src):
        json_refs.add(os.path.basename(m))
    for n in ast.walk(tree):
        tgt = None
        if isinstance(n, ast.Assign) and len(n.targets) == 1:
            tgt = n.targets[0]
        elif isinstance(n, ast.AnnAssign):
            tgt = n.target
        if tgt is not None and n.value is not None:
            tn = tgt.id if isinstance(tgt, ast.Name) else getattr(tgt, "attr", "")
            for x in ast.walk(n.value):
                if isinstance(x, ast.Constant) and isinstance(x.value, str) and x.value.endswith(".json"):
                    b = os.path.basename(x.value)
                    if RX_CFG_VAR.search(tn):
                        json_cfg_vars.add(b)
                    elif RX_DATA_VAR.search(tn):
                        json_data_vars.add(b)
        # 读取配置键的方式: X.get("k") 有兜底; X["k"] 没有兜底
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and n.args:
            if isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str) \
                    and getattr(n.func.value, "attr", "") != "environ" \
                    and getattr(n.func.value, "id", "") != "environ":
                cfg_get_keys.add(n.args[0].value.lower())
        elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load) \
                and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str) \
                and getattr(n.value, "attr", "") != "environ" and getattr(n.value, "id", "") != "environ":
            cfg_sub_keys.add(n.slice.value.lower())

    parent = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parent[c] = p

    # 别名: from os import getenv as g / environ as e ; env = os.environ ; from decouple import config
    getenv_names, environ_names, decouple_names, environs_objs = {"getenv"}, {"environ"}, set(), set()
    settings_bases = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                nm = a.asname or a.name
                if n.module == "os" and a.name == "getenv":
                    getenv_names.add(nm)
                elif n.module == "os" and a.name == "environ":
                    environ_names.add(nm)
                elif n.module.split(".")[0] == "decouple" and a.name in ("config", "AutoConfig", "Config"):
                    decouple_names.add(nm)
                elif n.module.split(".")[0] in ("pydantic_settings", "pydantic") and a.name == "BaseSettings":
                    settings_bases.add(nm)
        elif isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            d = ast.dump(n.value)
            t = n.targets[0].id
            if isinstance(n.value, ast.Attribute) and n.value.attr == "environ":
                environ_names.add(t)
            elif isinstance(n.value, ast.Attribute) and n.value.attr == "getenv":
                getenv_names.add(t)
            elif isinstance(n.value, ast.Call) and ("'Env'" in d or "'AutoConfig'" in d or "'Config'" in d and decouple_names):
                if "'Env'" in d:
                    environs_objs.add(t)
                else:
                    decouple_names.add(t)

    def is_environ(node):
        if isinstance(node, ast.Name):
            return node.id in environ_names
        if isinstance(node, ast.Attribute):
            return node.attr == "environ"
        return False

    def wrap_hint(node):
        p = parent.get(node)
        for _ in range(3):
            if p is None:
                break
            if isinstance(p, ast.Call) and isinstance(p.func, ast.Name):
                if p.func.id in ("int", "float"):
                    return "num"
                if p.func.id == "bool":
                    return "bool"
            if isinstance(p, ast.Attribute) and p.attr == "split":
                return "ids"
            if isinstance(p, ast.Compare) or (isinstance(p, ast.Attribute) and p.attr == "lower"):
                q = parent.get(p)
                if isinstance(p, ast.Compare) or isinstance(q, ast.Call):
                    return "bool"
            p = parent.get(p)
        return ""

    def kw(call, *names):
        for k in call.keywords:
            if k.arg in names:
                return k.value
        return None

    target_of = {}   # env name -> python variable it is assigned to
    hits = []

    for n in ast.walk(tree):
        name = None
        has_def, dflt, hint = False, None, ""
        if isinstance(n, ast.Call) and n.args:
            ok, a0 = const(n.args[0])
            if not ok or not isinstance(a0, str):
                continue
            f = n.func
            dv = n.args[1] if len(n.args) > 1 else kw(n, "default")
            if isinstance(f, ast.Name) and f.id in getenv_names:
                name = a0
            elif isinstance(f, ast.Attribute) and f.attr == "getenv":
                name = a0
            elif isinstance(f, ast.Attribute) and f.attr in ("get", "setdefault") and is_environ(f.value):
                name = a0
            elif isinstance(f, ast.Name) and f.id in decouple_names:
                name = a0
                dv = kw(n, "default")
                cast = kw(n, "cast")
                if isinstance(cast, ast.Name):
                    hint = {"int": "num", "float": "num", "bool": "bool"}.get(cast.id, "")
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in environs_objs:
                name = a0
                hint = {"int": "num", "float": "num", "bool": "bool", "list": "ids"}.get(f.attr, "")
            elif isinstance(f, ast.Name) and f.id in environs_objs:
                name = a0
            if name is None:
                continue
            if dv is not None:
                has_def = True
                okd, dflt = const(dv)
                if not okd:
                    dflt = "(由代码计算)"
            hint = hint or wrap_hint(n)
        elif isinstance(n, ast.Subscript) and is_environ(n.value) and isinstance(n.ctx, ast.Load):
            ok, a0 = const(n.slice)
            if not ok or not isinstance(a0, str):
                continue
            name, hint = a0, wrap_hint(n)
        else:
            continue
        hits.append((n, name, has_def, dflt, hint))

    node_name = {id(h[0]): h[1] for h in hits}

    def inner_name(v):  # BoolOp 里的一项 (可能套了 .strip() 之类) 对应的环境变量名
        for x in ast.walk(v):
            if id(x) in node_name:
                return node_name[id(x)]
        return None

    for n, name, has_def, dflt, hint in hits:
        # A or B or "默认值": 后面还有备选 = 有默认值; 同一串里的其他环境变量 = 备用名称
        c, p, alt = n, parent.get(n), None
        for _ in range(3):
            if isinstance(p, (ast.Attribute, ast.Call)) and not isinstance(p, ast.BoolOp):
                c, p = p, parent.get(p)
            else:
                break
        if isinstance(p, ast.BoolOp) and isinstance(p.op, ast.Or) and c in p.values:
            i = p.values.index(c)
            if i < len(p.values) - 1:
                has_def = True
                okd, d = const(p.values[-1])
                dflt = d if okd else (dflt if dflt not in (None, "") else "(由代码计算)")
            for v in p.values:
                first = inner_name(v)
                if first:
                    if first != name:
                        alt = first
                    break
        add_env(name, has_def, dflt, hint, path)
        if name not in env:
            continue
        # os.environ.get("X") or CONFIG.get("x"): 同一项配置的 json 写法, 记下来合并成一项
        if isinstance(p, ast.BoolOp) and isinstance(p.op, ast.Or) and c in p.values:
            for v in p.values:
                if v is c:
                    continue
                for x in ast.walk(v):
                    if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == "get" \
                            and x.args and isinstance(x.args[0], ast.Constant) and isinstance(x.args[0].value, str) \
                            and not is_environ(x.func.value):
                        env[name].setdefault("cfgkeys", set()).add(x.args[0].value.lower())
        if alt:
            env[name].setdefault("alt", alt)
        else:
            env[name]["primary"] = True
        # 记录赋值目标, 用来判断 "没填就退出" 的写法
        p = n
        for _ in range(4):
            p = parent.get(p)
            if p is None:
                break
            if isinstance(p, ast.Assign) and len(p.targets) == 1 and isinstance(p.targets[0], (ast.Name, ast.Attribute)):
                t = p.targets[0]
                target_of.setdefault(t.id if isinstance(t, ast.Name) else t.attr, set()).add(name)
                break
            if isinstance(p, ast.AnnAssign) and isinstance(p.target, ast.Name):
                target_of.setdefault(p.target.id, set()).add(name)
                break

    # pydantic BaseSettings: 字段名就是环境变量名
    for c in ast.walk(tree):
        if not isinstance(c, ast.ClassDef):
            continue
        bases = {b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in c.bases}
        if not (bases & (settings_bases | {"BaseSettings"})):
            continue
        prefix = ""
        for st in c.body:
            d = ast.dump(st)
            m = re.search(r"env_prefix'?, value=Constant\(value='([^']*)'", d) or \
                re.search(r"arg='env_prefix', value=Constant\(value='([^']*)'", d)
            if m:
                prefix = m.group(1)
            if isinstance(st, ast.ClassDef) and st.name == "Config":
                for s2 in st.body:
                    if isinstance(s2, ast.Assign) and any(getattr(t, "id", "") == "env_prefix" for t in s2.targets):
                        ok, v = const(s2.value)
                        if ok:
                            prefix = v
        for st in c.body:
            if not (isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)):
                continue
            fname = st.target.id
            if fname.startswith("_") or fname == "model_config":
                continue
            ann = ast.dump(st.annotation)
            hint = "num" if "'int'" in ann or "'float'" in ann else "bool" if "'bool'" in ann else \
                   "ids" if "'list'" in ann or "'List'" in ann or "'set'" in ann else ""
            ename = (prefix + fname).upper()
            v = st.value
            has_def, dflt = False, None
            if v is not None:
                if isinstance(v, ast.Call) and getattr(v.func, "id", getattr(v.func, "attr", "")) == "Field":
                    al = kw(v, "alias", "validation_alias", "env")
                    ok, a = const(al) if al is not None else (False, None)
                    if ok and isinstance(a, str):
                        ename = a
                    dv = v.args[0] if v.args else kw(v, "default")
                    if dv is not None and not (isinstance(dv, ast.Constant) and dv.value is Ellipsis):
                        has_def = True
                        ok, dflt = const(dv)
                        if not ok:
                            dflt = "(由代码计算)"
                    elif kw(v, "default_factory") is not None:
                        has_def, dflt = True, "(由代码计算)"
                else:
                    has_def = True
                    ok, dflt = const(v)
                    if not ok:
                        dflt = "(由代码计算)"
            add_env(ename, has_def, dflt, hint, path)

    # "没填就退出": if not X: sys.exit / raise / exit
    def exits(body):
        for s in body:
            for x in ast.walk(s):
                if isinstance(x, ast.Raise):
                    return True
                if isinstance(x, ast.Call):
                    fn = x.func
                    nm = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                    if nm in ("exit", "_exit", "quit", "SystemExit"):
                        return True
        return False

    for n in ast.walk(tree):
        if not isinstance(n, ast.If) or not exits(n.body):
            continue
        for x in ast.walk(n.test):
            vname = None
            if isinstance(x, ast.UnaryOp) and isinstance(x.op, ast.Not):
                o = x.operand
                vname = o.id if isinstance(o, ast.Name) else getattr(o, "attr", None)
            elif isinstance(x, ast.Compare) and any(isinstance(op, (ast.Is, ast.Eq)) for op in x.ops):
                o = x.left
                vname = o.id if isinstance(o, ast.Name) else getattr(o, "attr", None)
            elif isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id in ("all", "any"):
                for el in ast.walk(x):
                    if isinstance(el, ast.Name) and el.id in target_of:
                        for en in target_of[el.id]:
                            if en in env:
                                env[en]["forced"] = True
            if vname and vname in target_of:
                for en in target_of[vname]:
                    if en in env:
                        env[en]["forced"] = True

    # ------------------------------------------------------------ 2. 直接写在代码里的配置 (模块顶层常量)
    rel = os.path.relpath(path, root)
    for st in tree.body:
        tname, v = None, None
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            tname, v = st.targets[0].id, st.value
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and st.value is not None:
            tname, v = st.target.id, st.value
        if not tname or not RX_PY_CAND.search(tname):
            continue
        if not (tname.isupper() or tname[:1].isupper()):
            continue
        d = ast.dump(v)
        if "environ" in d or "getenv" in d or "config" in d.lower() and "Call" in d:
            continue
        ok, val = const(v)
        if not ok:
            if isinstance(v, (ast.List, ast.Tuple, ast.Set)):
                items = [const(e) for e in v.elts]
                if all(o for o, _ in items):
                    ok, val = True, [x for _, x in items]
        if not ok or isinstance(val, bool) or isinstance(val, bytes):
            continue
        k = kind_of(tname, "ids" if isinstance(val, list) else "num" if isinstance(val, int) else "")
        py_cands.append((tname, val, rel, k))

# ------------------------------------------------------------------ 3. .env.example 等示例文件
example_desc = {}
EX_NAMES = [".env.example", ".env.sample", ".env.template", ".env.dist", ".env.default", ".env.defaults",
            "env.example", "example.env", "sample.env", ".env.local.example", "env.sample"]
for exn in EX_NAMES:
    p = os.path.join(root, exn)
    if not os.path.isfile(p):
        continue
    pending = []
    for line in open(p, encoding="utf-8", errors="ignore"):
        s = line.strip()
        if not s:
            pending = []
            continue
        if s.startswith("#"):
            t = s.lstrip("#").strip()
            m = re.match(r"^(export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", t)
            if m:   # 被注释掉的变量 = 可选
                nm = m.group(2)
                if nm not in env and nm not in SYS_ENV:
                    env[nm] = {"req": False, "nodef": False, "default": "", "hint": "", "files": {exn},
                               "forced": False, "desc": " ".join(pending)}
                pending = []
            elif t:
                pending.append(t)
            continue
        m = re.match(r"^(export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", s)
        if not m:
            pending = []
            continue
        nm, val = m.group(2), m.group(3)
        inline = ""
        if " #" in val:
            val, inline = val.split(" #", 1)
        val = val.strip().strip("'\"")
        desc = " ".join(pending + ([inline.strip()] if inline.strip() else []))
        pending = []
        if nm in SYS_ENV:
            continue
        mark_opt = bool(re.search(r"可选|选填|optional|非必|不填", desc, re.I))
        mark_req = bool(re.search(r"必填|必须|required|mandatory", desc, re.I))
        e = env.get(nm)
        if e is None:
            e = env[nm] = {"req": False, "nodef": is_placeholder(val), "default": "" if is_placeholder(val) else val,
                           "hint": "", "files": {exn}, "forced": False, "desc": ""}
        else:
            e["files"].add(exn)
            if not e["default"] and not is_placeholder(val):
                e["default"] = val
        if desc:
            e["desc"] = desc
        if mark_req:
            e["forced"] = True
        if mark_opt:
            e["nodef"] = False
            e["forced"] = False
    break

# ------------------------------------------------------------------ 当前 .env 里已经填了什么
cur = {}
envf = os.path.join(root, ".env")
if os.path.isfile(envf):
    for line in open(envf, encoding="utf-8", errors="ignore"):
        m = re.match(r"^\s*(export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line.rstrip("\n"))
        if m:
            cur[m.group(2)] = m.group(3).strip().strip("'\"")


def clean(s):
    return re.sub(r"[\t\r\n\x1f]+", " ", str(s))[:120]


out = []
env_json_set = set()   # 环境变量没填, 但对应的 json 键已经有值 (代码会读 json)


def env_rows():
    rows = []
    for nm in sorted(env):
        e = env[nm]
        k = kind_of(nm, e["hint"])
        req = e["forced"] or e["nodef"]
        if e.get("alt") and not e.get("primary") and e["alt"] in env:
            grp = "adv"
            if not e["desc"]:
                e["desc"] = "%s 的备用名称（填了 %s 就不用填）" % (e["alt"], e["alt"])
        elif k == "port":
            grp = "req"           # 端口总要分配, 避免和其他机器人撞车
        elif req:
            grp = "req"
        elif RX_ADV.search(nm) and k not in ("token", "secret", "ids"):
            grp = "adv"
        else:
            grp = "opt"
        st = "set" if cur.get(nm, "") != "" or nm in env_json_set else "unset"
        if grp == "req" and k in ("token", "ids"):
            e["default"] = ""      # Token / 管理员 ID 不能靠默认值
        rows.append(["env", nm, grp, k, clean(e["default"]), clean(e["desc"]), ",".join(sorted(e["files"]))[:80], st])
    return rows

# 代码常量: 已经用环境变量的项目只列出占位符 (还没填) 的
env_names_upper = {n.upper() for n in env}
seen = set()
for nm, val, rel, k in py_cands:
    if (nm, rel) in seen or nm.upper() in env_names_upper:
        continue
    seen.add((nm, rel))
    ph = is_placeholder(val)
    if env:
        continue        # 项目用环境变量读配置, 代码里的常量只是备用值
    if not ph and k in ("str", "url", "num", "port"):
        continue        # 已经有正常值的普通配置, 不打扰
    if isinstance(val, list):
        shown = " ".join(str(x) for x in val)
    else:
        shown = "" if val is None else str(val)
    grp = "req" if ph and k in ("token", "ids", "secret") else "opt"
    out.append(["py", nm, grp, k, clean(shown if not ph else ""), "", rel, "unset" if ph else "set"])

# ------------------------------------------------------------------ 4. JSON 配置文件
CFG_EX = re.compile(r"^(.+?)[._-](example|sample|template|dist|default)(\.json)?$", re.I)
for f in sorted(os.listdir(root)):
    p = os.path.join(root, f)
    if not os.path.isfile(p):
        continue
    m = CFG_EX.match(f)
    if m and (f.endswith(".json") or m.group(1).endswith(".json")):
        tgt = m.group(1) if m.group(1).endswith(".json") else m.group(1) + ".json"
        out.append(["cfgex", tgt, "req", "file", "", "", f, "set" if os.path.exists(os.path.join(root, tgt)) else "unset"])
    elif m and re.search(r"\.(ya?ml|toml|ini|cfg)$", m.group(1) + (m.group(3) or ""), re.I):
        tgt = m.group(1)
        out.append(["cfgother", tgt, "req", "file", "", "", f, "set" if os.path.exists(os.path.join(root, tgt)) else "unset"])
    elif re.search(r"\.(ya?ml|toml|ini|cfg)\.(example|sample|template|dist)$", f, re.I):
        tgt = f.rsplit(".", 1)[0]
        out.append(["cfgother", tgt, "req", "file", "", "", f, "set" if os.path.exists(os.path.join(root, tgt)) else "unset"])



def is_cfg_json(f):  # 只扫配置文件; 用户数据 / 缓存 / 状态文件不扫
    if f in ("package.json", "package-lock.json", "tsconfig.json"):
        return False
    if f in json_cfg_vars:
        return True
    if f in json_data_vars:
        return False
    return bool(RX_CFG_VAR.search(f))


json_files = set(r[1] for r in out if r[0] == "cfgex")
for f in sorted(os.listdir(root)):
    if f.endswith(".json") and f in json_refs and is_cfg_json(f):
        json_files.add(f)

# 环境变量和 json 键是同一项配置时 (os.environ.get("X") or CONFIG.get("x")), 只保留环境变量那一项
linked = {}
for nm, e in env.items():
    linked.setdefault(nm.lower(), nm)
    for ck in e.get("cfgkeys", ()):
        linked.setdefault(ck, nm)


def walk_json(o, pfx):
    if isinstance(o, dict):
        for k, v in o.items():
            if not isinstance(k, str) or not re.match(r"^[A-Za-z0-9_-]+$", k):
                continue
            yield from walk_json(v, pfx + [k])
    else:
        yield pfx, o


for f in sorted(json_files):
    p = os.path.join(root, f)
    src = p if os.path.exists(p) else None
    if src is None:
        for r in out:
            if r[0] == "cfgex" and r[1] == f:
                src = os.path.join(root, r[6])
    try:
        data = json.load(open(src, encoding="utf-8"))
    except Exception:
        continue
    if not isinstance(data, dict):
        continue
    for path, v in walk_json(data, []):
        if not path:
            continue
        leaf = path[-1]
        lk = leaf.lower()
        if len(path) == 1 and lk in linked:
            if not is_placeholder(v):
                env_json_set.add(linked[lk])
            continue
        if not RX_PY_CAND.search(leaf) and not is_placeholder(v):
            continue
        if isinstance(v, (dict, bool)) or (isinstance(v, list) and v and not all(isinstance(x, (int, str)) for x in v)):
            continue
        k = kind_of(leaf, "ids" if isinstance(v, list) else "num" if isinstance(v, int) and not isinstance(v, bool) else "")
        ph = is_placeholder(v)
        if not ph and k in ("str", "url", "num", "port"):
            continue
        # 必填以代码为准: Token / 管理员 ID, 或代码里直接 X["key"] 读取 (没有兜底) 的才算必填
        strict = lk in cfg_sub_keys and lk not in cfg_get_keys
        grp = "req" if ph and (k == "token" or (k == "ids" and RX_ADMIN.search(leaf)) or strict) else "opt"
        shown = " ".join(str(x) for x in v) if isinstance(v, list) else ("" if v is None else str(v))
        out.append(["json", ".".join(path), grp, k, clean("" if ph else shown), "", f, "unset" if ph else "set"])

for r in env_rows() + out:
    print("\x1f".join(r))
PY
)
}

kind_desc() {  # 没有说明时, 按类型给一句
    case "$1" in
        token)  echo "Telegram 机器人 Token（@BotFather 获取）" ;;
        ids)    echo "数字 ID（多个用空格或逗号分隔；用户 ID 可找 @userinfobot 获取）" ;;
        secret) echo "密钥 / 密码" ;;
        port)   echo "监听端口" ;;
        url)    echo "地址 / 链接" ;;
        num)    echo "数字" ;;
        bool)   echo "开关（true / false）" ;;
        *)      echo "设置项" ;;
    esac
}

var_info() {  # var_info 序号 : 说明文字
    local i="$1" d="${VS_DESC[$1]}"
    if [[ -z "$d" ]]; then
        d="$(var_desc "${VS_NAME[i]}")"
        [[ "$d" == *"[自动识别]" ]] && d="$(kind_desc "${VS_KIND[i]}")"
        d="${d%% \[*\]}"; d="${d%%\[*\]}"
    fi
    if [[ "${VS_DEF[i]}" == "(由代码计算)" ]]; then d+="（不填时用代码里的默认设置）"
    elif [[ -n "${VS_DEF[i]}" ]]; then d+="（代码默认：${VS_DEF[i]}）"; fi
    echo "$d"
}

var_where() {  # 写到哪里, 给人看的
    case "${VS_SRC[$1]}" in
        env)  echo ".env" ;;
        py)   echo "${VS_FILE[$1]}" ;;
        json) echo "${VS_FILE[$1]}" ;;
        *)    echo "${VS_FILE[$1]}" ;;
    esac
}

tg_check_token() {  # 用 Telegram 接口验证 Token (token 不出现在命令行参数里)
    local out
    command -v curl >/dev/null 2>&1 || return 0
    out="$(printf 'url = "https://api.telegram.org/bot%s/getMe"\n' "$1" | curl -s -m 8 -K - 2>/dev/null)"
    if [[ "$out" == *'"ok":true'* ]]; then
        log "Token 有效：@$(sed -n 's/.*"username":"\([^"]*\)".*/\1/p' <<<"$out")"
        return 0
    elif [[ "$out" == *'"ok":false'* ]]; then
        err "Telegram 说这个 Token 无效（可能复制错了，或已在 @BotFather 里重置）。"
        return 1
    fi
    warn "暂时连不上 Telegram，没法验证 Token，先照样写入。"
    return 0
}

json_set() {  # json_set 文件 a.b.c 类型 值
    TG_VAL="$4" python3 - "$1" "$2" "$3" <<'PY'
import json, os, re, sys
path, key, kind = sys.argv[1:4]
val = os.environ["TG_VAL"]
data = json.load(open(path, encoding="utf-8"))
cur = data
parts = key.split(".")
for p in parts[:-1]:
    cur = cur.setdefault(p, {})
old = cur.get(parts[-1])
if kind == "ids" or isinstance(old, list):
    items = [x for x in re.split(r"[\s,，;；]+", val) if x]
    new = [int(x) if re.match(r"^-?\d+$", x) else x for x in items]
    if not isinstance(old, list) and len(new) == 1:
        new = new[0]
elif (kind in ("num", "port") or isinstance(old, int)) and not isinstance(old, bool) and re.match(r"^-?\d+$", val):
    new = int(val)
elif kind == "bool" or isinstance(old, bool):
    new = val.lower() in ("1", "true", "yes", "on", "y")
else:
    new = val
cur[parts[-1]] = new
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.replace(tmp, path)
PY
    local rc=$?
    chmod 600 "$1" 2>/dev/null
    (( rc == 0 )) && log "已写入 $2 到 $(basename "$1")" || err "写入 $2 失败。"
    return $rc
}

# fill_var 序号 : 按类型询问并写入一项
fill_var() {
    local i="$1" name="${VS_NAME[$1]}" kind="${VS_KIND[$1]}" src="${VS_SRC[$1]}" grp="${VS_GRP[$1]}"
    local val ids id bad=0 skip="回车跳过"
    if [[ "$src" == "env" && "$kind" == "port" ]]; then
        fill_port_var "$name" && VARS_CHANGED=1
        return 0
    fi
    echo
    echo "  ${BOLD}${name}${RESET}  $([[ "$grp" == req ]] && echo "${YELLOW}必填${RESET}" || echo "${DIM}可选${RESET}")  ${DIM}→ $(var_where "$i")${RESET}"
    echo "  ${DIM}$(var_info "$i")${RESET}"
    [[ "${VS_ST[i]}" == set ]] && skip="回车保持现在的值"
    [[ "${VS_ST[i]}" != set && -n "${VS_DEF[i]}" ]] && skip="回车使用代码默认值"
    while true; do
        case "$kind" in
            token|secret) rds val "请输入（输入不显示；${skip}）" ;;
            ids)          rd val "请输入（多个用空格或逗号分隔；${skip}）" ;;
            *)            rd val "请输入（${skip}）" ;;
        esac
        if [[ -z "$val" ]]; then
            if [[ "$grp" == req && "${VS_ST[i]}" != set && -z "${VS_DEF[i]}" ]]; then
                warn "已跳过。这是必填项，不填机器人可能启动失败（之后可在 管理机器人 → 5 里补填）。"
            else
                echo "  已跳过。"
            fi
            return 0
        fi
        if [[ "$kind" == token ]]; then
            if [[ ! "$val" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]]; then
                err "这不像 Telegram Token（格式是 数字:字母串，如 123456789:AAH...）。"
                confirm "仍然写入？" || continue
            elif ! tg_check_token "$val"; then
                confirm "仍然写入？" || continue
            fi
        elif [[ "$kind" == ids ]]; then
            ids="$(normalize_ids "$val")"; bad=0
            for id in $ids; do [[ "$id" =~ ^-?[0-9]+$ ]] || bad=1; done
            if (( bad )); then
                if [[ "$name" =~ [Cc][Hh][Aa][Nn][Nn][Ee][Ll]|[Gg][Rr][Oo][Uu][Pp] ]]; then
                    : # 频道 / 群组也可能填 @用户名
                else
                    err "ID 要是数字（多个用空格或逗号分隔），请重新输入。"; continue
                fi
            fi
            val="$ids"
        elif [[ "$kind" == num && ! "$val" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
            err "这里要填数字。"; continue
        fi
        break
    done
    case "$src" in
        env)
            [[ "$kind" == ids ]] && val="${val// /,}"
            set_env_var "$INSTALL_DIR/.env" "$name" "$val" || return 1 ;;
        py)
            case "$kind" in
                ids) write_value py "$INSTALL_DIR/${VS_FILE[i]}" "$name" ids list "$val" || return 1 ;;
                num) write_value py "$INSTALL_DIR/${VS_FILE[i]}" "$name" raw raw "$val" || return 1 ;;
                *)   write_value py "$INSTALL_DIR/${VS_FILE[i]}" "$name" str str "$val" || return 1 ;;
            esac ;;
        json)
            json_set "$INSTALL_DIR/${VS_FILE[i]}" "$name" "$kind" "$val" || return 1 ;;
    esac
    VS_ST[i]="set"
    VARS_CHANGED=1
    if [[ "$kind" == token ]]; then TOKEN_VAR="$name"; save_conf; fi
    if [[ "$kind" == ids && -z "$ID_VAR" ]]; then ID_VAR="$name"; save_conf; fi
    return 0
}

# 示例配置文件: 复制成正式文件 (已存在就不动)
prepare_cfg_files() {
    local i n=0 tgt
    for i in "${!VS_NAME[@]}"; do
        [[ "${VS_SRC[i]}" == cfgex || "${VS_SRC[i]}" == cfgother ]] || continue
        [[ "${VS_ST[i]}" == unset ]] || continue
        tgt="$INSTALL_DIR/${VS_NAME[i]}"
        [[ -e "$tgt" ]] && continue
        cp "$INSTALL_DIR/${VS_FILE[i]}" "$tgt" || continue
        chmod 600 "$tgt"; n=$((n + 1))
        log "已按示例 ${VS_FILE[i]} 创建配置文件 ${VS_NAME[i]}"
        if [[ "${VS_SRC[i]}" == cfgother ]]; then
            warn "这个格式需要手动编辑：nano $tgt （改完重启机器人）"
        fi
    done
    (( n > 0 )) && scan_all
    return 0
}

# 自动识别并填写:  guided_fill [all]   (all = 已填的也重新问一遍)
guided_fill() {
    local all="${1:-}" i req=() opt=() adv=() nreq=0 n
    command -v python3 >/dev/null 2>&1 || ensure_python || return 1
    scan_all
    prepare_cfg_files
    for i in "${!VS_NAME[@]}"; do
        case "${VS_SRC[i]}" in cfgex|cfgother) continue ;; esac
        case "${VS_GRP[i]}" in
            req) req+=("$i"); [[ "${VS_ST[i]}" == set ]] || nreq=$((nreq + 1)) ;;
            opt) opt+=("$i") ;;
            *)   adv+=("$i") ;;
        esac
    done
    if (( ${#req[@]} + ${#opt[@]} + ${#adv[@]} == 0 )); then
        warn "没有从代码里识别到需要填写的配置，改为手动指定变量名。"
        quick_keys_legacy
        return
    fi
    echo "  ${DIM}从代码中识别到：必填 ${#req[@]} 项（未填 ${nreq}）、可选 ${#opt[@]} 项、高级 ${#adv[@]} 项（一般不用改）${RESET}"
    for i in "${req[@]}"; do
        [[ -n "$all" || "${VS_ST[i]}" != set ]] && fill_var "$i"
    done
    local todo=()
    for i in "${opt[@]}"; do [[ -n "$all" || "${VS_ST[i]}" != set ]] && todo+=("$i"); done
    if (( ${#todo[@]} > 0 )); then
        echo
        echo "  ${DIM}可选项（不填也能运行，用到相关功能时再填）：${RESET}"
        for i in "${todo[@]}"; do
            printf '    %s  %s\n' "$(padr "${VS_NAME[i]}" 22)" "${DIM}$(var_info "$i")${RESET}"
        done
        if confirm "现在填写可选项？（每项都可以回车跳过）"; then
            for i in "${todo[@]}"; do fill_var "$i"; done
        fi
    fi
    n=0
    for i in "${req[@]}"; do [[ "${VS_ST[i]}" == set || -n "${VS_DEF[i]}" ]] || n=$((n + 1)); done
    echo
    if (( n > 0 )); then
        warn "还有 ${n} 个必填项没填，之后可在 管理机器人 → 5 里补填。"
    else
        log "必填项都已填好。"
    fi
}

# 查看 / 修改某一项
var_edit_menu() {
    local c i label st
    command -v python3 >/dev/null 2>&1 || ensure_python || return 1
    while true; do
        cls
        banner "查看 / 修改配置" "$BOT_NAME"
        scan_all
        echo
        local idx=() g
        for g in req opt adv; do
            local first=1
            for i in "${!VS_NAME[@]}"; do
                [[ "${VS_GRP[i]}" == "$g" ]] || continue
                case "${VS_SRC[i]}" in cfgex|cfgother) continue ;; esac
                if (( first )); then
                    case $g in req) label="必填" ;; opt) label="可选" ;; *) label="高级（一般不用改）" ;; esac
                    echo "  ${BOLD}${label}${RESET}"; first=0
                fi
                idx+=("$i")
                if [[ "${VS_ST[i]}" == set ]]; then st="${GREEN}已设置${RESET}"
                elif [[ -n "${VS_DEF[i]}" ]]; then st="${DIM}默认值${RESET}"
                else st="${YELLOW}未设置${RESET}"; fi
                printf '  %s) %s %s  %s\n' "${YELLOW}$(padr "${#idx[@]}" 2)${RESET}" "$(padr "${VS_NAME[i]}" 22)" "$st" "${DIM}$(var_info "$i")${RESET}"
            done
            (( first )) || echo
        done
        (( ${#idx[@]} )) || echo "  ${DIM}没有识别到配置项${RESET}"
        echo "  ${DIM}输入编号修改；0 返回${RESET}"
        rd c "请选择" ""
        [[ -z "$c" || "$c" == 0 ]] && return 0
        if [[ "$c" =~ ^[0-9]+$ ]] && (( c >= 1 && c <= ${#idx[@]} )); then
            fill_var "${idx[c-1]}"; pause
        else
            warn "无效选择：$c"; sleep 1
        fi
    done
}

# 改了配置, 机器人正在运行时问一下要不要重启
offer_restart() {
    (( VARS_CHANGED )) || return 0
    VARS_CHANGED=0
    if unit_exists && systemctl is-active --quiet "$SERVICE_NAME"; then
        echo
        confirm_y "配置已修改，重启机器人使其生效？" && bot_restart
    fi
    return 0
}

do_config() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        warn "目录不存在：$INSTALL_DIR，请先获取机器人代码。"
        return 1
    fi
    local c
    while true; do
        cls
        banner "写入密钥 / 配置" "$BOT_NAME"
        echo
        menu_item 1 "自动识别并填写（推荐）" "只问还没填的"
        menu_item 2 "查看 / 修改某一项" "列出全部识别到的配置"
        menu_item 3 "全部重新填写"
        menu_item 4 "手动指定变量名" "识别不到时用"
        menu_item 0 "返回"
        echo
        rd c "请选择"
        case "$c" in
            1) guided_fill; pause ;;
            2) var_edit_menu ;;
            3) guided_fill all; pause ;;
            4) do_config_manual ;;
            0) break ;;
            *) warn "无效选择：$c"; sleep 1 ;;
        esac
    done
    offer_restart
}

quick_keys() { guided_fill; }

add_bot() {
    local name url branch

    cls
    banner "添加机器人" "仓库拉取 → 密钥 → 启动"

    heading "[1/3] 机器人名称"
    echo "  ${DIM}用作 systemd 服务名；仅限小写字母、数字、- 和 _，例如 navbot${RESET}"
    while true; do
        rd name "名称（直接回车取消）"
        if [[ -z "$name" ]]; then warn "已取消。"; return 1; fi
        if [[ ! "$name" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then err "名称不合法（小写字母/数字开头，只含小写字母、数字、- 和 _）。"; continue; fi
        if [[ -f "$CONF_DIR/$name.conf" ]]; then err "已存在同名机器人：$name，请换一个。"; continue; fi
        if [[ -e "/etc/systemd/system/$name.service" || -e "/lib/systemd/system/$name.service" || -e "/usr/lib/systemd/system/$name.service" ]]; then
            err "系统里已存在同名服务 $name（会被覆盖），请换一个名称。"; continue
        fi
        break
    done

    heading "[2/3] 安装目录"
    pick_dir "/root/$name" || return 1
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

    heading "写入密钥 / 配置"
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
        menu_item 5 "写入密钥 / 配置" "自动识别代码需要的 Token、ID、密钥"
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

# ---------------------------------------------------------------- 快捷命令 tgbot
# 第一次运行时安装 /usr/local/bin/tgbot, 以后在 SSH 里输入 tgbot 就能打开本菜单。
# tgbot 每次都会拉取仓库里最新的脚本再运行 (和那条 curl 命令效果一样), 拉取失败就用上次保存的版本。
CMD_BIN=/usr/local/bin/tgbot
CMD_CACHE=/usr/local/lib/tgbot/tg-bot-gl.sh
SCRIPT_URL="https://raw.githubusercontent.com/chentiti888/tggl/main/tg-bot-gl.sh"

install_cmd() {
    [[ $EUID -eq 0 ]] || return 0
    if [[ -e "$CMD_BIN" ]]; then
        grep -q 'tgbot-launcher' "$CMD_BIN" 2>/dev/null || return 0   # 同名的别的程序, 不覆盖
        return 0
    fi
    mkdir -p "${CMD_CACHE%/*}"
    cat >"$CMD_BIN" <<EOF
#!/usr/bin/env bash
# tgbot-launcher: 每次运行都拉取仓库最新的 tg-bot-gl.sh, 拉取失败就用上次保存的版本
URL="$SCRIPT_URL"
CACHE="$CMD_CACHE"
mkdir -p "\${CACHE%/*}"
tmp=\$(mktemp)
if curl -fsSL --max-time 15 -H 'Cache-Control: no-cache' "\$URL?t=\$(date +%s)" -o "\$tmp" 2>/dev/null \\
   && head -1 "\$tmp" | grep -q bash && bash -n "\$tmp" 2>/dev/null; then
    mv -f "\$tmp" "\$CACHE"
else
    rm -f "\$tmp"
    [[ -f "\$CACHE" ]] || { echo "下载失败, 请检查网络: \$URL" >&2; exit 1; }
    echo "(获取最新版失败, 使用本地保存的版本)" >&2
fi
exec bash "\$CACHE" "\$@"
EOF
    chmod 755 "$CMD_BIN"
    echo
    log "已安装快捷命令：以后在 SSH 里输入 ${GREEN}tgbot${RESET} 就能打开本菜单"
    sleep 2
}
# ---------------------------------------------------------------- 自动导入已有机器人
# 本脚本创建的服务 (Description=Telegram Bot (名称)) 如果不在列表里 (比如用 wsm 全量备份搬到了新服务器),
# 自动加回列表, 菜单里就能直接管理, 不用重新部署
import_bots() {
    local f name wd ex main n=0
    has_systemd || return 0
    for f in /etc/systemd/system/*.service; do
        [[ -f "$f" ]] || continue
        grep -q '^Description=Telegram Bot (' "$f" || continue
        name="$(basename "$f" .service)"
        [[ "$name" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || continue
        [[ -f "$CONF_DIR/$name.conf" ]] && continue
        wd="$(sed -n 's/^WorkingDirectory=//p' "$f" | head -1)"
        [[ -n "$wd" && -d "$wd" ]] || continue
        ex="$(sed -n 's/^ExecStart=//p' "$f" | head -1)"
        main="${ex##* }"
        (
            reset_defaults
            BOT_NAME="$name"; SERVICE_NAME="$name"; INSTALL_DIR="$wd"; MAIN_PY="$main"
            CONF_FILE="$CONF_DIR/$name.conf"
            if [[ -d "$wd/.git" ]] && command -v git >/dev/null 2>&1; then
                SOURCE="repo"
                REPO_URL="$(git -C "$wd" remote get-url origin 2>/dev/null)"
                BRANCH="$(git -C "$wd" rev-parse --abbrev-ref HEAD 2>/dev/null)"
                [[ "$BRANCH" == HEAD ]] && BRANCH=""
            else
                SOURCE="local"
            fi
            save_conf
        )
        mkdir -p "$CONF_DIR"
        grep -qxF -- "$wd" "$DIRS_FILE" 2>/dev/null || echo "$wd" >> "$DIRS_FILE"
        n=$((n + 1))
        log "已导入机器人「$name」（$wd）"
    done
    (( n > 0 )) && sleep 2
    return 0
}

install_cmd
import_bots

# ---------------------------------------------------------------- 主菜单

while true; do
    list_dirs; list_bots
    cls
    banner "Telegram Bot Manager" "服务器 IP: ${SERVER_IP:-未知}"
    echo
    echo "  $(lab 目录)${#DIRS[@]} 个    $(lab 机器人)${#BOTS[@]} 个"
    [[ -x "$CMD_BIN" ]] && echo "  ${DIM}快捷命令：tgbot${RESET}"
    echo
    menu_item 1 "创建安装目录" "默认 /root/mybot，回车即可"
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
