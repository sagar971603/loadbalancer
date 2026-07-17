import json
import os
import shutil
import sys


try:
    import curl_cffi
    curl_cffi_available = True
    curl_cffi_version = getattr(curl_cffi, "__version__", "unknown")
except Exception as exc:
    curl_cffi_available = False
    curl_cffi_version = str(exc)


interesting_env = {
    key: os.environ.get(key)
    for key in [
        "PATH",
        "HOME",
        "USER",
        "EP_PORTAL_HTTP_TRANSPORT",
        "EP_PORTAL_CURL",
        "EP_PORTAL_CURL_CFFI_IMPERSONATE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    ]
}


print(json.dumps({
    "python": sys.executable,
    "version": sys.version,
    "cwd": os.getcwd(),
    "curl": shutil.which("curl") or shutil.which("curl.exe"),
    "curl_cffi_available": curl_cffi_available,
    "curl_cffi_version": curl_cffi_version,
    "env": interesting_env,
}, indent=2))
