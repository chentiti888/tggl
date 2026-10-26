<?php
// 首页「网络测速」用：测的是 手机 ↔ 本站服务器 的下载/上传速度。
// 先用 Mini App 身份换一张 5 分钟有效的测速票（只发给管理员），下载/上传都要带票，别人拿不到就刷不了流量。
declare(strict_types=1);
header('Cache-Control: no-store, no-transform');
header('X-Content-Type-Options: nosniff');

function not_found(): never {
    http_response_code(404);
    header('Content-Type: text/html; charset=utf-8');
    echo "<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n<center><h1>404 Not Found</h1></center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n";
    exit;
}
function out(array $a): never {
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($a, JSON_UNESCAPED_UNICODE);
    exit;
}

$cfgFile = __DIR__ . '/config.php';
if (!is_file($cfgFile)) not_found();
$cfg = require $cfgFile;
$key = (string)($cfg['api_key'] ?? '');
if ($key === '') not_found();
$op = (string)($_GET['op'] ?? '');
$method = $_SERVER['REQUEST_METHOD'] ?? '';

// ---- 换票：让机器人接口验证身份，是管理员才发票
if ($op === 't') {
    $init = (string)($_SERVER['HTTP_X_INIT_DATA'] ?? '');
    $dev = (string)($_SERVER['HTTP_X_DEVICE_TOKEN'] ?? '');       // 主屏幕设备令牌
    if ($dev !== '' && preg_match('/^[0-9a-f]{64}$/', $dev)) $init = '';
    else $dev = '';
    if ($method !== 'POST' || ($init === '' && $dev === '') || strlen($init) > 4096) not_found();
    // 先在这一层验 Telegram 签名，没有合法签名的直接丢掉，不转给机器人（与 api.php 相同）
    $secret = (string)($cfg['init_secret'] ?? '');
    if ($secret !== '' && $init !== '') {
        if (!preg_match('/^[0-9a-f]{64}$/', $secret)) not_found();
        $pairs = [];
        foreach (explode('&', $init) as $kv) {
            if ($kv === '') continue;
            $p = strpos($kv, '=');
            $k = urldecode($p === false ? $kv : substr($kv, 0, $p));
            $pairs[$k] = $p === false ? '' : urldecode(substr($kv, $p + 1));
        }
        $got = (string)($pairs['hash'] ?? '');
        unset($pairs['hash']);
        ksort($pairs, SORT_STRING);
        $lines = [];
        foreach ($pairs as $k => $v) $lines[] = $k . '=' . $v;
        $calc = hash_hmac('sha256', implode("\n", $lines), hex2bin($secret));
        if ($got === '' || !hash_equals($calc, $got)
            || time() - (int)($pairs['auth_date'] ?? 0) > (int)($cfg['max_age'] ?? 86400)) {
            out(['ok' => false, 'error' => '身份验证失败，请从机器人里重新打开']);
        }
    }
    $ch = curl_init(rtrim((string)($cfg['api_url'] ?? 'http://127.0.0.1:8787'), '/') . '/api/home');
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => '{}',
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 3,
        CURLOPT_TIMEOUT => 15,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'X-Api-Key: ' . $key, 'X-Init-Data: ' . $init, 'X-Device-Token: ' . $dev],
    ]);
    $res = curl_exec($ch);
    curl_close($ch);
    $j = is_string($res) ? json_decode($res, true) : null;
    if (!is_array($j) || empty($j['ok'])) out(['ok' => false, 'error' => '身份验证失败，请从机器人里重新打开']);
    if (empty($j['data']['admin'])) out(['ok' => false, 'error' => '只有管理员可以测速']);
    $exp = time() + 300;
    out(['ok' => true, 't' => $exp . '.' . hash_hmac('sha256', 'speed|' . $exp, $key)]);
}

// ---- 其余操作都要带有效的票
$t = explode('.', (string)($_GET['t'] ?? ''), 2);
if (count($t) !== 2 || !ctype_digit($t[0]) || (int)$t[0] < time()
    || !hash_equals(hash_hmac('sha256', 'speed|' . $t[0], $key), $t[1])) not_found();

@set_time_limit(60);
ignore_user_abort(false);

// 下载：吐出 n 字节随机数据（最多 50MB）
if ($op === 'd' && $method === 'GET') {
    $n = max(1 << 20, min(50 << 20, (int)($_GET['n'] ?? 0)));
    while (ob_get_level() > 0) ob_end_clean();
    header('Content-Type: application/octet-stream');
    header('Content-Length: ' . $n);
    header('X-Accel-Buffering: no');
    $chunk = random_bytes(65536);
    for ($left = $n; $left > 0; $left -= 65536) {
        echo $left >= 65536 ? $chunk : substr($chunk, 0, $left);
        flush();
        if (connection_aborted()) break;
    }
    exit;
}

// 上传：把收到的数据读完丢掉
if ($op === 'u' && $method === 'POST') {
    $in = fopen('php://input', 'rb');
    $got = 0;
    if ($in) { while (!feof($in)) { $s = fread($in, 65536); if ($s === false) break; $got += strlen($s); } fclose($in); }
    out(['ok' => true, 'n' => $got]);
}

not_found();
