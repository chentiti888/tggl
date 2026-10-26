#!/usr/bin/env bash
# 安装代理：节点 agent（纯 bash + curl）
#
# 安装（在节点服务器上用 root 执行；命令由操作台「工具 → 安装代理 → 添加节点」生成）：
#   curl -fsSL 'https://你的网址/px.php?a=sh' | bash -s -- https://你的网址 节点密钥
# 卸载 agent（不会卸载已装好的 hy2 / anytls）：
#   bash /opt/px-agent/px.sh uninstall
#
# agent 定时向 https://你的网址/px.php 报到并领取任务（短连接），只向外发请求，不监听任何端口。
# 服务器下发的任务只有固定几种（安装 / 卸载 hy2、anytls，重启，更新），参数都会再校验一遍。
# 更新 agent 时新脚本必须通过签名校验（公钥在下面 PX_PUB，安装时由机器人填入），否则拒绝替换。

AGENT_VER=5
PX_PUB=""
DIR=/opt/px-agent
SVC=px-agent
HY_DIR=/etc/hysteria
HY_SVC=hysteria-server.service
AT_DIR=/etc/anytls
AT_BIN=/usr/local/bin/sing-box-anytls
AT_SVC=anytls-server.service
HOP_SVC=px-hop.service
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
    if ss -ltn 2>/dev/null | grep -qE '[:.]80[[:space:]]'; then echo "80 端口被占用（nginx / apache？），无法申请证书"; return 1; fi
    fw_open 80 tcp
    "$acme" --set-default-ca --server letsencrypt >/dev/null 2>&1
    echo "向 Let's Encrypt 申请 $dom 的证书..."
    out=$("$acme" --issue -d "$dom" --standalone --httpport 80 --keylength ec-256 2>&1); rc=$?
    if [ "$rc" != 0 ] && [ "$rc" != 2 ]; then
        echo "申请证书失败（检查：域名是否解析到本机 / 云厂商安全组是否放行 TCP 80）："; echo "$out" | tail -n 8; return 1
    fi
    mkdir -p "$dir"
    "$acme" --install-cert -d "$dom" --ecc --fullchain-file "$dir/fullchain.pem" --key-file "$dir/key.pem" \
        --reloadcmd "systemctl restart hysteria-server.service >/dev/null 2>&1; systemctl restart anytls-server.service >/dev/null 2>&1; true" >/dev/null 2>&1 \
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

