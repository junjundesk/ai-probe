# api.shuaiapi.com 服务器网络诊断

测试目标：`root@38.76.209.3`（主机名 `ser476178292087`），时间 2026-08-25 UTC。只读检查，未修改远端。

## DNS / 路由

- `api.shuaiapi.com CNAME aaa.wei-yan.cn`。
- IPv4：`104.26.6.67`, `104.26.7.67`, `172.67.74.19`。
- IPv6：`2606:4700:20::681a:643`, `2606:4700:20::681a:743`, `2606:4700:20::ac43:4a13`。
- 服务器仅有链路本地 IPv6（`fe80::/64`），`ip -6 route get ...` 返回 `Network is unreachable`；所以当前请求不会走 IPv6。
- IPv4 路由：`104.26.6.67 via 10.1.126.1 dev eth0 src 10.1.126.2`。
- DNS 查询本身约 1 ms；`resolvectl` 显示上游 DNS `223.5.5.5/119.29.29.29`（链路状态另有 `8.8.8.8/8.8.8.4`）。

## HTTP timing

`curl 8.5.0`，默认协商 HTTP/2，10 次固定解析测试：

| Cloudflare IPv4 | 典型 TCP connect | TLS 完成 | TTFB/总耗时 | 备注 |
|---|---:|---:|---:|---|
| 104.26.6.67 | 3.7–34 ms | 65–150 ms | 0.30–0.44 s | 较稳定 |
| 104.26.7.67 | 3.6–22 ms | 75–162 ms | 0.32–0.66 s | 有抖动 |
| 172.67.74.19 | 3.4–5.4 ms | 74–577 ms | 0.31–1.30 s | 偶发 TLS 抖动；一次 429 |

默认 5 次示例：DNS `0.003–0.012 s`，TCP `0.007–0.022 s`，TLS `0.100–0.156 s`，TTFB `0.333–0.658 s`，总计 `0.333–0.658 s`。

强制 HTTP/1.1 与 HTTP/2 均约 `0.33–0.40 s`，没有明显协议收益。已安装 libcurl 不支持 `--http3-only`，而响应头有 `alt-svc: h3=":443"`，暂时无法验证 HTTP/3。

所有固定 IP 响应的 `cf-ray` 区域均为 `HKG`，例如 `cf-ray: ...-HKG`；响应 `cf-cache-status: DYNAMIC`，说明请求落到香港 Cloudflare 边缘且没有缓存，TTFB 大头发生在 TLS 之后。

## 路径

`mtr -4 -n -r -c 10 api.shuaiapi.com`：首跳 `10.1.126.1` 显示 50% ICMP 丢包（下游正常，疑似限速），中间 hop 有 ICMP 丢包/延迟伪象；末端 `104.26.6.67` 0% 丢包，平均约 9.6 ms。因此不能把中间 ICMP 丢包直接视为 TCP/HTTPS 丢包。

## 结论与建议

1. 本机 DNS、MTU、TCP 拥塞算法（`cubic`）不是主要瓶颈；建立连接很快。
2. 主要等待在 Cloudflare TLS 后的 TTFB（通常约 0.2–0.3 s 额外），并伴随个别 IP/TLS 抖动；Cloudflare 回源或 API 应用处理更可疑。
3. 应用侧启用长连接/HTTP keep-alive、连接池并复用 HTTP/2 session，避免每次重复 DNS/TCP/TLS；可直接优先固定实测较稳的 `104.26.6.67`，但 Cloudflare IP 会变，不建议永久硬编码。
4. 不使用当前 `38.76.209.3:7892` 代理：此前代理平均约 0.93–1.16 s，明显慢于直连约 0.4 s。
5. 若可控制 Cloudflare，检查香港边缘到源站的回源 RTT、源站应用 TTFB、Workers/规则；考虑让 API 响应可缓存（当前 `DYNAMIC`）或在更近区域部署源站。
6. 若必须从该服务器进一步降延迟，应更换网络出口/云区域或使用靠近 API 源站的 egress；单纯调 sysctl 很难把 0.3 s TTFB 降下来。
