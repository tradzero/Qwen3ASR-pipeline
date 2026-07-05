from __future__ import annotations

from urllib.parse import unquote, urlparse


def normalize_input_path(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()

    parsed = urlparse(text)
    if parsed.scheme.lower() != "file":
        return text

    path = unquote(parsed.path or "")
    if parsed.netloc:
        return "\\\\" + parsed.netloc + path.replace("/", "\\")

    if len(path) >= 4 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path.replace("/", "\\")
