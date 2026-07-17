import json
import shutil
import subprocess


curl_bin = shutil.which("curl") or shutil.which("curl.exe")
print("curl_bin:", curl_bin)

if not curl_bin:
    raise SystemExit("curl not found in PATH")

url = "https://eportal.incometax.gov.in/iec/foservices/#/pre-login/register"


def probe(name, extra_args):
    return [
        name,
        [
            curl_bin,
            "-sS",
            "-i",
            "--connect-timeout",
            "15",
            "--max-time",
            "45",
            *extra_args,
            "-H",
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "-H",
            "Accept: application/json, text/plain, */*",
            "-H",
            "Connection: close",
            url,
        ],
    ]


commands = [
    ["version", [curl_bin, "--version"]],
    probe("compressed_http11_ipv4_tls12", ["--compressed", "--http1.1", "--ipv4", "--tlsv1.2", "--tls-max", "1.2", "--no-keepalive"]),
    probe("identity_http11_ipv4_tls12", ["--http1.1", "--ipv4", "--tlsv1.2", "--tls-max", "1.2", "--no-keepalive", "-H", "Accept-Encoding: identity"]),
    probe("identity_http10_ipv4_tls12", ["--http1.0", "--ipv4", "--tlsv1.2", "--tls-max", "1.2", "-H", "Accept-Encoding: identity"]),
    probe("insecure_http11_ipv4", ["--http1.1", "--ipv4", "--insecure", "--no-keepalive", "-H", "Accept-Encoding: identity"]),
]

for name, cmd in commands:
    print("\nPROFILE:", name)
    print("\nCOMMAND:", json.dumps(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    print("returncode:", result.returncode)
    print("stdout_head:")
    print(result.stdout[:2000])
    print("stderr:")
    print(result.stderr[:2000])
