import unittest
from unittest.mock import patch

from chain_observer_v1_3 import ChainObserver, VerificationAttestation, RuleConfigurationError
from chain_observer_v1_3.events import EventStore, EventValidationError


class ProjectAContextTests(unittest.TestCase):
    def setUp(self):
        self.o = ChainObserver()
        self.o.register_claim("c1", "Lil Red Dots remains the overarching topic.", "[K]",
                              properties={"topic": "Lil Red Dots"})

    def test_context_anchor_survives_domain_analogy(self):
        self.o.create_context("ctx-main", "Lil Red Dots", "human")
        self.o.activate_context("ctx-main", "human")
        self.o.register_reference_marker("m-claim", "claim", "c1", "human")
        relation = self.o.record_context_relation(
            "rel-analogy", "c1", "ctx-main", "analogy", "human",
            domain_label="construction", basis_marker_ids=["m-claim"],
            initial_assessment_status="PROVISIONAL")
        self.assertEqual(relation.relation_type, "analogy")
        self.assertEqual(self.o.active_context_id, "ctx-main")
        self.assertEqual(self.o.current_relation_assessment("rel-analogy"), "PROVISIONAL")

    def test_initial_assessment_is_in_creation_event(self):
        self.o.create_context("ctx", "Lil Red Dots", "human")
        self.o.record_context_relation("r", "c1", "ctx", "example", "human",
                                       initial_assessment_status="UNRESOLVED")
        event = self.o.events[-1]
        self.assertEqual(event["event_type"], "CONTEXT_RELATION_RECORDED")
        self.assertEqual(event["payload"]["initial_assessment_status"], "UNRESOLVED")
        self.assertEqual(self.o.current_relation_assessment("r"), "UNRESOLVED")

    def test_later_assessment_is_append_only_and_derived(self):
        self.o.create_context("ctx", "Lil Red Dots", "human")
        original = self.o.record_context_relation("r", "c1", "ctx", "analogy", "human")
        event_count = len(self.o.events)
        self.o.assess_context_relation("r", "CONFIRMED", "Human reviewed the relation.")
        self.assertEqual(len(self.o.events), event_count + 1)
        self.assertEqual(original.initial_assessment_status, "PROVISIONAL")
        self.assertEqual(self.o.relations["r"].initial_assessment_status, "PROVISIONAL")
        self.assertEqual(self.o.current_relation_assessment("r"), "CONFIRMED")

    def test_unresolved_is_assessment_not_relation_type(self):
        self.o.create_context("ctx", "Lil Red Dots", "human")
        with self.assertRaises(ValueError):
            self.o.record_context_relation("r", "c1", "ctx", "unresolved", "human")

    def test_context_parent_must_exist(self):
        with self.assertRaises(KeyError):
            self.o.create_context("child", "Child", "human", "missing")
        self.assertNotIn("child", self.o.contexts)

    def test_domain_change_does_not_implicitly_activate_new_topic(self):
        self.o.create_context("ctx", "Lil Red Dots", "human")
        self.o.activate_context("ctx")
        self.o.record_context_relation("r", "c1", "ctx", "analogy", "human", domain_label="construction")
        self.assertEqual(self.o.active_context_id, "ctx")


class ProjectAReferenceAndFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.o = ChainObserver()
        self.o.register_claim("c1", "User clarification is a human assertion.", "[K]")

    def test_internal_reference_must_exist(self):
        with self.assertRaises(KeyError):
            self.o.register_reference_marker("m", "claim", "missing", "human")
        self.assertNotIn("m", self.o.markers)

    def test_external_marker_requires_locator(self):
        with self.assertRaises(ValueError):
            self.o.register_reference_marker("m", "external_source", "source-x", "human")
        self.assertNotIn("m", self.o.markers)

    def test_whitespace_locator_is_normalized_before_marker_mutation(self):
        before_events = len(self.o.events)
        self.o.register_reference_marker("m", "claim", "c1", "human", "   ")
        self.assertIsNone(self.o.markers["m"].locator)
        self.assertEqual(len(self.o.events), before_events + 1)
        self.assertTrue(self.o.validate_integrity()["valid"])

    def test_reference_resolution_separates_validity_and_availability(self):
        self.o.register_reference_marker("m", "external_source", "source-x", "human", "https://example.test/source")
        self.assertEqual(self.o.resolve_reference_marker("m"), "UNKNOWN")
        self.assertEqual(self.o.resolve_reference_marker("m", source_available=False), "UNAVAILABLE")
        self.assertEqual(self.o.resolve_reference_marker("m", source_available=True), "RESOLVED")
        self.assertIn("m", self.o.markers)

    def test_feedback_resolution_points_to_claim_marker(self):
        self.o.register_reference_marker("m-clarification", "claim", "c1", "human")
        self.o.request_feedback("q1", "claim", "c1", "Was this analogy intended?", "Relation ambiguous.")
        self.o.resolve_feedback("q1", "m-clarification")
        event = self.o.events[-1]
        self.assertEqual(event["event_type"], "FEEDBACK_RESOLVED")
        self.assertEqual(event["payload"]["resolution_marker_id"], "m-clarification")

    def test_feedback_has_only_one_terminal_event(self):
        self.o.register_reference_marker("m-clarification", "claim", "c1", "human")
        self.o.request_feedback("q1", "claim", "c1", "Question?", "Reason.")
        self.o.resolve_feedback("q1", "m-clarification")
        with self.assertRaises(ValueError):
            self.o.cancel_feedback("q1", "Too late")

    def test_cancelled_feedback_cannot_be_resolved(self):
        self.o.register_reference_marker("m-clarification", "claim", "c1", "human")
        self.o.request_feedback("q1", "claim", "c1", "Question?", "Reason.")
        self.o.cancel_feedback("q1", "No longer needed")
        with self.assertRaises(ValueError):
            self.o.resolve_feedback("q1", "m-clarification")

    def test_repeated_question_must_link_to_prior_request(self):
        self.o.request_feedback("q1", "claim", "c1", "Question?", "Reason.")
        self.o.request_feedback("q2", "claim", "c1", "A materially different question?", "New information.",
                                supersedes_request_id="q1")
        self.assertEqual(self.o.events[-1]["payload"]["supersedes_request_id"], "q1")


class ProjectAIntegrityAndCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.o = ChainObserver()

    def test_external_source_reference_is_consistent_with_chain_validation(self):
        state = self.o._state
        EventStore.append(
            state, "REFERENCE_MARKER_RECORDED", {"type": "marker", "id": "m-ext"},
            {"marker_id": "m-ext", "target_type": "external_source", "target_id": "source-x",
             "locator": "https://example.test", "created_at": "2026-01-01T00:00:00+00:00",
             "created_by": "human"}, "human", "human",
            references=[{"type": "external_source", "id": "https://example.test"}],
        )
        self.assertEqual(EventStore.validate_chain(state), [])

    def test_external_source_reference_requires_nonempty_identifier(self):
        state = self.o._state
        event = EventStore.append(
            state, "REFERENCE_MARKER_RECORDED", {"type": "marker", "id": "m-ext"},
            {"marker_id": "m-ext", "target_type": "external_source", "target_id": "source-x",
             "locator": "https://example.test", "created_at": "2026-01-01T00:00:00+00:00",
             "created_by": "human"}, "human", "human",
            references=[{"type": "external_source", "id": "https://example.test"}],
        )
        state.events[-1]["references"][0]["id"] = "   "
        self.assertTrue(any("invalid external_source reference" in e for e in EventStore.validate_chain(state)))

    def test_legacy_snapshot_keys_and_event_fields_are_retained(self):
        self.o.register_claim("c", "Claim", "[K]")
        snap = self.o.snapshot()
        for key in ("prototype", "claims", "transformations", "events", "conflict_candidates", "scope_note"):
            self.assertIn(key, snap)
        event = snap["events"][0]
        for key in ("event_id", "timestamp", "event", "claim_id", "details"):
            self.assertIn(key, event)
        self.assertEqual(event["event"], "CLAIM_REGISTERED")

    def test_events_are_read_only_copies(self):
        self.o.register_claim("c", "Claim", "[K]")
        exposed = self.o.events
        exposed[0]["details"]["status"] = "[R]"
        exposed.clear()
        self.assertEqual(self.o.events[0]["details"]["status"], "[K]")
        self.assertEqual(len(self.o.events), 1)

    def test_claim_reads_cannot_mutate_internal_state(self):
        self.o.register_claim("c", "Claim", "[K]", properties={"nested": {"x": 1}})
        exposed = self.o.registry["c"]
        exposed.properties["nested"]["x"] = 9
        self.assertEqual(self.o.registry["c"].properties["nested"]["x"], 1)

    def test_event_sequence_and_previous_link(self):
        self.o.register_claim("c", "Claim", "[K]")
        self.o.apply_transition("c", "[I]", "Interpretation")
        events = self.o.events
        self.assertEqual([e["sequence"] for e in events], [1, 2])
        self.assertIsNone(events[0]["previous_event_id"])
        self.assertEqual(events[1]["previous_event_id"], events[0]["event_id"])
        self.assertTrue(self.o.validate_integrity()["valid"])

    def test_trigger_and_evidence_markers_remain_distinct(self):
        self.o.register_claim("c", "Claim", "[K]")
        self.o.register_reference_marker("trigger", "claim", "c", "human")
        self.o.register_reference_marker("evidence", "claim", "c", "human")
        self.o.apply_transition("c", "[I]", "Reassessed", trigger_marker_ids=["trigger"],
                                evidence_marker_ids=["evidence"], validation_result="UNDETERMINED")
        payload = self.o.events[-1]["payload"]
        self.assertEqual(payload["trigger_marker_ids"], ["trigger"])
        self.assertEqual(payload["evidence_marker_ids"], ["evidence"])
        self.assertEqual(payload["validation_result"], "UNDETERMINED")

    def test_transition_blocked_retains_false_return_and_legacy_event_name(self):
        self.o.register_claim("c", "Claim", "[K]")
        self.o.record_transformation("t", "c", "out", "rewrite", output_properties={}, rules=[])
        att = VerificationAttestation.create("src", "checker", "manual")
        self.assertFalse(self.o.apply_transition("out", "[R]", "Promote", "src", att))
        event = self.o.events[-1]
        self.assertEqual(event["event_type"], "TRANSITION_BLOCKED")
        self.assertEqual(event["event"], "TRANSITION_BLOCKED")

    def test_transformation_is_atomic_if_second_event_append_fails(self):
        self.o.register_claim("in", "Input", "[K]", properties={"x": 1})
        original_events = len(self.o.events)
        real_append = EventStore.append
        calls = {"n": 0}
        def fail_second(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated event append failure")
            return real_append(*args, **kwargs)
        with patch.object(EventStore, "append", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.o.record_transformation("t", "in", "out", "copy", output_properties={"x": 1},
                                              rules=[{"property": "x", "mode": "preserve"}])
        self.assertNotIn("out", self.o.registry)
        self.assertNotIn("t", self.o.transformations)
        self.assertEqual(len(self.o.events), original_events)

    def test_failed_context_relation_does_not_mutate_or_append_event(self):
        self.o.register_claim("c", "Claim", "[K]")
        self.o.create_context("ctx", "Topic", "human")
        count = len(self.o.events)
        with self.assertRaises(KeyError):
            self.o.record_context_relation("r", "c", "ctx", "analogy", "human", basis_marker_ids=["missing"])
        self.assertNotIn("r", self.o.relations)
        self.assertEqual(len(self.o.events), count)

    def test_event_integrity_detects_internal_tampering(self):
        self.o.register_claim("c", "Claim", "[K]")
        # Private-state mutation is outside the public API, but the audit should detect it.
        self.o._state.events[0]["payload"]["status"] = "[R]"
        result = self.o.validate_integrity()
        self.assertFalse(result["valid"])
        self.assertFalse(result["tamper_proof"])

    def test_legacy_alias_still_returns_bool(self):
        self.o.register_claim("c", "Claim", "[K]")
        self.assertIs(self.o.evaluate_transition("c", "[I]", "Legacy call"), True)

    def test_verified_promotion_is_blocked_when_validation_is_undetermined(self):
        self.o.register_claim("c", "Claim", "[K]")
        att = VerificationAttestation.create("src", "checker", "manual")
        result = self.o.apply_transition("c", "[R]", "Promote", "src", att,
                                         validation_result="UNDETERMINED")
        self.assertFalse(result)
        self.assertEqual(self.o.registry["c"].status, "[K]")
        self.assertEqual(self.o.events[-1]["payload"]["to_status"], "[R]")


    def test_typed_event_schema_rejects_unknown_event_types_and_fields(self):
        from chain_observer_v1_3.models import ObserverState
        state = ObserverState()
        with self.assertRaises(EventValidationError):
            EventStore.append(state, "UNDECLARED_EVENT", {"type": "context", "id": "x"}, {}, "system")
        with self.assertRaises(EventValidationError):
            EventStore.append(state, "CONTEXT_CREATED", {"type": "context", "id": "x"},
                              {"context_id": "x", "topic_label": "Topic", "parent_context_id": None,
                               "created_at": "2026-11-21T00:00:00+00:00", "created_by": "human",
                               "surprise_field": "not allowed"}, "human", "human")
        self.assertEqual(state.events, [])

    def test_integrity_audit_covers_transformation_and_conflict_records(self):
        self.o.register_claim("a", "Input A", "[K]", properties={"x": 1})
        self.o.register_claim("b", "Input B", "[K]", properties={"x": 2})
        self.o.record_transformation("t", "a", "out", "copy", output_properties={"x": 1},
                                     rules=[{"property": "x", "mode": "preserve"}])
        self.o.record_conflict_candidate("a", "b", "reviewer", "possible conflict")
        result = self.o.validate_integrity()
        self.assertTrue(result["valid"], result["errors"])

    def test_integrity_audit_covers_context_reference_relation_and_feedback_state(self):
        self.o.register_claim("c", "Claim", "[K]")
        self.o.create_context("ctx", "Topic", "human")
        self.o.activate_context("ctx")
        self.o.register_reference_marker("m", "claim", "c", "human")
        self.o.record_context_relation("r", "c", "ctx", "analogy", "human", basis_marker_ids=["m"])
        self.o.assess_context_relation("r", "CONFIRMED", "Reviewed")
        self.o.request_feedback("q", "relation", "r", "Clarify?", "Unclear relation")
        self.assertTrue(self.o.validate_integrity()["valid"], self.o.validate_integrity()["errors"])


if __name__ == "__main__":
    unittest.main()
