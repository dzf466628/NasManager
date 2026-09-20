"""Pure helpers for system configuration view."""


def shq(value: str) -> str:
    """Quote a value for a POSIX shell command."""
    return "'" + value.replace("'", "'\\''") + "'"


def main_site_info(conn):
    """Return the configured main site's web directory and node command."""
    web_dir, node_proc = "", ""
    if conn:
        sites = conn.sites or []
        main = next((s for s in sites if s.get("startable")), (sites[0] if sites else None))
        web_dir = conn.web_dir or (main.get("web_dir") if main else "") or ""
        node_proc = conn.node_proc or (main.get("node_proc") if main else "") or ""
    return web_dir, node_proc
