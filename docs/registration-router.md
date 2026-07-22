# Registration router and adding a backend

Registration browser objects stay in one backend process and cannot move between machines. The load balancer therefore sends each new `/init` job by weighted round-robin with one turn per outgoing IP, prefixes the returned session ID with that route, and sends every OTP/close request carrying that ID back to the same backend.

## Capacity rule

- One healthy outgoing IP: `weight=1`, five simultaneous Registration sessions.
- Two healthy outgoing IPs: `weight=2`, ten simultaneous Registration sessions.
- Put one incoming address per physical machine in `regpan4_backend`. Do not add the machine's outgoing alias as a second incoming server.
- Mark an unhealthy outgoing IP out of that backend's `EGRESS_PROXY_POOL`; reduce the NGINX weight to the number of remaining healthy outgoing IPs.

The backend's `EGRESS_MAX_ACTIVE=5` setting is the hard per-IP limit. The local proxy pool chooses the least-used outgoing IP and holds that slot until the browser closes.

If the portal closes a connection before returning any HTTP response, the browser transport makes up to three attempts with 1.5-second and 3-second delays. HTTP responses and business errors such as invalid PAN or OTP are never retried.

The current Registration pool has capacity weights `2, 0, 1, 1, 2, 2` for A-F (40 sessions total). B remains disabled for Registration. C uses `.116`, D uses `.241`, and both outgoing IPs are enabled on A, E, and F. Fast failures do not make a backend receive extra turns; this prevents a degraded route from being selected repeatedly just because it has fewer retained sessions.

## Add a prepared backend

1. On the new backend, verify the application and every source-bound proxy:

   ```bash
   curl -fsS http://127.0.0.1:8010/health
   curl -fsS http://NEW_INCOMING_IP:8002/health
   curl -x http://127.0.0.1:18888 https://api.ipify.org
   curl -x http://127.0.0.1:18889 https://api.ipify.org  # dual-IP only
   ```

2. Set the Registration service drop-in and restart it only while its health response reports zero active sessions:

   ```ini
   [Service]
   Environment="EGRESS_PROXY_POOL=http://127.0.0.1:18888,http://127.0.0.1:18889"
   Environment="EGRESS_MAX_ACTIVE=5"
   Environment="MAX_REQUESTS_PER_IP=1000"
   ```

3. On the load balancer, back up `/etc/nginx/sites-available/regpan4.fskindia.com`. Add one line inside `upstream regpan4_backend`; use `weight=2` for two verified outgoing IPs or `weight=1` for one:

   ```nginx
   server NEW_INCOMING_IP:8002 weight=2;
   ```

4. Validate and reload without restarting NGINX:

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

The router discovers a valid new port-8002 peer automatically; no router restart or source edit is required.

5. Confirm the new route appears and receives work:

   ```bash
   curl -fsS http://127.0.0.1:18002/health | python3 -m json.tool
   grep '/api/v1/registration/' /var/log/nginx/access.log | tail
   ```

## Drain before maintenance

Disable the incoming Registration route from the production dashboard. This prevents new `/init` jobs but prefixed OTP requests continue to the draining backend. Wait until its direct health response reports `active_registration_sessions: 0`, then restart or deploy it. Re-enable it only after direct health and outgoing-IP checks pass.

## Rollback

Restore the timestamped NGINX backup, run `sudo nginx -t`, and reload NGINX. The router has no session database: routing is encoded in the session ID, so a router restart does not lose active route mappings.
