"""The registrations of docs/research are immutable
(docs/forex-research-protocol.md, section 2): each one's SHA-256 is pinned
here, so an edit after the fact fails the suite. Results live in a separate
`<id>-results.md`, which is free to be written once.

The hash is taken with line endings normalized to LF, so that Windows and
macOS checkouts agree.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from investment_committee.simulation import ema_pullback

RESEARCH = Path(__file__).resolve().parents[2] / "docs" / "research"
RESULTS_SUFFIX = "-results.md"

# registration file -> SHA-256 of its text. Adding a line registers a hypothesis; changing one needs the user's approval.
REGISTERED = {
    "ema-pullback-forex-0.1.0.md": "56e2fedefdd1dc317ff203f89f8b305917b4b7ecd95b8aef2abfeb7f05f3e90a",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


@pytest.mark.parametrize("name", sorted(REGISTERED))
def test_a_registration_has_not_been_edited(name: str) -> None:
    assert _sha256(RESEARCH / name) == REGISTERED[name], f"{name} was modified after it was registered"


def test_every_file_of_the_folder_is_a_registration_or_its_results() -> None:
    allowed = set(REGISTERED) | {name[: -len(".md")] + RESULTS_SUFFIX for name in REGISTERED}
    assert {path.name for path in RESEARCH.iterdir()} <= allowed


def test_the_registration_of_c_names_the_parameters_the_code_has() -> None:
    text = (RESEARCH / "ema-pullback-forex-0.1.0.md").read_text(encoding="utf-8")
    assert ema_pullback.STRATEGY_ID in text and ema_pullback.fingerprint() in text


def test_the_script_of_the_registered_run_checks_the_same_registration() -> None:
    script = (RESEARCH.parents[1] / "scripts" / "backtest_ema_pullback_d2.py").read_text(encoding="utf-8")
    text = (RESEARCH / "ema-pullback-forex-0.1.0.md").read_text(encoding="utf-8")
    assert f'REGISTRATION_SHA256 = "{REGISTERED["ema-pullback-forex-0.1.0.md"]}"' in script
    assert f'REGISTERED_FINGERPRINT = "{ema_pullback.fingerprint()}"' in script
    dataset = script.split('REGISTERED_DATASET = "')[1].split('"')[0]
    code = script.split('REGISTERED_CODE = "')[1].split('"')[0]
    assert dataset in text and f"commit `{code}`" in text