job_hy2() {                # 需要 PORT PASS SNI；可选 OBFS HOP
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
        echo "auth:"
        echo "  type: password"
        echo "  password: \"$PASS\""
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

at_install_bin() {
    local arch ver tmp url bin
    arch=$(at_arch); [ -n "$arch" ] || { echo "不支持的架构：$(uname -m)"; return 1; }
    local rel name want got
    rel=$(curl -fsSL --connect-timeout 8 https://api.github.com/repos/SagerNet/sing-box/releases/latest 2>/dev/null)
    ver=$(echo "$rel" | grep -m1 '"tag_name"' | sed -E 's/.*"v?([0-9][^"]*)".*/\1/')
    echo "$ver" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$' || { echo "查不到 sing-box 最新版本（节点连不上 api.github.com？）"; return 1; }
    name="sing-box-${ver}-linux-${arch}.tar.gz"
    # GitHub 给每个发布文件算的 SHA256（"digest":"sha256:..."），用来校验下载内容
    want=$(echo "$rel" | tr ',{}' '\n\n\n' | awk -v n="\"name\":\"$name\"" '
        { gsub(/^[ \t]+/, ""); gsub(/": "/, "\":\"") }
        $0 == n { f = 1; next }
        f && /^"browser_download_url":/ { f = 0 }
        f && /^"digest":"sha256:/ { sub(/^"digest":"sha256:/, ""); sub(/".*/, ""); print; exit }')
    echo "$want" | grep -qE '^[0-9a-f]{64}$' || { echo "GitHub 没给出 $name 的校验值，为安全起见不安装"; return 1; }
    tmp=$(mktemp -d)
    url="https://github.com/SagerNet/sing-box/releases/download/v${ver}/${name}"
    echo "下载 sing-box v$ver ($arch)..."
    curl -fL --retry 3 --connect-timeout 15 -s -o "$tmp/sb.tgz" "$url" || { echo "下载失败（节点连不上 github？）"; rm -rf "$tmp"; return 1; }
    got=$(sha256sum "$tmp/sb.tgz" | cut -d' ' -f1)
    [ "$got" = "$want" ] || { echo "sing-box 校验失败（下载内容和官方不一致），不安装"; rm -rf "$tmp"; return 1; }
    echo "SHA256 校验通过"
    tar -xzf "$tmp/sb.tgz" -C "$tmp" || { rm -rf "$tmp"; return 1; }
    bin=$(find "$tmp" -type f -name sing-box | head -n1)
    [ -n "$bin" ] || { echo "安装包里没有 sing-box"; rm -rf "$tmp"; return 1; }
    install -m 755 "$bin" "$AT_BIN"; rm -rf "$tmp"
    at_bin_ok || { echo "sing-box 版本过低（anytls 需要 >= 1.12.0）"; return 1; }
}

job_anytls() {             # 需要 PORT PASS SNI
    pkg_install curl; pkg_install openssl; pkg_install tar
    at_bin_ok || at_install_bin || return 1
    mkdir -p "$AT_DIR"; chmod 700 "$AT_DIR"
    local crt="$AT_DIR/server.crt" key="$AT_DIR/server.key"
    if [ -n "$DOMAIN" ]; then get_cert "$DOMAIN" || return 1; crt="$CERT_CRT"; key="$CERT_KEY"
    else mkcert "$AT_DIR" "$SNI" || return 1; fi
    cat > "$AT_DIR/config.new.json" <<EOF
{
  "log": {"level": "warn", "timestamp": true},
  "inbounds": [{
    "type": "anytls", "tag": "anytls-in", "listen": "::", "listen_port": $PORT,
    "users": [{"name": "user", "password": "$PASS"}],
    "tls": {"enabled": true, "server_name": "$SNI", "certificate_path": "$crt", "key_path": "$key"}
  }],
  "outbounds": [{"type": "direct", "tag": "direct"}]
}
EOF
    local out
    if ! out=$("$AT_BIN" check -c "$AT_DIR/config.new.json" 2>&1); then rm -f "$AT_DIR/config.new.json"; echo "配置校验失败：$out"; return 1; fi
    mv -f "$AT_DIR/config.new.json" "$AT_DIR/config.json"; chmod 600 "$AT_DIR/config.json"
    cat > /etc/systemd/system/$AT_SVC <<EOF
[Unit]
Description=AnyTLS Server (sing-box)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$AT_BIN run -c $AT_DIR/config.json
Restart=on-failure
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
    fw_open "$PORT" tcp
    systemctl daemon-reload >/dev/null 2>&1
    systemctl enable "$AT_SVC" >/dev/null 2>&1
    systemctl restart "$AT_SVC"
    sleep 2
    if ! systemctl is-active --quiet "$AT_SVC"; then echo "AnyTLS 启动失败，日志："; journalctl -u "$AT_SVC" -n 15 --no-pager 2>&1 | tail -n 15; return 1; fi
    [ -n "$DOMAIN" ] || FP=$(cert_fp "$AT_DIR/server.crt")
    echo "AnyTLS 已运行：TCP $PORT${DOMAIN:+ · 域名证书 $DOMAIN}"
}

job_rm_anytls() {
    systemctl disable --now "$AT_SVC" >/dev/null 2>&1
    rm -f /etc/systemd/system/$AT_SVC "$AT_BIN"; rm -rf "$AT_DIR"
    systemctl daemon-reload >/dev/null 2>&1
    echo "AnyTLS 已卸载"
}

# ---------------------------------------------------------------- 任务分发（先校验再执行）
OP=""; JID=""; PORT=""; PASS=""; OBFS=""; SNI=""; HOP=""; FP=""; DOMAIN=""

valid_job() {
    [[ "$JID" =~ ^[A-Za-z0-9]{6,16}$ ]] || return 1
    case "$OP" in
        update|restart_hy2|restart_anytls|uninstall_agent|rm_hy2|rm_anytls) return 0 ;;
        hy2|anytls)
            [[ "$PORT" =~ ^[0-9]{1,5}$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || return 1
            [[ "$PASS" =~ ^[A-Za-z0-9]{8,64}$ ]] || return 1
            [[ "$SNI" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,98}[A-Za-z0-9])?$ ]] || return 1
            [ -z "$OBFS" ] || [[ "$OBFS" =~ ^[A-Za-z0-9]{8,64}$ ]] || return 1
            [ -z "$HOP" ] || [[ "$HOP" =~ ^[0-9]{1,5}-[0-9]{1,5}$ ]] || return 1
            [ -z "$DOMAIN" ] || { [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,98}[A-Za-z0-9])?$ ]] && [[ "$DOMAIN" == *.* ]]; } || return 1
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
    RESTART_AGENT=0; FP=""
    {
        case "$OP" in
            hy2) job_hy2 ;;
            anytls) job_anytls ;;
            rm_hy2) job_rm_hy2 ;;
            rm_anytls) job_rm_anytls ;;
            restart_hy2) systemctl restart "$HY_SVC" && echo "已重启 hy2" ;;
            restart_anytls) systemctl restart "$AT_SVC" && echo "已重启 anytls" ;;
            update) self_update ;;
            uninstall_agent) echo "agent 即将卸载（hy2 / anytls 不受影响）"; UNINSTALL=1 ;;
        esac
    } > "$DIR/job.log" 2>&1
    rc=$?
    [ "$rc" = 0 ] || true
    if [ -z "$DOMAIN" ] && [ "$OP" = hy2 ] && [ "$rc" = 0 ]; then FP=$(cert_fp "$HY_DIR/server.crt"); fi
    if [ -z "$DOMAIN" ] && [ "$OP" = anytls ] && [ "$rc" = 0 ]; then FP=$(cert_fp "$AT_DIR/server.crt"); fi
    { printf 'JOB=%s\nRC=%s\nFP=%s\n' "$JID" "$rc" "$FP"; printf 'LOG=%s\n' "$(tail -c 2500 "$DIR/job.log" | b64)"; } > "$DIR/result.txt"
}

