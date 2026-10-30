#!/usr/bin/env bash
# 安装代理：节点 agent（纯 bash + curl）
#
# 安装（在节点服务器上用 root 执行；命令由操作台「工具 → 安装代理 → 添加节点」生成）：
#   curl -fsSL 'https://你的网址/px.php?a=sh' | bash -s -- https://你的网址 节点密钥
# 卸载 agent（不会卸载已装好的 hy2 / anytls / tuic / reality）：
#   bash /opt/px-agent/px.sh uninstall
#
# agent 定时向 https://你的网址/px.php 报到并领取任务（短连接），只向外发请求，不监听任何端口。
# 服务器下发的任务只有固定几种（安装 / 卸载 hy2、anytls、tuic、reality，重启，更新），参数都会再校验一遍。
# 更新 agent 时新脚本必须通过签名校验（公钥在下面 PX_PUB，安装时由机器人填入），否则拒绝替换。

AGENT_VER=14
PX_PUB=""
DIR=/opt/px-agent
SVC=px-agent
HY_DIR=/etc/hysteria
HY_SVC=hysteria-server.service
AT_DIR=/etc/anytls
AT_BIN=/usr/local/bin/sing-box-anytls     # anytls 和 tuic 共用这一个 sing-box（文件名沿用旧版，已装的节点不用动）
AT_SVC=anytls-server.service
TU_DIR=/etc/tuic
TU_SVC=tuic-server.service
RE_DIR=/etc/reality                       # VLESS + Reality：同样用共用的 sing-box，不需要证书
RE_SVC=reality-server.service
HOP_SVC=px-hop.service
FWD_SVC=px-fwd.service                       # 端口转发：内核 iptables DNAT，不跑额外进程
HY_STAT_PORT=25413                           # hy2 流量统计接口，只听本机
INTERVAL=10              # 默认 10 秒拉一次；以服务器下发的 NEXT 为准（有人在看 / 有任务时 3 秒）

# ---------------------------------------------------------------- 小工具
need_root() { [ "$(id -u)" = 0 ] || { echo "请用 root 执行"; exit 1; }; }

pkg_install() {            # pkg_install 命令名 [包名]
    command -v "$1" >/dev/null 2>&1 && return 0
    local p="${2:-$1}"
    if command -v apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get install -y "$p" >/dev/null 2>&1 || { apt-get update -y >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y "$p" >/dev/null 2>&1; }
    elif command -v dnf >/dev/null 2>&1; then dnf install -y "$p" >/dev/null 2>&1
    elif command -v yum >/dev/null 2>&1; then yum install -y "$p" >/dev/null 2>&1
    elif command -v apk >/dev/null 2>&1; then apk add --no-cache "$p" >/dev/null 2>&1
    fi
    command -v "$1" >/dev/null 2>&1
}

port80_busy() {            # 80 端口是否被占用。只用系统自带的 ss（ELF 程序）：有人在 /usr/local/bin 放了同名脚本会死循环，所以不走 PATH
    local b
    for b in /usr/bin/ss /usr/sbin/ss /bin/ss /sbin/ss; do
        [ -x "$b" ] && head -c4 "$b" 2>/dev/null | grep -q ELF || continue
        timeout 10 "$b" -ltn 2>/dev/null | grep -qE '[:.]80[[:space:]]'; return
    done
    return 1
}

b64() { base64 -w0 2>/dev/null || base64 | tr -d '\n'; }

svc_state() {              # 服务状态：active / inactive / failed / none
    local u="$1"
    if ! systemctl cat "$u" >/dev/null 2>&1; then echo none; return; fi
    local s; s=$(systemctl is-active "$u" 2>/dev/null); echo "${s:-inactive}"
}

fw_open() {                # fw_open 端口 tcp|udp  —— 有 ufw / firewalld 才处理，纯 iptables 默认放行不用管
    local port="$1" proto="$2"
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then ufw allow "$port/$proto" >/dev/null 2>&1; fi
    if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld 2>/dev/null; then
        firewall-cmd --permanent --add-port="$port/$proto" >/dev/null 2>&1; firewall-cmd --reload >/dev/null 2>&1
    fi
}

mkcert() {                 # mkcert 目录 域名 —— 自签证书（EC，10 年）；域名没变就沿用
    local dir="$1" sni="$2"
    mkdir -p "$dir"
    if [ -s "$dir/server.crt" ] && [ -s "$dir/server.key" ] && openssl x509 -in "$dir/server.crt" -noout -subject 2>/dev/null | grep -q "CN *= *$sni\$"; then return 0; fi
    openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -keyout "$dir/server.key" -out "$dir/server.crt" \
        -subj "/CN=$sni" -days 3650 >/dev/null 2>&1 || { echo "生成证书失败（需要 openssl）"; return 1; }
    chmod 600 "$dir/server.key"; chmod 644 "$dir/server.crt"
}

# 域名证书：节点用 80 端口向 Let's Encrypt 申请（acme.sh standalone，不需要 Cloudflare 令牌）；域名要已经解析到本机（小程序会先建好灰云解析）
CERT_DIR=/etc/px-cert
CERT_CRT=""; CERT_KEY=""
get_cert() {
    local dom="$1" dir="$CERT_DIR/$1" out rc acme="$HOME/.acme.sh/acme.sh"
    # 已经有证书、至少还有 14 天有效：直接沿用（改端口 / 加分享账号重装时不用再占 80 端口去申请）
    if [ -s "$dir/fullchain.pem" ] && [ -s "$dir/key.pem" ] && openssl x509 -in "$dir/fullchain.pem" -noout -checkend 1209600 >/dev/null 2>&1; then
        CERT_CRT="$dir/fullchain.pem"; CERT_KEY="$dir/key.pem"; echo "沿用已有证书"; return 0
    fi
    pkg_install curl; pkg_install socat; pkg_install openssl
    if ! command -v crontab >/dev/null 2>&1; then
        pkg_install crontab cron || pkg_install crontab cronie
        systemctl enable --now cron >/dev/null 2>&1 || systemctl enable --now crond >/dev/null 2>&1
    fi
    if [ ! -x "$acme" ]; then
        echo "安装 acme.sh..."
        curl -fsSL --connect-timeout 15 https://get.acme.sh 2>/dev/null | sh -s email="px@$dom" >/dev/null 2>&1
        [ -x "$acme" ] || { echo "acme.sh 安装失败（节点连不上 get.acme.sh / github？）"; return 1; }
    fi
    if port80_busy; then echo "80 端口被占用（nginx / apache？），无法申请证书"; return 1; fi
    fw_open 80 tcp
    local SAFE_PATH=/usr/sbin:/usr/bin:/sbin:/bin     # acme.sh 内部也会调用 ss；PATH 里去掉 /usr/local/bin，避免被同名脚本顶替而卡死
    env PATH=$SAFE_PATH "$acme" --set-default-ca --server letsencrypt >/dev/null 2>&1
    echo "向 Let's Encrypt 申请 $dom 的证书..."
    out=$(env PATH=$SAFE_PATH timeout 300 "$acme" --issue -d "$dom" --standalone --httpport 80 --keylength ec-256 2>&1); rc=$?
    if [ "$rc" != 0 ] && [ "$rc" != 2 ]; then
        echo "申请证书失败（检查：域名是否解析到本机 / 云厂商安全组是否放行 TCP 80）："; echo "$out" | tail -n 8; return 1
    fi
    mkdir -p "$dir"
    env PATH=$SAFE_PATH "$acme" --install-cert -d "$dom" --ecc --fullchain-file "$dir/fullchain.pem" --key-file "$dir/key.pem" \
        --reloadcmd "for s in hysteria-server anytls-server tuic-server; do systemctl is-enabled --quiet \$s.service 2>/dev/null && systemctl restart \$s.service >/dev/null 2>&1; done; true" >/dev/null 2>&1 \
        || { echo "安装证书失败"; return 1; }
    chmod 755 "$CERT_DIR" "$dir"; chmod 644 "$dir/fullchain.pem"
    if id hysteria >/dev/null 2>&1; then chgrp hysteria "$dir/key.pem"; chmod 640 "$dir/key.pem"; else chmod 600 "$dir/key.pem"; fi
    CERT_CRT="$dir/fullchain.pem"; CERT_KEY="$dir/key.pem"
    echo "证书已就绪（到期前由 acme.sh 自动续期）"
}

