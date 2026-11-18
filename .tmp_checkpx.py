import concurrent.futures as cf, json, socket, ssl, time

PROXIES = [(l.split(":")[0], int(l.split(":")[1])) for l in open("Webshare 10 proxies.txt", encoding="utf-8") if l.strip()]
U, P = b"nvvctlro", b"eabxdl4ee7nj"

def socks(host, port, target, tport=443, timeout=12):
    s = socket.create_connection((host, port), timeout=timeout)
    s.sendall(b"\x05\x02\x00\x02"); s.settimeout(timeout)
    if s.recv(2) != b"\x05\x02": raise RuntimeError("no auth method")
    s.sendall(b"\x01" + bytes([len(U)]) + U + bytes([len(P)]) + P)
    if s.recv(2) != b"\x01\x00": raise RuntimeError("auth rejected")
    n = target.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(n)]) + n + tport.to_bytes(2, "big"))
    r = s.recv(4)
    if len(r) < 4 or r[1] != 0: raise RuntimeError(f"connect rep={r[:2]!r}")
    atyp = r[3]
    if atyp == 1: s.recv(6)
    elif atyp == 3: s.recv(s.recv(1)[0] + 2)
    elif atyp == 4: s.recv(18)
    return s

def fetch(host, port, target, path, timeout=18):
    s = socks(host, port, target, timeout=timeout)
    t = ssl._create_unverified_context().wrap_socket(s, server_hostname=target)
    t.sendall(f"GET {path} HTTP/1.1\r\nHost: {target}\r\nUser-Agent: curl/8\r\nConnection: close\r\n\r\n".encode())
    buf = b""; t.settimeout(timeout)
    while len(buf) < 400000:
        c = t.recv(8192)
        if not c: break
        buf += c
    t.close()
    return buf.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in buf else b""

def check(item):
    host, port = item
    tag = f"{host}:{port}"
    try:
        t0 = time.time()
        me = json.loads(fetch(host, port, "who.1ms.dog", "/api/me"))
        lat = time.time() - t0
        cf_ = me.get("cf") or {}
        return dict(tag=tag, cc=cf_.get("country"), city=cf_.get("city"), asn=cf_.get("asn"),
                    org=cf_.get("asOrganization"), colo=cf_.get("colo"), rtt=cf_.get("clientTcpRtt"), lat=round(lat, 1))
    except Exception as e:
        return dict(tag=tag, err=f"{type(e).__name__}:{e}"[:44])

with cf.ThreadPoolExecutor(max_workers=5) as ex:
    for r in ex.map(check, PROXIES):
        if "err" in r: print(f"{r['tag']:24} FAIL {r['err']}")
        else: print(f"{r['tag']:24} {r['cc']}/{r['city']:<12} asn={r['asn']} {str(r['org'])[:26]:26} colo={r['colo']} rtt={r['rtt']}ms {r['lat']}s")
