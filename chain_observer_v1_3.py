"""Backward-compatible V1.3 import surface for Project A."""
from .observer import ChainObserver, VALID_STATUSES
from .models import VerificationAttestation, EpistemicClaim
from .rules import RuleConfigurationError, VALID_CHECKS, COMPARATORS
from .events import utc_now

__all__ = ["ChainObserver", "VerificationAttestation", "EpistemicClaim",
           "RuleConfigurationError", "VALID_STATUSES", "VALID_CHECKS",
           "COMPARATORS", "utc_now"]
