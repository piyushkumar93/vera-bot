"""
Constants and configuration settings for the Vera Deterministic Decision Engine.
Centralizes all thresholds, taboos, allowed defaults, and priority rules.
"""

from typing import Dict, List, Set

# Uptime reference
APP_VERSION = "1.0.0"
APP_MODEL = "vera-deterministic-engine-v1"
TEAM_NAME = "Vera-Lead-Engineers"

# Allowed send_as identities
SEND_AS_VERA = "vera"
SEND_AS_MERCHANT_ON_BEHALF = "merchant_on_behalf"

# Allowed CTAs
CTA_OPEN_ENDED = "open_ended"
CTA_BINARY_YES_NO = "binary_yes_no"
CTA_NONE = "none"

# Urgency / Priority base mapping
KIND_PRIORITY: Dict[str, int] = {
    # Emergency / Compliance / Security
    "supply_alert": 100,
    "regulation_change": 95,
    "renewal_due": 90,
    "winback_eligible": 85,
    # High urgency operational & customer triggers
    "perf_dip": 80,
    "recall_due": 78,
    "chronic_refill_due": 76,
    "wedding_package_followup": 74,
    "trial_followup": 72,
    "appointment_tomorrow": 70,
    "customer_lapsed_hard": 68,
    "customer_lapsed_soft": 65,
    "active_planning_intent": 62,
    "review_theme_emerged": 60,
    "ipl_match_today": 58,
    "perf_spike": 55,
    "milestone_reached": 50,
    "category_seasonal": 48,
    "gbp_unverified": 45,
    "competitor_opened": 42,
    "research_digest": 40,
    "cde_opportunity": 38,
    "dormant_with_vera": 35,
    "festival_upcoming": 30,
    "curious_ask_due": 25,
    "seasonal_perf_dip": 20,
}

# Auto-reply signature phrases
AUTO_REPLY_PHRASES: List[str] = [
    "thank you for contacting",
    "thanks for reaching out",
    "our team will respond shortly",
    "automated response",
    "we are currently closed",
    "will get back to you soon",
    "this is an automated message",
    "aapki jaankari ke liye",
    "hamari team tak pahuncha",
    "automated assistant",
    "shukriya",
    "connect with you soon",
]

# Hostile / Opt-out phrases
OPT_OUT_PHRASES: List[str] = [
    "stop",
    "unsubscribe",
    "not interested",
    "stop messaging me",
    "useless spam",
    "don't message",
    "dont message",
    "stop sending",
    "bothering me",
    "why are you bothering",
    "remove me",
    "leave me alone",
    "report spam",
]

# Action intent transition phrases
COMMITMENT_PHRASES: List[str] = [
    "yes",
    "ok",
    "okay",
    "lets do it",
    "let's do it",
    "whats next",
    "what's next",
    "go ahead",
    "proceed",
    "send it",
    "draft it",
    "i want to join",
    "schedule it",
    "confirm",
    "please send",
    "start",
    "done",
    "activate",
]

# Taboo words universally avoided
GLOBAL_TABOOS: Set[str] = {
    "guaranteed",
    "100% safe",
    "completely cure",
    "miracle",
    "best in city",
    "viral guarantee",
    "miracle marketing",
    "shred in 7 days",
    "fastest results",
}

# A safety class is ordered before caller-supplied urgency. Remaining triggers
# retain the supplied urgency order within their class.
SAFETY_PRIORITY: Dict[str, int] = {
    "supply_alert": 3,
    "regulation_change": 3,
    "recall_alert": 3,
    "gbp_unverified": 2,
}
