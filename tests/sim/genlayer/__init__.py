"""A strict, minimal stand-in for the GenLayer Python SDK (offline test double).

It implements only the SDK surface used by contracts/kept_word.py and is deliberately
stricter than the real thing wherever strictness finds bugs:

* storage fields typed ``u256`` accept only ``u256`` values (a plain ``int`` is an error)
  and ``u256`` itself rejects negative / overflowing numbers,
* TreeMap / DynArray check key and element types,
* a dataclass put into storage is COPIED; only the object returned by ``get`` /
  ``[]`` is live (so code that mutates a stale local object is caught),
* storage cannot be read or written inside a non-deterministic block; the violation is
  a BaseException so a broad ``except Exception`` cannot hide it,
* web / LLM access outside a non-deterministic block is a violation too,
* ``Address.as_hex`` is EIP-55 mixed case, so a forgotten ``.lower()`` breaks keys,
* values returned from a non-deterministic leader must be plain calldata types.
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
import inspect
import types
import typing

__all__ = ["gl", "u256", "Address", "TreeMap", "DynArray", "allow_storage", "Keccak256"]


class SimViolation(BaseException):
    """The contract did something the real GenVM would not allow."""


class ConsensusFailure(Exception):
    """Validators did not reach agreement on the leader's result."""


class _State:
    def __init__(self) -> None:
        self.in_nondet = False
        self.node = None
        self.nodes: list = []
        self.events: list = []
        self.nondet_calls = 0


_state = _State()


def _guard_storage(what: str) -> None:
    if _state.in_nondet:
        raise SimViolation(f"storage access inside a non-deterministic block: {what}")


# ----------------------------------------------------------------------
# keccak-256 (pure python, original Keccak padding as used by Ethereum)
# ----------------------------------------------------------------------
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [
    [0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56], [27, 20, 39, 8, 14],
]
_M64 = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _M64 if n else x


def _keccak_f(a: list) -> None:
    for rnd in range(24):
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        a[0][0] ^= _RC[rnd]


