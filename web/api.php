<?php
// Mini App → 机器人接口 的转发。页面只和这个文件通信，机器人接口只监听本机。
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');

function fail(int $code, string $msg): never {
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $msg], JSON_UNESCAPED_UNICODE);
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') fail(405, '只支持 POST');
$cfgFile = __DIR__ . '/config.php';
if (!is_file($cfgFile)) fail(500, '还没有配置：请把 config.sample.php 复制为 config.php 并填写');
$cfg = require $cfgFile;
if (empty($cfg['api_key']) || strpos((string)$cfg['api_key'], '粘贴') !== false) fail(500, 'config.php 里的 api_key 还没有填写');

$action = (string)($_GET['a'] ?? '');
if (!preg_match('/^[a-z][a-z0-9_]{1,29}$/', $action)) fail(400, '接口名不正确');

$maxBody = $action === 'ssh_up_chunk' ? 64 * 1024 * 1024 : 65536;     // 只有远程服务器的上传分块允许大请求
$body = file_get_contents('php://input', false, null, 0, $maxBody + 1);
if ($body === false || strlen($body) > $maxBody) fail(413, '请求太大');
if ($body === '') $body = '{}';
$init = (string)($_SERVER['HTTP_X_INIT_DATA'] ?? '');
$dev = (string)($_SERVER['HTTP_X_DEVICE_TOKEN'] ?? '');
$clientIp = preg_replace('/[^0-9a-fA-F:.]/', '', (string)($_SERVER['REMOTE_ADDR'] ?? ''));
if ($action === 'pair_claim') {
    // 配对：主屏幕 App 里输入配对码，不带任何登录信息；真正的校验和限速在机器人那一层
    $init = '';
    $dev = '';
} elseif ($dev !== '') {
    // 主屏幕设备令牌：这里只验格式，是否有效、有没有被撤销由机器人那一层判断
    if (!preg_match('/^[0-9a-f]{64}$/', $dev)) fail(401, '设备登录已失效，请重新配对');
    $init = '';
} else {
    if ($init === '' || strlen($init) > 4096) fail(401, '请从 Telegram 机器人里打开本页面');
}

// 先在这一层验 Telegram 签名：没有合法签名的请求（刷接口的）直接丢掉，不会走到机器人
$secret = (string)($cfg['init_secret'] ?? '');
if ($secret !== '' && $init !== '') {
    if (!preg_match('/^[0-9a-f]{64}$/', $secret)) fail(500, 'config.php 里的 init_secret 格式不对');
    $pairs = [];
    foreach (explode('&', $init) as $kv) {
        if ($kv === '') continue;
        $p = strpos($kv, '=');
        $k = urldecode($p === false ? $kv : substr($kv, 0, $p));
        $pairs[$k] = $p === false ? '' : urldecode(substr($kv, $p + 1));
    }
    $got = (string)($pairs['hash'] ?? '');
    unset($pairs['hash']);
    if ($got === '') fail(401, '登录信息不完整');
    ksort($pairs, SORT_STRING);
    $lines = [];
    foreach ($pairs as $k => $v) $lines[] = $k . '=' . $v;
    $calc = hash_hmac('sha256', implode("\n", $lines), hex2bin($secret));
    if (!hash_equals($calc, $got)) fail(401, '登录签名不正确');
    $maxAge = (int)($cfg['max_age'] ?? 86400);
    if (time() - (int)($pairs['auth_date'] ?? 0) > $maxAge) fail(401, '登录已过期，请关闭后重新打开');
}

$ch = curl_init(rtrim((string)($cfg['api_url'] ?? 'http://127.0.0.1:8787'), '/') . '/api/' . $action);
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_POSTFIELDS => $body,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_CONNECTTIMEOUT => 3,
    CURLOPT_TIMEOUT => 45,
    CURLOPT_HTTPHEADER => [
        'Content-Type: application/json',
        'X-Api-Key: ' . $cfg['api_key'],
        'X-Init-Data: ' . $init,
        'X-Device-Token: ' . $dev,
        'X-Client-Ip: ' . $clientIp,
    ],
]);
$out = curl_exec($ch);
$code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
$err = curl_errno($ch);
curl_close($ch);

if ($out === false || $err) fail(502, '连接不上机器人接口：请确认机器人是用 miniapp_api.py 启动的');
http_response_code($code >= 200 && $code < 600 ? $code : 502);
echo $out;
