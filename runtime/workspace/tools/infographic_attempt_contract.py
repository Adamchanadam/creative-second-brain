from __future__ import annotations


FORMAL_INFOGRAPHIC_PROVIDER = "codex_imagegen"
FORMAL_INFOGRAPHIC_MODEL = "gpt-image-2"
INFOGRAPHIC_GENERATION_CONTRACT = "codex_imagegen_gpt_image_2_v1"
FORMAL_INFOGRAPHIC_RUNTIME_TOOL = "Codex imagegen"
FORMAL_INFOGRAPHIC_SIZE = "1024x1536"
FORMAL_INFOGRAPHIC_QUALITY = "high"
FORMAL_INFOGRAPHIC_COUNT = 1

IMAGEGEN_REPAIRABLE_FAILURES = {
    "wrong_source_or_subject",
    "wrong_or_missing_locked_text",
    "semantic_contradiction",
    "internal_process_pollution",
    "cropped_content",
    "missing_footer",
}
IMAGEGEN_NON_REPAIRABLE_FAILURES = {
    "invalid_image_dimensions",
    "reverse_review_failed",
}
IMAGEGEN_BLOCKING_FAILURES = (
    IMAGEGEN_REPAIRABLE_FAILURES | IMAGEGEN_NON_REPAIRABLE_FAILURES
)

IMAGEGEN_GENERATION_POLICY = {
    "max_candidates_per_asset": 2,
    "max_regeneration_rounds": 1,
    "on_limit": "stop_needs_review",
    "attempt_authorization_required": True,
    "blocking_failures": sorted(IMAGEGEN_BLOCKING_FAILURES),
    "repairable_failures": sorted(IMAGEGEN_REPAIRABLE_FAILURES),
    "non_repairable_failures": sorted(IMAGEGEN_NON_REPAIRABLE_FAILURES),
    "automatic_retry": False,
    "non_blocking_visual_warnings": [
        "small footer-height variance",
        "valid canvas orientation or aspect-ratio variance",
        "minor layout, alignment, spacing, or style variance",
    ],
}

FORMAL_INFOGRAPHIC_GENERATION_REQUEST = {
    "runtime_tool": FORMAL_INFOGRAPHIC_RUNTIME_TOOL,
    "model": FORMAL_INFOGRAPHIC_MODEL,
    "target_size": FORMAL_INFOGRAPHIC_SIZE,
    "quality_instruction": FORMAL_INFOGRAPHIC_QUALITY,
    "one_generation_per_action": True,
}

COMPAT_IMAGEGEN_ACTION_ENVELOPE_VERSION = "source_ingest_imagegen_action_v1"
COMPAT_IMAGEGEN_RESPONSE_VERSION = "source_ingest_imagegen_response_v1"
V2_IMAGEGEN_ATTEMPT_LEDGER_VERSION = "compiler_worker_v2_imagegen_attempts_v1"
V2_IMAGEGEN_ACTION_ENVELOPE_VERSION = "compiler_worker_v2_imagegen_action_v3"
V2_IMAGEGEN_RESPONSE_VERSION = "compiler_worker_v2_imagegen_response_v2"