cert_fp() { openssl x509 -in "$1" -noout -fingerprint -sha256 2>/dev/null | sed 's/.*=//'; }

# ---------------------------------------------------------------- hy2
hop_script() {             # 端口跳跃：把一段 UDP 端口转到 hy2 主端口，开机由 px-hop.service 恢复
    cat > "$DIR/hop.sh" <<'EOF'
#!/bin/bash
# 用法：hop.sh up|down   配置：/etc/hysteria/port-hopping.conf（RANGE=起:止  TARGET_PORT=主端口）
[ -f /etc/hysteria/port-hopping.conf ] || exit 0
. /etc/hysteria/port-hopping.conf
for ipt in iptables ip6tables; do
    command -v $ipt >/dev/null 2>&1 || continue
    $ipt -t nat -D PREROUTING -p udp --dport "$RANGE" -j REDIRECT --to-ports "$TARGET_PORT" 2>/dev/null
    [ "$1" = up ] && $ipt -t nat -A PREROUTING -p udp --dport "$RANGE" -j REDIRECT --to-ports "$TARGET_PORT" 2>/dev/null
done
exit 0
EOF
    chmod 755 "$DIR/hop.sh"
}

hop_off() {
    if [ -f "$DIR/hop.sh" ]; then bash "$DIR/hop.sh" down; fi
    systemctl disable --now "$HOP_SVC" >/dev/null 2>&1
    rm -f /etc/systemd/system/$HOP_SVC "$HY_DIR/port-hopping.conf"
    systemctl daemon-reload >/dev/null 2>&1
}

hop_on() {                 # hop_on 范围(20000-30000) 主端口
    local range="${1/-/:}" port="$2"
    command -v iptables >/dev/null 2>&1 || pkg_install iptables || { echo "没有 iptables，无法设置端口跳跃"; return 1; }
    hop_script
    [ -f "$DIR/hop.sh" ] && bash "$DIR/hop.sh" down
    printf 'RANGE=%s\nTARGET_PORT=%s\n' "$range" "$port" > "$HY_DIR/port-hopping.conf"
    cat > /etc/systemd/system/$HOP_SVC <<EOF
[Unit]
Description=Hysteria2 port hopping (px-agent)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash $DIR/hop.sh up
ExecStop=/bin/bash $DIR/hop.sh down

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload >/dev/null 2>&1
    systemctl enable "$HOP_SVC" >/dev/null 2>&1
    systemctl restart "$HOP_SVC" || { echo "端口跳跃服务启动失败"; return 1; }
    if ! iptables -t nat -C PREROUTING -p udp --dport "$range" -j REDIRECT --to-ports "$port" 2>/dev/null; then echo "端口跳跃规则没有生效"; return 1; fi
    echo "端口跳跃已开启：UDP $1 → $port"
}

# ---------------------------------------------------------------- 分享账号（USERS=编号:uuid:密码,... ；由机器人下发，每个分享一组独立账号）
valid_users() {
    local e id uu pw n
    [ -z "$USERS" ] && return 0
    n=$(printf '%s' "$USERS" | tr ',' '\n' | wc -l)
    [ "$n" -le 100 ] || return 1
    local IFS=','
    for e in $USERS; do
        IFS=: read -r id uu pw <<< "$e"
        [[ "$id" =~ ^[a-z0-9]{8}$ ]] || return 1
        [[ "$uu" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || return 1
        [[ "$pw" =~ ^[A-Za-z0-9]{16}$ ]] || return 1
    done
    return 0
}

hy_stat_secret() {         # 统计接口密钥：第一次用时随机生成，只有 root 能读
    local f="$HY_DIR/stats.secret"
    [ -s "$f" ] || ( umask 077; head -c 200 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 24 > "$f" )
    cat "$f"
}

hy_users_yaml() {          # 有分享账号：改成 userpass（owner 是自己，其余是分享）并打开本机流量统计
    local e id uu pw
    echo "auth:"
    echo "  type: userpass"
    echo "  userpass:"
    echo "    \"owner\": \"$PASS\""
    local IFS=','
    for e in $USERS; do
        IFS=: read -r id uu pw <<< "$e"
        echo "    \"$id\": \"$pw\""
    done
    echo "trafficStats:"
    echo "  listen: 127.0.0.1:$HY_STAT_PORT"
    echo "  secret: \"$(hy_stat_secret)\""
}

sb_users() {               # sb_users anytls|tuic|reality → sing-box users 数组的内容（自己的账号在最前）
    local out e id uu pw
    case "$1" in
        anytls) out="{\"name\": \"user\", \"password\": \"$PASS\"}" ;;
        tuic) out="{\"name\": \"user\", \"uuid\": \"$UUID\", \"password\": \"$PASS\"}" ;;
        reality) out="{\"name\": \"user\", \"uuid\": \"$UUID\", \"flow\": \"xtls-rprx-vision\"}" ;;
    esac
    local IFS=','
    for e in $USERS; do
        IFS=: read -r id uu pw <<< "$e"
        case "$1" in
            anytls) out="$out, {\"name\": \"$id\", \"password\": \"$pw\"}" ;;
            tuic) out="$out, {\"name\": \"$id\", \"uuid\": \"$uu\", \"password\": \"$pw\"}" ;;
            reality) out="$out, {\"name\": \"$id\", \"uuid\": \"$uu\", \"flow\": \"xtls-rprx-vision\"}" ;;
        esac
    done
    printf '%s' "$out"
}

