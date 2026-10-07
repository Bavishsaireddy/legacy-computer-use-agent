"""HarborCore: a deliberately hostile mock credit-union back office.

Stand-in for a legacy vendor product: frameset, nested layout tables, no ids,
no test ids, unlabeled inputs, session cookie. All data is fake.

Runtime faults are injected out-of-band (not through the browser):
    curl 'http://127.0.0.1:8765/__fault?notice=1'
        notice=1    next function page is replaced by a system-notice interstitial
        expire=1    the session is dropped on the next function page
        expire_confirm=1  the session is dropped as the sub-account confirm arrives
        slow=2500   next function page is delayed by that many ms
        error=1     next function page is an application error
        flat=1      member lookup form is rendered without its table (layout drift)
        lost=1      next sub-account confirm commits, then shows an error page
    curl 'http://127.0.0.1:8765/__reset'   restore data and clear faults
"""

from __future__ import annotations

import argparse
import copy
import html
import re
import secrets
import threading
import time
from decimal import Decimal, InvalidOperation
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

OPERATOR = ("teller01", "harbor-demo-pw")

MEMBERS = {
    "10042": {"name": "Jane Q. Example", "status": "Active", "ssn": "123-45-6789",
              "savings": Decimal("1234.56"), "checking": Decimal("310.07"), "subs": []},
    "10077": {"name": "Rafael Sample", "status": "Active", "ssn": "987-65-4321",
              "savings": Decimal("58210.00"), "checking": Decimal("4400.19"), "subs": []},
    "20013": {"name": "Restricted Person", "status": "Employee", "ssn": "555-12-3456",
              "savings": Decimal("900.00"), "checking": Decimal("12.00"), "subs": [],
              "restricted": True},
}
ACCOUNT_TYPES = ["Holiday Club", "Vacation Club", "Money Market"]

# Each variant is "the same vendor product at another institution": same
# flow, different branding and field labels.
VARIANTS = {
    "harbor": {"brand": "Harbor Federal Credit Union", "member_no": "Member Number:",
               "search": "Search", "savings": "Share Savings Balance:", "accent": "#14427a"},
    "lakeside": {"brand": "Lakeside Community Bank", "member_no": "Account Holder No:",
                 "search": "Find", "savings": "Primary Savings Bal:", "accent": "#17694d"},
}


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.members = copy.deepcopy(MEMBERS)
        self.sessions: set[str] = set()
        self.faults: dict[str, int] = {}
        self.confirmations = 100


STATE = State()
LABELS = VARIANTS["harbor"]


def money(d: Decimal) -> str:
    return f"${d:,.2f}"


def style() -> str:
    """The product's stylesheet. It is the only presentation layer and uses element
    selectors alone, because the markup still has no ids, classes or labels to hang
    anything on. A clean skin over hostile structure is exactly what the automation meets."""
    return """<style>
* { box-sizing: border-box; }
body { margin: 0; padding: 28px 32px; background: #f3f5f8; color: #16202c;
       font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
font { font: inherit; }
body > table { background: #fff; border: 1px solid #e2e7ed; border-radius: 12px; border-collapse: separate;
               box-shadow: 0 1px 2px rgba(16, 24, 40, 0.05); }
body > table > tbody > tr > td { padding: 24px 28px 28px; }
body > table > tbody > tr > td > font { display: block; font-size: 20px; font-weight: 650; letter-spacing: -0.01em; }
hr { border: 0; border-top: 1px solid #e9edf2; margin: 14px 0 20px; }
p { margin: 0 0 14px; color: #3b4857; }
table table { border-collapse: collapse; }
table[border="1"] { border: 1px solid #e2e7ed; border-radius: 8px; border-collapse: separate; border-spacing: 0; overflow: hidden; margin-bottom: 20px; }
table[border="1"] td, th { border: 0; border-bottom: 1px solid #edf0f4; padding: 10px 16px; text-align: left; }
table[border="1"] tr:last-child td { border-bottom: 0; }
table table table td:first-child { background: #fafbfc; color: #5b6878; font-weight: 500; white-space: nowrap; }
th { background: #f6f8fa; color: #5b6878; font-size: 12.5px; font-weight: 600; }
input[type=text], input[type=password], select {
  font: inherit; height: 38px; padding: 0 12px; min-width: 240px; background: #fff; color: inherit;
  border: 1px solid #cdd5de; border-radius: 8px; outline: none; }
input[type=text]:focus, input[type=password]:focus, select:focus { border-color: ACCENT; box-shadow: 0 0 0 3px rgba(20, 66, 122, 0.15); }
input[type=submit] { font: inherit; font-weight: 600; height: 38px; padding: 0 20px; border: 0; border-radius: 8px;
                     background: ACCENT; color: #fff; cursor: pointer; }
input[type=submit]:hover { filter: brightness(1.12); }
a { color: ACCENT; font-weight: 500; text-decoration: none; }
a:hover { text-decoration: underline; }
font[color=red] { display: inline-block; padding: 9px 14px; border: 1px solid #f1c4c0; border-radius: 8px;
                  background: #fdf1f0; color: #b3261e; font-weight: 500; }
</style>""".replace("ACCENT", LABELS["accent"])


