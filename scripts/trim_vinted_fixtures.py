"""Trims real Vinted HTML exports down to just the item/order cards for use as test fixtures.

Usage: python scripts/trim_vinted_fixtures.py <src_dir> <dest_dir>
"""
import sys
from pathlib import Path
from bs4 import BeautifulSoup

KEEP_CLASS_KEYWORDS = ("status", "badge", "overlay", "feed-grid__item", "ItemBox")
KEEP_TESTID_KEYWORDS = ("grid-item", "product-item-id-", "status", "my-orders-item")


def _strip(el):
    for svg in el.find_all("svg"):
        t = svg.find("title")
        if t and t.get_text(strip=True):
            span = BeautifulSoup("", "html.parser").new_tag("span")
            span.string = t.get_text(strip=True)
            svg.replace_with(span)
        else:
            svg.decompose()
    for junk in el.find_all(["picture", "source", "noscript", "script", "style"]):
        junk.decompose()
    for tag in [el] + el.find_all(True):
        for a in list(tag.attrs):
            if a in ("href", "alt", "title"):
                continue
            if a == "class":
                kept = [c for c in tag.get("class", []) if any(k in c for k in KEEP_CLASS_KEYWORDS)]
                if kept:
                    tag["class"] = kept
                else:
                    del tag.attrs["class"]
                continue
            if a == "data-testid":
                v = tag.get("data-testid", "")
                if v == "my-orders-item" or "status" in v or (any(k in v for k in KEEP_TESTID_KEYWORDS) and "--" not in v):
                    continue
            del tag.attrs[a]
    return el


FILES = {
    "tlodato24 - member profile _ Vinted.html": "closet_active.html",
    "tlodato24 - member profile _ Vinted_sold.html": "closet_sold.html",
    "tlodato24 - member profile _ Vinted_all.html": "closet_all.html",
    "My orders _ Vinted.html": "my_orders.html",
}


def trim(src: Path) -> str:
    soup = BeautifulSoup(src.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    cards = soup.find_all("a", attrs={"data-testid": "my-orders-item"})
    if not cards:
        cards = soup.select("[data-testid*='grid-item']")
    body = "\n".join(str(_strip(c)) for c in cards)
    return f"<html><head><title>{title}</title></head><body>\n{body}\n</body></html>\n"


if __name__ == "__main__":
    src_dir, dest_dir = Path(sys.argv[1]), Path(sys.argv[2])
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src_name, dest_name in FILES.items():
        p = src_dir / src_name
        if not p.exists():
            print(f"skip (missing): {src_name}")
            continue
        out = trim(p)
        (dest_dir / dest_name).write_text(out, encoding="utf-8")
        print(f"{dest_name}: {len(out) // 1024} KB")
