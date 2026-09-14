"""资源判断不能用超限预算伪造就绪。"""

from pcdb.m6.e4_preflight import SCORER_CALL_BOUND, resource_decision


def test_resource_bound_and_fail_closed_budget() -> None:
    assert SCORER_CALL_BOUND == 19680
    assert resource_decision(1.0, 10.0)["status"] == "RESOURCE_STOP"
    assert resource_decision(0.5, 10.0)["status"] == "PASS"
    assert resource_decision(0.5, 21600.0)["status"] == "RESOURCE_STOP"
