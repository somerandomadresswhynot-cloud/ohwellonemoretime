from __future__ import annotations


_CLOZE_OPEN = "{{c::"
_CLOZE_CLOSE = "}}"


def replace_nth_cloze(markdown_text: str, cloze_index: int) -> str:
    text = str(markdown_text or "")
    target = int(cloze_index)
    if target < 0:
        return text
    idx = 0
    out: list[str] = []
    pos = 0
    while pos < len(text):
        start = text.find(_CLOZE_OPEN, pos)
        if start < 0:
            out.append(text[pos:])
            break
        end = text.find(_CLOZE_CLOSE, start + len(_CLOZE_OPEN))
        if end < 0:
            out.append(text[pos:])
            break
        out.append(text[pos:start])
        body_start = start + len(_CLOZE_OPEN)
        body = text[body_start:end]
        if idx == target:
            out.append(body)
        else:
            out.append(text[start:end + len(_CLOZE_CLOSE)])
        idx += 1
        pos = end + len(_CLOZE_CLOSE)
    return "".join(out)
