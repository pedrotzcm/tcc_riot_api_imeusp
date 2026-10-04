import json
import os
import socket
from pathlib import Path

from dotenv import load_dotenv
from requests.exceptions import ChunkedEncodingError, ConnectionError as RequestsConnectionError
from riotwatcher import LolWatcher
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()

# retry de falha de rede : 5 tentativas com espera crescente
network_retry = retry(
    retry=retry_if_exception_type((ChunkedEncodingError, RequestsConnectionError)),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)

orig_getaddrinfo = socket.getaddrinfo
DNS_CACHE = Path(os.environ.get("RIOT_DNS_CACHE", "~/.riot_dns_cache.json")).expanduser()
save: dict[str, list[str]] = {}


def cache_dns() -> dict[str, list[str]]:
    try:
        return json.loads(DNS_CACHE.read_text())
    except (OSError, ValueError):
        return {}


def grava_ip(host: str, ips: list[str]) -> None:
    #guarda os IPs do host em disco, só quando mudam
    if save.get(host) == ips:
        return
    save[host] = ips

    cache = cache_dns()
    cache[host] = ips

    tmp = DNS_CACHE.with_name(f"{DNS_CACHE.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(cache))
        tmp.replace(DNS_CACHE)
    except OSError:
        pass


def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        res = orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    except socket.gaierror:
        ips = save.get(host) or cache_dns().get(host)

        if not ips:
            raise

        cached = []

        for ip in ips:
            cached.append((socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, int(port))))
        return cached

    if isinstance(host, str) and host.endswith(".riotgames.com"):
        ips = set()
        for r in res:
            ips.add(r[4][0])

        grava_ip(host, sorted(ips))
    return res


socket.getaddrinfo = ipv4_getaddrinfo

watcher: LolWatcher | None = None


def get_watcher() -> LolWatcher:
    global watcher
    if watcher is None:
        watcher = LolWatcher(os.environ["RIOT_API_KEY"])

    return watcher
