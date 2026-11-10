"""Drafting and sending. One message per company, your resume attached, sent from your own mailbox."""
import imaplib
import mimetypes
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid


class _Blank(dict):
    def __missing__(self, key):
        return ""


def render(template, ctx):
    return template.format_map(_Blank(ctx)).strip()


def draft_path(drafts_dir, lead):
    return os.path.join(drafts_dir, lead["id"] + ".txt")


def write_draft(drafts_dir, lead, subject, body):
    os.makedirs(drafts_dir, exist_ok=True)
    with open(draft_path(drafts_dir, lead), "w", encoding="utf-8") as f:
        f.write(f"Subject: {subject}\n\n{body}\n")


def read_draft(drafts_dir, lead):
    with open(draft_path(drafts_dir, lead), encoding="utf-8") as f:
        first, _, body = f.read().partition("\n")
    return first.removeprefix("Subject:").strip(), body.strip() + "\n"


def build_message(me, to, subject, body, resume_path, in_reply_to=""):
    msg = EmailMessage()
    msg["From"] = formataddr((me["name"], me["email"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=me["email"].split("@")[1])
    if in_reply_to:
        msg["In-Reply-To"] = msg["References"] = in_reply_to
    msg.set_content(body)
    ctype = mimetypes.guess_type(resume_path)[0] or "application/octet-stream"
    with open(resume_path, "rb") as f:
        msg.add_attachment(f.read(), maintype=ctype.split("/")[0], subtype=ctype.split("/")[1],
                           filename=os.path.basename(resume_path))
    return msg


def smtp_send(send_cfg, me, msg, password):
    host, port = send_cfg["smtp_host"], int(send_cfg["smtp_port"])
    security = send_cfg.get("smtp_security", "starttls")
    if security == "ssl":
        s = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
    else:
        s = smtplib.SMTP(host, port, timeout=30)
    with s:
        if security == "starttls":
            s.starttls(context=ssl.create_default_context())
        if password:
            s.login(send_cfg.get("smtp_user") or me["email"], password)
        s.send_message(msg)


def check_inbox(send_cfg, me, password, leads):
    """Return {lead_id: 'replied' | 'bounced'} by looking for mail from each contacted address."""
    result = {}
    with imaplib.IMAP4_SSL(send_cfg.get("imap_host", "imap.gmail.com")) as im:
        im.login(send_cfg.get("smtp_user") or me["email"], password)
        im.select("INBOX", readonly=True)
        for lead in leads:
            since = datetime.fromisoformat(lead["sent_at"]).strftime("%d-%b-%Y")
            addr = lead["email"]
            if im.search(None, f'(SINCE {since} FROM "{addr}")')[1][0].split():
                result[lead["id"]] = "replied"
            elif im.search(None, f'(SINCE {since} FROM "mailer-daemon" BODY "{addr}")')[1][0].split():
                result[lead["id"]] = "bounced"
    return result
