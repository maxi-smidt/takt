from __future__ import annotations

import unittest

from takt.fleet_actions import (
    ALLOWED_ACTIONS,
    DISRUPTIVE_ACTIONS,
    FLEET_ACTIONS,
    NO_REQUEUE_ON_LEASE_EXPIRY,
    OVERRIDABLE_ACTIONS,
    REQUIRES_READY_ACTIONS,
    capability_for,
    get_action,
)


class FleetActionsTests(unittest.TestCase):
    def test_every_action_name_matches_its_table_key(self) -> None:
        for key, action in FLEET_ACTIONS.items():
            self.assertEqual(key, action.name)

    def test_derived_sets_are_consistent_with_the_table(self) -> None:
        self.assertEqual(ALLOWED_ACTIONS, frozenset(FLEET_ACTIONS))
        self.assertEqual(
            DISRUPTIVE_ACTIONS,
            frozenset(name for name, action in FLEET_ACTIONS.items() if action.disruptive),
        )
        self.assertEqual(
            OVERRIDABLE_ACTIONS,
            frozenset(name for name, action in FLEET_ACTIONS.items() if action.overridable),
        )
        self.assertEqual(
            REQUIRES_READY_ACTIONS,
            frozenset(name for name, action in FLEET_ACTIONS.items() if action.requires_ready),
        )

    def test_overridable_actions_are_a_subset_of_actions_requiring_ready(self) -> None:
        self.assertTrue(OVERRIDABLE_ACTIONS <= REQUIRES_READY_ACTIONS)

    def test_no_requeue_actions_are_disruptive_power_actions(self) -> None:
        self.assertTrue(NO_REQUEUE_ON_LEASE_EXPIRY <= DISRUPTIVE_ACTIONS)
        for name in NO_REQUEUE_ON_LEASE_EXPIRY:
            self.assertEqual(FLEET_ACTIONS[name].capability, "power-control-v1")

    def test_every_action_declares_a_positive_timeout(self) -> None:
        for action in FLEET_ACTIONS.values():
            self.assertGreater(action.timeout_seconds, 0)

    def test_stages_include_queued_and_a_terminal_state_when_declared(self) -> None:
        for action in FLEET_ACTIONS.values():
            if not action.stages:
                continue
            self.assertIn("queued", action.stages)
            self.assertIn("succeeded", action.stages)
            self.assertIn("cancelled", action.stages)

    def test_get_action_returns_none_for_unknown_names(self) -> None:
        self.assertIsNone(get_action("does_not_exist"))
        self.assertIsNotNone(get_action("restart_takt"))

    def test_capability_for_unknown_action_is_none(self) -> None:
        self.assertIsNone(capability_for("does_not_exist"))
        self.assertEqual(capability_for("start_takt"), "service-control-v1")


if __name__ == "__main__":
    unittest.main()
