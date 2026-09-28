"""LIB-07：DataclassJSONEncoder 必須序列化 dataclass 本體（而非 encoder self）。"""
import json
from dataclasses import dataclass

from funlab.core import DataclassJSONEncoder


@dataclass
class Plain:
    x: int = 1
    label: str = "a"


def test_plain_dataclass_encodes_to_dict():
    assert json.loads(json.dumps(Plain(7), cls=DataclassJSONEncoder)) == {"x": 7, "label": "a"}


def test_nested_plain_dataclass():
    @dataclass
    class Outer:
        inner: Plain = None
    out = json.dumps({"o": Outer(Plain(2))}, cls=DataclassJSONEncoder)
    assert json.loads(out)["o"]["inner"] == {"x": 2, "label": "a"}