def page(title: str, body: str, css: str = "") -> str:
    return (
        '<html><head><title>%s</title>%s<style>%s</style></head><body bgcolor="#ffffff">'
        '<table width="100%%" cellpadding="0" cellspacing="0"><tr><td>'
        '<font face="Arial" size="4"><b>%s</b></font><hr>%s'
        "</td></tr></table></body></html>" % (title, style(), css, title, body)
    )


# The sign-on screen: the same table markup, presented as a centred card with stacked fields.
LOGIN_CSS = """
html, body { height: 100%; }
body { display: flex; align-items: center; justify-content: center; }
body > table { width: 440px; box-shadow: 0 10px 30px rgba(16, 24, 40, 0.08); }
body > table > tbody > tr > td > font { font-size: 19px; }
body > table > tbody > tr > td { padding: 36px 36px 26px; }
body > table > tbody > tr > td > font::before {
  content: ""; display: block; width: 40px; height: 40px; margin-bottom: 18px; border-radius: 10px; background: ACCENT; }
hr { margin: 16px 0 22px; }
table table { width: 100%; }
table[border="1"], table[border="1"] tbody, table[border="1"] tr, table[border="1"] td { display: block; }
table[border="1"] { border: 0; border-radius: 0; overflow: visible; margin-bottom: 0; }
table[border="1"] td { border: 0; padding: 0; }
table[border="1"] tr { margin-bottom: 16px; }
table[border="1"] td:empty { display: none; }
table table table td:first-child { background: none; color: #2a3644; font-size: 13px; font-weight: 600; margin-bottom: 6px; }
input[type=text], input[type=password] { width: 100%; height: 42px; }
input[type=submit] { width: 100%; height: 42px; margin-top: 4px; }
body > table > tbody > tr > td > font[color=red] { display: block; font-size: 13px; font-weight: 500; letter-spacing: 0; margin-bottom: 18px; }
body > table > tbody > tr > td > font[color=red]::before { display: none; }
p { margin: 22px 0 0; padding-top: 16px; border-top: 1px solid #e9edf2; color: #6b7785; font-size: 12px; text-align: center; }
"""


def chrome_page(body: str, css: str) -> str:
    """Banner and menu frames: same stylesheet, plus a few rules of their own."""
    return f"<html><head>{style()}<style>{css}</style></head><body>{body}</body></html>"


def field_rows(rows: list[tuple[str, str]]) -> str:
    """Label/value rows inside a nested layout table, the legacy idiom."""
    inner = "".join(
        f'<tr><td align="right"><font size="2">{html.escape(k)}</font></td><td>{v}</td></tr>'
        for k, v in rows
    )
    return f'<table border="0"><tr><td><table border="1" cellpadding="3">{inner}</table></td></tr></table>'


def login_page(msg: str = "") -> str:
    note = f'<font color="red">{html.escape(msg)}</font><br>' if msg else ""
    form = field_rows([
        ("Operator ID:", '<input type="text" name="u">'),
        ("Password:", '<input type="password" name="p">'),
        ("", '<input type="submit" value="Sign On">'),
    ])
    footer = "<p>HarborCore Teller 4.2 &middot; Authorized personnel only</p>"
    return page(f"{LABELS['brand']} - Sign On", f'{note}<form method="post" action="/login">{form}</form>{footer}',
                LOGIN_CSS.replace("ACCENT", LABELS["accent"]))


