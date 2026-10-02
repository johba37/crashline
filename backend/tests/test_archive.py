"""The archive fallback of app.chain: what the node refuses because it pruned a block's
state is asked of the archive RPC, and nothing else is. No node needed: both RPCs are fakes.
"""

from __future__ import annotations

import pytest

from app import chain as chainmod
from app.chain import Chain, RpcError

pytestmark = pytest.mark.offline

GONE = {"code": -32000, "message": "historical state 5a16..c433 is not available"}
TRIE = {"code": -32000, "message": "missing trie node dcec (path ) state 0xab is not available, not found"}
REVERT = {"code": 3, "message": "execution reverted", "data": "0x4b1b2814"}


class FakeRpc:
    """Answers each call with `answer(method, params)`: a result, or an error dict."""

    def __init__(self, answer):
        self.answer = answer
        self.requests: list[list[tuple[str, list]]] = []

    def _one(self, i, method, params):
        r = self.answer(method, params)
        if isinstance(r, Exception):
            raise r
        return {"id": i, "error": r} if isinstance(r, dict) and "code" in r else {"id": i, "result": r}

    def make_request(self, method, params):
        self.requests.append([(method, params)])
        return self._one(0, method, params)

    def make_batch_request(self, calls):
        self.requests.append(list(calls))
        return [self._one(i, m, p) for i, (m, p) in enumerate(calls)]


def chain_with(node, archive=None) -> Chain:
    c = Chain("http://node.invalid", archive_url="http://archive.invalid" if archive else None)
    c.w3.provider = node
    if archive:
        c.archive.w3.provider = archive
    return c


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(chainmod, "ARCHIVE_CALLS_PER_SEC", 10**6)
    monkeypatch.setattr(chainmod, "ARCHIVE_WAIT", 0.0)


def call(tag: str) -> tuple[str, list]:
    return ("eth_call", [{"to": "0x00", "data": tag}, "0x10"])


def test_batch_asks_the_archive_only_for_pruned_state():
    node = FakeRpc(lambda m, p: {"0xa": "0x01", "0xb": GONE, "0xc": REVERT, "0xd": TRIE}[p[0]["data"]])
    archive = FakeRpc(lambda m, p: "0xfrom-archive-" + p[0]["data"][2:])
    out = chain_with(node, archive).batch([call("0xa"), call("0xb"), call("0xc"), call("0xd")])
    assert out[0] == "0x01"
    assert out[1] == "0xfrom-archive-b" and out[3] == "0xfrom-archive-d"
    assert isinstance(out[2], RpcError) and out[2].code == 3  # a revert stays the node's answer
    assert archive.requests == [[call("0xb"), call("0xd")]]


def test_single_request_falls_back_and_other_errors_do_not():
    archive = FakeRpc(lambda m, p: "0x2a")
    c = chain_with(FakeRpc(lambda m, p: GONE if p[1] == "0x10" else REVERT), archive)
    assert c.rpc("eth_call", [{"to": "0x00", "data": "0xa"}, "0x10"]) == "0x2a"
    with pytest.raises(RpcError) as e:
        c.rpc("eth_call", [{"to": "0x00", "data": "0xa"}, "latest"])
    assert e.value.code == 3 and len(archive.requests) == 1


def test_without_an_archive_the_error_stays():
    out = chain_with(FakeRpc(lambda m, p: GONE)).batch([call("0xa")])
    assert isinstance(out[0], RpcError) and "historical state" in out[0].message


def test_archive_errors_come_back_as_they_are():
    out = chain_with(FakeRpc(lambda m, p: GONE), FakeRpc(lambda m, p: REVERT)).batch([call("0xa")])
    assert isinstance(out[0], RpcError) and out[0].code == 3


def test_archive_requests_are_small_batches():
    archive = FakeRpc(lambda m, p: "0x01")
    calls = [call(hex(i)) for i in range(45)]
    out = chain_with(FakeRpc(lambda m, p: GONE), archive).batch(calls)
    assert out == ["0x01"] * 45
    assert [len(r) for r in archive.requests] == [20, 20, 5]
    assert [c for r in archive.requests for c in r] == calls  # in order


def test_a_rate_limited_archive_is_asked_again():
    seen = {"n": 0}

    def limited(m, p):
        seen["n"] += 1
        return {"code": 429, "message": "compute units per second"} if seen["n"] <= 2 else "0x01"

    archive = FakeRpc(limited)
    assert chain_with(FakeRpc(lambda m, p: GONE), archive).batch([call("0xa")]) == ["0x01"]
    assert len(archive.requests) == 3


def test_an_archive_that_stays_rate_limited_raises():
    class Http429(Exception):
        class response:  # what requests' HTTPError carries
            status_code = 429

    c = chain_with(FakeRpc(lambda m, p: GONE), FakeRpc(lambda m, p: Http429()))
    with pytest.raises(RpcError) as e:
        c.batch([call("0xa")])
    assert e.value.code == 429 and len(c.archive.w3.provider.requests) == chainmod.ARCHIVE_TRIES