# 流量统计：读一次就清零，累加在 tr.pend 里，机器人回 TRACK 确认收到后才删（没收到就下次接着报，不会丢）
tr_collect() {
    local sec out pr
    for pr in anytls tuic reality; do
        [ -f "$DIR/sbstat.$pr" ] && sb_stat_read "$(sb_stat_port "$pr")" >> "$DIR/tr.pend"
    done
    [ -s "$HY_DIR/stats.secret" ] || return 0
    sec=$(cat "$HY_DIR/stats.secret" 2>/dev/null)
    out=$(curl -fsS -m 3 -H "Authorization: $sec" "http://127.0.0.1:$HY_STAT_PORT/traffic?clear=1" 2>/dev/null) || return 0
    printf '%s' "$out" | grep -oE '"[a-z0-9]{8}":\{"tx":[0-9]+,"rx":[0-9]+\}' \
        | sed -E 's/"([a-z0-9]{8})":\{"tx":([0-9]+),"rx":([0-9]+)\}/\1 \2 \3/' \
        | while read -r i a b; do echo "$i $((a + b))"; done >> "$DIR/tr.pend"
}

tr_line() {
    [ -s "$DIR/tr.pend" ] || return 0
    awk '{s[$1]+=$2} END{o=""; for(k in s){o=o (o==""?"":",") k ":" sprintf("%d", s[k])} print "TR=" o}' "$DIR/tr.pend"
}

job_hy2() {                # 需要 PORT PASS SNI；可选 OBFS HOP、USERS
    pkg_install curl; pkg_install openssl
    if ! command -v hysteria >/dev/null 2>&1; then
        echo "安装 Hysteria2 内核..."
        bash <(curl -fsSL --connect-timeout 15 https://get.hy2.sh/) 2>&1 | tail -n 8
        command -v hysteria >/dev/null 2>&1 || { echo "Hysteria2 安装失败（节点连不上 github / get.hy2.sh？）"; return 1; }
    fi
    mkdir -p "$HY_DIR"
    local crt="$HY_DIR/server.crt" key="$HY_DIR/server.key" masq="$SNI"
    if [ -n "$DOMAIN" ]; then get_cert "$DOMAIN" || return 1; crt="$CERT_CRT"; key="$CERT_KEY"; masq="www.bing.com"
    else mkcert "$HY_DIR" "$SNI" || return 1; fi
    if id hysteria >/dev/null 2>&1; then chown -R hysteria:hysteria "$HY_DIR" 2>/dev/null; else chmod 644 "$HY_DIR/server.key" 2>/dev/null; fi
    {
        echo "listen: :$PORT"
        echo "tls:"
        echo "  cert: $crt"
        echo "  key: $key"
        if [ -n "$USERS" ]; then hy_users_yaml
        else
            echo "auth:"
            echo "  type: password"
            echo "  password: \"$PASS\""
        fi
        if [ -n "$OBFS" ]; then
            echo "obfs:"
            echo "  type: salamander"
            echo "  salamander:"
            echo "    password: \"$OBFS\""
        fi
        echo "masquerade:"
        echo "  type: proxy"
        echo "  proxy:"
        echo "    url: https://$masq/"
        echo "    rewriteHost: true"
    } > "$HY_DIR/config.yaml"
    [ -n "$USERS" ] || rm -f "$HY_DIR/stats.secret" "$DIR/tr.pend"
    chmod 640 "$HY_DIR/config.yaml"; id hysteria >/dev/null 2>&1 && chown hysteria:hysteria "$HY_DIR/config.yaml"
    fw_open "$PORT" udp
    systemctl daemon-reload >/dev/null 2>&1
    systemctl enable "$HY_SVC" >/dev/null 2>&1
    systemctl restart "$HY_SVC"
    sleep 2
    if ! systemctl is-active --quiet "$HY_SVC"; then
        echo "Hysteria2 启动失败，日志："; journalctl -u "$HY_SVC" -n 15 --no-pager 2>&1 | tail -n 15; return 1
    fi
    if [ -n "$HOP" ]; then hop_on "$HOP" "$PORT" || return 1; else hop_off; fi
    [ -n "$DOMAIN" ] || FP=$(cert_fp "$HY_DIR/server.crt")
    echo "Hysteria2 已运行：UDP $PORT${DOMAIN:+ · 域名证书 $DOMAIN}${OBFS:+ · 混淆 salamander}${HOP:+ · 端口跳跃 $HOP}"
}

job_rm_hy2() {
    hop_off
    systemctl disable --now "$HY_SVC" >/dev/null 2>&1
    bash <(curl -fsSL --connect-timeout 15 https://get.hy2.sh/) --remove 2>&1 | tail -n 3
    rm -rf "$HY_DIR"
    echo "Hysteria2 已卸载"
}

# ---------------------------------------------------------------- anytls（sing-box >= 1.12）
at_arch() { case "$(uname -m)" in x86_64|amd64) echo amd64;; aarch64|arm64) echo arm64;; armv7l) echo armv7;; i386|i686) echo 386;; *) echo "";; esac; }

at_bin_ok() { [ -x "$AT_BIN" ] && "$AT_BIN" version 2>/dev/null | head -n1 | grep -qE 'version 1\.(1[2-9]|[2-9][0-9])|version [2-9]\.'; }

AT_FALLBACK_VER=1.12.0     # GitHub 的接口和跳转都查不到最新版本时用这个

