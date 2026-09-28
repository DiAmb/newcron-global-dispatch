import io
import json
import queue
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest import mock

import carrier_dashboard
import shipper_dashboard
import shipper_submit
import validator_service
from newcron import topics
from newcron.validator import process_request


def solace_match(subscription: str, topic: str) -> bool:
    sub, top = subscription.split("/"), topic.split("/")
    for i, level in enumerate(sub):
        if level == ">":
            return len(top) > i
        if i >= len(top):
            return False
        if level != top[i]:
            return False
    return len(sub) == len(top)


class FakeMessage:
    def __init__(self, text):
        self._text = text

    def get_payload_as_string(self):
        return self._text


class FakeReceiver:
    def __init__(self):
        self.acked = []

    def ack(self, message):
        self.acked.append(message)


class FakePublisher:
    def __init__(self, fail_on=None):
        self.sent = []
        self.fail_on = fail_on

    def publish(self, topic, payload, message_id=None, timeout_ms=5000):
        if self.fail_on and self.fail_on in topic:
            raise RuntimeError("broker down")
        self.sent.append((topic, json.loads(payload)))


ORDER = {
    "shipperOrderId": "6600111",
    "pickupDate": "2026-09-28",
    "deliveryDate": "2026-09-29",
    "price": 900,
    "stops": [
        {"stopNumber": 1, "city": "Milford", "state": "MA", "postalCode": "01757"},
        {"stopNumber": 2, "city": "Shippensburg", "state": "PA", "postalCode": "17257"},
    ],
    "vehicles": [{"year": "2010", "make": "Toyota", "model": "Corolla"}],
}
NOW = lambda: datetime(2026, 9, 27, 10, 0) 


class TopicRouting(unittest.TestCase):

    def queues_for(self, topic):
        plan = {
            "requests": topics.SUBSCRIPTION_REQUESTS,
            "available": topics.SUBSCRIPTION_AVAILABLE,
            "results": topics.SUBSCRIPTION_RESULTS,
        }
        return sorted(name for name, sub in plan.items() if solace_match(sub, topic))

    def test_submitted_request_lands_only_in_requests_queue(self):
        self.assertEqual(self.queues_for(topics.for_order(topics.REQUEST_SUBMITTED, "6600111")), ["requests"])

    def test_valid_order_goes_to_carriers_and_shipper(self):
        decision = process_request(json.dumps(ORDER), NOW())
        routed = [self.queues_for(p.topic) for p in decision.publications]
        self.assertEqual(routed, [["available"], ["results"]])

    def test_cancelled_goes_only_to_shipper(self):
        bad = dict(ORDER, pickupDate="2026-09-01")
        decision = process_request(json.dumps(bad), NOW())
        self.assertEqual([self.queues_for(p.topic) for p in decision.publications], [["results"]])

    def test_carrier_events(self):
        self.assertEqual(self.queues_for(topics.for_order(topics.RESULT_CARRIER_ASSIGNED, "1")), ["results"])
        self.assertEqual(self.queues_for(topics.for_order(topics.ORDER_ASSIGNED, "1")), [])

    def test_wildcard_semantics(self):
        self.assertTrue(solace_match("a/b/>", "a/b/c"))
        self.assertTrue(solace_match("a/b/>", "a/b/c/d"))
        self.assertFalse(solace_match("a/b/>", "a/b"))
        self.assertFalse(solace_match("a/b/>", "a/x/c"))


class ValidatorService(unittest.TestCase):
    def test_valid_message_is_published_then_acked(self):
        receiver, publisher, message = FakeReceiver(), FakePublisher(), FakeMessage(json.dumps(ORDER))
        decision = validator_service.handle_message(message, receiver, publisher, NOW)
        self.assertTrue(decision.valid)
        self.assertEqual(len(publisher.sent), 2)
        self.assertEqual(publisher.sent[1][1]["status"], "Accepted")
        self.assertEqual(receiver.acked, [message])

    def test_invalid_message_is_cancelled_and_acked(self):
        receiver, publisher = FakeReceiver(), FakePublisher()
        message = FakeMessage(json.dumps(dict(ORDER, deliveryDate="2026-09-28")))
        validator_service.handle_message(message, receiver, publisher, NOW)
        self.assertEqual([t for t, _ in publisher.sent], ["newcron/dispatch/result/cancelled/v1/6600111"])
        self.assertEqual(receiver.acked, [message])

    def test_no_ack_if_publish_fails(self):
        receiver, publisher = FakeReceiver(), FakePublisher(fail_on="result/accepted")
        message = FakeMessage(json.dumps(ORDER))
        with self.assertRaises(RuntimeError):
            validator_service.handle_message(message, receiver, publisher, NOW)
        self.assertEqual(receiver.acked, []) 


