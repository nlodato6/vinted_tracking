"""Dev tool: survey + dump raw Vinted emails via IMAP (read-only, BODY.PEEK).

Usage:
  ./venv/bin/python scripts/dump_vinted_emails.py survey            # subject histogram
  ./venv/bin/python scripts/dump_vinted_emails.py dump OUT_DIR [N]  # save up to N raw .eml per subject template

Raw dumps contain personal data -- write them OUTSIDE the repo (or to a gitignored dir).
Use scripts/trim_vinted_emails.py to produce anonymized test fixtures.
"""

import email
import email.utils
import imaplib
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import settings  # noqa: E402
from src.email_parser import decode_mime_words  # noqa: E402


def subject_template(subject: str) -> str:
    """Collapses variable parts (quoted titles, prices, numbers) so similar subjects group together."""
    s = re.sub(r"[\"“”'‘’].*?[\"“”'‘’]", "<T>", subject)
    s = re.sub(r"\$\s*\d+(?:[.,]\d+)?", "<$>", s)
    s = re.sub(r"\d+", "<N>", s)
    return s.strip()


def connect() -> imaplib.IMAP4_SSL:
    user, pw = settings.gmail_user, settings.gmail_app_password
    if not user or not pw:
        sys.exit("GMAIL_USER / GMAIL_APP_PASSWORD missing in .env")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(user, pw.replace(" ", ""))
    for box in ('"[Gmail]/All Mail"', '"[Google Mail]/All Mail"', "INBOX"):
        if mail.select(box, readonly=True)[0] == "OK":
            break
    return mail


def search_ids(mail) -> list:
    status, data = mail.search(None, "X-GM-RAW", '"from:vinted"')
    return data[0].split() if status == "OK" and data and data[0] else []


def fetch_headers(mail, ids, chunk=200):
    out = []
    for i in range(0, len(ids), chunk):
        part = b",".join(ids[i:i + chunk])
        status, data = mail.fetch(part, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
        if status != "OK":
            continue
        cur_ids = iter(ids[i:i + chunk])
        for resp in data:
            if isinstance(resp, tuple):
                msg = email.message_from_bytes(resp[1])
                mid = re.match(rb"(\d+)", resp[0]).group(1)
                out.append((mid, decode_mime_words(msg.get("Subject", "")), msg.get("From", ""), msg.get("Date", "")))
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "survey"
    mail = connect()
    ids = search_ids(mail)
    headers = fetch_headers(mail, ids)
    print(f"Total vinted emails: {len(headers)}")
    groups = defaultdict(list)
    senders = Counter()
    for mid, subj, frm, dt in headers:
        groups[subject_template(subj)].append((mid, subj, dt))
        senders[email.utils.parseaddr(frm)[1]] += 1
    print("Senders:", dict(senders))
    for tpl, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"{len(items):5d}  {tpl}   e.g. {items[-1][1][:90]!r}")

    if mode == "dump":
        out_dir = Path(sys.argv[2])
        per = int(sys.argv[3]) if len(sys.argv) > 3 else 3
        out_dir.mkdir(parents=True, exist_ok=True)
        for g_i, (tpl, items) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
            for mid, subj, _ in items[-per:]:
                status, data = mail.fetch(mid, "(BODY.PEEK[])")
                if status == "OK":
                    raw = next(r[1] for r in data if isinstance(r, tuple))
                    slug = re.sub(r"[^a-z0-9]+", "_", tpl.lower())[:50].strip("_")
                    (out_dir / f"g{g_i:02d}_{slug}_{mid.decode()}.eml").write_bytes(raw)
        print(f"Dumped to {out_dir}")
    mail.logout()


if __name__ == "__main__":
    main()
