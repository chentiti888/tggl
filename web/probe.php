<?php
// 多服务器监控：探针上报入口。目标服务器的探针把数据 POST 到这里，再转给机器人接口。
// 只接受带探针密钥的小数据包；真正的密钥校验在机器人接口里做。
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function fail(int $code, string $msg): never {
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $msg], JSON_UNESCAPED_UNICODE);
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') fail(405, 'POST only');
$cfgFile = __DIR__ . '/config.php';
if (!is_file($cfgFile)) fail(500, 'not configured');
$cfg = require $cfgFile;

$token = (string)($_SERVER['HTTP_X_PROBE_TOKEN'] ?? '');
if (!preg_match('/^[A-Za-z0-9]{16,64}$/', $token)) fail(403, 'bad token');

$body = file_get_contents('php://input', false, null, 0, 4097);
if ($body === false || $body === '' || strlen($body) > 4096) fail(413, 'bad body');

// 探针所在服务器的 IP（经过 Cloudflare 时取它给的真实 IP）
$ip = (string)($_SERVER['HTTP_CF_CONNECTING_IP'] ?? $_SERVER['REMOTE_ADDR'] ?? '');
if (!filter_var($ip, FILTER_VALIDATE_IP)) $ip = '';

$ch = curl_init(rtrim((string)($cfg['api_url'] ?? 'http://127.0.0.1:8787'), '/') . '/probe');
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_POSTFIELDS => $body,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_CONNECTTIMEOUT => 3,
    CURLOPT_TIMEOUT => 6,
    CURLOPT_HTTPHEADER => [
        'Content-Type: application/json',
        'X-Api-Key: ' . $cfg['api_key'],
        'X-Probe-Token: ' . $token,
        'X-Probe-Ip: ' . $ip,
    ],
]);
$out = curl_exec($ch);
$code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);
if ($out === false) fail(502, 'upstream down');
http_response_code($code >= 200 && $code < 600 ? $code : 502);
echo $out;
