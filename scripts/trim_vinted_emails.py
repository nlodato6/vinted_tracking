"""Builds anonymized, trimmed Vinted email fixtures for tests.

Usage:
  ./venv/bin/python scripts/trim_vinted_emails.py RAW_DIR [OUT_FILE]

RAW_DIR holds raw .eml files produced by scripts/dump_vinted_emails.py (keep those OUTSIDE the repo).
Writes one JSON object per email ({subject, date, message_id, html}) to
tests/fixtures/emails/vinted_emails.jsonl. Bodies are reduced to <p>/<a>/<img> lines; buyer
usernames, the seller username, street addresses, tracking codes and payout details are scrubbed,
and tracking/login tokens are stripped from links (links stay base64-wrapped like the originals).
"""

import base64
import email
import email.message
import email.utils
import glob
import html as html_lib
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.email_parser import decode_mime_words, unwrap_vinted_link  # noqa: E402

SELLER = "tlodato24"
KEEP_ALL = re.compile(
    r"shipping label – use by|^You sold|^This order is completed|^New offer for|was just favorited", re.I
)
KEEP_SOME = {  # subject regex -> max kept (template coverage for ignored / status emails)
    r"^Order update for": None,  # filtered below: all cancellations + a few of each other status
    r"^Your receipt for": 1,
    r"^Leave feedback for the seller": 1,
    r"Confirmation\s*needed": 1,
    r"^New message about": 1,
    r"^Action needed": 1,
    r"^Item deleted": 1,
    r"^Find out what": 1,
}
SKIP = re.compile(r"payment is being sent|phone number|Spreadsheet shared|New item uploaded", re.I)


def _wrap(url: str) -> str:
    return "https://links.vinted.com/t/" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def _clean_url(url: str) -> str:
    url = unwrap_vinted_link(url)
    if "/inbox/" in url:
        return url.split("?")[0]
    if re.search(r"/e/item\?id=\d+|/items/\d+", url):
        m = re.search(r"(https://www\.vinted\.com/(?:e/item\?id=\d+|items/[\w-]+))", url)
        return m.group(1) if m else url
    return ""


def _html_part(msg) -> str:
    for p in msg.walk():
        if p.get_content_type() == "text/html":
            return p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "ignore")
    for p in msg.walk():
        if p.get_content_type() == "text/plain":
            return "<pre>" + html_lib.escape(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "ignore")) + "</pre>"
    return ""


def _buyers(lines):
    names = set()
    for i, ln in enumerate(lines):
        if (ln == "has bought" or ln.startswith("has favo")) and i:
            names.add(lines[i - 1])
        for rx in (r"^(\S+) has received their order", r"waiting for (\S+) to pick up", r"Reserved for (\S+?)\.",
                   r"It’s also a good idea to get in touch with (\S+) and", r"^(\S+) just offered to buy",
                   r"^(\S+) has favou?rited"):
            m = re.search(rx, ln)
            if m:
                names.add(m.group(1))
    return names


def trim(raw: bytes):
    msg = email.message_from_bytes(raw)
    subject = decode_mime_words(msg.get("Subject", "")).replace("\r\n", " ").replace("\n", " ")
    soup = BeautifulSoup(_html_part(msg), "html.parser")
    for t in soup(["style", "script", "head", "title"]):
        t.decompose()
    links = []
    for a in soup.find_all("a", href=True):
        url = _clean_url(a["href"])
        txt = a.get_text(" ", strip=True)
        if url:
            links.append((url, txt))
            a.replace_with(soup.new_string(f"\n\x00A{len(links) - 1}\x00\n"))
    imgs = []
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if "images1.vinted.net" in src:
            imgs.append((img.get("alt") or "", src.split("?")[0]))
            img.replace_with(soup.new_string(f"\n\x00I{len(imgs) - 1}\x00\n"))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in soup.get_text("\n").split("\n")]
    lines = [ln for ln in lines if ln and ln.strip("\u200c\u00a0 \u034f")]

    # Drop street address block and payout data
    out, skip = [], False
    for ln in lines:
        if ln.startswith("Vinted Inc.") and "Suite" not in ln:
            skip = True
            continue
        if skip and ln.startswith("Item price"):
            skip = False
        if skip:
            continue
        out.append(ln)
    lines = out

    secrets = _buyers(lines)
    body = []
    prev = ""
    for ln in lines:
        m = re.fullmatch(r"\x00([AI])(\d+)\x00", ln)
        if m:
            if m.group(1) == "A":
                url, txt = links[int(m.group(2))]
                body.append(f'<a href="{_wrap(url)}">{html_lib.escape(txt)}</a>')
            else:
                alt, src = imgs[int(m.group(2))]
                body.append(f'<img alt="{html_lib.escape(alt)}" src="{src}">')
            continue
        if prev in ("Tracking code:",):
            ln = "TRACK000000"
        for name in secrets:
            ln = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", "buyer_x", ln)
        ln = ln.replace(SELLER, "seller_x")
        body.append(f"<p>{html_lib.escape(ln)}</p>")
        prev = ln
    for name in secrets:
        subject = subject.replace(name, "buyer_x")
    return {
        "subject": subject,
        "date": msg.get("Date"),
        "message_id": (msg.get("Message-ID") or "").strip(),
        "html": "<div>" + "".join(body) + "</div>",
    }


def main():
    raw_dir = Path(sys.argv[1])
    out_file = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / "tests/fixtures/emails/vinted_emails.jsonl"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    kept, counts, status_counts = [], {}, {}
    for f in sorted(glob.glob(str(raw_dir / "*.eml"))):
        raw = Path(f).read_bytes()
        subj = decode_mime_words(email.message_from_bytes(raw).get("Subject", "")).replace("\r\n", " ")
        if SKIP.search(subj):
            continue
        rec = None
        if KEEP_ALL.search(subj):
            rec = trim(raw)
        elif re.search(r"^Order update for", subj):
            rec = trim(raw)
            kind = ("canceled" if "has been canceled" in rec["html"] else "shipped" if "on its way" in rec["html"]
                    else "received" if "received their order" in rec["html"] else "other")
            status_counts[kind] = status_counts.get(kind, 0) + 1
            if kind != "canceled" and status_counts[kind] > 3:
                rec = None
        else:
            for rx, cap in KEEP_SOME.items():
                if cap and re.search(rx, subj):
                    counts[rx] = counts.get(rx, 0) + 1
                    if counts[rx] <= cap:
                        rec = trim(raw)
                    break
        if rec:
            kept.append(rec)
    kept.sort(key=lambda r: email.utils.parsedate_to_datetime(r["date"]))
    with out_file.open("w") as fh:
        for r in kept:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote {len(kept)} emails to {out_file} ({out_file.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
