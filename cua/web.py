"""Playwright implementation of Surface. The only module that knows about browsers."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Frame, Locator, sync_playwright

from .handoff import Lease
from .policy import Approval, ApprovalRequired, PolicyGuard, PolicyViolation
from .schema import A11yLocator, NearTextLocator, Risk, Target, render
from .surface import (STRUCTURAL, ActionError, ControlError, Observation, ObsNode,
                      TargetError, derive_target, parse_tree, render_tree)

# Runs in every frame. Reports what a human did, never what they typed.
CAPTURE_JS = """
(() => {
  const send = (kind, e) => {
    const t = e.target;
    if (!t || !t.tagName || !window.__cua_event) return;
    const isButton = /submit|button/.test(t.type || '');
    const cell = t.closest ? t.closest('td') : null;
    const row = t.closest ? t.closest('tr') : null;
    const near = row && row.cells.length && row.cells[0] !== cell ? row.cells[0].innerText : '';
    window.__cua_event({
      kind, tag: t.tagName.toLowerCase(), frame: window.name, path: location.pathname,
      label: ((isButton ? t.value : t.innerText) || '').trim().slice(0, 60),
      near: near.trim().slice(0, 60),
      value_length: kind === 'change' ? String(t.value || '').length : undefined,
    });
  };
  document.addEventListener('click', e => send('click', e), true);
  document.addEventListener('change', e => send('change', e), true);
})();
"""

READ_JS = """el => el.tagName === 'SELECT' ? (el.selectedOptions[0] ? el.selectedOptions[0].text : '')
  : (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') ? el.value : el.innerText"""

_FIELD_XPATH = {
    "textbox": "self::input[not(@type) or @type='text' or @type='password'] or self::textarea",
    "combobox": "self::select",
}


def _quote(text: str) -> str | None:
    if '"' not in text:
        return f'"{text}"'
    return f"'{text}'" if "'" not in text else None


class WebSurface:
    def __init__(self, base_url: str, guard: PolicyGuard, headed: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.guard = guard
        self.lease = Lease()
        self.warnings: list[str] = []
        self.blocked: list[str] = []
        self._human_events: list[dict] = []
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=not headed)
        self._context = self._browser.new_context(viewport={"width": 1100, "height": 800})
        self._context.route("**/*", self._route)
        self._loading = 0  # document requests in flight, so "settled" means the next page has arrived
        self._context.on("request", lambda r: self._count(r, 1))
        self._context.on("requestfinished", lambda r: self._count(r, -1))
        self._context.on("requestfailed", lambda r: self._count(r, -1))
        self._context.expose_binding("__cua_event", self._on_event)
        self._context.add_init_script(CAPTURE_JS)
        self.page = self._context.new_page()

    # -- guardrails at the network edge --------------------------------------
    def _route(self, route) -> None:
        url = route.request.url
        if self.guard.url_allowed(url):
            route.continue_()
        else:
            self.blocked.append(url)
            route.abort()

    def _count(self, request, delta: int) -> None:
        if request.resource_type == "document":
            self._loading = max(0, self._loading + delta)

    def _on_event(self, source, event: dict) -> None:
        if self.lease.owner == "human":
            self._human_events.append({k: v for k, v in event.items() if v not in (None, "")})

    def drain_human_events(self) -> list[dict]:
        events, self._human_events = self._human_events, []
        return events

    # -- navigation and timing -----------------------------------------------
    def open(self, path: str) -> None:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        if not self.guard.url_allowed(url):
            raise PolicyViolation(f"navigation to {urlsplit(url).path} is not allowed")
        self.page.goto(url)
        self.settle()

    def settle(self) -> None:
        for _ in range(60):  # up to ~3s for in-flight page loads; the caller's checkpoint has the final say
            if not self._loading:
                break
            self.page.wait_for_timeout(50)
        for frame in self.page.frames:
            try:
                frame.wait_for_load_state("load", timeout=3000)
            except PlaywrightError:
                pass  # slow or detached frames are judged by the caller's checkpoint

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()

    # -- frames ------------------------------------------------------------
    def _frame(self, path: list[str]) -> Frame | None:
        frame = self.page.main_frame
        for name in path:
            frame = next((f for f in frame.child_frames if f.name == name and not f.is_detached()), None)
            if frame is None:
                return None
        return frame

    def _frames(self, frame: Frame | None = None, path: list[str] | None = None):
        frame, path = frame or self.page.main_frame, path or []
        yield path, frame
        for child in frame.child_frames:
            if child.is_detached():
                continue
            yield from self._frames(child, [*path, child.name])

    def _snapshot(self, frame: Frame) -> str:
        try:
            if frame.evaluate("document.body ? document.body.tagName : ''") != "BODY":
                return ""  # a frameset document has no content of its own
            return frame.locator("body").aria_snapshot(timeout=3000)
        except PlaywrightError:
            return ""

    # -- perception --------------------------------------------------------
    def observe(self) -> Observation:
        self.settle()
        nodes: dict[str, ObsNode] = {}
        lines = [f"URL path: {urlsplit(self.page.url).path}"]
        for path, frame in self._frames():
            snapshot = self._snapshot(frame)
            if not snapshot:
                continue
            root = parse_tree(snapshot)
            refs: dict[int, str] = {}
            for node in root.walk():
                if node.role in STRUCTURAL:
                    continue
                ref = f"e{len(nodes) + 1}"
                refs[id(node)] = ref
                nodes[ref] = ObsNode(ref, node.role, node.name, derive_target(node, root, path))
            lines.append(f"frame {'/'.join(path) or '(top)'}  path={urlsplit(frame.url).path}")
            lines.extend(render_tree(root, refs))
        return Observation(self.page.url, "\n".join(lines), nodes)

    def dump(self) -> str:
        self.settle()
        parts = []
        for path, frame in self._frames():
            parts.append(f"--- frame {'/'.join(path) or '(top)'} {urlsplit(frame.url).path}")
            parts.append(self._snapshot(frame) or "(no content)")
        return "\n".join(parts)

    # -- target resolution ---------------------------------------------------
    def _resolve(self, loc: A11yLocator | NearTextLocator, values: dict[str, str]) -> tuple[Locator | None, int]:
        frame = self._frame(loc.frame)
        if frame is None:
            return None, 0
        if isinstance(loc, NearTextLocator):
            q = _quote(render(loc.text, values))
            if q is None:
                return None, 0
            anchor = f"//*[text()[normalize-space()={q}]]"
            if loc.role in _FIELD_XPATH:
                found = frame.locator(f"xpath={anchor}/following::*[{_FIELD_XPATH[loc.role]}][1]")
            elif loc.role in ("cell", "gridcell"):
                found = frame.locator(f"xpath={anchor}/following::td[1]")
            else:
                found = frame.locator(
                    f"xpath=//input[@value={q}] | //button[normalize-space()={q}] | //a[normalize-space()={q}]")
            return found, found.count()

        scope: Frame | Locator = frame
        if loc.row is not None:
            caption = frame.get_by_role("cell", name=render(loc.row, values), exact=True)
            if (n := caption.count()) != 1:
                return None, n
            # Layout tables nest, so several rows contain the caption; the innermost is the last.
            scope = frame.get_by_role("row").filter(has=caption).last
        name = render(loc.name, values)
        if loc.role == "text":
            found = scope.get_by_text(name or "", exact=True)
        elif name:
            found = scope.get_by_role(loc.role, name=name, exact=True)
        else:
            found = scope.get_by_role(loc.role)
        count = found.count()
        if loc.nth is not None:
            return (found.nth(loc.nth), 1) if loc.nth < count else (None, 0)
        return found, count

    def _find(self, target: Target, values: dict[str, str], quiet: bool = False) -> tuple[Locator, A11yLocator | NearTextLocator]:
        """Resolve to exactly one control. Zero or several matches is an error,
        never "take the first": acting on the wrong control is the worst outcome."""
        problems, ambiguous = [], False
        for i, loc in enumerate(target.locators):
            try:
                found, count = self._resolve(loc, values)
            except PlaywrightError as e:
                found, count = None, 0
                problems.append(f"{loc.kind}: {str(e).splitlines()[0]}")
                continue
            if count == 1 and found is not None:
                if i > 0 and not quiet:
                    note = (f"drift: {target.description!r} matched by fallback locator "
                            f"#{i} ({loc.kind}); earlier: {'; '.join(problems)}")
                    if note not in self.warnings:
                        self.warnings.append(note)
                return found, loc
            problems.append(f"{loc.kind}: {count} matches")
            ambiguous = ambiguous or count > 1
        kind = "TARGET_AMBIGUOUS" if ambiguous else "TARGET_NOT_FOUND"
        raise TargetError(kind, f"{target.description}: {'; '.join(problems)}")

    def present(self, target: Target, values: dict[str, str]) -> bool:
        try:
            found, _ = self._find(target, values, quiet=True)
            return found.is_visible()
        except (TargetError, PlaywrightError):
            return False

    def read(self, target: Target, values: dict[str, str]) -> str:
        found, _ = self._find(target, values)
        return str(found.evaluate(READ_JS)).strip()

    # -- action: the single choke point for policy and control ----------------
    def act(self, action: str, target: Target, values: dict[str, str],
            value: str | None = None, approval: Approval | None = None) -> Risk:
        if self.lease.owner != "automation":
            raise ControlError("a human holds the session; automation may not act")
        self.guard.check_action(action)
        found, loc = self._find(target, values)
        frame = self._frame(loc.frame)
        if not self.guard.url_allowed(frame.url):
            raise PolicyViolation(f"page {urlsplit(frame.url).path} is outside the allowlist")
        label = render(getattr(loc, "name", None) or getattr(loc, "row", None) or getattr(loc, "text", None) or "", values)
        risk = self.guard.classify(action, loc.role, label)
        if risk == "irreversible" and approval is None:
            raise ApprovalRequired(f'{loc.role} "{label}" is irreversible and needs approval')
        try:
            if action == "click":
                href = found.get_attribute("href") if loc.role == "link" else None
                if href and not self.guard.url_allowed(urljoin(frame.url, href)):
                    raise PolicyViolation(f'link "{label}" leads outside the allowlist')
                self.blocked.clear()
                found.click(timeout=5000)
                self.page.wait_for_timeout(150)  # let a navigation the click triggered get under way
            elif action == "type":
                found.fill(value or "", timeout=5000)
            elif action == "select":
                found.select_option(label=value, timeout=5000)
            else:
                raise PolicyViolation(f"unsupported action {action!r}")
        except PlaywrightError as e:
            raise ActionError(str(e).splitlines()[0]) from e
        self.settle()
        if action == "click" and self.blocked:
            raise PolicyViolation(f"blocked request to {urlsplit(self.blocked[0]).netloc}{urlsplit(self.blocked[0]).path}")
        return risk

    def screenshot(self, path: str, mask: list[Target], values: dict[str, str]) -> None:
        masks = []
        for target in mask:
            try:
                masks.append(self._find(target, values, quiet=True)[0])
            except (TargetError, PlaywrightError):
                pass
        self.page.screenshot(path=path, mask=masks, full_page=True)
