import json
import unittest
from datetime import datetime

from newcron import topics
from newcron.validator import ACCEPTED_NOTE, extract_order_id, process_request, validate_order


def order(**overrides):
    base = {
        "shipperOrderId": "6600111",
        "pickupDate": "2026-09-28",
        "deliveryDate": "2026-09-29",
        "price": 900,
        "stops": [
            {"stopNumber": 1, "city": "Milford", "state": "MA", "postalCode": "01757"},
            {"stopNumber": 2, "city": "Shippensburg", "state": "PA", "postalCode": "17257"},
        ],
        "vehicles": [{"year": "2010", "make": "Toyota", "model": "Corolla"}],
        "transportationReleaseNotes": "Verify the pickup date.",
    }
    base.update(overrides)
    return base


NOW_MORNING = datetime(2026, 9, 27, 10, 0, 0)


class DateRules(unittest.TestCase):
    def test_valid_future_pickup(self):
        self.assertEqual(validate_order(order(), NOW_MORNING), [])

    def test_pickup_in_the_past(self):
        errors = validate_order(order(pickupDate="2026-09-26", deliveryDate="2026-09-28"), NOW_MORNING)
        self.assertEqual(errors, ["Pickup date cannot be earlier than the current date."])

    def test_pickup_today_before_cutoff_is_ok(self):
        payload = order(pickupDate="2026-09-27", deliveryDate="2026-09-28")
        self.assertEqual(validate_order(payload, datetime(2026, 9, 27, 14, 59, 59)), [])

    def test_pickup_today_exactly_at_3pm_is_ok(self):
        payload = order(pickupDate="2026-09-27", deliveryDate="2026-09-28")
        self.assertEqual(validate_order(payload, datetime(2026, 9, 27, 15, 0, 0)), [])

    def test_pickup_today_after_3pm_is_rejected(self):
        payload = order(pickupDate="2026-09-27", deliveryDate="2026-09-28")
        errors = validate_order(payload, datetime(2026, 9, 27, 15, 0, 1))
        self.assertEqual(len(errors), 1)
        self.assertIn("after 3:00 p.m.", errors[0])

    def test_after_3pm_does_not_affect_future_pickup(self):
        self.assertEqual(validate_order(order(), datetime(2026, 9, 27, 23, 30)), [])

    def test_delivery_same_day_as_pickup(self):
        errors = validate_order(order(deliveryDate="2026-09-28"), NOW_MORNING)
        self.assertEqual(errors, ["Delivery date must be at least one day after the pickup date."])

    def test_delivery_before_pickup(self):
        errors = validate_order(order(deliveryDate="2026-09-27"), NOW_MORNING)
        self.assertEqual(errors, ["Delivery date must be at least one day after the pickup date."])

    def test_delivery_exactly_one_day_after_is_ok(self):
        self.assertEqual(validate_order(order(deliveryDate="2026-09-29"), NOW_MORNING), [])

    def test_bad_date_formats(self):
        errors = validate_order(order(pickupDate="28/09/2026", deliveryDate="2026-13-40"), NOW_MORNING)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("YYYY-MM-DD" in e for e in errors))

    def test_missing_dates(self):
        payload = order()
        del payload["pickupDate"]
        del payload["deliveryDate"]
        self.assertEqual(
            validate_order(payload, NOW_MORNING), ["Pickup date is required.", "Delivery date is required."]
        )

    def test_multiple_errors_are_all_reported(self):
        errors = validate_order(order(pickupDate="2026-09-20", deliveryDate="2026-09-20"), NOW_MORNING)
        self.assertEqual(len(errors), 2)