# ---------------------------------------------------------------- 主循环
heartbeat() {
    printf 'VER=%s\nHOST=%s\nOS=%s\nS_HY2=%s\nS_ANYTLS=%s\n' "$AGENT_VER" "$(hostname 2>/dev/null | tr -cd 'A-Za-z0-9._-')" \
        "$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" | tr -cd 'A-Za-z0-9._ ()/-')" "$(svc_state "$HY_SVC")" "$(svc_state "$AT_SVC")"
    [ -s "$DIR/result.txt" ] && cat "$DIR/result.txt"
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
            OP=""; JID=""; PORT=""; PASS=""; OBFS=""; SNI=""; HOP=""; DOMAIN=""
            while IFS= read -r line; do
                k=${line%%=*}; v=${line#*=}
                case "$k" in
                    ACK) rm -f "$DIR/result.txt" ;;
                    NEXT) [[ "$v" =~ ^[0-9]{1,3}$ ]] && [ "$v" -ge 2 ] && [ "$v" -le 300 ] && NEXT=$v ;;
                    JOB) JID="$v" ;;
                    OP) OP="$v" ;;
                    PORT) PORT="$v" ;;
                    PASS) PASS="$v" ;;
                    OBFS) OBFS="$v" ;;
                    SNI) SNI="$v" ;;
                    HOP) HOP="$v" ;;
                    DOMAIN) DOMAIN="$v" ;;
                esac
            done <<< "$resp"
            if [ -n "$JID" ] && [ -n "$OP" ]; then
                if valid_job; then run_job
                else printf 'JOB=%s\nRC=1\nFP=\nLOG=%s\n' "$JID" "$(echo "任务参数没有通过 agent 的校验，已拒绝执行" | b64)" > "$DIR/result.txt"; fi
                # 立刻回报结果，不等下一轮
                if resp=$(heartbeat | curl -fsS -m 25 -X POST -H "X-Px-Token: $TOKEN" --data-binary @- "$WEB/px.php" 2>/dev/null) && echo "$resp" | grep -q '^ACK='; then rm -f "$DIR/result.txt"; fi
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
        echo "✅ agent 已安装并运行。回到小程序刷新，节点会显示在线，然后就可以安装 hy2 / anytls。"
    else
        echo "agent 启动失败："; journalctl -u "$SVC" -n 10 --no-pager; exit 1
    fi
}

case "${1:-}" in
    run) agent_run ;;
    uninstall) need_root; do_uninstall; echo "agent 已卸载（hy2 / anytls 不受影响）" ;;
    https://*) agent_install "$1" "$2" ;;
    *) echo "用法：bash px.sh 网址 节点密钥 | run | uninstall"; exit 1 ;;
esac