def lookup_page(msg: str = "") -> str:
    note = f'<p><font color="red">{html.escape(msg)}</font></p>' if msg else ""
    box = '<input type="text" name="q" size="10">'
    button = f'<input type="submit" value="{LABELS["search"]}">'
    if STATE.faults.get("flat"):
        form = f'<font size="2">{LABELS["member_no"]}</font> {box} {button}'
    else:
        form = field_rows([(LABELS["member_no"], box), ("", button)])
    return page("Member Lookup", f'{note}<form method="get" action="/search">{form}</form>')


def results_page(mid: str, m: dict) -> str:
    table = (
        '<table border="1" cellpadding="3"><tr><th>Member No</th><th>Name</th><th>Status</th><th>Action</th></tr>'
        f'<tr><td>{mid}</td><td>{html.escape(m["name"])}</td><td>{m["status"]}</td>'
        f'<td><a href="/member?id={mid}">View</a></td></tr></table>'
    )
    return page("Search Results", f"<p>1 member found.</p>{table}")


def member_page(mid: str, m: dict) -> str:
    rows = [
        ("Member No:", mid),
        ("Member Name:", html.escape(m["name"])),
        ("Status:", m["status"]),
        ("Tax ID:", m["ssn"]),
        (LABELS["savings"], money(m["savings"])),
        ("Checking Balance:", money(m["checking"])),
    ]
    subs = "".join(
        f'<tr><td>{s["suffix"]}</td><td>{s["type"]}</td><td>{html.escape(s["nick"])}</td><td>{money(s["balance"])}</td></tr>'
        for s in m["subs"]
    ) or '<tr><td colspan="4">No sub-accounts</td></tr>'
    subs_table = (
        '<p><b>Sub-Accounts</b></p><table border="1" cellpadding="3">'
        f"<tr><th>Suffix</th><th>Type</th><th>Nickname</th><th>Balance</th></tr>{subs}</table>"
    )
    action = f'<p><a href="/subacct/new?id={mid}">Open Sub-Account</a></p>'
    return page("Member Detail", field_rows(rows) + subs_table + action)


def subacct_form(mid: str, msg: str = "", vals: dict | None = None) -> str:
    vals = vals or {}
    note = f'<p><font color="red">{html.escape(msg)}</font></p>' if msg else ""
    options = "".join(
        f'<option{" selected" if t == vals.get("type") else ""}>{t}</option>' for t in ["", *ACCOUNT_TYPES]
    )
    form = field_rows([
        ("Account Type:", f'<select name="type">{options}</select>'),
        ("Nickname:", f'<input type="text" name="nick" value="{html.escape(vals.get("nick", ""))}">'),
        ("Opening Deposit:", f'<input type="text" name="deposit" value="{html.escape(vals.get("deposit", ""))}">'),
        ("", '<input type="submit" value="Continue">'),
    ])
    return page(
        "Open Sub-Account",
        f'{note}<form method="post" action="/subacct/review"><input type="hidden" name="id" value="{mid}">{form}</form>',
    )


def review_page(mid: str, vals: dict) -> str:
    rows = field_rows([
        ("Member No:", mid),
        ("Account Type:", vals["type"]),
        ("Nickname:", html.escape(vals["nick"])),
        ("Opening Deposit:", money(Decimal(vals["deposit"]))),
    ])
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{html.escape(v)}">' for k, v in {"id": mid, **vals}.items()
    )
    return page(
        "Review Sub-Account",
        f"<p>Review the details below. This will move funds from share savings.</p>{rows}"
        f'<form method="post" action="/subacct/confirm">{hidden}'
        '<input type="submit" value="Confirm and Open Account"></form>'
        f'<p><a href="/member?id={mid}">Cancel</a></p>',
    )


