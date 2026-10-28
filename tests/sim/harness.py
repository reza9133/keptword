"""Chain harness around the strict SDK shim: deploy, send transactions, read views.

Features that matter for debugging:
* every write runs on a deep copy of the state and is rolled back when it raises
  (``rollback=True``, like the real chain); with ``rollback=False`` a failed call
  keeps whatever it wrote, which proves every check runs BEFORE any mutation,
* every view is checked for purity (state before == state after),
* each node has its own web pages and its own "model", so divergent validators,
  forged leaders and flaky pages can be simulated.
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import importlib.util
import sys
import typing
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parent
if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))

import genlayer  # noqa: E402
from genlayer import Address, ConsensusFailure, DynArray, SimViolation, TreeMap, gl, u256  # noqa: E402,F401
from genlayer import _state  # noqa: E402

ROOT = SIM_DIR.parents[1]
CONTRACT_PATH = Path(__import__("os").environ.get("KW_CONTRACT") or ROOT / "contracts" / "kept_word.py")


class Reverted(Exception):
    """The contract raised gl.vm.UserError."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat().replace("+00:00", "Z")


class Node:
    """One validator: its own view of the web and its own model."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.pages: dict = {}  # url -> text | Exception
        self.llm: typing.Callable[[str], typing.Any] = lambda prompt: {"quotes": [], "note": "nothing"}
        self.prompts: list = []

    def web(self, url: str) -> str:
        if url not in self.pages:
            raise RuntimeError(f"404 {url}")
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


def snapshot(value, depth: int = 0):
    """Canonical plain-python picture of contract storage (for equality checks)."""
    if isinstance(value, TreeMap):
        return {("addr:" + k.as_hex if isinstance(k, Address) else k): snapshot(v, depth + 1) for k, v in value._d.items()}
    if isinstance(value, DynArray):
        return [snapshot(v, depth + 1) for v in value._items]
    if dataclasses.is_dataclass(value):
        return {f.name: snapshot(object.__getattribute__(value, f.name), depth + 1) for f in dataclasses.fields(value)}
    if isinstance(value, Address):
        return "addr:" + value.as_hex
    return value


def contract_snapshot(contract) -> dict:
    hints = object.__getattribute__(contract, "_gl_hints")
    return {name: snapshot(object.__getattribute__(contract, name)) for name in hints}


class Chain:
    def __init__(self, nodes: int = 3, rollback: bool = True, start: int = 1_800_000_000) -> None:
        self.rollback = rollback
        self.now = start
        self.nodes = [Node(f"node{i}") for i in range(nodes)]
        self.contract = None
        self.module = None
        self.sender: typing.Optional[Address] = None
        self.tx_count = 0
        self.check_view_purity = True

    # ---- deployment -------------------------------------------------
    def deploy(self, path: Path = CONTRACT_PATH, deployer: typing.Optional[Address] = None):
        spec = importlib.util.spec_from_file_location("kept_word_under_test", str(path))
        module = importlib.util.module_from_spec(spec)
        self._prepare("deploy", deployer or Address(bytes([1]) * 20))
        spec.loader.exec_module(module)
        self.module = module
        cls = next(
            v for v in vars(module).values()
            if isinstance(v, type) and issubclass(v, genlayer._Contract) and v is not genlayer._Contract
        )
        self.contract = cls()
        return self.contract

    # ---- plumbing ---------------------------------------------------
    def _prepare(self, label: str, sender: Address) -> None:
        gl.message = type("Msg", (), {"sender_address": sender})()
        gl.message_raw = {"datetime": iso(self.now)}
        _state.nodes = self.nodes
        _state.events = []
        _state.in_nondet = False

    def _coerce(self, fn, args: tuple) -> list:
        import inspect

        hints = typing.get_type_hints(fn)
        names = [p for p in inspect.signature(fn).parameters if p != "self"]
        if len(args) != len(names):
            raise TypeError(f"{fn.__name__} takes {len(names)} args, got {len(args)}")
        out = []
        for name, value in zip(names, args):
            if hints.get(name) is u256:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise TypeError(f"{fn.__name__}.{name} needs an int")
                value = u256(value)
            out.append(value)
        return out

    def _method(self, name: str, kind: str):
        fn = getattr(type(self.contract), name, None)
        if fn is None or getattr(fn, "_gl_kind", None) != kind:
            raise AttributeError(f"{name} is not a public {kind} method")
        return fn

    # ---- transactions -----------------------------------------------
    def send(self, sender: Address, name: str, *args):
        """A write transaction. Raises Reverted on UserError (state rolled back)."""
        fn = self._method(name, "write")
        coerced = self._coerce(fn, args)
        self._prepare(name, sender)
        self.tx_count += 1
        saved = copy.deepcopy(self.contract.__dict__) if self.rollback else None
        try:
            result = fn(self.contract, *coerced)
        except gl.vm.UserError as exc:
            if saved is not None:
                self.contract.__dict__.clear()
                self.contract.__dict__.update(saved)
            _state.events = []
            raise Reverted(str(exc)) from None
        except ConsensusFailure:
            if saved is not None:
                self.contract.__dict__.clear()
                self.contract.__dict__.update(saved)
            _state.events = []
            raise
        return result

    def view(self, name: str, *args):
        fn = self._method(name, "view")
        coerced = self._coerce(fn, args)
        self._prepare(name, Address(bytes([9]) * 20))
        before = contract_snapshot(self.contract) if self.check_view_purity else None
        try:
            result = fn(self.contract, *coerced)
        except gl.vm.UserError as exc:
            raise Reverted(str(exc)) from None
        after = contract_snapshot(self.contract) if self.check_view_purity else None
        if before != after:
            raise SimViolation(f"view {name} changed contract state")
        return result

    @property
    def events(self) -> list:
        return list(_state.events)

    def advance(self, seconds: int) -> None:
        self.now += seconds

    def warp_to(self, ts: int) -> None:
        self.now = ts

    def state(self) -> dict:
        return contract_snapshot(self.contract)
