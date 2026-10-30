<?php
// 安装代理：节点 agent 报到入口 + 订阅地址。真正的校验在机器人接口里做（agent 密钥 / 订阅密钥）。
//   POST px.php            agent 报到，头 X-Px-Token
//   GET  px.php?s=订阅密钥  订阅内容（&f=clash|sb|b64 指定格式，默认按客户端自动识别）
//   GET  px.php?a=sh        节点 agent 脚本（脚本放在机器人目录，带签名头 X-Px-Sig，节点更新时验签）
//   GET  px.php?a=sb&arch=  统计版 sing-box（放在机器人目录，带签名头，节点装之前验签）
declare(strict_types=1);
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');

function fail(int $code, string $msg): never {
    http_response_code($code);
    header('Content-Type: text/plain; charset=utf-8');
    echo $msg;
    exit;
}

$cfgFile = __DIR__ . '/config.php';
if (!is_file($cfgFile)) fail(500, 'not configured');
$cfg = require $cfgFile;
$base = rtrim((string)($cfg['api_url'] ?? 'http://127.0.0.1:8787'), '/');
$method = $_SERVER['REQUEST_METHOD'] ?? '';
$headers = ['X-Api-Key: ' . $cfg['api_key']];

if ($method === 'POST') {
    $token = (string)($_SERVER['HTTP_X_PX_TOKEN'] ?? '');
    if (!preg_match('/^[A-Za-z0-9]{16,64}$/', $token)) fail(403, "ERR=token\n");
    $body = file_get_contents('php://input', false, null, 0, 8193);
    if ($body === false || strlen($body) > 8192) fail(413, "ERR=body\n");
    $ip = (string)($_SERVER['HTTP_CF_CONNECTING_IP'] ?? $_SERVER['REMOTE_ADDR'] ?? '');
    if (!filter_var($ip, FILTER_VALIDATE_IP)) $ip = '';
    $url = $base . '/pub/agent';
    $headers[] = 'X-Px-Token: ' . $token;
    $headers[] = 'X-Px-Ip: ' . $ip;
    $timeout = 10;
} elseif ($method === 'GET' && ($_GET['a'] ?? '') === 'sh') {
    $url = $base . '/pub/sh';
    $body = '';
    $timeout = 15;
} elseif ($method === 'GET' && ($_GET['a'] ?? '') === 'sb') {
    $arch = (string)($_GET['arch'] ?? '');
    if (!in_array($arch, ['amd64', 'arm64'], true)) fail(400, 'bad arch');
    $url = $base . '/pub/sb?arch=' . $arch;
    $body = '';
    $timeout = 120;
} elseif ($method === 'GET') {
    $s = (string)($_GET['s'] ?? '');
    if (!preg_match('/^[A-Za-z0-9]{16,64}$/', $s)) fail(403, 'bad token');
    $f = (string)($_GET['f'] ?? '');
    $m = (string)($_GET['m'] ?? '');
    if (!in_array($m, ['', 'direct', 'fwd', 'all'], true)) $m = '';
    $url = $base . '/pub/sub?s=' . rawurlencode($s) . ($f !== '' ? '&f=' . rawurlencode(substr($f, 0, 8)) : '') . ($m !== '' ? '&m=' . $m : '');
    $body = '';
    $headers[] = 'X-Px-Ua: ' . substr(preg_replace('/[\r\n]/', '', (string)($_SERVER['HTTP_USER_AGENT'] ?? '')), 0, 200);
    $timeout = 10;
} else {
    fail(405, 'GET/POST only');
}

$ch = curl_init($url);
$respHeaders = [];
curl_setopt_array($ch, [
    CURLOPT_POST => $method === 'POST',
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_CONNECTTIMEOUT => 3,
    CURLOPT_TIMEOUT => $timeout,
    CURLOPT_HTTPHEADER => $headers,
    CURLOPT_HEADERFUNCTION => function ($ch, $line) use (&$respHeaders) {
        $p = strpos($line, ':');
        if ($p !== false) $respHeaders[strtolower(trim(substr($line, 0, $p)))] = trim(substr($line, $p + 1));
        return strlen($line);
    },
]);
if ($method === 'POST') curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
$out = curl_exec($ch);
$code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);
if ($out === false) fail(502, "ERR=upstream\n");
http_response_code($code >= 200 && $code < 600 ? $code : 502);
foreach (['content-type', 'content-disposition', 'profile-update-interval', 'profile-title', 'subscription-userinfo', 'x-px-sig'] as $h) {
    if (isset($respHeaders[$h])) header($h . ': ' . $respHeaders[$h]);
}
echo $out;
