# 共享服务器隔离部署（Caddy 前置）

适用于“一台服务器已有 Caddy 与多个站点，新增 MOLIPLY 但不能影响现有业务”的场景。

## 隔离原则

- Compose 项目名固定为 `moliply`，容器名为 `moliply-*`；
- 数据库/Redis/API/Worker/Beat 不映射任何宿主机端口；
- Web 仅映射 `127.0.0.1:18088` 供本机验证；
- Web 加入现有外部 Docker 网络 `web`（别名 `moliply-web`），由宿主机现有 Caddy 通过 `reverse_proxy moliply-web:3000` 对外提供服务；
- 不修改现有站点的 Compose、容器、网络或 Caddy 站点块；只追加新的 Caddy site block；
- Caddy 修改前必须备份，先 `caddy validate`，再 `caddy reload`，失败立即恢复。

## 目录

```text
$BASE/
├── .env                       # 600，含真实密钥
├── docker-compose.server.yml
├── current -> releases/<timestamp>
├── releases/<timestamp>/
│   ├── recharge-gateway-backend/
│   └── recharge-gateway-frontend/
├── caddy-backups/
├── enable-moliply-domain.sh
└── domain-watch.log
```

## 首次部署

1. 在服务器准备源码 release 目录与 `current` 软链接；
2. 复制 `.env.example` 为 `.env`，填写数据库密码、`MASTER_AES_KEY`、管理员密码、正式域名；
3. 启动：

```bash
cd "$BASE"
docker compose -f docker-compose.server.yml --env-file .env up -d --build
docker compose -f docker-compose.server.yml ps
```

4. 确认 Web 只监听 `127.0.0.1:18088`：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18088/
```

## 接入现有 Caddy

Caddyfile 通常位于宿主机 `/opt/caddy/Caddyfile`，由现有 Caddy 容器加载。添加站点前先备份：

```bash
cp /opt/caddy/Caddyfile "$BASE/caddy-backups/Caddyfile.$(date +%Y%m%d-%H%M%S)"
```

追加：

```caddyfile
moliply.shop {
    encode zstd gzip
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options SAMEORIGIN
        Referrer-Policy strict-origin-when-cross-origin
    }
    reverse_proxy moliply-web:3000
}
```

验证并平滑 reload：

```bash
docker exec caddy-caddy-1 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec caddy-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

> 不要重启或 down 整个 Caddy 项目；只 reload 配置。
> 如果服务器上的 Caddy 容器名不是 `caddy-caddy-1`，请替换为实际名称。

## DNS 与证书

`moliply.shop` 需要一条 A 记录指向服务器公网 IP（如果使用 Cloudflare，先设为 DNS only）。DNS 生效后：

```bash
export MOLIPLY_SERVER_IP=<server-public-ip>
export MOLIPLY_DOMAIN=moliply.shop
export MOLIPLY_BASE=$BASE
"$BASE/enable-moliply-domain.sh"
```

该脚本会检查 DNS → 备份 Caddyfile → 追加站点 → 校验 → reload → 等待证书。Caddy 申请 Let's Encrypt 证书通常需要 10–60 秒。

也可以把脚本放入循环，在 DNS 生效前每 5 分钟检查一次：

```bash
nohup bash -c 'for i in $(seq 1 288); do "$BASE/enable-moliply-domain.sh"; rc=$?; if [ $rc -eq 0 ]; then exit 0; fi; sleep 300; done' > "$BASE/domain-watch.log" 2>&1 &
```


## 运维脚本

| 脚本 | 用途 |
| --- | --- |
| `backup-db.sh` | 备份 PostgreSQL 与 `.env` 到 `$BASE/backups/`，保留 14 天 |
| `preflight.sh` | 只读检查：支付、库存、演示数据、备份、健康检查；未通过则不能收款 |
| `purge-demo-data.sh` | 清理演示卡密/成品号/优惠码，并关闭演示库存；执行前自动备份 |
| `enable-moliply-domain.sh` | DNS 生效后追加 Caddy 站点并申请证书 |
| `GO-LIVE.md` | 站主必须完成的开业清单 |

每日备份 cron 示例（只追加 MOLIPLY 自己的条目，不要覆盖其他项目）：

```cron
30 3 * * * MOLIPLY_BASE=/opt/moliply /opt/moliply/backup-db.sh >> /opt/moliply/backup.log 2>&1
```

上线前：

```bash
MOLIPLY_BASE=/opt/moliply /opt/moliply/preflight.sh
```

全部 PASS 后，再切换 `PAYMENT_MODE=live`、关闭 mock、导入真实库存并对外推广。

## V2 自营通道路由（增量升级）

- 新增列由应用启动时的 `ensure_schema_compatibility()` 自动补齐，只做加法，不删改 V1 数据；
- Worker 镜像需要内置 Chromium，`docker-compose.server.yml` 已将 worker 的 `INSTALL_BROWSER` 设为 `true`（API/Beat 保持 `false`）；
- 路由策略、菲/美住宅代理、卡池余额阈值、扣款金额都可在后台「系统设置」或「VCC 卡池」调整；
- 新增环境变量见 `.env.example` 的 `ROUTING_POLICY` / `PH_RESIDENTIAL_PROXY_URL` / `US_RESIDENTIAL_PROXY_URL` 等；
- 升级后先跑 `preflight.sh`，再在后台录入测试卡并以演练模式验证，最后才关闭 `SELF_VCC_DRY_RUN`。

## 发布 / 回滚

```bash
# 新源码解压到 releases/<timestamp> 后
cd "$BASE"
ln -sfn releases/<timestamp> current
docker compose -f docker-compose.server.yml --env-file .env up -d --build

# 回滚
ln -sfn releases/<previous-timestamp> current
docker compose -f docker-compose.server.yml --env-file .env up -d --build
```

保留最近 2 个 release 与 2 个镜像；清理时只处理 `moliply-*` 相关镜像/构建目录，**不要动其他项目的镜像或卷**。

## 运维

```bash
cd "$BASE"
docker compose -f docker-compose.server.yml ps
docker compose -f docker-compose.server.yml logs -f api worker
docker compose -f docker-compose.server.yml restart api worker
```

数据库迁移目前由应用启动时 `create_all` 处理；如后续引入 Alembic，需要在发布流程中增加迁移步骤。
