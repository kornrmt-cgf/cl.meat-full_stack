"""
Tests for the state machine framework.
"""
from django.test import TestCase
from common.state_machine import can_transition, TRANSITIONS


class CanTransitionTest(TestCase):
    """Test the can_transition function."""
    
    def test_valid_forward_transitions(self):
        """Test all valid forward transitions."""
        valid_pairs = [
            ('PACKED', 'FREEZING'),
            ('FREEZING', 'FROZEN'),
            ('FROZEN', 'READY_FOR_THAW'),
            ('READY_FOR_THAW', 'THAW_QUEUED'),
            ('THAW_QUEUED', 'THAWING'),
            ('THAW_QUEUED', 'PACKED'),  # cancel
            ('THAWING', 'READY_FOR_SALE'),
            ('READY_FOR_SALE', 'ON_DISPLAY'),
            ('ON_DISPLAY', 'REFREEZE_PENDING'),
            ('ON_DISPLAY', 'PROCESSING'),
            ('ON_DISPLAY', 'DISCARDED'),
            ('REFREEZE_PENDING', 'FREEZING'),
            ('PROCESSING', 'COMPLETED'),
            ('DISCARDED', 'COMPLETED'),
        ]
        
        for from_state, to_state in valid_pairs:
            with self.subTest(from_state=from_state, to_state=to_state):
                self.assertTrue(
                    can_transition(from_state, to_state),
                    f"Expected {from_state} → {to_state} to be valid"
                )
    
    def test_invalid_transitions(self):
        """Test that invalid transitions are rejected."""
        invalid_pairs = [
            ('PACKED', 'FROZEN'),
            ('PACKED', 'THAWING'),
            ('FREEZING', 'PACKED'),
            ('FREEZING', 'THAWING'),
            ('FROZEN', 'PACKED'),
            ('FROZEN', 'THAWING'),
            ('THAWING', 'FROZEN'),
            ('ON_DISPLAY', 'FROZEN'),
            ('COMPLETED', 'PACKED'),
        ]
        
        for from_state, to_state in invalid_pairs:
            with self.subTest(from_state=from_state, to_state=to_state):
                self.assertFalse(
                    can_transition(from_state, to_state),
                    f"Expected {from_state} → {to_state} to be invalid"
                )
    
    def test_unknown_state(self):
        """Test that unknown states return empty list."""
        self.assertFalse(can_transition('UNKNOWN_STATE', 'PACKED'))
        self.assertFalse(can_transition('PACKED', 'UNKNOWN_STATE'))
    
    def test_same_state_transition(self):
        """Test that same-state transitions are not allowed."""
        for state in TRANSITIONS.keys():
            with self.subTest(state=state):
                self.assertFalse(
                    can_transition(state, state),
                    f"Expected {state} → {state} to be invalid"
                )


class TransitionsTableTest(TestCase):
    """Test the transitions table is complete."""
    
    def test_all_states_have_transitions(self):
        """Test that all non-terminal states have at least one transition."""
        terminal_states = ['COMPLETED', 'DISCARDED', 'PROCESSING']
        
        for state in TRANSITIONS.keys():
            if state not in terminal_states:
                with self.subTest(state=state):
                    self.assertGreater(
                        len(TRANSITIONS[state]),
                        0,
                        f"State {state} should have at least one transition"
                    )
    
    def test_no_self_transitions(self):
        """Test that no state transitions to itself."""
        for state, targets in TRANSITIONS.items():
            with self.subTest(state=state):
                self.assertNotIn(
                    state, targets,
                    f"State {state} should not transition to itself"
                )