at_install_bin() {
    local arch ver tmp url bin rel name want="" got html
    arch=$(at_arch); [ -n "$arch" ] || { echo "不支持的架构：$(uname -m)"; return 1; }
    # 1) 先问 GitHub 接口（带每个文件的 SHA256）；2) 接口连不上 / 被限流，就从 github.com 的“最新发布”跳转里取版本号
    rel=$(curl -fsSL --connect-timeout 8 -m 20 https://api.github.com/repos/SagerNet/sing-box/releases/latest 2>/dev/null)
    ver=$(echo "$rel" | grep -m1 '"tag_name"' | sed -E 's/.*"v?([0-9][^"]*)".*/\1/')
    if ! echo "$ver" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
        rel=""
        ver=$(curl -fsSI --connect-timeout 8 -m 20 https://github.com/SagerNet/sing-box/releases/latest 2>/dev/null | tr -d '\r' | grep -i '^location:' | sed -E 's#.*/tag/v([0-9]+\.[0-9]+\.[0-9]+).*#\1#' | head -n1)
    fi
    if ! echo "$ver" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
        ver=$AT_FALLBACK_VER; echo "查不到 sing-box 最新版本（节点连不上 api.github.com / github.com？），改用内置版本 v$ver"
    fi
    name="sing-box-${ver}-linux-${arch}.tar.gz"
    # 官方校验值：接口里有 "digest":"sha256:..."；没有就从发布页的文件列表里找
    if [ -n "$rel" ]; then
        want=$(echo "$rel" | tr ',{}' '\n\n\n' | awk -v n="\"name\":\"$name\"" '
            { gsub(/^[ \t]+/, ""); gsub(/": "/, "\":\"") }
            $0 == n { f = 1; next }
            f && /^"browser_download_url":/ { f = 0 }
            f && /^"digest":"sha256:/ { sub(/^"digest":"sha256:/, ""); sub(/".*/, ""); print; exit }')
    fi
    if ! echo "$want" | grep -qE '^[0-9a-f]{64}$'; then
        html=$(curl -fsSL --connect-timeout 8 -m 20 "https://github.com/SagerNet/sing-box/releases/expanded_assets/v${ver}" 2>/dev/null)
        want=$(echo "$html" | tr '<>' '\n\n' | awk -v n="$name" '
            $0 == n { f = 1; c = 0; next }
            f { c++; if (match($0, /sha256:[0-9a-f]{64}/)) { print substr($0, RSTART + 7, 64); exit } if (c > 40) exit }')
    fi
    tmp=$(mktemp -d)
    url="https://github.com/SagerNet/sing-box/releases/download/v${ver}/${name}"
    echo "下载 sing-box v$ver ($arch)..."
    curl -fL --retry 3 --connect-timeout 15 -s -o "$tmp/sb.tgz" "$url" || { echo "下载失败（节点连不上 github？）"; rm -rf "$tmp"; return 1; }
    if echo "$want" | grep -qE '^[0-9a-f]{64}$'; then
        got=$(sha256sum "$tmp/sb.tgz" | cut -d' ' -f1)
        [ "$got" = "$want" ] || { echo "sing-box 校验失败（下载内容和官方不一致），不安装"; rm -rf "$tmp"; return 1; }
        echo "SHA256 校验通过"
    else
        echo "⚠ 没拿到官方校验值，这次下载没有做 SHA256 校验（地址是 GitHub 官方发布，走 HTTPS）"
    fi
    tar -xzf "$tmp/sb.tgz" -C "$tmp" || { rm -rf "$tmp"; return 1; }
    bin=$(find "$tmp" -type f -name sing-box | head -n1)
    [ -n "$bin" ] || { echo "安装包里没有 sing-box"; rm -rf "$tmp"; return 1; }
    install -m 755 "$bin" "$AT_BIN"; rm -rf "$tmp"
    at_bin_ok || { echo "sing-box 版本过低（anytls 需要 >= 1.12.0）"; return 1; }
}

# ---------------------------------------------------------------- 统计版 sing-box（anytls / tuic / reality 按分享统计流量用）
# 官方 sing-box 没编译用户统计功能；机器人目录里放了自己编译的（带 v2ray_api），agent 从机器人下载并验签后替换共用的 sing-box。
sb_stat_port() { case "$1" in anytls) echo 25421 ;; tuic) echo 25422 ;; reality) echo 25423 ;; esac; }

at_stats_ok() { [ -x "$AT_BIN" ] && "$AT_BIN" version 2>/dev/null | grep -q with_v2ray_api; }

at_install_stats() {
    local arch tmp sig
    arch=$(at_arch); [ -n "$arch" ] || { echo "不支持的架构：$(uname -m)"; return 1; }
    pkg_install curl; pkg_install openssl
    tmp=$(mktemp -d)
    echo "下载统计版 sing-box ($arch)..."
    curl -fsS --connect-timeout 10 -m 300 -D "$tmp/h" -o "$tmp/sb.gz" "$WEB/px.php?a=sb&arch=$arch" || { echo "下载失败：机器人那边没放统计版 sing-box（sb-stats-linux-$arch.gz），或网络不通"; rm -rf "$tmp"; return 1; }
    if [ -n "$PX_PUB" ]; then
        sig=$(grep -i '^x-px-sig:' "$tmp/h" | head -n1 | cut -d: -f2- | tr -d ' \r\n')
        [ -n "$sig" ] || { echo "统计版 sing-box 没有签名，拒绝安装"; rm -rf "$tmp"; return 1; }
        echo "$PX_PUB" | base64 -d > "$tmp/pub" 2>/dev/null; echo "$sig" | base64 -d > "$tmp/sig" 2>/dev/null
        openssl dgst -sha256 -verify "$tmp/pub" -signature "$tmp/sig" "$tmp/sb.gz" >/dev/null 2>&1 || { echo "统计版 sing-box 签名校验失败，拒绝安装"; rm -rf "$tmp"; return 1; }
    fi
    gunzip -c "$tmp/sb.gz" > "$tmp/sb" 2>/dev/null && chmod 755 "$tmp/sb" || { echo "解压失败"; rm -rf "$tmp"; return 1; }
    "$tmp/sb" version 2>/dev/null | grep -q with_v2ray_api || { echo "这个 sing-box 没带统计功能"; rm -rf "$tmp"; return 1; }
    install -m 755 "$tmp/sb" "$AT_BIN"; rm -rf "$tmp"
    echo "统计版 sing-box 已安装"
}

sb_need() {                # sb_need：按 STATS 决定要官方版还是统计版 sing-box
    if [ "$STATS" = 1 ]; then at_stats_ok || at_install_stats; else at_bin_ok || at_install_bin; fi
}

sb_exp() {                 # sb_exp 协议 → 配置里 experimental 那一段（没开统计就什么都不输出）
    local ids="" e id uu pw
    if [ "$STATS" != 1 ]; then rm -f "$DIR/sbstat.$1"; return 0; fi
    local IFS=','
    for e in $USERS; do
        IFS=: read -r id uu pw <<< "$e"
        ids="$ids${ids:+, }\"$id\""
    done
    echo "$1" > "$DIR/sbstat.$1"
    printf ', "experimental": {"v2ray_api": {"listen": "127.0.0.1:%s", "stats": {"enabled": true, "users": [%s]}}}' "$(sb_stat_port "$1")" "$ids"
}

# 读一次统计（gRPC QueryStats，pattern=user>>>，reset=true 读完清零）→ 输出「编号 字节数」。只用 curl 的 HTTP/2，不装 grpcurl
sb_stat_read() {           # sb_stat_read 端口；curl 出错返回 1
    local req="$DIR/sbq.bin" resp="$DIR/sbr.bin"
    printf '\x00\x00\x00\x00\x0b\x0a\x07user>>>\x10\x01' > "$req"
    curl -fsS -m 3 --http2-prior-knowledge -H 'content-type: application/grpc' -H 'te: trailers' --data-binary @"$req" -o "$resp" \
        "http://127.0.0.1:$1/v2ray.core.app.stats.command.StatsService/QueryStats" 2>/dev/null || return 1
    od -An -v -tu1 "$resp" | awk '
        { for (i = 1; i <= NF; i++) b[n++] = $i }
        END {
            p = 5
            while (p < n) {
                if (b[p++] != 10) break
                len = 0; sh = 0
                while (1) { c = b[p++]; len += (c % 128) * (2 ^ sh); sh += 7; if (c < 128) break }
                pe = p + len; name = ""; val = 0
                while (p < pe) {
                    t = b[p++]
                    if (t == 10) { l = b[p++]; for (k = 0; k < l; k++) name = name sprintf("%c", b[p++]) }
                    else if (t == 16) { v = 0; sh = 0; while (1) { c = b[p++]; v += (c % 128) * (2 ^ sh); sh += 7; if (c < 128) break } val = v }
                    else break
                }
                p = pe
                split(name, a, ">>>")
                if (a[1] == "user" && a[2] != "" && val > 0) s[a[2]] += val
            }
            for (k in s) printf "%s %d\n", k, s[k]
        }'
    rm -f "$req" "$resp"
    return 0
}

sb_stat_probe() {          # 装好后试读一次：读不通（curl 太旧不支持 HTTP/2 等）就报错，不然流量统计会悄悄失效
    [ "$STATS" = 1 ] || return 0
    sleep 1
    sb_stat_read "$(sb_stat_port "$1")" >/dev/null || { echo "统计接口读取失败（节点的 curl 需要支持 HTTP/2，或统计端口被占用）"; return 1; }
}

sb_unit() {                # sb_unit 服务名 说明 配置文件：用共用的 sing-box 跑一个服务
    cat > "/etc/systemd/system/$1" <<EOF
[Unit]
Description=$2
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$AT_BIN run -c $3
Restart=on-failure
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
}

sb_bin_rm() {              # anytls / tuic / reality 都卸载了才删共用的 sing-box
    [ -f "/etc/systemd/system/$AT_SVC" ] || [ -f "/etc/systemd/system/$TU_SVC" ] || [ -f "/etc/systemd/system/$RE_SVC" ] || rm -f "$AT_BIN"
}

job_anytls() {             # 需要 PORT PASS SNI
    pkg_install curl; pkg_install openssl; pkg_install tar
    sb_need || return 1
    mkdir -p "$AT_DIR"; chmod 700 "$AT_DIR"
    local crt="$AT_DIR/server.crt" key="$AT_DIR/server.key"
    if [ -n "$DOMAIN" ]; then get_cert "$DOMAIN" || return 1; crt="$CERT_CRT"; key="$CERT_KEY"
    else mkcert "$AT_DIR" "$SNI" || return 1; fi
    cat > "$AT_DIR/config.new.json" <<EOF
{
  "log": {"level": "warn", "timestamp": true},
  "inbounds": [{
    "type": "anytls", "tag": "anytls-in", "listen": "::", "listen_port": $PORT,
    "users": [$(sb_users anytls)],
    "tls": {"enabled": true, "server_name": "$SNI", "certificate_path": "$crt", "key_path": "$key"}
  }],
  "outbounds": [{"type": "direct", "tag": "direct"}]$(sb_exp anytls)
}
EOF
    local out
    if ! out=$("$AT_BIN" check -c "$AT_DIR/config.new.json" 2>&1); then rm -f "$AT_DIR/config.new.json"; echo "配置校验失败：$out"; return 1; fi
    mv -f "$AT_DIR/config.new.json" "$AT_DIR/config.json"; chmod 600 "$AT_DIR/config.json"
    sb_unit "$AT_SVC" "AnyTLS Server (sing-box)" "$AT_DIR/config.json"
    fw_open "$PORT" tcp
    systemctl daemon-reload >/dev/null 2>&1
    systemctl enable "$AT_SVC" >/dev/null 2>&1
    systemctl restart "$AT_SVC"
    sleep 2
    if ! systemctl is-active --quiet "$AT_SVC"; then echo "AnyTLS 启动失败，日志："; journalctl -u "$AT_SVC" -n 15 --no-pager 2>&1 | tail -n 15; return 1; fi
    sb_stat_probe anytls || return 1
    [ -n "$DOMAIN" ] || FP=$(cert_fp "$AT_DIR/server.crt")
    echo "AnyTLS 已运行：TCP $PORT${DOMAIN:+ · 域名证书 $DOMAIN}"
}

job_rm_anytls() {
    systemctl disable --now "$AT_SVC" >/dev/null 2>&1
    rm -f /etc/systemd/system/$AT_SVC "$DIR/sbstat.anytls"; rm -rf "$AT_DIR"
    sb_bin_rm
    systemctl daemon-reload >/dev/null 2>&1
    echo "AnyTLS 已卸载"
}

# ---------------------------------------------------------------- VLESS + Reality（和 anytls / tuic 共用同一个 sing-box）
# 不需要域名和证书：借一个真实网站（SNI）的 TLS 握手伪装。私钥只留在节点上，公钥回报给小程序生成链接。
job_reality() {            # 需要 PORT UUID SID SNI
    pkg_install curl; pkg_install tar
    sb_need || return 1
    mkdir -p "$RE_DIR"; chmod 700 "$RE_DIR"
    # 伪装网站必须从这台节点能连上、并支持 TLS 1.3，否则客户端握手会失败
    if ! curl -sS -o /dev/null --connect-timeout 8 -m 15 --tlsv1.3 "https://$SNI/" 2>"$RE_DIR/probe.err"; then
        echo "节点连不上伪装网站 $SNI（要能用 TLS 1.3 访问 https://$SNI/）：$(tail -n1 "$RE_DIR/probe.err")"; rm -f "$RE_DIR/probe.err"; return 1
    fi
    rm -f "$RE_DIR/probe.err"
    local priv pub kp
    if [ -s "$RE_DIR/keys" ]; then
        priv=$(sed -n 's/^PrivateKey: *//p' "$RE_DIR/keys" | head -n1); pub=$(sed -n 's/^PublicKey: *//p' "$RE_DIR/keys" | head -n1)
    fi
    if ! [[ "$priv" =~ ^[A-Za-z0-9_-]{43}$ && "$pub" =~ ^[A-Za-z0-9_-]{43}$ ]]; then
        kp=$("$AT_BIN" generate reality-keypair 2>&1) || { echo "生成 Reality 密钥失败：$kp"; return 1; }
        priv=$(echo "$kp" | sed -n 's/^PrivateKey: *//p' | head -n1); pub=$(echo "$kp" | sed -n 's/^PublicKey: *//p' | head -n1)
        [[ "$priv" =~ ^[A-Za-z0-9_-]{43}$ && "$pub" =~ ^[A-Za-z0-9_-]{43}$ ]] || { echo "没有读到 Reality 密钥：$kp"; return 1; }
        ( umask 077; printf 'PrivateKey: %s\nPublicKey: %s\n' "$priv" "$pub" > "$RE_DIR/keys" )
    fi
    cat > "$RE_DIR/config.new.json" <<EOF
{
  "log": {"level": "warn", "timestamp": true},
  "inbounds": [{
    "type": "vless", "tag": "vless-in", "listen": "::", "listen_port": $PORT,
    "users": [$(sb_users reality)],
    "tls": {"enabled": true, "server_name": "$SNI",
      "reality": {"enabled": true, "handshake": {"server": "$SNI", "server_port": 443}, "private_key": "$priv", "short_id": ["$SID"]}}
  }],
  "outbounds": [{"type": "direct", "tag": "direct"}]$(sb_exp reality)
}
EOF
    local out
    if ! out=$("$AT_BIN" check -c "$RE_DIR/config.new.json" 2>&1); then rm -f "$RE_DIR/config.new.json"; echo "配置校验失败：$out"; return 1; fi
    mv -f "$RE_DIR/config.new.json" "$RE_DIR/config.json"; chmod 600 "$RE_DIR/config.json"
    sb_unit "$RE_SVC" "VLESS Reality Server (sing-box)" "$RE_DIR/config.json"
    fw_open "$PORT" tcp
    systemctl daemon-reload >/dev/null 2>&1
    systemctl enable "$RE_SVC" >/dev/null 2>&1
    systemctl restart "$RE_SVC"
    sleep 2
    if ! systemctl is-active --quiet "$RE_SVC"; then echo "VLESS Reality 启动失败，日志："; journalctl -u "$RE_SVC" -n 15 --no-pager 2>&1 | tail -n 15; return 1; fi
    sb_stat_probe reality || return 1
    PBK="$pub"
    echo "VLESS Reality 已运行：TCP $PORT · 伪装 $SNI"
}

job_rm_reality() {
    systemctl disable --now "$RE_SVC" >/dev/null 2>&1
    rm -f /etc/systemd/system/$RE_SVC "$DIR/sbstat.reality"; rm -rf "$RE_DIR"
    sb_bin_rm
    systemctl daemon-reload >/dev/null 2>&1
    echo "VLESS Reality 已卸载"
}

# ---------------------------------------------------------------- tuic v5（和 anytls 共用同一个 sing-box）
job_tuic() {               # 需要 PORT UUID PASS SNI
    pkg_install curl; pkg_install openssl; pkg_install tar
    sb_need || return 1
    mkdir -p "$TU_DIR"; chmod 700 "$TU_DIR"
    local crt="$TU_DIR/server.crt" key="$TU_DIR/server.key"
    if [ -n "$DOMAIN" ]; then get_cert "$DOMAIN" || return 1; crt="$CERT_CRT"; key="$CERT_KEY"
    else mkcert "$TU_DIR" "$SNI" || return 1; fi
    cat > "$TU_DIR/config.new.json" <<EOF
{
  "log": {"level": "warn", "timestamp": true},
  "inbounds": [{
    "type": "tuic", "tag": "tuic-in", "listen": "::", "listen_port": $PORT,
    "users": [$(sb_users tuic)],
    "congestion_control": "bbr", "auth_timeout": "3s", "zero_rtt_handshake": false, "heartbeat": "10s",
    "tls": {"enabled": true, "server_name": "$SNI", "alpn": ["h3"], "certificate_path": "$crt", "key_path": "$key"}
  }],
  "outbounds": [{"type": "direct", "tag": "direct"}]$(sb_exp tuic)
}
EOF
    local out
    if ! out=$("$AT_BIN" check -c "$TU_DIR/config.new.json" 2>&1); then rm -f "$TU_DIR/config.new.json"; echo "配置校验失败：$out"; return 1; fi
    mv -f "$TU_DIR/config.new.json" "$TU_DIR/config.json"; chmod 600 "$TU_DIR/config.json"
    sb_unit "$TU_SVC" "TUIC Server (sing-box)" "$TU_DIR/config.json"
    fw_open "$PORT" udp
    systemctl daemon-reload >/dev/null 2>&1
    systemctl enable "$TU_SVC" >/dev/null 2>&1
    systemctl restart "$TU_SVC"
    sleep 2
    if ! systemctl is-active --quiet "$TU_SVC"; then echo "TUIC 启动失败，日志："; journalctl -u "$TU_SVC" -n 15 --no-pager 2>&1 | tail -n 15; return 1; fi
    sb_stat_probe tuic || return 1
    echo "TUIC 已运行：UDP $PORT · bbr${DOMAIN:+ · 域名证书 $DOMAIN}"
}

job_rm_tuic() {
    systemctl disable --now "$TU_SVC" >/dev/null 2>&1
    rm -f /etc/systemd/system/$TU_SVC "$DIR/sbstat.tuic"; rm -rf "$TU_DIR"
    sb_bin_rm
    systemctl daemon-reload >/dev/null 2>&1
    echo "TUIC 已卸载"
}

# ---------------------------------------------------------------- 任务分发（先校验再执行）
OP=""; JID=""; PORT=""; PASS=""; OBFS=""; SNI=""; HOP=""; FP=""; DOMAIN=""; UUID=""; SID=""; PBK=""; USERS=""; STATS=""

# ---------------------------------------------------------------- 端口转发（内核 iptables，不装任何转发工具）
fwd_script() {             # 规则在 $DIR/fwd.rules，每行：协议 本机端口 目标IP 目标端口；开机由 px-fwd.service 恢复
    cat > "$DIR/fwd.sh" <<'EOF'
#!/bin/bash
# 用法：fwd.sh up|down
R=/opt/px-agent/fwd.rules
command -v iptables >/dev/null 2>&1 || exit 0
del() {
    iptables -t nat -D PREROUTING -j PXFWD_PRE 2>/dev/null
    iptables -t nat -D POSTROUTING -j PXFWD_POST 2>/dev/null
    iptables -D FORWARD -j PXFWD_FWD 2>/dev/null
    local c
    for c in "nat PXFWD_PRE" "nat PXFWD_POST" "filter PXFWD_FWD"; do
        set -- $c
        iptables -t "$1" -F "$2" 2>/dev/null; iptables -t "$1" -X "$2" 2>/dev/null
    done
}
del
[ "$1" = up ] || exit 0
[ -s "$R" ] || exit 0
iptables -t nat -N PXFWD_PRE; iptables -t nat -N PXFWD_POST; iptables -N PXFWD_FWD
iptables -A PXFWD_FWD -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
while read -r proto port ip tport; do
    [ -n "$proto" ] || continue
    iptables -t nat -A PXFWD_PRE -p "$proto" --dport "$port" -j DNAT --to-destination "$ip:$tport"
    iptables -t nat -A PXFWD_POST -p "$proto" -d "$ip" --dport "$tport" -j MASQUERADE
    iptables -A PXFWD_FWD -p "$proto" -d "$ip" --dport "$tport" -j ACCEPT
done < "$R"
iptables -t nat -I PREROUTING 1 -j PXFWD_PRE
iptables -t nat -I POSTROUTING 1 -j PXFWD_POST
iptables -I FORWARD 1 -j PXFWD_FWD
exit 0
EOF
    chmod 755 "$DIR/fwd.sh"
}

fwd_off() {
    [ -f "$DIR/fwd.sh" ] && bash "$DIR/fwd.sh" down
    systemctl disable --now "$FWD_SVC" >/dev/null 2>&1
    rm -f /etc/systemd/system/$FWD_SVC "$DIR/fwd.rules" /etc/sysctl.d/99-px-fwd.conf
    systemctl daemon-reload >/dev/null 2>&1
}

job_fwd() {                # 需要 RULES：协议:本机端口:目标IP:目标端口，逗号分隔；"-" 表示清空
    if [ "$RULES" = "-" ]; then fwd_off; echo "已清除全部端口转发"; return 0; fi
    command -v iptables >/dev/null 2>&1 || pkg_install iptables || { echo "没有 iptables，无法做端口转发"; return 1; }
    fwd_script
    local item proto port ip tport n=0
    : > "$DIR/fwd.rules.new"
    local IFS=','
    for item in $RULES; do
        IFS=: read -r proto port ip tport <<< "$item"
        echo "$proto $port $ip $tport" >> "$DIR/fwd.rules.new"
        fw_open "$port" "$proto"
        n=$((n + 1))
    done
    unset IFS
    mv -f "$DIR/fwd.rules.new" "$DIR/fwd.rules"
    printf 'net.ipv4.ip_forward=1\n' > /etc/sysctl.d/99-px-fwd.conf
    sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1
    cat > /etc/systemd/system/$FWD_SVC <<EOF
[Unit]
Description=Port forwarding (px-agent, kernel iptables)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash $DIR/fwd.sh up
ExecStop=/bin/bash $DIR/fwd.sh down

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload >/dev/null 2>&1
    systemctl enable "$FWD_SVC" >/dev/null 2>&1
    systemctl restart "$FWD_SVC" || { echo "启动转发服务失败"; return 1; }
    local got; got=$(iptables -t nat -S PXFWD_PRE 2>/dev/null | grep -c -- '-j DNAT')
    [ "$got" -ge 1 ] || { echo "转发规则没有生效（iptables 里没有 DNAT 规则）"; return 1; }
    echo "已生效 $got 条转发规则（内核 DNAT）"
}

valid_job() {
    [[ "$JID" =~ ^[A-Za-z0-9]{6,16}$ ]] || return 1
    case "$OP" in
        fwd)
            [ "$RULES" = "-" ] && return 0
            local it pr po ip tp o
            local IFS=','
            for it in $RULES; do
                IFS=: read -r pr po ip tp <<< "$it"
                [[ "$pr" == tcp || "$pr" == udp ]] || return 1
                [[ "$po" =~ ^[0-9]{1,5}$ ]] && [ "$po" -ge 1 ] && [ "$po" -le 65535 ] || return 1
                [[ "$tp" =~ ^[0-9]{1,5}$ ]] && [ "$tp" -ge 1 ] && [ "$tp" -le 65535 ] || return 1
                [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
                for o in 1 2 3 4; do [ "${BASH_REMATCH[$o]}" -le 255 ] || return 1; done
            done
            return 0 ;;
        update|restart_hy2|restart_anytls|restart_tuic|restart_reality|uninstall_agent|rm_hy2|rm_anytls|rm_tuic|rm_reality) return 0 ;;
        reality)
            [[ "$PORT" =~ ^[0-9]{1,5}$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || return 1
            [[ "$SNI" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,98}[A-Za-z0-9])?$ ]] && [[ "$SNI" == *.* ]] || return 1
            [[ "$UUID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || return 1
            [[ "$SID" =~ ^[0-9a-f]{8}$ ]] || return 1
            [ -z "$STATS" ] || [ "$STATS" = 1 ] || return 1
            valid_users || return 1
            return 0 ;;
        hy2|anytls|tuic)
            [[ "$PORT" =~ ^[0-9]{1,5}$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || return 1
            [[ "$PASS" =~ ^[A-Za-z0-9]{8,64}$ ]] || return 1
            [[ "$SNI" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,98}[A-Za-z0-9])?$ ]] || return 1
            [ -z "$OBFS" ] || [[ "$OBFS" =~ ^[A-Za-z0-9]{8,64}$ ]] || return 1
            [ -z "$HOP" ] || [[ "$HOP" =~ ^[0-9]{1,5}-[0-9]{1,5}$ ]] || return 1
            [ -z "$DOMAIN" ] || { [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,98}[A-Za-z0-9])?$ ]] && [[ "$DOMAIN" == *.* ]]; } || return 1
            [ "$OP" != tuic ] || [[ "$UUID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || return 1
            [ -z "$STATS" ] || [ "$STATS" = 1 ] || return 1
            valid_users || return 1
            return 0 ;;
    esac
    return 1
}

fetch_agent() {           # fetch_agent 保存到哪：下载 agent 脚本并用 PX_PUB 验签
    local out="$1" sig
    curl -fsS --connect-timeout 10 -m 60 -D "$out.hdr" -o "$out" "$WEB/px.php?a=sh" || { rm -f "$out" "$out.hdr"; echo "下载 agent 失败：$WEB/px.php?a=sh"; return 1; }
    sig=$(grep -i '^x-px-sig:' "$out.hdr" | head -n1 | cut -d: -f2- | tr -d ' \r\n'); rm -f "$out.hdr"
    bash -n "$out" 2>/dev/null && grep -q '^AGENT_VER=' "$out" || { rm -f "$out"; echo "agent 文件不完整"; return 1; }
    [ -n "$PX_PUB" ] || return 0
    [ -n "$sig" ] || { rm -f "$out"; echo "新脚本没有签名，拒绝替换"; return 1; }
    pkg_install openssl || { rm -f "$out"; echo "需要 openssl 来验签"; return 1; }
    echo "$PX_PUB" | base64 -d > "$out.pub" 2>/dev/null; echo "$sig" | base64 -d > "$out.sig" 2>/dev/null
    if openssl dgst -sha256 -verify "$out.pub" -signature "$out.sig" "$out" >/dev/null 2>&1; then
        rm -f "$out.pub" "$out.sig"; return 0
    fi
    rm -f "$out" "$out.pub" "$out.sig"; echo "签名校验失败，拒绝替换（脚本可能被篡改，或机器人的签名密钥换过：重新执行安装命令）"; return 1
}

self_update() {
    local tmp="$DIR/px.sh.new"
    fetch_agent "$tmp" || return 1
    mv -f "$tmp" "$DIR/px.sh"; chmod 755 "$DIR/px.sh"
    echo "已更新到 v$(grep -m1 '^AGENT_VER=' "$DIR/px.sh" | cut -d= -f2)，agent 即将重启"
    RESTART_AGENT=1
}

run_job() {
    local rc=0
    RESTART_AGENT=0; FP=""; PBK=""
    {
        case "$OP" in
            hy2) job_hy2 ;;
            anytls) job_anytls ;;
            tuic) job_tuic ;;
            reality) job_reality ;;
            rm_hy2) job_rm_hy2 ;;
            rm_anytls) job_rm_anytls ;;
            rm_tuic) job_rm_tuic ;;
            rm_reality) job_rm_reality ;;
            restart_hy2) systemctl restart "$HY_SVC" && echo "已重启 hy2" ;;
            restart_anytls) systemctl restart "$AT_SVC" && echo "已重启 anytls" ;;
            restart_tuic) systemctl restart "$TU_SVC" && echo "已重启 tuic" ;;
            restart_reality) systemctl restart "$RE_SVC" && echo "已重启 reality" ;;
            fwd) job_fwd ;;
            update) self_update ;;
            uninstall_agent) echo "agent 即将卸载（hy2 / anytls / tuic / reality 不受影响）"; UNINSTALL=1 ;;
        esac
    } > "$DIR/job.log" 2>&1
    rc=$?
    [ "$rc" = 0 ] || true
    if [ -z "$DOMAIN" ] && [ "$OP" = hy2 ] && [ "$rc" = 0 ]; then FP=$(cert_fp "$HY_DIR/server.crt"); fi
    if [ -z "$DOMAIN" ] && [ "$OP" = anytls ] && [ "$rc" = 0 ]; then FP=$(cert_fp "$AT_DIR/server.crt"); fi
    if [ -z "$DOMAIN" ] && [ "$OP" = tuic ] && [ "$rc" = 0 ]; then FP=$(cert_fp "$TU_DIR/server.crt"); fi
    { printf 'JOB=%s\nRC=%s\nFP=%s\nPBK=%s\n' "$JID" "$rc" "$FP" "$PBK"; printf 'LOG=%s\n' "$(tail -c 2500 "$DIR/job.log" | b64)"; } > "$DIR/result.txt"
}

