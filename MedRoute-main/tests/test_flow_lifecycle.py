import unittest

from flow_lifecycle import (
    COOKIE_NAMESPACE, COOKIE_NAMESPACE_MASK, build_rule_plan, make_cookie,
    path_contains_any_edge, reverse_transport_ports,
)


class FlowLifecycleTests(unittest.TestCase):
    def test_cookie_is_stable_versioned_and_directional(self):
        first = make_cookie("17|10.0.0.1:1->10.0.0.2:5001", 1, "forward")
        self.assertEqual(first & COOKIE_NAMESPACE_MASK, COOKIE_NAMESPACE)
        self.assertEqual(first, make_cookie(
            "17|10.0.0.1:1->10.0.0.2:5001", 1, "forward"
        ))
        self.assertNotEqual(first, make_cookie(
            "17|10.0.0.1:1->10.0.0.2:5001", 2, "forward"
        ))
        self.assertNotEqual(first, make_cookie(
            "17|10.0.0.1:1->10.0.0.2:5001", 1, "reverse"
        ))

    def test_plan_installs_destination_before_ingress(self):
        plan = build_rule_plan(
            (1, 2, 4), "10.0.0.1", "10.0.0.2", 17, 42000, 5001,
            destination_host_port=7, port_map={(1, 2): 2, (2, 4): 3},
        )
        self.assertEqual([rule.dpid for rule in plan], [4, 2, 1])
        self.assertEqual([rule.output_port for rule in plan], [7, 3, 2])
        self.assertEqual(plan[0].match_fields["udp_dst"], 5001)

    def test_reverse_ports_and_failed_edge_detection(self):
        self.assertEqual(reverse_transport_ports(42000, 5001), (5001, 42000))
        self.assertTrue(path_contains_any_edge((1, 2, 4), ((2, 4),)))
        self.assertFalse(path_contains_any_edge((1, 3, 4), ((2, 4),)))


if __name__ == "__main__":
    unittest.main()
