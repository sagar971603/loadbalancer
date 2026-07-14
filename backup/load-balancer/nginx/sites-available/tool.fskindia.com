server {
    listen 443 ssl http2;
    server_name tool.fskindia.com;

    # SSL Configuration - managed by Certbot
    ssl_certificate /etc/letsencrypt/live/tool.fskindia.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tool.fskindia.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Logging
    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;

    # Large client body (file uploads)
    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8001;

        # Required headers
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";

        # Long timeout handling
        proxy_connect_timeout 320s;
        proxy_send_timeout 320s;
        proxy_read_timeout 320s;
        send_timeout 320s;

        # Allow WebSocket support (if needed)
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Optional health check route
    location = /health {
        return 200 'healthy';
        add_header Content-Type text/plain;
    }
}

# HTTP redirect to HTTPS
server {
    listen 80;
    server_name tool.fskindia.com;

    # Redirect to HTTPS
    return 301 https://$host$request_uri;
}