# ---------------------------------------------------------------- 主循环
ip4_get() {                # 本机公网 IPv4（缓存 30 分钟；取不到记成 none，避免每次心跳都去等超时）。端口转发要用 IPv4 地址
    local f="$DIR/ip4" now v u mt
    now=$(date +%s); mt=$(stat -c %Y "$f" 2>/dev/null || echo 0)
    if [ -s "$f" ] && [ $((now - mt)) -lt 1800 ]; then cat "$f"; return; fi
    v=""
    for u in https://api.ipify.org https://ipv4.icanhazip.com https://ifconfig.me/ip; do
        v=$(curl -4 -fsS -m 5 "$u" 2>/dev/null | tr -d ' \r\n')
        [[ "$v" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] && break
        v=""
    done
    [ -n "$v" ] || v=none
    echo "$v" > "$f"; echo "$v"
}

heartbeat() {
    printf 'VER=%s\nHOST=%s\nOS=%s\nS_HY2=%s\nS_ANYTLS=%s\nS_TUIC=%s\nS_REALITY=%s\nS_FWD=%s\nIP4=%s\nARCH=%s\n' "$AGENT_VER" "$(hostname 2>/dev/null | tr -cd 'A-Za-z0-9._-')" \
        "$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" | tr -cd 'A-Za-z0-9._ ()/-')" "$(svc_state "$HY_SVC")" "$(svc_state "$AT_SVC")" "$(svc_state "$TU_SVC")" "$(svc_state "$RE_SVC")" "$(svc_state "$FWD_SVC")" "$(ip4_get)" "$(at_arch)"
    [ -s "$DIR/result.txt" ] && cat "$DIR/result.txt"
    tr_collect; tr_line
    return 0
}

