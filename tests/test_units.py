import pytest

from cua.policy import PolicyGuard
from cua.redact import Redactor
from cua.schema import AppProfile, Capability, load, parse_value
from cua.surface import derive_target, parse_tree

SNAPSHOT = """
- table:
  - rowgroup:
    - 'row "Member Number: Search"':
      - 'cell "Member Number: Search"':
        - table:
          - rowgroup:
            - 'row "Member Number:"':
              - 'cell "Member Number:"'
              - cell:
                - textbox
            - 'row "Balance: $5.00"':
              - 'cell "Balance:"'
              - cell "$5.00"
            - row "Go Go":
              - cell:
                - button "Go"
              - cell:
                - button "Go"
- paragraph: No member on file.
"""


def node(root, role, index=0):
    return [n for n in root.walk() if n.role == role][index]


def test_unlabelled_field_is_anchored_to_its_row_caption():
    root = parse_tree(SNAPSHOT)
    target = derive_target(node(root, "textbox"), root, ["main"])
    primary, fallback = target.locators
    assert (primary.role, primary.row, primary.name, primary.nth, primary.frame) == ("textbox", "Member Number:", None, None, ["main"])
    assert (fallback.kind, fallback.text) == ("near_text", "Member Number:")


def test_value_cell_is_located_by_caption_not_by_its_data():
    root = parse_tree(SNAPSHOT)
    primary = derive_target(node(root, "cell", 4), root, []).locators[0]
    assert (primary.row, primary.nth, primary.name) == ("Balance:", 1, None)


def test_ambiguous_control_is_flagged_weak_and_text_is_a_target():
    root = parse_tree(SNAPSHOT)
    weak = derive_target(node(root, "button", 1), root, [])
    assert "weak" in weak.description and weak.locators[0].nth == 1
    text = derive_target(node(root, "text"), root, [])
    assert (text.locators[0].role, text.locators[0].name) == ("text", "No member on file.")


@pytest.mark.parametrize("kind, raw, parsed", [
    ("money", "$1,234.56", "1234.56"), ("money", "12", "12.00"), ("integer", "1,200", 1200), ("string", " SA-1 ", "SA-1"),
])
def test_parse_value(kind, raw, parsed):
    assert parse_value(kind, raw) == parsed


@pytest.mark.parametrize("kind, raw", [("money", "N/A"), ("money", "1.234"), ("integer", "12a"), ("string", "  ")])
def test_parse_value_rejects_rather_than_guesses(kind, raw):
    with pytest.raises(ValueError):
        parse_value(kind, raw)


def test_policy_allowlist_and_risk():
    guard = PolicyGuard(load(AppProfile, "apps/harborcore.yaml").policy, "http://127.0.0.1:8765")
    assert guard.url_allowed("http://127.0.0.1:8765/subacct/review")
    assert not guard.url_allowed("http://127.0.0.1:8765/admin")
    assert not guard.url_allowed("http://127.0.0.1:9999/lookup")
    assert not guard.url_allowed("http://support.example.invalid/")
    assert guard.classify("click", "button", "Confirm and Open Account") == "irreversible"
    assert guard.classify("click", "button", "Search") == "safe"
    assert guard.classify("type", "textbox", "Confirm") == "safe"


def test_redactor_scrubs_declared_values_and_known_shapes():
    r = Redactor()
    r.add("10042", "{{member_id}}")
    assert r.text("member 10042, SSN 123-45-6789, acct 000123456789") == "member {{member_id}}, SSN [SSN], acct [NUMBER]"
    assert r.deep({"a": ["10042"]}) == {"a": ["{{member_id}}"]}


def test_capability_rejects_dangling_references():
    with pytest.raises(ValueError, match="undefined targets"):
        Capability(capability="x", app={"product": "p"}, targets={}, steps=[], success="missing")