class StructureRules(unittest.TestCase):
    def test_missing_order_id(self):
        payload = order()
        del payload["shipperOrderId"]
        self.assertIn("shipperOrderId is required.", validate_order(payload, NOW_MORNING))

    def test_bad_price(self):
        for bad in (0, -5, "900", None, True):
            self.assertIn("Price must be a number greater than 0.", validate_order(order(price=bad), NOW_MORNING))

    def test_needs_two_stops(self):
        payload = order(stops=[{"stopNumber": 1, "city": "A", "state": "MA", "postalCode": "1"}])
        self.assertIn("At least two stops (pickup and delivery) are required.", validate_order(payload, NOW_MORNING))

    def test_stop_missing_field(self):
        stops = order()["stops"]
        del stops[1]["postalCode"]
        self.assertIn("Stop 2 is missing: postalCode.", validate_order(order(stops=stops), NOW_MORNING))

    def test_needs_a_vehicle(self):
        self.assertIn("At least one vehicle is required.", validate_order(order(vehicles=[]), NOW_MORNING))

    def test_vehicle_missing_field(self):
        errors = validate_order(order(vehicles=[{"year": "2010", "make": "Toyota"}]), NOW_MORNING)
        self.assertIn("Vehicle 1 is missing: model.", errors)


class ProcessRequest(unittest.TestCase):
    def test_valid_request_publishes_order_then_accepted(self):
        decision = process_request(json.dumps(order()), NOW_MORNING)
        self.assertTrue(decision.valid)
        self.assertEqual(len(decision.publications), 2)
        available, result = decision.publications
        self.assertEqual(available.topic, "newcron/dispatch/order/available/v1/6600111")
        self.assertEqual(json.loads(available.payload)["shipperOrderId"], "6600111")
        self.assertEqual(result.topic, "newcron/dispatch/result/accepted/v1/6600111")
        self.assertEqual(
            json.loads(result.payload),
            {"shipperOrderId": "6600111", "status": "Accepted", "notes": ACCEPTED_NOTE},
        )

    def test_invalid_request_only_publishes_cancelled(self):
        raw = json.dumps(order(shipperOrderId="7743789", pickupDate="2026-09-20", deliveryDate="2026-09-29"))
        decision = process_request(raw, NOW_MORNING)
        self.assertFalse(decision.valid)
        self.assertEqual(len(decision.publications), 1)
        self.assertEqual(decision.publications[0].topic, "newcron/dispatch/result/cancelled/v1/7743789")
        self.assertEqual(
            json.loads(decision.publications[0].payload),
            {
                "shipperOrderId": "7743789",
                "status": "Cancelled",
                "notes": "Pickup date cannot be earlier than the current date.",
            },
        )

    def test_cancelled_never_goes_to_carriers(self):
        decision = process_request(json.dumps(order(deliveryDate="2026-09-28")), NOW_MORNING)
        self.assertFalse(any(p.topic.startswith(topics.ORDER_AVAILABLE) for p in decision.publications))

    def test_malformed_json_is_cancelled_and_id_recovered(self):
        raw = '{"shipperOrderId": "6600111", "vehicles": [], "transportationReleaseNotes": "x"}'
        decision = process_request(raw, NOW_MORNING)
        self.assertFalse(decision.valid)
        self.assertEqual(decision.order_id, "6600111")
        self.assertIn("not valid JSON", decision.notes)
        self.assertEqual(decision.publications[0].topic, "newcron/dispatch/result/cancelled/v1/6600111")

    def test_garbage_payload(self):
        decision = process_request("hola", NOW_MORNING)
        self.assertFalse(decision.valid)
        self.assertIsNone(decision.order_id)
        self.assertEqual(decision.publications[0].topic, "newcron/dispatch/result/cancelled/v1/unknown")

    def test_json_that_is_not_an_object(self):
        decision = process_request("[1, 2, 3]", NOW_MORNING)
        self.assertFalse(decision.valid)
        self.assertEqual(decision.notes, "Payload must be a JSON object.")

    def test_numeric_order_id_is_supported(self):
        decision = process_request(json.dumps(order(shipperOrderId=6600111)), NOW_MORNING)
        self.assertTrue(decision.valid)
        self.assertEqual(decision.order_id, "6600111")


class TopicHelpers(unittest.TestCase):
    def test_unsafe_ids_cannot_break_the_topic_tree(self):
        self.assertEqual(topics.safe_segment("a/b>*c d"), "a_b__c_d")
        self.assertEqual(topics.safe_segment(""), "unknown")
        self.assertEqual(topics.safe_segment(None), "unknown")

    def test_extract_order_id(self):
        self.assertEqual(extract_order_id('{"shipperOrderId": 42, oops'), "42")
        self.assertIsNone(extract_order_id("nada"))


if __name__ == "__main__":
    unittest.main()