agent_run() {
    # shellcheck disable=SC1090
    . "$DIR/conf"
    local fails=0 resp line k v NEXT
    UNINSTALL=0
    while true; do
        NEXT=$INTERVAL
        if resp=$(heartbeat | curl -fsS -m 25 -X POST -H "X-Px-Token: $TOKEN" --data-binary @- "$WEB/px.php" 2>/dev/null); then
            fails=0
            OP=""; JID=""; PORT=""; PASS=""; OBFS=""; SNI=""; HOP=""; DOMAIN=""; UUID=""; SID=""; RULES=""; USERS=""; STATS=""
            while IFS= read -r line; do
                k=${line%%=*}; v=${line#*=}
                case "$k" in
                    ACK) rm -f "$DIR/result.txt" ;;
                    TRACK) rm -f "$DIR/tr.pend" ;;
                    NEXT) [[ "$v" =~ ^[0-9]{1,3}$ ]] && [ "$v" -ge 2 ] && [ "$v" -le 300 ] && NEXT=$v ;;
                    JOB) JID="$v" ;;
                    OP) OP="$v" ;;
                    PORT) PORT="$v" ;;
                    PASS) PASS="$v" ;;
                    OBFS) OBFS="$v" ;;
                    SNI) SNI="$v" ;;
                    HOP) HOP="$v" ;;
                    DOMAIN) DOMAIN="$v" ;;
                    UUID) UUID="$v" ;;
                    SID) SID="$v" ;;
                    RULES) RULES="$v" ;;
                    USERS) USERS="$v" ;;
                    STATS) STATS="$v" ;;
                esac
            done <<< "$resp"
            if [ -n "$JID" ] && [ -n "$OP" ]; then
                if valid_job; then run_job
                else printf 'JOB=%s\nRC=1\nFP=\nLOG=%s\n' "$JID" "$(echo "任务参数没有通过 agent 的校验，已拒绝执行" | b64)" > "$DIR/result.txt"; fi
                # 立刻回报结果，不等下一轮
                if resp=$(heartbeat | curl -fsS -m 25 -X POST -H "X-Px-Token: $TOKEN" --data-binary @- "$WEB/px.php" 2>/dev/null) && echo "$resp" | grep -q '^ACK='; then rm -f "$DIR/result.txt"; fi
                echo "$resp" | grep -q '^TRACK=' && rm -f "$DIR/tr.pend"
                if [ "$UNINSTALL" = 1 ]; then do_uninstall; exit 0; fi
                if [ "${RESTART_AGENT:-0}" = 1 ]; then exit 0; fi          # systemd 的 Restart=always 会用新版本重新拉起
                continue
            fi
        else
            fails=$((fails + 1)); [ "$fails" -gt 12 ] && fails=12
            sleep $((fails * 5))
        fi
        sleep "$NEXT"
    done
}

