# Mini App（功能导航 / 操作台）部署说明

这个小程序由两部分组成：

| 部分 | 文件 | 跑在哪里 |
|---|---|---|
| 机器人 + 小程序后端 | `dh.py` `miniapp_api.py` `webpush.py` `px.sh` | Python，一个进程，只监听 `127.0.0.1:8787` |
| 网站（界面 + 转发） | `web/` 目录里的全部文件 | nginx + PHP，需要 HTTPS 域名 |

浏览器 / Telegram 里打开的是网站；网站的 `api.php` 把请求转发给本机的机器人接口。
接口只监听本机，外网访问不到，所以**网站和机器人必须在同一台服务器上**。

---

## 一、准备

- Linux 服务器（root），systemd
- 一个已经解析到服务器的域名，并有 HTTPS 证书（Telegram 小程序必须是 https）
- 一个 Telegram 机器人：在 @BotFather 创建，拿到 **Token**；再拿到你自己的 **用户 ID**（数字）
- 软件：`python3`（3.9+）、`pip`、`nginx`、`php-fpm`（含 `php-curl`）

Debian / Ubuntu 示例：

```bash
apt update && apt install -y python3 python3-pip nginx php-fpm php-curl
```

## 二、部署机器人（后端）

### 方法 A：用仓库里的一键部署脚本 `tg-bot-gl.sh`

按 README 的说明，选“从 GitHub 仓库拉取”，主程序选 **`miniapp_api.py`**（不是 `dh.py`），Token 和用户 ID 选 `.env` 方式写入：

```
BOT_TOKEN=你的机器人Token
ADMIN_IDS=你的用户ID          # 多个用逗号分隔，这些人是“所有者”
```

### 方法 B：手动

```bash
git clone https://github.com/你的用户名/仓库名.git /root/mybot && cd /root/mybot
pip install -r requirements.txt --break-system-packages
export BOT_TOKEN=你的机器人Token ADMIN_IDS=你的用户ID
python3 miniapp_api.py          # 先前台跑一下看有没有报错
```

确认没问题后做成服务 `/etc/systemd/system/mybot.service`：

```ini
[Unit]
Description=Telegram bot + Mini App
After=network-online.target

[Service]
WorkingDirectory=/root/mybot
EnvironmentFile=/root/mybot/.env
ExecStart=/usr/bin/python3 -u miniapp_api.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now mybot
```

> 注意：启动命令是 `python3 miniapp_api.py`，它会同时启动机器人，不要再另外启动 `dh.py`。
> `px.sh` 必须和 `miniapp_api.py` 放在同一个目录（安装代理功能用）。

第一次启动会在程序目录自动生成 `nav_miniapp.json`，里面有 `api_key`（网站和机器人共用的密钥）。

## 三、部署网站

1. 把 `web/` 里的所有文件放到网站目录，例如 `/var/www/mini/`，保持 `assets/` 子目录结构。
2. 复制配置并填写：

```bash
cd /var/www/mini
cp config.sample.php config.php
nano config.php        # 把 api_key 改成 nav_miniapp.json 里的 api_key
```

3. nginx 站点配置示例（证书路径、php-fpm 套接字按你的系统改）：

```nginx
server {
    listen 443 ssl http2;
    server_name mini.example.com;
    ssl_certificate     /etc/ssl/mini.example.com/fullchain.pem;
    ssl_certificate_key /etc/ssl/mini.example.com/key.pem;

    root /var/www/mini;
    index index.php;
    client_max_body_size 70m;          # SFTP 上传分块用，不要太小

    location / { try_files $uri $uri/ =404; }
    location ~ \.php$ {
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_pass unix:/run/php/php-fpm.sock;   # 或 127.0.0.1:9000
    }
    # 配置、数据类文件不让直接访问
    location ~ /\.(?!well-known) { deny all; }
}
```

```bash
nginx -t && systemctl reload nginx
```

4. **如果站点放在 Cloudflare 后面**，并且服务器防火墙只放行了 Cloudflare 的 IP，要在 nginx 里启用真实 IP（`set_real_ip_from` + `real_ip_header CF-Connecting-IP`），否则接口看到的“用户 IP”都是 Cloudflare 的，防爆破、设备记录都会不准。

## 四、告诉机器人网站地址

编辑程序目录下的 `nav_miniapp.json`，加上你的网站地址，然后 `systemctl restart mybot`：

```json
{
  "api_key": "（自动生成，不要改）",
  "web_url": "https://mini.example.com"
}
```

之后机器人首页会多一个“💠 打开操作台”按钮，用你的 Telegram 账号点开即可。
（也可以在 @BotFather 里给机器人设置菜单按钮指向同一个地址。）

可选配置（都写在 `nav_miniapp.json` 里）：

| 键 | 作用 |
|---|---|
| `port` | 本机接口端口，默认 8787（改了要同步改 `config.php` 的 `api_url`） |
| `max_age` | 登录签名有效期（秒），默认 86400 |
| `px_url` | 安装代理用的单独域名（也可以以后在小程序里设置，不用改文件） |
| `cf_token` | Cloudflare API 令牌（或环境变量 `CF_API_TOKEN`），解析 / 防护 / 证书申请要用 |
| `r2_key_id` `r2_secret` | R2 存储（或环境变量） |

## 五、各功能需要什么

- **服务器监控**：小程序里添加服务器，会得到一条安装命令，到被监控的服务器上执行（装的是 `probe.sh`，纯 bash）。需要网站能被被监控服务器访问。
- **安装代理**：工具 → 安装代理。需要网站目录里有 `px.php`、机器人目录里有 `px.sh`。域名证书和自动放行需要 Cloudflare 令牌（DNS 编辑 + 配置规则权限）。
- **SFTP / SSH**：需要 `paramiko`（`requirements.txt` 已包含）。
- **R2、Cloudflare、GitHub**：只有“所有者”（`ADMIN_IDS` 里的人）能用；在小程序里按提示填令牌。
- **网页推送**：在小程序里开启即可，需要 HTTPS。

## 六、安全提醒

- `config.php`、`nav_*.json`、`.env`、`nav_px_sign.key` 里有密钥，**不要提交到公开仓库**（仓库自带 `.gitignore`）。
- “授权用户”可以使用查询类工具（ping、路由追踪、端口扫描等），也就是能让你的服务器去探测网络，只授权给你信任的人。授权用户看不到你的 Cloudflare、SSH、R2、GitHub、安装代理。
- 授权用户可以把自己的域名加进自己的导航，服务器会去取这个域名的图标；不信任的人不要授权。
- 备份：定期备份程序目录里的 `nav_*.json`、`cf_*.json`、`nav_px_sign.key`（丢了签名密钥，已装的代理节点要重新执行一次安装命令）。

## 七、更新

```bash
cd /root/mybot && git pull && systemctl restart mybot
```

网站文件（`web/`）更新后直接覆盖即可；浏览器里如果看到旧界面，关掉小程序重新打开（`sw.js` 有缓存）。
