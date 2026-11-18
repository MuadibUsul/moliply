import socket, ssl, time
PX = [("45.38.107.97",6014),("64.137.96.74",6641),("198.23.243.226",6361),("38.154.185.97",6370),
      ("84.247.60.125",6095),("191.96.254.138",6185),("198.46.161.42",5092),("64.137.10.153",5803),
      ("104.164.49.38",7693),("23.26.71.145",5628)]
U, P = b"nvvctlro", b"eabxdl4ee7nj"

def get(host, port, target, path, timeout=25):
    s = socket.create_connection((host, port), timeout=timeout)
    s.sendall(b"\x05\x02\x00\x02"); s.settimeout(timeout)
    s.recv(2)
    s.sendall(b"\x01" + bytes([len(U)]) + U + bytes([len(P)]) + P)
    s.recv(2)
    n = target.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(n)]) + n + (443).to_bytes(2, "big"))
    r = s.recv(4)
    if len(r) < 4 or r[1] != 0: raise RuntimeError(f"socks rep={r[:2]!r}")
    a = r[3]
    if a == 1: s.recv(6)
    elif a == 3: s.recv(s.recv(1)[0] + 2)
    elif a == 4: s.recv(18)
    t = ssl._create_unverified_context().wrap_socket(s, server_hostname=target)
    t.sendall(f"GET {path} HTTP/1.1\r\nHost: {target}\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36\r\nAccept: text/html\r\nConnection: close\r\n\r\n".encode())
    buf = b""; t.settimeout(timeout)
    while len(buf) < 600000:
        c = t.recv(8192)
        if not c: break
        buf += c
    t.close()
    return buf

for h, p in PX:
    tag = f"{h}:{p}"
    try:
        t0 = time.time()
        raw = get(h, p, "claude.ai", "/login")
        lat = time.time() - t0
        line = raw.split(b"\r\n", 1)[0].decode(errors="replace")
        low = raw.lower()
        flags = []
        if b"just a moment" in low or b"cf-browser-verification" in low: flags.append("CF_CHALLENGE")
        if b"hcaptcha" in low: flags.append("HCAPTCHA")
        if b"captcha" in low and b"hcaptcha" not in low: flags.append("CAPTCHA")
        if b"access denied" in low or b"blocked" in low: flags.append("BLOCKED")
        print(f"{tag:24} {line:<16} {lat:4.1f}s {len(raw)//1024}KB {' '.join(flags) or 'ok'}")
    except Exception as e:
        print(f"{tag:24} FAIL {type(e).__name__}: {e}")
