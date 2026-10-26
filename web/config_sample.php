<?php
// 复制为 config.php 并填写（config.php 不要上传到公开仓库，已在 .gitignore 里排除）
return [
    // 机器人的本地接口地址，端口和 nav_miniapp.json 里的 port 一致（默认 8787）
    'api_url' => 'http://127.0.0.1:8787',
    // 和机器人共用的密钥：机器人第一次用 miniapp_api.py 启动时自动生成，在 nav_miniapp.json 的 api_key 里
    'api_key' => '把 nav_miniapp.json 里的 api_key 粘贴到这里',
    // 页面标题
    'title'   => '功能导航',
    // 可选：Telegram 登录签名预检（64 位十六进制），填了可以在 PHP 这一层先丢掉乱刷的请求。
    // 算法：HMAC-SHA256(key="WebAppData", msg=机器人Token)，在服务器上执行：
    //   python3 -c "import hmac,hashlib;print(hmac.new(b'WebAppData',b'你的机器人Token',hashlib.sha256).hexdigest())"
    // 不想填就删掉这一行。
    // 'init_secret' => '',
    // 可选：登录签名有效期（秒），默认 86400
    // 'max_age' => 86400,
];
