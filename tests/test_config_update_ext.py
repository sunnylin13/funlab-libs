"""LIB-09：update_with_ext 帶/不帶 section 都要可用。"""
from funlab.core.config import Config


def test_no_section_merges_top_level():
    c1 = Config({"A": 1})
    c2 = Config({"B": 2})
    c1.update_with_ext(c2)
    assert c1.get("A") == 1 and c1.get("B") == 2


def test_section_merge_overwrites_only_that_section():
    c1 = Config({"S": {"x": 1, "y": 2}, "T": {"z": 3}})
    c2 = Config({"S": {"y": 99}})
    c1.update_with_ext(c2, section="S")
    assert c1.get("S") == {"x": 1, "y": 99}
    assert c1.get("T") == {"z": 3}


def test_dict_ext_conf_accepted():
    c1 = Config({"A": 1})
    c1.update_with_ext({"B": 2})
    assert c1.get("B") == 2
