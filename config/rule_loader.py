# shared/config/rule_loader.py
"""
File Use:
    Loads per-camera rule configurations from JSON files and builds
    RuleEngine instances. Each camera can have its own rule config;
    cameras without a specific config file use the default.

    Later this will load rule configs from MongoDB instead of JSON files.

Implements:
    - build_rule_engine (create RuleEngine from a single config dict)
    - load_rules (load all rule configs from a directory)

Depends On:
    - apps.event_processor.rule_engine
    - shared.utils.logging

Used By:
    - main.py
"""

import json
from pathlib import Path

from core.rule_engine import (
    AlertRule,
    RuleEngine,
    SustainedDetectionRule,
    ThresholdAlertRule,
    WindowDetectionRule,
)
from contracts.alert_event import AlertSeverity
from utils.logging import get_logger

logger = get_logger("rule_loader")

DEFAULT_RULES_DIR = Path("config/rules")


def _parse_severity(rule_config: dict) -> AlertSeverity:
    severity_str = rule_config.get("severity", "medium")
    if isinstance(severity_str, str):
        severity_str = severity_str.lower()
    try:
        return AlertSeverity(severity_str)
    except ValueError:
        logger.warning("invalid_severity", severity=severity_str, defaulting_to="medium")
        return AlertSeverity.MEDIUM


def _build_rule(rule_config: dict) -> AlertRule | None:
    rule_type = rule_config.get("type")
    # cooldown_sec is per-rule — each rule suppresses its own alerts independently
    cooldown_sec = float(rule_config.get("cooldown_sec", 30.0))
    # draw=true → annotate alert media (live, snapshot, clip); draw=false → raw media
    draw = bool(rule_config.get("draw", True))
    severity = _parse_severity(rule_config)

    if rule_type == "threshold":
        return ThresholdAlertRule(
            pipeline_id=rule_config["pipeline_id"],
            label=rule_config["label"],
            confidence_threshold=rule_config.get("confidence_threshold", 0.5),
            alert_type=rule_config.get("alert_type"),
            severity=severity,
            cooldown_sec=cooldown_sec,
            draw=draw,
        )

    if rule_type == "sustained":
        return SustainedDetectionRule(
            pipeline_id=rule_config["pipeline_id"],
            label=rule_config["label"],
            confidence_threshold=rule_config.get("confidence_threshold", 0.5),
            required_consecutive_frames=rule_config.get("required_consecutive_frames", 5),
            alert_type=rule_config.get("alert_type"),
            severity=severity,
            cooldown_sec=cooldown_sec,
            draw=draw,
        )

    if rule_type == "window":
        return WindowDetectionRule(
            pipeline_id=rule_config["pipeline_id"],
            label=rule_config["label"],
            confidence_threshold=rule_config.get("confidence_threshold", 0.5),
            window_size=rule_config.get("window_size", 5),
            min_detections=rule_config.get("min_detections", 3),
            alert_type=rule_config.get("alert_type"),
            severity=severity,
            cooldown_sec=cooldown_sec,
            draw=draw,
        )

    logger.error("unknown_rule_type", rule_type=rule_type)
    return None


def build_rule_engine(config: dict) -> RuleEngine:
    rules: list[AlertRule] = []
    for rule_config in config.get("rules", []):
        rule = _build_rule(rule_config)
        if rule is not None:
            rules.append(rule)
    return RuleEngine(rules=rules)


def load_rules(
    rules_dir: Path | str = DEFAULT_RULES_DIR,
) -> tuple[RuleEngine, dict[str, RuleEngine]]:
    """Load rule configurations from a directory of JSON files.

    Expects:
        - default.json: Fallback rules for cameras without specific config.
        - {camera_id}.json: Camera-specific rule overrides.

    Returns:
        Tuple of (default_engine, per_camera_engines).
        per_camera_engines maps camera_id → RuleEngine.

    Raises:
        FileNotFoundError: If rules_dir or default.json does not exist.
    """
    rules_dir = Path(rules_dir)
    if not rules_dir.exists():
        raise FileNotFoundError(f"Rules directory not found: {rules_dir}")

    default_path = rules_dir / "default.json"
    if not default_path.exists():
        raise FileNotFoundError(f"Default rule config not found: {default_path}")

    with open(default_path) as f:
        default_config = json.load(f)

    default_engine = build_rule_engine(default_config)
    logger.info("default_rules_loaded", rule_count=len(default_config.get("rules", [])))

    per_camera: dict[str, RuleEngine] = {}
    for config_file in sorted(rules_dir.glob("*.json")):
        if config_file.name == "default.json":
            continue

        camera_id = config_file.stem
        try:
            with open(config_file) as f:
                camera_config = json.load(f)

            per_camera[camera_id] = build_rule_engine(camera_config)
            logger.info(
                "camera_rules_loaded",
                camera_id=camera_id,
                rule_count=len(camera_config.get("rules", [])),
            )
        except Exception as e:
            logger.error("camera_rules_load_failed", camera_id=camera_id, error=str(e))

    logger.info(
        "rules_loaded",
        default_rules=len(default_config.get("rules", [])),
        camera_overrides=len(per_camera),
    )

    return default_engine, per_camera
