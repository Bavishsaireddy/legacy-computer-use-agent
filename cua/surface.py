"""The seam between "how we perceive and act on an application" and "the recorded flow".

The agent, compiler and replay engine depend only on this module. A surface
turns Targets (role/name descriptions) into concrete controls; web.py does
that with Playwright, and a desktop surface would do it with UIA / AX.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

import yaml

from .handoff import Lease
from .policy import Approval
from .schema import A11yLocator, NearTextLocator, Risk, Target


class TargetError(Exception):
    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind  # TARGET_NOT_FOUND | TARGET_AMBIGUOUS


class ControlError(Exception):
    """Automation tried to act while a human holds the session."""


class ActionError(Exception):
    """The control was found but the action did not complete."""


@dataclass
class ObsNode:
    ref: str
    role: str
    name: str
    target: Target


@dataclass
class Observation:
    url: str
    text: str  # rendered tree shown to the model
    nodes: dict[str, ObsNode]


class Surface(Protocol):
    lease: Lease
    warnings: list[str]

    def open(self, path: str) -> None: ...
    def observe(self) -> Observation: ...
    def present(self, target: Target, values: dict[str, str]) -> bool: ...
    def read(self, target: Target, values: dict[str, str]) -> str: ...
    def act(self, action: str, target: Target, values: dict[str, str],
            value: str | None = None, approval: Approval | None = None) -> Risk: ...
    def settle(self) -> None: ...
    def wait(self, ms: int) -> None: ...
    def dump(self) -> str: ...
    def screenshot(self, path: str, mask: list[Target], values: dict[str, str]) -> None: ...
    def drain_human_events(self) -> list[dict]: ...
    def close(self) -> None: ...


# -- accessibility tree ----------------------------------------------------
# Pure functions over a parsed tree, so target derivation is testable without
# a browser and reusable by any surface that can produce role/name nodes.

CONTROL_ROLES = {"button", "link", "textbox", "combobox", "checkbox", "radio", "searchbox", "spinbutton"}
CELL_ROLES = {"cell", "rowheader", "columnheader", "gridcell"}
STRUCTURAL = {"table", "rowgroup", "row", "separator", "list", "group", "generic"}
_KEY = re.compile(r'^([a-z]+)(?:\s+"((?:[^"\\]|\\.)*)")?')


@dataclass(eq=False)
class Node:
    role: str
    name: str = ""
    value: str = ""
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = None

    def walk(self):
        for child in self.children:
            yield child
            yield from child.walk()


def parse_tree(snapshot: str) -> Node:
    """Parse Playwright's YAML accessibility snapshot into Nodes."""
    root = Node("root")

    def add(parent: Node, item) -> None:
        children = None
        if isinstance(item, dict):
            (key, children), = item.items()
        else:
            key = item
        key = str(key)
        if key.startswith("/"):  # element properties such as /url
            return
        m = _KEY.match(key)
        if not m:
            return
        node = Node(m.group(1), json.loads(f'"{m.group(2)}"') if m.group(2) else "", parent=parent)
        if isinstance(children, list):
            for child in children:
                add(node, child)
        elif children is not None:
            node.value = str(children)
        if not node.name and node.value and node.role not in CONTROL_ROLES:
            node.role, node.name = "text", node.value  # paragraph/text: the content is the identity
        parent.children.append(node)

    for item in yaml.safe_load(snapshot) or []:
        add(root, item)
    return root


def derive_target(node: Node, root: Node, frame: list[str]) -> Target:
    """Choose the most durable description of `node`.

    Preference: unique role+name for controls; otherwise the label cell of the
    enclosing table row (how legacy forms associate a caption with a field);
    position only as a flagged last resort.
    """
    everything = list(root.walk())

    def count(role: str, name: str) -> int:
        return sum(1 for n in everything if n.role == role and n.name == name)

    row = node.parent
    while row is not None and row.role != "row":
        row = row.parent
    label = ""
    if row is not None:
        for cell in row.children:
            if cell.role in CELL_ROLES and cell.name:
                # The first captioned cell labels the row, unless it is the node itself.
                inside = cell is node or any(n is node for n in cell.walk())
                label = "" if inside else cell.name
                break
        if label and sum(1 for n in everything if n.role in CELL_ROLES and n.name == label) != 1:
            label = ""

    named_unique = bool(node.name) and count(node.role, node.name) == 1
    weak = False
    if node.role == "text" and named_unique:
        primary = A11yLocator(frame=frame, role="text", name=node.name)
    elif node.role in CONTROL_ROLES and named_unique:
        primary = A11yLocator(frame=frame, role=node.role, name=node.name)
    elif label:
        peers = [n for n in row.walk() if n.role == node.role]
        nth = next(i for i, n in enumerate(peers) if n is node) if len(peers) > 1 else None
        primary = A11yLocator(frame=frame, role=node.role, row=label, nth=nth)
    elif named_unique:
        primary = A11yLocator(frame=frame, role=node.role, name=node.name)
    else:
        peers = [n for n in everything if n.role == node.role and n.name == node.name]
        nth = next(i for i, n in enumerate(peers) if n is node)
        primary = A11yLocator(frame=frame, role=node.role, name=node.name or None, nth=nth)
        weak = True

    locators: list = [primary]
    if primary.row and (primary.nth is None or (node.role in CELL_ROLES and primary.nth == 1)):
        locators.append(NearTextLocator(frame=frame, role=node.role, text=primary.row))
    elif primary.name and primary.nth is None and node.role in ("button", "link"):
        locators.append(NearTextLocator(frame=frame, role=node.role, text=primary.name))

    where = f'in row "{primary.row}"' if primary.row else f'"{primary.name or ""}"'
    description = f"{node.role} {where}" + (" (positional, weak)" if weak else "")
    return Target(description=description, locators=locators)


def render_tree(root: Node, refs: dict[int, str], indent: int = 1) -> list[str]:
    """Text shown to the model. Container names are omitted: they only repeat
    their children and would drown the controls."""
    lines = []
    for node in root.children:
        ref = refs.get(id(node))
        show_name = node.name and (not node.children or node.role in CONTROL_ROLES)
        label = f' "{node.name}"' if show_name else ""
        value = f" = {node.value!r}" if node.value and node.role in CONTROL_ROLES else ""
        lines.append(f'{"  " * indent}{f"[{ref}] " if ref else ""}{node.role}{label}{value}')
        lines.extend(render_tree(node, refs, indent + 1))
    return lines
