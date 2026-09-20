"""Pure helpers for the service manager."""


def filter_warnings(text: str) -> str:
    """过滤掉非致命的警告行，保留真正的错误。"""
    lines = []
    for line in text.splitlines():
        low = line.lower()
        if "could not chdir to home" in low:
            continue
        if "[warn]" in low or "are meaningless" in low or "duplicate extension" in low:
            continue
        if low.startswith("nginx: [warn]"):
            continue
        lines.append(line)
    return "\n".join(l for l in lines if l.strip()).strip() or "(无输出，可能已成功)"
