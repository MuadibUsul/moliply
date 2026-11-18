import json
PX = [l.strip().split(":") for l in open("Webshare 10 proxies.txt", encoding="utf-8") if l.strip()]
inb, out, rules = [], [], []
for i, (host, port, user, pwd) in enumerate(PX):
    lp = 10101 + i
    inb.append({"type": "mixed", "tag": f"p-{lp}", "listen": "127.0.0.1", "listen_port": lp})
    out.append({"type": "socks", "tag": f"up-{lp}", "server": host, "server_port": int(port),
                "version": "5", "username": user, "password": pwd})
    rules.append({"inbound": [f"p-{lp}"], "outbound": f"up-{lp}"})
cfg = {"log": {"level": "warn"}, "inbounds": inb,
       "outbounds": out + [{"type": "direct", "tag": "direct"}], "route": {"rules": rules}}
open(".tmp_singbox.json", "w").write(json.dumps(cfg, indent=1))
print("ports 10101-10110 -> the 10 proxies")
