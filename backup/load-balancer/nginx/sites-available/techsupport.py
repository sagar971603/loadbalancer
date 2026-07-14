server {
    server_name supportdesk.fskindia.com;

    # Logging
    access_log /var/log/nginx/supportdesk.access.log;
    error_log /var/log/nginx/supportdesk.error.log;

    # Large client body (for file uploads)
    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8004;

        # WebSocket and proxy headers
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeout settings
        proxy_connect_timeout 320s;
        proxy_send_timeout 320s;
        proxy_read_timeout 320s;
        send_timeout 320s;
    }

    # Optional health check route
    location = /health {
        return 200 'healthy';
        add_header Content-Type text/plain;
    }

    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/supportdesk.fskindia.com/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/supportdesk.fskindia.com/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot

}
server {
    if ($host = supportdesk.fskindia.com) {
        return 301 https://$host$request_uri;
    } # managed by Certbot


    listen 80;
    server_name supportdesk.fskindia.com;
    return 404; # managed by Certbot


}