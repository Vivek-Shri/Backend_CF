# outreach/proxies.py

from .config import PROXY_LIST, NOPECHA_PROXY_PAYLOAD

def get_proxy_for_worker(worker_index: int) -> tuple[dict, str]:
    """
    Assign a proxy deterministically by worker index.
    """
    slot = worker_index % len(PROXY_LIST)
    host, port, user, pwd, label = PROXY_LIST[slot]
    config = {
        "server": f"http://{host}:{port}",
        "username": user,
        "password": pwd,
    }
    return config, f"{label}(slot{slot})"

def is_proxy_bootstrap_error(err_text: str) -> bool:
    s = str(err_text or "").upper()
    markers = (
        "ERR_PROXY_AUTH_UNSUPPORTED",
        "ERR_PROXY_AUTH_REQUESTED",
        "ERR_PROXY_CONNECTION_FAILED",
        "ERR_TUNNEL_CONNECTION_FAILED",
        "ERR_NO_SUPPORTED_PROXIES",
        "ERR_INVALID_AUTH_CREDENTIALS",
        "ERR_HTTP_RESPONSE_CODE_FAILURE",
    )
    return any(m in s for m in markers)