def validate_subacct(m: dict, vals: dict) -> str | None:
    if vals["type"] not in ACCOUNT_TYPES:
        return "Account type is required."
    try:
        deposit = Decimal(vals["deposit"])
    except InvalidOperation:
        return "Opening deposit must be a dollar amount."
    if deposit < 5:
        return "Opening deposit must be at least $5.00."
    if deposit > m["savings"]:
        return "Insufficient funds in share savings for this opening deposit."
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep test output quiet
        pass

    # -- plumbing ---------------------------------------------------------
    def send_html(self, body: str, status: int = 200, headers: dict | None = None) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, to: str, headers: dict | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", to)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def logged_in(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return "sid" in cookie and cookie["sid"].value in STATE.sessions

    def take_fault(self, name: str) -> int:
        with STATE.lock:
            return STATE.faults.pop(name, 0)

    def form(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        return {k: v[0].strip() for k, v in parse_qs(raw, keep_blank_values=True).items()}

    # -- routes -----------------------------------------------------------
    def do_GET(self) -> None:
        url = urlsplit(self.path)
        q = {k: v[0].strip() for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        path = url.path

        if path == "/__fault":
            with STATE.lock:
                STATE.faults.update({k: int(v) for k, v in q.items()})
            return self.send_html("ok")
        if path == "/__reset":
            with STATE.lock:
                STATE.reset()
            return self.send_html("ok")
        if path == "/login":
            return self.send_html(login_page("Your session has expired." if "expired" in q else ""))
        if path == "/logout":
            STATE.sessions.clear()
            return self.redirect("/login")

        if path not in ("/", "/banner", "/nav", "/home") and self.take_fault("expire"):
            STATE.sessions.clear()
        if not self.logged_in():
            if path == "/":
                return self.redirect("/login")
            # Legacy idiom: a frame that lost its session breaks out to the top.
            return self.send_html('<html><body><script>top.location.href="/login?expired=1"</script></body></html>')

        if path == "/":
            return self.send_html(
                f'<html><head><title>{LABELS["brand"]}</title></head>'
                '<frameset rows="56,*" border="0"><frame name="banner" src="/banner" scrolling="no" noresize>'
                '<frameset cols="224,*" border="0"><frame name="nav" src="/nav"><frame name="main" src="/home"></frameset>'
                "</frameset></html>"
            )
        if path == "/banner":
            return self.send_html(chrome_page(
                f'<table width="100%"><tr><td><b>{LABELS["brand"]}</b></td>'
                f'<td align="right">HarborCore Teller 4.2 &nbsp;&middot;&nbsp; Operator {OPERATOR[0]}</td></tr></table>',
                "html, body { height: 100%; } body { padding: 0; background: #fff; border-bottom: 1px solid #e2e7ed; }"
                " body > table { height: 100%; background: none; border: 0; border-radius: 0; box-shadow: none; }"
                " body > table > tbody > tr > td { padding: 0 24px; vertical-align: middle; color: #5b6878; font-size: 13px; }"
                " b { color: #16202c; font-size: 15px; font-weight: 650; }"
                f" b::before {{ content: ''; display: inline-block; width: 22px; height: 22px; margin-right: 10px;"
                f" border-radius: 6px; background: {LABELS['accent']}; vertical-align: -5px; }}",
            ))
        if path == "/nav":
            links = [("Member Lookup", "/lookup"), ("Reports", "/reports"), ("Admin Console", "/admin"),
                     ("Vendor Support", "http://support.example.invalid/")]
            body = "".join(f'<tr><td><a href="{h}" target="main">{t}</a></td></tr>' for t, h in links)
            body += '<tr><td><a href="/logout" target="_top">Sign Off</a></td></tr>'
            return self.send_html(chrome_page(
                f"<table>{body}</table>",
                "html, body { height: 100%; } body { padding: 16px 12px; background: #fff; border-right: 1px solid #e2e7ed; }"
                " body > table { width: 100%; background: none; border: 0; border-radius: 0; box-shadow: none; }"
                " body > table > tbody > tr > td { padding: 0; }"
                " a { display: block; padding: 9px 12px; margin-bottom: 2px; border-radius: 8px; color: #2a3644; }"
                " a:hover { background: #f1f4f8; text-decoration: none; }",
            ))

        if path == "/home":
            return self.send_html(page("Home", "<p>Select a function from the menu.</p>"))

        # Function pages render in the main frame and are subject to faults.
        if delay := self.take_fault("slow"):
            time.sleep(delay / 1000)
        if self.take_fault("error"):
            return self.send_html(page("Application Error", "<p>HC-5001: An unexpected error occurred. Contact support.</p>"), 500)
        if self.take_fault("notice"):
            return self.send_html(page(
                "System Notice",
                "<p>Nightly processing begins at 9:00 PM. Posting will be unavailable.</p>"
                f'<form method="get" action="{html.escape(self.path)}"><input type="submit" value="Acknowledge"></form>',
            ))

        if path == "/lookup":
            return self.send_html(lookup_page())
        if path == "/reports":
            return self.send_html(page("Reports", "<p>No reports are scheduled.</p>"))
        if path == "/admin":
            return self.send_html(page("Admin Console", '<form><input type="submit" value="Purge Audit Log"></form>'))
        if path == "/search":
            mid = q.get("q", "")
            if not re.fullmatch(r"\d{5}", mid):
                return self.send_html(lookup_page("Member number must be 5 digits."))
            m = STATE.members.get(mid)
            if m is None:
                return self.send_html(lookup_page(f"No member on file for number {mid}."))
            if m.get("restricted"):
                return self.send_html(page("Access Restricted", "<p>You are not authorized to view this member record.</p>"))
            return self.send_html(results_page(mid, m))
        if path in ("/member", "/subacct/new"):
            mid = q.get("id", "")
            m = STATE.members.get(mid)
            if m is None or m.get("restricted"):
                return self.send_html(page("Access Restricted", "<p>You are not authorized to view this member record.</p>"))
            return self.send_html(member_page(mid, m) if path == "/member" else subacct_form(mid))
        self.send_html(page("Not Found", "<p>Unknown function.</p>"), 404)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        f = self.form()
        if path == "/login":
            if (f.get("u"), f.get("p")) != OPERATOR:
                return self.send_html(login_page("Invalid operator ID or password."))
            sid = secrets.token_hex(8)
            STATE.sessions.add(sid)
            return self.redirect("/", {"Set-Cookie": f"sid={sid}; Path=/; HttpOnly"})

        if self.take_fault("expire") or (path == "/subacct/confirm" and self.take_fault("expire_confirm")):
            STATE.sessions.clear()
        if not self.logged_in():
            return self.send_html('<html><body><script>top.location.href="/login?expired=1"</script></body></html>')

        mid = f.get("id", "")
        m = STATE.members.get(mid)
        vals = {k: f.get(k, "") for k in ("type", "nick", "deposit")}
        vals["deposit"] = vals["deposit"].replace("$", "").replace(",", "")
        if m is None or path not in ("/subacct/review", "/subacct/confirm"):
            return self.send_html(page("Not Found", "<p>Unknown function.</p>"), 404)
        if err := validate_subacct(m, vals):
            return self.send_html(subacct_form(mid, err, vals))
        if path == "/subacct/review":
            return self.send_html(review_page(mid, vals))

        with STATE.lock:
            deposit = Decimal(vals["deposit"])
            m["savings"] -= deposit
            suffix = f"S{len(m['subs']) + 2:02d}"
            m["subs"].append({"suffix": suffix, "type": vals["type"], "nick": vals["nick"], "balance": deposit})
            STATE.confirmations += 1
            conf = f"SA-{STATE.confirmations:06d}"
        if self.take_fault("lost"):
            return self.send_html(page("Application Error", "<p>HC-5001: An unexpected error occurred. Contact support.</p>"), 500)
        self.send_html(page(
            "Sub-Account Opened",
            field_rows([("Confirmation Number:", conf), ("New Account Suffix:", suffix),
                        ("Opening Deposit:", money(deposit))])
            + f'<p><a href="/member?id={mid}">Return to Member</a></p>',
        ))


def serve(port: int, variant: str = "harbor") -> ThreadingHTTPServer:
    global LABELS
    LABELS = VARIANTS[variant]
    STATE.reset()
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="harbor")
    args = ap.parse_args()
    server = serve(args.port, args.variant)
    print(f"HarborCore mock ({args.variant}) on http://127.0.0.1:{args.port}  login: {OPERATOR[0]} / {OPERATOR[1]}")
    server.serve_forever()


if __name__ == "__main__":
    main()
