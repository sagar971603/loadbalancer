upstream regpan4_backend {
    ip_hash;
    server 217.216.78.96:8002;
    server 217.216.78.35:8002;
    server 217.217.249.145:8002;
    server 147.93.168.214:8002 max_fails=3 fail_timeout=30s;
    server 147.93.171.116:8002 max_fails=3 fail_timeout=30s;
    server 147.93.168.221:8002 max_fails=3 fail_timeout=30s;


}

server {
    server_name regpan4.fskindia.com;

    location / {
        proxy_pass http://regpan4_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
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
