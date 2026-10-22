"""研究复盘包与判定结果的目录查找。"""

from pathlib import Path


def research_dir(kind: str, name: str, root: Path) -> Path:
    """按名字查找 review 或 stance 目录，找不到时返回顶层新目录。"""
    if kind not in ("review", "stance"):
        raise ValueError(f"研究目录类型无效：{kind}")
    base = root / "data" / "research" / kind
    if "/" in name:
        return base / name

    markers = ("review.toml",) if kind == "review" else ("windows.csv", "facts.csv")

    def is_pack(path: Path) -> bool:
        return path.is_dir() and any((path / marker).is_file() for marker in markers)

    direct = base / name
    if is_pack(direct):
        return direct
    if not base.is_dir():
        return direct
    found = sorted((category / name for category in base.iterdir()
                    if category.is_dir() and not category.name.startswith((".", "_"))
                    and is_pack(category / name)), key=str)
    if len(found) > 1:
        raise ValueError(f"研究目录 {name} 有多个位置：{', '.join(map(str, found))}")
    return found[0] if found else direct
