upstream regpan4_backend {
    # The session-aware router reads these peers and weights for every new job.
    ip_hash;
    server 217.217.249.145:8002 weight=2;
    server 217.216.78.35:8002 weight=1 down;
    server 217.216.78.96:8002 weight=1;
    server 147.93.171.241:8002 weight=1;
    server 147.93.169.153:8002 weight=2;
    server 147.93.171.101:8002 weight=2;
}

server {
    server_name regpan4.fskindia.com;

    location / {
        proxy_pass http://127.0.0.1:18002;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_send_timeout 360s;
        proxy_read_timeout 360s;
    }

    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/regpan4.fskindia.com/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/regpan4.fskindia.com/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot

}

server {
    if ($host = regpan4.fskindia.com) {
        return 301 https://$host$request_uri;
    } # managed by Certbot
    listen 80;
    server_name regpan4.fskindia.com;
    return 404; # managed by Certbot
}
