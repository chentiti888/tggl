"""网页推送（Web Push）发送：纯 Python 实现，不需要装任何第三方库。

用到的标准：
- RFC 8291 / 8188：推送内容加密（ECDH P-256 + HKDF + AES-128-GCM，aes128gcm 编码）
- RFC 8292：VAPID（ES256 签名的 JWT，证明推送是这台服务器发的）
苹果（web.push.apple.com）、谷歌（fcm）、火狐都按同一套标准收。
装了 cryptography 库时自动用它算（更快），没装就用下面的纯 Python 实现，结果完全一样。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- 小工具


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text):
    text = str(text or "")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key, data):
    return hmac.new(key, data, hashlib.sha256).digest()


# ---------------------------------------------------------------- P-256 椭圆曲线

_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_A = _P - 3
_B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
_G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
      0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)


def _jadd(p, q):
    if p is None:
        return q
    if q is None:
        return p
    x1, y1, z1 = p
    x2, y2, z2 = q
    z1z1, z2z2 = z1 * z1 % _P, z2 * z2 % _P
    u1, u2 = x1 * z2z2 % _P, x2 * z1z1 % _P
    s1, s2 = y1 * z2 * z2z2 % _P, y2 * z1 * z1z1 % _P
    if u1 == u2:
        if s1 != s2:
            return None
        return _jdouble(p)
    h = (u2 - u1) % _P
    r = (s2 - s1) % _P
    hh = h * h % _P
    hhh = h * hh % _P
    v = u1 * hh % _P
    x3 = (r * r - hhh - 2 * v) % _P
    y3 = (r * (v - x3) - s1 * hhh) % _P
    z3 = h * z1 * z2 % _P
    return (x3, y3, z3)


def _jdouble(p):
    if p is None:
        return None
    x, y, z = p
    if y == 0:
        return None
    yy = y * y % _P
    s = 4 * x * yy % _P
    zz = z * z % _P
    m = 3 * (x - zz) * (x + zz) % _P          # a = -3
    x3 = (m * m - 2 * s) % _P
    y3 = (m * (s - x3) - 8 * yy * yy) % _P
    z3 = 2 * y * z % _P
    return (x3, y3, z3)


def _mul(k, pt):
    acc = None
    add = (pt[0], pt[1], 1)
    while k:
        if k & 1:
            acc = _jadd(acc, add)
        add = _jdouble(add)
        k >>= 1
    if acc is None:
        raise ValueError("无效的点")
    x, y, z = acc
    zi = pow(z, -1, _P)
    zi2 = zi * zi % _P
    return (x * zi2 % _P, y * zi2 * zi % _P)


def _on_curve(pt):
    x, y = pt
    return 0 <= x < _P and 0 <= y < _P and (y * y - (x * x * x + _A * x + _B)) % _P == 0


def _pub_bytes(pt):
    return b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


def _pub_point(raw):
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("公钥格式不对")
    pt = (int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big"))
    if not _on_curve(pt):
        raise ValueError("公钥不在曲线上")
    return pt


def new_key():
    """返回 (私钥整数, 65 字节公钥)。"""
    d = secrets.randbelow(_N - 1) + 1
    return d, _pub_bytes(_mul(d, _G))


def _ecdh(d, peer_raw):
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        priv = ec.derive_private_key(d, ec.SECP256R1())
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), peer_raw)
        return priv.exchange(ec.ECDH(), peer)
    except ImportError:
        pass
    return _mul(d, _pub_point(peer_raw))[0].to_bytes(32, "big")


def _es256(d, msg):
    """ES256 签名，返回 64 字节 r||s。"""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        der = ec.derive_private_key(d, ec.SECP256R1()).sign(msg, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")
    except ImportError:
        pass
    z = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    while True:
        k = secrets.randbelow(_N - 1) + 1
        r = _mul(k, _G)[0] % _N
        if not r:
            continue
        s = pow(k, -1, _N) * (z + r * d) % _N
        if s:
            return r.to_bytes(32, "big") + s.to_bytes(32, "big")


# ---------------------------------------------------------------- AES-128-GCM（纯 Python 版）

_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16")
_RCON = (1, 2, 4, 8, 16, 32, 64, 128, 27, 54)


def _xt(a):
    a <<= 1
    return (a ^ 0x11b) if a & 0x100 else a


def _expand(key):
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [sum(w[r * 4:r * 4 + 4], []) for r in range(11)]


def _aes_block(rk, block):
    s = [block[i] ^ rk[0][i] for i in range(16)]
    for rnd in range(1, 11):
        s = [_SBOX[b] for b in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]     # ShiftRows（按列存储）
        if rnd != 10:
            out = []
            for c in range(4):
                a = s[c * 4:c * 4 + 4]
                t = a[0] ^ a[1] ^ a[2] ^ a[3]
                out += [a[0] ^ t ^ _xt(a[0] ^ a[1]) & 0xff, a[1] ^ t ^ _xt(a[1] ^ a[2]) & 0xff,
                        a[2] ^ t ^ _xt(a[2] ^ a[3]) & 0xff, a[3] ^ t ^ _xt(a[3] ^ a[0]) & 0xff]
            s = out
        s = [s[i] ^ rk[rnd][i] for i in range(16)]
    return bytes(s)


def _gmul(x, y):
    r = 0xe1 << 120
    z = 0
    for i in range(127, -1, -1):
        if (y >> i) & 1:
            z ^= x
        x = (x >> 1) ^ r if x & 1 else x >> 1
    return z


def _aesgcm_encrypt(key, nonce, data):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).encrypt(nonce, data, None)
    except ImportError:
        pass
    rk = _expand(key)
    h = int.from_bytes(_aes_block(rk, bytes(16)), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    out = bytearray()
    for i in range(0, len(data), 16):
        ctr = nonce + struct.pack(">I", 2 + i // 16)
        ks = _aes_block(rk, ctr)
        out += bytes(a ^ b for a, b in zip(data[i:i + 16], ks))
    g = 0
    for i in range(0, len(out), 16):
        blk = bytes(out[i:i + 16]).ljust(16, b"\x00")
        g = _gmul(g ^ int.from_bytes(blk, "big"), h)
    g = _gmul(g ^ ((0 << 64) | (len(out) * 8)), h)
    tag = bytes(a ^ b for a, b in zip(g.to_bytes(16, "big"), _aes_block(rk, j0)))
    return bytes(out) + tag


# ---------------------------------------------------------------- 加密 + 发送


def encrypt(payload, p256dh, auth):
    """RFC 8291：按订阅者的公钥加密，返回请求体。"""
    ua_pub = unb64u(p256dh)
    auth_secret = unb64u(auth)
    _pub_point(ua_pub)
    as_d, as_pub = new_key()
    shared = _ecdh(as_d, ua_pub)
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")
    salt = secrets.token_bytes(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    body = _aesgcm_encrypt(cek, nonce, payload + b"\x02")
    return salt + struct.pack(">I", 4096) + bytes([len(as_pub)]) + as_pub + body


def vapid_header(endpoint, d, pub_raw, sub):
    u = urllib.parse.urlsplit(endpoint)
    aud = f"{u.scheme}://{u.netloc}"
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": aud, "exp": int(time.time()) + 12 * 3600, "sub": sub}, separators=(",", ":")).encode())
    signing = (head + "." + claims).encode()
    jwt = head + "." + claims + "." + b64u(_es256(d, signing))
    return f"vapid t={jwt}, k={b64u(pub_raw)}"


def send(sub, payload, d, pub_raw, contact, ttl=86400, urgency="high", topic=None):
    """发一条推送。返回 HTTP 状态码（201 成功；404/410 表示订阅已失效，应删除）。"""
    body = encrypt(json.dumps(payload, ensure_ascii=False).encode(), sub["p256dh"], sub["auth"])
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Encoding": "aes128gcm",
        "TTL": str(int(ttl)),
        "Urgency": urgency,
        "Authorization": vapid_header(sub["endpoint"], d, pub_raw, contact),
    }
    if topic:
        headers["Topic"] = topic[:32]
    req = urllib.request.Request(sub["endpoint"], data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