def keccak256(data: bytes) -> bytes:
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate:
        padded.append(0)
    padded[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(padded), rate):
        block = padded[off : off + rate]
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(block[8 * i : 8 * i + 8], "little")
        _keccak_f(a)
    out = b""
    for i in range(4):
        out += a[i % 5][i // 5].to_bytes(8, "little")
    return out


class Keccak256:
    def __init__(self) -> None:
        self._buf = b""

    def update(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("Keccak256.update needs bytes")
        self._buf += bytes(data)

    def hexdigest(self) -> str:
        return keccak256(self._buf).hex()

    def digest(self) -> bytes:
        return keccak256(self._buf)


# ----------------------------------------------------------------------
# scalar types
# ----------------------------------------------------------------------
class u256(int):
    def __new__(cls, value: int = 0):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"u256 needs an int, got {type(value).__name__}")
        if value < 0 or value >= 1 << 256:
            raise OverflowError(f"{value} does not fit into u256")
        return int.__new__(cls, int(value))

    def __repr__(self) -> str:
        return f"u256({int(self)})"


class Address:
    __slots__ = ("_b",)

    def __init__(self, value) -> None:
        if isinstance(value, Address):
            raw = value._b
        elif isinstance(value, (bytes, bytearray)):
            raw = bytes(value)
        elif isinstance(value, str):
            text = value[2:] if value[:2] in ("0x", "0X") else None
            if text is None or len(text) != 40:
                raise ValueError("bad address")
            raw = bytes.fromhex(text)
        else:
            raise TypeError("bad address type")
        if len(raw) != 20:
            raise ValueError("address must be 20 bytes")
        object.__setattr__(self, "_b", raw)

    def __setattr__(self, name, value):
        raise AttributeError("Address is immutable")

    @property
    def as_bytes(self) -> bytes:
        return self._b

    @property
    def as_hex(self) -> str:
        """EIP-55 checksummed (mixed case) like the real SDK."""
        lower = self._b.hex()
        digest = keccak256(lower.encode()).hex()
        return "0x" + "".join(c.upper() if c.isalpha() and int(digest[i], 16) >= 8 else c for i, c in enumerate(lower))

    def __eq__(self, other) -> bool:
        return isinstance(other, Address) and other._b == self._b

    def __hash__(self) -> int:
        return hash(self._b)

    def __lt__(self, other) -> bool:
        return self._b < other._b

    def __repr__(self) -> str:
        return f"Address({self.as_hex})"

    def __deepcopy__(self, memo):
        return self


# ----------------------------------------------------------------------
# storage containers
# ----------------------------------------------------------------------
_SCALARS = (u256, str, bool, Address)


def _check_type(expected, value, where: str) -> None:
    if expected is u256:
        ok = type(value) is u256
    elif expected is bool:
        ok = type(value) is bool
    elif expected is str:
        ok = isinstance(value, str)
    elif expected is Address:
        ok = isinstance(value, Address)
    elif isinstance(expected, type) and dataclasses.is_dataclass(expected):
        ok = isinstance(value, expected)
    else:
        ok = True
    if not ok:
        raise TypeError(f"{where}: expected {getattr(expected, '__name__', expected)}, got {type(value).__name__} ({value!r})")


def _store_value(expected, value, where: str):
    _check_type(expected, value, where)
    if isinstance(expected, type) and dataclasses.is_dataclass(expected):
        stored = copy.deepcopy(value)
        object.__setattr__(stored, "_stored", True)
        return stored
    return value


class _Generic:
    _arity = 1

    def __class_getitem__(cls, params):
        if not isinstance(params, tuple):
            params = (params,)
        return types.GenericAlias(cls, params)


class DynArray(_Generic):
    def __init__(self, elem_type=None) -> None:
        self._t = elem_type
        self._items: list = []

    def append(self, value) -> None:
        _guard_storage("DynArray.append")
        self._items.append(_store_value(self._t, value, "DynArray.append"))

    def __len__(self) -> int:
        _guard_storage("DynArray.len")
        return len(self._items)

    def __getitem__(self, index):
        _guard_storage("DynArray.get")
        if not isinstance(index, int):
            raise TypeError("DynArray index must be an int")
        return self._items[index]

    def __setitem__(self, index, value) -> None:
        _guard_storage("DynArray.set")
        self._items[index] = _store_value(self._t, value, "DynArray.set")

    def __iter__(self):
        _guard_storage("DynArray.iter")
        return iter(list(self._items))


class TreeMap(_Generic):
    def __init__(self, key_type=None, value_type=None) -> None:
        self._k = key_type
        self._v = value_type
        self._d: dict = {}

    def _key(self, key, where: str):
        _check_type(self._k, key, f"TreeMap key ({where})")
        return key

    def get(self, key, default=None):
        _guard_storage("TreeMap.get")
        return self._d.get(self._key(key, "get"), default)

    def __getitem__(self, key):
        _guard_storage("TreeMap.getitem")
        return self._d[self._key(key, "getitem")]

    def __setitem__(self, key, value) -> None:
        _guard_storage("TreeMap.setitem")
        self._d[self._key(key, "setitem")] = _store_value(self._v, value, "TreeMap.setitem")

    def __contains__(self, key) -> bool:
        _guard_storage("TreeMap.contains")
        return self._key(key, "contains") in self._d

    def __len__(self) -> int:
        _guard_storage("TreeMap.len")
        return len(self._d)

    def get_or_insert_default(self, key):
        _guard_storage("TreeMap.get_or_insert_default")
        key = self._key(key, "get_or_insert_default")
        if key not in self._d:
            origin = typing.get_origin(self._v)
            if origin is DynArray:
                self._d[key] = DynArray(typing.get_args(self._v)[0])
            elif self._v is u256:
                self._d[key] = u256(0)
            elif self._v is str:
                self._d[key] = ""
            else:
                raise SimViolation(f"no default for {self._v}")
        return self._d[key]


def _make_default(annotation):
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is TreeMap:
        return TreeMap(args[0], args[1])
    if origin is DynArray:
        return DynArray(args[0])
    if annotation is u256:
        return u256(0)
    if annotation is str:
        return ""
    if annotation is bool:
        return False
    raise SimViolation(f"unsupported contract field type {annotation}")


def allow_storage(cls):
    hints = typing.get_type_hints(cls)
    fields = set(hints)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        if name not in fields:
            raise AttributeError(f"{cls.__name__} has no storage field {name}")
        if self.__dict__.get("_stored"):
            _guard_storage(f"{cls.__name__}.{name} write")
        _check_type(hints[name], value, f"{cls.__name__}.{name}")
        object.__setattr__(self, name, value)

    def __getattribute__(self, name):
        if name in fields and object.__getattribute__(self, "__dict__").get("_stored"):
            _guard_storage(f"{cls.__name__}.{name} read")
        return object.__getattribute__(self, name)

    cls.__setattr__ = __setattr__
    cls.__getattribute__ = __getattribute__
    return cls


# ----------------------------------------------------------------------
# events and contract base
# ----------------------------------------------------------------------
class _Event:
    def __new__(cls, *args, **kwargs):
        obj = object.__new__(cls)
        object.__setattr__(obj, "_args", args)
        object.__setattr__(obj, "_kwargs", kwargs)
        return obj

    def emit(self) -> None:
        if _state.in_nondet:
            raise SimViolation("event emitted inside a non-deterministic block")
        hints = typing.get_type_hints(type(self).__init__)
        names = [p for p in inspect.signature(type(self).__init__).parameters if p not in ("self", "blob")]
        for name, value in zip(names, self._args):
            if hints.get(name) is u256 and type(value) is not u256:
                raise TypeError(f"event {type(self).__name__}.{name} needs u256, got {type(value).__name__}")
        if len(self._args) != len(names):
            raise TypeError(f"event {type(self).__name__}: expected {len(names)} positional args")
        for key, value in self._kwargs.items():
            if not isinstance(value, (int, str, bool)):
                raise TypeError(f"event blob {key} must be int/str/bool")
        _state.events.append((type(self).__name__, tuple(self._args), dict(self._kwargs)))


class _Contract:
    _gl_hints: dict = {}

    def __new__(cls, *args, **kwargs):
        obj = object.__new__(cls)
        hints = {k: v for k, v in typing.get_type_hints(cls).items() if not k.startswith("_")}
        object.__setattr__(obj, "_gl_hints", hints)
        for name, annotation in hints.items():
            object.__setattr__(obj, name, _make_default(annotation))
        return obj

    def __setattr__(self, name, value):
        hints = object.__getattribute__(self, "_gl_hints")
        if name in hints:
            _guard_storage(f"contract field {name} write")
            annotation = hints[name]
            if typing.get_origin(annotation) in (TreeMap, DynArray):
                raise SimViolation(f"cannot reassign container field {name}")
            _check_type(annotation, value, f"contract.{name}")
        object.__setattr__(self, name, value)

    def __getattribute__(self, name):
        hints = object.__getattribute__(self, "_gl_hints")
        if name in hints:
            _guard_storage(f"contract field {name} read")
        return object.__getattribute__(self, name)


# ----------------------------------------------------------------------
# gl namespace
# ----------------------------------------------------------------------
class _UserError(Exception):
    pass


class _Return:
    def __init__(self, calldata) -> None:
        self.calldata = calldata


def _check_calldata(value, path: str = "result") -> None:
    if value is None or isinstance(value, (bool, int, str, bytes, Address)):
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_calldata(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, item in value.items():
            if not isinstance(k, str):
                raise SimViolation(f"{path}: calldata dict keys must be str")
            _check_calldata(item, f"{path}.{k}")
        return
    raise SimViolation(f"{path}: {type(value).__name__} is not calldata")


def _run_nondet_unsafe(leader_fn, validator_fn):
    if _state.in_nondet:
        raise SimViolation("nested non-deterministic block")
    nodes = _state.nodes
    if not nodes:
        raise SimViolation("no nodes configured")
    _state.nondet_calls += 1
    _state.in_nondet = True
    try:
        _state.node = nodes[0]
        try:
            raw = leader_fn()
            _check_calldata(raw)
            leaders_res = _Return(copy.deepcopy(raw))
            leader_error = None
        except (_UserError, Exception) as exc:  # a leader crash is data for the validators
            leaders_res = exc
            leader_error = exc
        agree = 1
        for node in nodes[1:]:
            _state.node = node
            verdict = validator_fn(leaders_res)
            if not isinstance(verdict, bool):
                raise SimViolation(f"validator returned {type(verdict).__name__}, not bool")
            if verdict:
                agree += 1
    finally:
        _state.in_nondet = False
        _state.node = None
    if agree * 2 <= len(nodes):
        raise ConsensusFailure(f"{agree}/{len(nodes)} nodes agreed")
    if leader_error is not None:
        raise leader_error
    return copy.deepcopy(leaders_res.calldata)


def _render(url, mode="text"):
    if not _state.in_nondet:
        raise SimViolation("web access outside a non-deterministic block")
    if mode not in ("text", "html"):
        raise SimViolation(f"unknown render mode {mode}")
    return _state.node.web(url)


def _exec_prompt(prompt, response_format="text", **kwargs):
    if not _state.in_nondet:
        raise SimViolation("LLM access outside a non-deterministic block")
    if not isinstance(prompt, str):
        raise SimViolation("prompt must be a string")
    _state.node.prompts.append(prompt)
    out = _state.node.llm(prompt)
    if response_format == "json":
        if not isinstance(out, dict):
            raise ValueError("model did not return a JSON object")
        return copy.deepcopy(out)
    return str(out)


class _Public:
    @staticmethod
    def write(fn):
        fn._gl_kind = "write"
        return fn

    @staticmethod
    def view(fn):
        fn._gl_kind = "view"
        return fn


gl = types.SimpleNamespace(
    Contract=_Contract,
    Event=_Event,
    public=_Public,
    vm=types.SimpleNamespace(UserError=_UserError, Return=_Return, run_nondet_unsafe=_run_nondet_unsafe),
    nondet=types.SimpleNamespace(
        web=types.SimpleNamespace(render=_render), exec_prompt=_exec_prompt
    ),
    message=None,
    message_raw={},
)
