import unittest

from experiment_runner import parse_iperf_udp_report


class IperfUdpParserTests(unittest.TestCase):
    def assert_udp_result(self, result, source):
        self.assertEqual(result["throughput_bps"], 2_000_000.0)
        self.assertEqual(result["jitter_ms"], 0.132)
        self.assertEqual(result["packet_loss_pct"], 0.0)
        self.assertEqual(result["lost_packets"], 0)
        self.assertEqual(result["packets"], 2590)
        self.assertEqual(result["summary_source"], source)

    @staticmethod
    def udp_summary():
        return {
            "bits_per_second": 2_000_000,
            "jitter_ms": 0.132,
            "lost_packets": 0,
            "packets": 2590,
            "lost_percent": 0.0,
        }

    def test_parses_sum_received_when_it_is_a_complete_udp_summary(self):
        report = {"end": {"sum_received": self.udp_summary()}}
        result = parse_iperf_udp_report(report)
        self.assert_udp_result(result, "end.sum_received")

    def test_parses_iperf_3_9_end_sum_without_sum_received(self):
        report = {
            "end": {
                "streams": [{"udp": dict(self.udp_summary(), jitter_ms=9.9)}],
                "sum": self.udp_summary(),
            }
        }
        result = parse_iperf_udp_report(report)
        self.assert_udp_result(result, "end.sum")

    def test_falls_back_to_iperf_3_9_stream_udp_summary(self):
        report = {"end": {"streams": [{"udp": self.udp_summary()}]}}
        result = parse_iperf_udp_report(report)
        self.assert_udp_result(result, "end.streams[0].udp")


if __name__ == "__main__":
    unittest.main()