class CarrierBoard(unittest.TestCase):
    def setUp(self):
        self.board = carrier_dashboard.OrderBoard()
        self.board.add("6600111", dict(ORDER), "msg1")
        self.board.add("6600112", dict(ORDER, shipperOrderId="6600112"), "msg2")

    def test_resolve_by_id_and_row_number(self):
        self.assertEqual(self.board.resolve("6600112"), "6600112")
        self.assertEqual(self.board.resolve("1"), "6600111")
        self.assertEqual(self.board.resolve("2"), "6600112")
        self.assertIsNone(self.board.resolve("3"))
        self.assertIsNone(self.board.resolve("zzz"))

    def test_duplicates_are_rejected(self):
        self.assertFalse(self.board.add("6600111", {}, "msg3"))

    def test_accept_publishes_events_and_releases_message(self):
        publisher, acks = FakePublisher(), queue.Queue()
        with redirect_stdout(io.StringIO()):
            carrier_dashboard.accept_order("1", "Diego", self.board, publisher, acks)
        topics_sent = [t for t, _ in publisher.sent]
        self.assertEqual(
            topics_sent,
            ["newcron/dispatch/order/assigned/v1/6600111", "newcron/dispatch/result/carrier-assigned/v1/6600111"],
        )
        self.assertEqual(publisher.sent[1][1]["status"], "CarrierAssigned")
        self.assertEqual(acks.get_nowait(), "msg1")
        self.assertIsNone(self.board.resolve("6600111"))  

    def test_failed_accept_keeps_order_available(self):
        publisher, acks = FakePublisher(fail_on="order/assigned"), queue.Queue()
        with redirect_stdout(io.StringIO()):
            carrier_dashboard.accept_order("6600111", "Diego", self.board, publisher, acks)
        self.assertTrue(acks.empty())
        self.assertEqual(self.board.resolve("6600111"), "6600111")

    def test_render_smoke(self):
        with mock.patch("carrier_dashboard.clear"), redirect_stdout(io.StringIO()) as out:
            carrier_dashboard.render(self.board, "Diego")
        text = out.getvalue()
        self.assertIn("6600111", text)
        self.assertIn("Milford, MA -> Shippensburg, PA", text)
        self.assertIn("2010 Toyota Corolla", text)
        self.assertIn("$900.00", text)


class ShipperBoard(unittest.TestCase):
    def test_latest_status_wins_and_newest_first(self):
        board = shipper_dashboard.ResultsBoard()
        board.record({"shipperOrderId": "1", "status": "Accepted", "notes": "n"})
        board.record({"shipperOrderId": "2", "status": "Cancelled", "notes": "n"})
        board.record({"shipperOrderId": "1", "status": "CarrierAssigned", "notes": "n", "carrier": "Diego"})
        rows = board.snapshot()
        self.assertEqual([r["shipperOrderId"] for r in rows], ["1", "2"])
        self.assertEqual(rows[0]["status"], "CarrierAssigned")
        self.assertTrue(board.wait_for_change(0.01))
        self.assertFalse(board.wait_for_change(0.01))

    def test_render_smoke(self):
        board = shipper_dashboard.ResultsBoard()
        board.record({"shipperOrderId": "7743789", "status": "Cancelled",
                      "notes": "Pickup date cannot be earlier than the current date.", "receivedAt": "2026-09-27 10:00:00"})
        with mock.patch("shipper_dashboard.clear"), redirect_stdout(io.StringIO()) as out:
            shipper_dashboard.render(board)
        self.assertIn("7743789", out.getvalue())
        self.assertIn("Cancelled: 1", out.getvalue())


class DemoScenarios(unittest.TestCase):
    def test_demo_orders_behave_as_labelled(self):
        now = datetime(2026, 9, 27, 10, 0)
        with mock.patch("shipper_submit.settings.now", return_value=now):
            demo = shipper_submit.demo_orders()
        results = [process_request(raw, now) for _label, raw in demo]
        self.assertEqual([r.valid for r in results], [True, False, False, True, False])
        self.assertIn("not valid JSON", results[4].notes)
        self.assertEqual(results[4].order_id, demo[4][1].split('"')[3])  

    def test_same_day_demo_flips_after_3pm(self):
        late = datetime(2026, 9, 27, 16, 0)
        with mock.patch("shipper_submit.settings.now", return_value=late):
            demo = shipper_submit.demo_orders()
        self.assertFalse(process_request(demo[3][1], late).valid)

    def test_sample_files(self):
        now = datetime(2026, 9, 27, 10, 0)
        loaded = dict(shipper_submit.load_files(["samples"]))
        self.assertFalse(process_request(loaded["order_assignment_example.json"], now).valid) 
        self.assertTrue(process_request(loaded["order_future.json"], now).valid)
        self.assertIn("not valid JSON", process_request(loaded["order_malformed.json"], now).notes)


if __name__ == "__main__":
    unittest.main()