do_uninstall() {            # 先删文件，最后才停服务（停服务会把正在跑的自己结束掉）
    systemctl disable "$SVC" >/dev/null 2>&1
    rm -f /etc/systemd/system/$SVC.service
    rm -rf "$DIR"
    systemctl daemon-reload >/dev/null 2>&1
    systemctl stop --no-block "$SVC" >/dev/null 2>&1
}

# ---------------------------------------------------------------- 安装 agent
agent_install() {
    WEB="${1%/}"; TOKEN="$2"
    need_root
    [[ "$WEB" =~ ^https://[A-Za-z0-9.:-]+(/[A-Za-z0-9._~/-]*)?$ ]] || { echo "网址格式不对"; exit 1; }
    [[ "$TOKEN" =~ ^[A-Za-z0-9]{16,64}$ ]] || { echo "节点密钥格式不对"; exit 1; }
    command -v systemctl >/dev/null 2>&1 || { echo "需要 systemd"; exit 1; }
    pkg_install curl || { echo "需要 curl"; exit 1; }
    mkdir -p "$DIR"; chmod 700 "$DIR"
    fetch_agent "$DIR/px.sh.new" || exit 1
    mv -f "$DIR/px.sh.new" "$DIR/px.sh"; chmod 755 "$DIR/px.sh"
    printf 'WEB=%s\nTOKEN=%s\n' "$WEB" "$TOKEN" > "$DIR/conf"; chmod 600 "$DIR/conf"
    cat > /etc/systemd/system/$SVC.service <<EOF
[Unit]
Description=Proxy installer agent
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/bin/bash $DIR/px.sh run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$SVC" >/dev/null 2>&1
    systemctl restart "$SVC"
    sleep 2
    if systemctl is-active --quiet "$SVC"; then
        echo "✅ agent 已安装并运行。回到小程序刷新，节点会显示在线，然后就可以安装 hy2 / anytls / tuic / reality。"
    else
        echo "agent 启动失败："; journalctl -u "$SVC" -n 10 --no-pager; exit 1
    fi
}

case "${1:-}" in
    run) agent_run ;;
    uninstall) need_root; do_uninstall; echo "agent 已卸载（hy2 / anytls / tuic / reality 不受影响）" ;;
    https://*) agent_install "$1" "$2" ;;
    *) echo "用法：bash px.sh 网址 节点密钥 | run | uninstall"; exit 1 ;;
esac
