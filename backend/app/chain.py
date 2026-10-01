"""Chain access for the service and the dev-node scripts.

JSON-RPC goes through web3.py's HTTP provider; calldata, return values, events
and reverts are coded with eth_abi against the ABIs in `abi/` (the exported
interfaces) and `backend/abi/` (the Desk implementation and the two mocks,
which add `heldSeries`, `pushRound`, `mint` and the OpenZeppelin errors).

    chain = Chain("http://127.0.0.1:8647")
    desk = chain.at("desk", addr)
    cost, price = desk.call("quoteBuy", series, 10**6, 0, block=123)
    receipt = desk.send(key, "buy", series, amount, max_cost, 0, fee_to, to)

A revert (eth_call, eth_estimateGas or a mined tx with status 0) raises
`Revert` with the decoded error name and args, e.g. Revert("FeedStale",
{"updatedAt": 1790000000}).
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_utils import keccak, to_checksum_address
from web3 import Web3

ROOT = Path(__file__).resolve().parents[2]  # repo root
ABI_DIRS = (ROOT / "abi", ROOT / "backend" / "abi")

# contract kind -> ABI files merged in this order (first definition of a name wins)
KINDS: dict[str, tuple[str, ...]] = {
    "factory": ("ISeriesFactory",),
    "series": ("INoteSeries",),
    "token": ("ISeriesToken",),
    "quoter": ("INoteQuoter",),
    "desk": ("Desk", "IDeskCover", "IDeskQueue"),
    "recorder": ("IFixingsRecorder",),
    "pricer": ("ISurrogatePricer",),
    "feed": ("MockChainlinkFeed", "IAggregatorV3"),
    "usdg": ("MockUSDG",),
}

ERROR_STRING = bytes.fromhex("08c379a0")  # Error(string)
PANIC = bytes.fromhex("4e487b71")  # Panic(uint256)


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class Revert(Exception):
    """A decoded revert. `name` is the Solidity error name ("Unknown" if the
    selector is not in any ABI), `fields` its named arguments (not `args`,
    which BaseException owns)."""

    def __init__(self, name: str, fields: dict | None = None, data: str = "0x", inputs: list | None = None):
        super().__init__(f"{name}{json.dumps(fields or {}, default=str)}")
        self.name = name
        self.fields = fields or {}
        self.data = data
        self.inputs = inputs or []

    def args_json(self) -> dict:
        """The args with integers wider than 53 bits as decimal strings."""
        by_name = {p["name"]: p for p in self.inputs}
        return {k: json_value(v, by_name[k]) if k in by_name else jsonable(v) for k, v in self.fields.items()}

    def to_json(self) -> dict:
        return {"error": self.name, "args": self.args_json()}


def jsonable(v: Any) -> Any:
    """uint amounts as decimal strings only where the caller asks; here: bytes -> hex, tuples -> lists."""
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def json_value(v: Any, p: dict) -> Any:
    """A decoded ABI value for JSON: integers wider than 53 bits (JavaScript's exact
    range) as decimal strings, so uint256 amounts, uint96 prices and uint80 round ids
    survive the frontend; times, bps and counts stay numbers."""
    t = p["type"]
    if t.startswith("tuple"):
        if t.endswith("[]"):
            return [json_value(x, dict(p, type=t[:-2])) for x in v]
        return {c["name"]: json_value(v[c["name"]], c) for c in p["components"]}
    if t.endswith("]"):
        return [json_value(x, dict(p, type=t[:t.rindex("[")])) for x in v]
    m = re.fullmatch(r"u?int(\d*)", t)
    if m and int(m.group(1) or 256) > 53:
        return str(v)
    return jsonable(v)


# ---------------------------------------------------------------------------
# ABI coding
# ---------------------------------------------------------------------------

def canonical_type(p: dict) -> str:
    t = p["type"]
    if t.startswith("tuple"):
        return "(" + ",".join(canonical_type(c) for c in p["components"]) + ")" + t[len("tuple"):]
    return t


def _to_py(v: Any, p: dict) -> Any:
    """eth_abi output -> python: structs as dicts by component name, bytesN as 0x hex."""
    t = p["type"]
    if t.startswith("tuple"):
        if t.endswith("[]"):
            inner = dict(p, type=t[:-2])
            return [_to_py(x, inner) for x in v]
        return {c["name"]: _to_py(x, c) for c, x in zip(p["components"], v)}
    if t.endswith("[]"):
        inner = dict(p, type=t[:-2])
        return [_to_py(x, inner) for x in v]
    if t.startswith("bytes"):
        return "0x" + bytes(v).hex()
    if t == "address":
        return v.lower()
    return v


def _from_py(v: Any, p: dict) -> Any:
    """python -> eth_abi input: dicts for structs, hex strings for bytes, any-case addresses."""
    t = p["type"]
    if t.startswith("tuple"):
        if t.endswith("[]"):
            inner = dict(p, type=t[:-2])
            return [_from_py(x, inner) for x in v]
        if isinstance(v, dict):
            return tuple(_from_py(v[c["name"]], c) for c in p["components"])
        return tuple(_from_py(x, c) for x, c in zip(v, p["components"]))
    if t.endswith("[]"):
        inner = dict(p, type=t[:-2])
        return [_from_py(x, inner) for x in v]
    if t.startswith("bytes") and isinstance(v, str):
        return bytes.fromhex(v[2:] if v.startswith("0x") else v)
    if t == "address":
        return to_checksum_address(v)
    return v


@dataclass
class Function:
    name: str
    inputs: list[dict]
    outputs: list[dict]

    @property
    def signature(self) -> str:
        return f"{self.name}({','.join(canonical_type(p) for p in self.inputs)})"

    @property
    def selector(self) -> bytes:
        return keccak(text=self.signature)[:4]

    def encode(self, *args) -> str:
        if len(args) != len(self.inputs):
            raise TypeError(f"{self.signature} takes {len(self.inputs)} args, got {len(args)}")
        types = [canonical_type(p) for p in self.inputs]
        vals = [_from_py(a, p) for a, p in zip(args, self.inputs)]
        return "0x" + (self.selector + abi_encode(types, vals)).hex()

    def decode(self, data: str | bytes) -> Any:
        raw = bytes.fromhex(data[2:]) if isinstance(data, str) else data
        types = [canonical_type(p) for p in self.outputs]
        vals = abi_decode(types, raw)
        out = [_to_py(v, p) for v, p in zip(vals, self.outputs)]
        if len(out) == 1:
            return out[0]
        return tuple(out)


@dataclass
class Event:
    name: str
    inputs: list[dict]

    @property
    def signature(self) -> str:
        return f"{self.name}({','.join(canonical_type(p) for p in self.inputs)})"

    @property
    def topic0(self) -> str:
        return "0x" + keccak(text=self.signature).hex()

    def decode_json(self, log: dict) -> dict:
        """decode() with json_value applied per input."""
        args = self.decode(log)
        return {p["name"]: json_value(args[p["name"]], p) for p in self.inputs}

    def decode(self, log: dict) -> dict:
        topics = log["topics"][1:]
        indexed = [p for p in self.inputs if p.get("indexed")]
        plain = [p for p in self.inputs if not p.get("indexed")]
        out: dict[str, Any] = {}
        for p, t in zip(indexed, topics):
            (v,) = abi_decode([canonical_type(p)], bytes.fromhex(t[2:]))
            out[p["name"]] = _to_py(v, p)
        data = bytes.fromhex(log["data"][2:])
        vals = abi_decode([canonical_type(p) for p in plain], data)
        for p, v in zip(plain, vals):
            out[p["name"]] = _to_py(v, p)
        return out


@dataclass
class ErrorDef:
    name: str
    inputs: list[dict]

    @property
    def selector(self) -> bytes:
        return keccak(text=f"{self.name}({','.join(canonical_type(p) for p in self.inputs)})")[:4]

    def decode(self, data: bytes) -> dict:
        vals = abi_decode([canonical_type(p) for p in self.inputs], data)
        return {(p["name"] or f"arg{i}"): _to_py(v, p) for i, (p, v) in enumerate(zip(self.inputs, vals))}


@dataclass
class Interface:
    kind: str
    functions: dict[str, Function] = field(default_factory=dict)  # by name and by signature
    events: dict[str, Event] = field(default_factory=dict)

    def fn(self, name: str) -> Function:
        try:
            return self.functions[name]
        except KeyError:
            raise KeyError(f"{self.kind} has no function {name}") from None


class Registry:
    """Every ABI, by contract kind; errors and events by selector across all of them."""

    def __init__(self, dirs=ABI_DIRS):
        self.raw: dict[str, list] = {}
        for d in dirs:
            for f in sorted(Path(d).glob("*.json")):
                self.raw[f.stem] = json.loads(f.read_text())
        self.kinds: dict[str, Interface] = {}
        self.errors: dict[bytes, ErrorDef] = {}
        self.events_by_topic: dict[str, Event] = {}
        for kind, files in KINDS.items():
            iface = Interface(kind)
            for name in files:
                for item in self.raw.get(name, []):
                    if item["type"] == "function":
                        f = Function(item["name"], item["inputs"], item.get("outputs", []))
                        iface.functions.setdefault(f.signature, f)
                        iface.functions.setdefault(f.name, f)
                    elif item["type"] == "event":
                        iface.events.setdefault(item["name"], Event(item["name"], item["inputs"]))
            self.kinds[kind] = iface
        for items in self.raw.values():
            for item in items:
                if item["type"] == "error":
                    e = ErrorDef(item["name"], item["inputs"])
                    self.errors.setdefault(e.selector, e)

    def decode_revert(self, data: str | None) -> Revert:
        if not data or data == "0x":
            return Revert("Reverted", {}, data or "0x")
        raw = bytes.fromhex(data[2:])
        sel, body = raw[:4], raw[4:]
        try:
            if sel == ERROR_STRING:
                (msg,) = abi_decode(["string"], body)
                return Revert("Error", {"message": msg}, data)
            if sel == PANIC:
                (code,) = abi_decode(["uint256"], body)
                return Revert("Panic", {"code": code}, data)
            e = self.errors.get(sel)
            if e is not None:
                return Revert(e.name, e.decode(body), data, e.inputs)
        except Exception:  # malformed payload: report the selector
            pass
        return Revert("Unknown", {"selector": "0x" + sel.hex()}, data)


REGISTRY = Registry()


# ---------------------------------------------------------------------------
# RPC
# ---------------------------------------------------------------------------

def block_tag(block: int | str | None) -> str:
    if block is None:
        return "latest"
    if isinstance(block, int):
        return hex(block)
    return block


class Chain:
    def __init__(self, rpc_url: str, registry: Registry = REGISTRY, timeout: float = 30.0):
        self.rpc_url = rpc_url
        self.registry = registry
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}))
        self._send_lock = threading.Lock()
        self._chain_id: int | None = None

    # --- raw ---------------------------------------------------------------
    def rpc(self, method: str, params: list | None = None) -> Any:
        resp = self.w3.provider.make_request(method, params or [])
        if "error" in resp and resp["error"]:
            err = resp["error"]
            if isinstance(err, str):
                raise RpcError(-1, err)
            raise RpcError(err.get("code", -1), err.get("message", ""), err.get("data"))
        return resp.get("result")

    def batch(self, calls: list[tuple[str, list]]) -> list[Any]:
        """Several requests in one HTTP round trip; each result or an RpcError instance."""
        if not calls:
            return []
        resps = self.w3.provider.make_batch_request(calls)
        if isinstance(resps, dict):  # the whole batch failed
            err = resps.get("error") or {}
            raise RpcError(err.get("code", -1), err.get("message", str(resps)), err.get("data"))
        by_id = sorted(resps, key=lambda r: r.get("id", 0))
        out: list[Any] = []
        for r in by_id:
            if r.get("error"):
                e = r["error"]
                out.append(RpcError(e.get("code", -1), e.get("message", ""), e.get("data")))
            else:
                out.append(r.get("result"))
        return out

    def call_many(self, calls: list[tuple["Contract", str, tuple]], block: int | str | None = None) -> list[Any]:
        """Batched eth_calls at one block: each decoded result, or the Revert it raised."""
        fns, reqs = [], []
        for c, fn, args in calls:
            f = c.iface.fn(fn)
            fns.append(f)
            reqs.append(("eth_call", [{"to": c.address, "data": f.encode(*args)}, block_tag(block)]))
        results: list[Any] = []
        for i in range(0, len(reqs), 500):  # geth's default batch limit is 1000
            results += self.batch(reqs[i:i + 500])
        out: list[Any] = []
        for f, r in zip(fns, results):
            if isinstance(r, RpcError):
                e = self._revert_from(r)
                if not isinstance(e, Revert):
                    raise e
                out.append(e)
            else:
                out.append(f.decode(r))
        return out

    @property
    def chain_id(self) -> int:
        if self._chain_id is None:
            self._chain_id = int(self.rpc("eth_chainId"), 16)
        return self._chain_id

    def block_number(self) -> int:
        return int(self.rpc("eth_blockNumber"), 16)

    def block(self, n: int | str = "latest") -> dict:
        b = self.rpc("eth_getBlockByNumber", [block_tag(n), False])
        if b is None:
            raise RpcError(-1, f"no block {n}")
        return {"number": int(b["number"], 16), "hash": b["hash"], "time": int(b["timestamp"], 16),
                "parentHash": b["parentHash"]}

    def code(self, addr: str, block: int | str | None = None) -> str:
        return self.rpc("eth_getCode", [addr, block_tag(block)])

    def balance(self, addr: str, block: int | str | None = None) -> int:
        return int(self.rpc("eth_getBalance", [addr, block_tag(block)]), 16)

    def get_logs(self, from_block: int, to_block: int, topics: list | None = None,
                 address: str | list[str] | None = None) -> list[dict]:
        f: dict[str, Any] = {"fromBlock": hex(from_block), "toBlock": hex(to_block)}
        if topics is not None:
            f["topics"] = topics
        if address is not None:
            f["address"] = address
        return self.rpc("eth_getLogs", [f])

    # --- contracts -----------------------------------------------------------
    def at(self, kind: str, address: str) -> "Contract":
        return Contract(self, self.registry.kinds[kind], address.lower())

    def eth_call(self, to: str, data: str, block: int | str | None = None) -> str:
        try:
            return self.rpc("eth_call", [{"to": to, "data": data}, block_tag(block)])
        except RpcError as e:
            raise self._revert_from(e) from None

    def _revert_from(self, e: RpcError) -> Exception:
        if e.code == 3 or "revert" in (e.message or "").lower():
            data = e.data
            if isinstance(data, dict):  # some clients nest it
                data = data.get("data")
            return self.registry.decode_revert(data if isinstance(data, str) else None)
        return e

    # --- transactions ----------------------------------------------------------
    def send_tx(self, key: str, to: str | None, data: str = "0x", value: int = 0) -> dict:
        """Signs with `key`, sends, waits for the receipt; raises Revert on a
        failed estimate or a status-0 receipt. Sends from one process are serialized."""
        acct = Account.from_key(key)
        with self._send_lock:
            tx: dict[str, Any] = {"from": acct.address, "data": data, "value": hex(value)}
            if to is not None:
                tx["to"] = to
            try:
                est = int(self.rpc("eth_estimateGas", [tx, "latest"]), 16)
            except RpcError as e:
                raise self._revert_from(e) from None
            gas = est * 12 // 10 + 50_000
            base = int(self.rpc("eth_getBlockByNumber", ["latest", False])["baseFeePerGas"], 16)
            nonce = int(self.rpc("eth_getTransactionCount", [acct.address, "pending"]), 16)
            raw = {"type": 2, "chainId": self.chain_id, "nonce": nonce, "value": value, "data": data,
                   "gas": gas, "maxFeePerGas": base * 2 + 1, "maxPriorityFeePerGas": 0}
            if to is not None:
                raw["to"] = to_checksum_address(to)
            signed = acct.sign_transaction(raw)
            tx_hash = self.rpc("eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()])
            receipt = self.wait_receipt(tx_hash)
        if receipt["status"] != "0x1":
            # replay as a call at the parent block to get the revert data
            try:
                self.rpc("eth_call", [dict(tx, gas=hex(gas)), hex(int(receipt["blockNumber"], 16) - 1)])
            except RpcError as e:
                raise self._revert_from(e) from None
            raise Revert("TxFailed", {"txHash": tx_hash})
        return receipt

    def wait_receipt(self, tx_hash: str, timeout: float = 60.0) -> dict:
        t0 = time.monotonic()
        while True:
            r = self.rpc("eth_getTransactionReceipt", [tx_hash])
            if r is not None:
                return r
            if time.monotonic() - t0 > timeout:
                raise TimeoutError(f"no receipt for {tx_hash}")
            time.sleep(0.05)

    def deploy(self, key: str, bytecode: str, ctor_types: list[str] | None = None,
               ctor_args: list | None = None) -> str:
        data = bytecode if bytecode.startswith("0x") else "0x" + bytecode
        if ctor_types:
            data += abi_encode(ctor_types, ctor_args or []).hex()
        r = self.send_tx(key, None, data)
        return r["contractAddress"].lower()

    def transfer_eth(self, key: str, to: str, wei: int) -> dict:
        return self.send_tx(key, to, "0x", value=wei)  # Nitro gas includes L1 data: estimate, never 21000


@dataclass
class Contract:
    chain: Chain
    iface: Interface
    address: str

    def call(self, fn: str, *args, block: int | str | None = None) -> Any:
        f = self.iface.fn(fn)
        return f.decode(self.chain.eth_call(self.address, f.encode(*args), block))

    def send(self, key: str, fn: str, *args, value: int = 0) -> dict:
        return self.chain.send_tx(key, self.address, self.iface.fn(fn).encode(*args), value=value)

    def event(self, name: str) -> Event:
        return self.iface.events[name]


def address_of(key: str) -> str:
    return Account.from_key(key).address.lower()
