#!/usr/bin/env python3
"""Cross-community / cross-country guardrail demo.

This script exercises the on-device guardrail pipeline across a wide
matrix of community types, countries with core-language messages, and
mixed-language / code-switching scenarios. It produces:

* A structured JSON report with per-scenario classification output and
  per-group + overall latency aggregates.
* A human-readable Markdown report with summary tables and a pass/fail
  verdict against the 250 ms p95 target.

Spec references:

* ARCHITECTURE.md — "Hybrid Local Pipeline" + "Performance envelope".
* PHASES.md Phase 6 — performance optimization benchmarking.
* PROGRESS.md — operational demo layer (no phase change).

Run from the repo root::

    python tools/demo_guardrail.py

Result files land under ``results/`` with ISO-8601 timestamps.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Make ``kchat-skills/compiler`` importable, mirroring
# ``tools/regenerate_compiled_examples.py``.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "kchat-skills" / "compiler"))

from benchmark import (  # type: ignore[import-not-found]  # noqa: E402
    P95_LATENCY_TARGET_MS,
    BenchmarkCase,
    BenchmarkReport,
    PipelineBenchmark,
)
from pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    GuardrailPipeline,
    SkillBundle,
)
from encoder_adapter import MockEncoderAdapter  # type: ignore[import-not-found]  # noqa: E402
from threshold_policy import ThresholdPolicy  # type: ignore[import-not-found]  # noqa: E402


# ---------------------------------------------------------------------------
# Scenario definitions.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Scenario:
    """One demo scenario.

    ``scenario_id`` is unique. ``bucket`` groups scenarios for tables in
    the Markdown report (``community`` / ``country`` / ``mixed`` /
    ``harmful``). ``group_kind`` / ``age_mode`` mirror the corresponding
    ``context`` fields of ``kchat.guardrail.local_signal.v1``.
    """

    scenario_id: str
    text: str
    lang_hint: str
    locale: str
    jurisdiction_cc: Optional[str]
    community: Optional[str]
    bucket: str
    description: str
    group_kind: str = "small_group"
    age_mode: str = "mixed_age"


def _jurisdiction_id(cc: Optional[str]) -> Optional[str]:
    return f"kchat.jurisdiction.{cc}.guardrail.v1" if cc else None


def _community_id(name: Optional[str]) -> Optional[str]:
    return f"kchat.community.{name}.guardrail.v1" if name else None


# Community-type scenarios — at least eight different communities.
COMMUNITY_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "community_school_homework",
        "Hey does anyone have the homework for Ms. Johnson's class? "
        "I totally forgot about it lol",
        "en",
        "en-US",
        "us",
        "school",
        "community",
        "School (minor_present) — benign homework chatter.",
        group_kind="small_group",
        age_mode="minor_present",
    ),
    Scenario(
        "community_workplace_q3budget",
        "Please review the Q3 budget spreadsheet I shared. The client "
        "meeting is at 3pm.",
        "en",
        "en-US",
        "us",
        "workplace",
        "community",
        "Workplace — routine professional coordination.",
        group_kind="small_group",
        age_mode="adult_only",
    ),
    Scenario(
        "community_gaming_queue",
        "GG everyone! That last round was insane. Anyone want to queue "
        "up for another match?",
        "en",
        "en-US",
        "us",
        "gaming",
        "community",
        "Gaming — post-match social chatter.",
        group_kind="large_room",
    ),
    Scenario(
        "community_dating_dinner",
        "Had a great time last night! Would love to grab dinner again "
        "this weekend.",
        "en",
        "en-US",
        "us",
        "dating",
        "community",
        "Dating (adult_only) — benign post-date follow-up.",
        group_kind="dyadic",
        age_mode="adult_only",
    ),
    Scenario(
        "community_health_support_down",
        "I've been feeling really down lately and don't know who to "
        "talk to. Has anyone else dealt with this?",
        "en",
        "en-US",
        "us",
        "health_support",
        "community",
        "Health support — peer-support disclosure.",
        group_kind="large_room",
    ),
    Scenario(
        "community_marketplace_iphone",
        "Selling my old iPhone 13 for $200. DM me if interested. Local "
        "pickup only.",
        "en",
        "en-US",
        "us",
        "marketplace",
        "community",
        "Marketplace — benign sale listing.",
        group_kind="large_room",
    ),
    Scenario(
        "community_political_broadband",
        "The new infrastructure bill has some interesting provisions "
        "for rural broadband access.",
        "en",
        "en-US",
        "us",
        "political",
        "community",
        "Political — civic policy discussion.",
        group_kind="large_room",
    ),
    Scenario(
        "community_journalism_transit",
        "Breaking: City council votes 7-2 to approve the new transit "
        "expansion plan.",
        "en",
        "en-US",
        "us",
        "journalism",
        "community",
        "Journalism — neutral news bulletin.",
        group_kind="large_room",
    ),
    Scenario(
        "community_family_dinner",
        "Don't forget Grandma's birthday dinner on Sunday — bring the "
        "photo album!",
        "en",
        "en-US",
        "us",
        "family",
        "community",
        "Family — household coordination.",
        group_kind="small_group",
        age_mode="mixed_age",
    ),
    Scenario(
        "community_adult_only_party",
        "After-party at my place — drinks on me. 21+ only please.",
        "en",
        "en-US",
        "us",
        "adult_only",
        "community",
        "Adult-only — adult-gated social plan.",
        group_kind="small_group",
        age_mode="adult_only",
    ),
    Scenario(
        "community_vn_school_homework",
        "Các bạn ơi, ai có bài tập toán của cô Hương không? Mình quên "
        "làm rồi.",
        "vi",
        "vi-VN",
        "vn",
        "school",
        "community",
        "VN (vi) school (minor_present) — benign forgotten-homework chatter.",
        group_kind="small_group",
        age_mode="minor_present",
    ),
    Scenario(
        "community_vn_workplace_q3report",
        "Anh chị xem lại báo cáo quý 3 giúp em nhé. Cuộc họp với khách "
        "hàng lúc 3 giờ chiều.",
        "vi",
        "vi-VN",
        "vn",
        "workplace",
        "community",
        "VN (vi) workplace — benign Q3-report + client-meeting reminder.",
        group_kind="small_group",
        age_mode="adult_only",
    ),
    Scenario(
        "community_vn_marketplace_iphone",
        "Bán iPhone 13 cũ giá 5 triệu. Ai quan tâm inbox mình nhé. Giao "
        "dịch trực tiếp tại Hà Nội.",
        "vi",
        "vi-VN",
        "vn",
        "marketplace",
        "community",
        "VN (vi) marketplace — benign Hà Nội iPhone 13 sale listing.",
        group_kind="open_group",
        age_mode="mixed_age",
    ),
    Scenario(
        "community_vn_health_support_tired",
        "Dạo này mình cảm thấy rất mệt mỏi và không biết nói chuyện với "
        "ai. Có ai từng trải qua điều này không?",
        "vi",
        "vi-VN",
        "vn",
        "health_support",
        "community",
        "VN (vi) health-support — peer-support disclosure.",
        group_kind="small_group",
        age_mode="mixed_age",
    ),
)


# Country / core-language scenarios — at least ten countries.
COUNTRY_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "country_us_scam_xyz",
        "Check out this amazing deal at www.totally-legit-prizes.xyz "
        "— you won a free iPhone!",
        "en",
        "en-US",
        "us",
        None,
        "country",
        "US (en) — fake giveaway with .xyz TLD (should flag).",
    ),
    Scenario(
        "country_de_bike",
        "Verkaufe mein altes Fahrrad für 50 Euro. Abholung in Berlin "
        "möglich.",
        "de",
        "de-DE",
        "de",
        None,
        "country",
        "DE (de) — benign marketplace listing.",
    ),
    Scenario(
        "country_jp_meeting",
        "今日の会議は午後3時からです。資料を事前に確認してください。",
        "ja",
        "ja-JP",
        "jp",
        None,
        "country",
        "JP (ja) — benign meeting reminder.",
    ),
    Scenario(
        "country_br_restaurant",
        "Alguém sabe onde posso encontrar um bom restaurante no centro?",
        "pt",
        "pt-BR",
        "br",
        None,
        "country",
        "BR (pt) — benign question about restaurants.",
    ),
    Scenario(
        "country_fr_meeting",
        "Bonjour à tous, la réunion de demain est reportée à jeudi.",
        "fr",
        "fr-FR",
        "fr",
        None,
        "country",
        "FR (fr) — benign meeting reschedule.",
    ),
    Scenario(
        "country_sa_meeting",
        "مرحبا بالجميع، اجتماع الغد تم تأجيله إلى يوم الخميس",
        "ar",
        "ar-SA",
        "sa",
        None,
        "country",
        "SA (ar) — benign meeting reschedule.",
    ),
    Scenario(
        "country_in_phone_email",
        "Kya aap mujhe apna phone number de sakte hain? Mera email hai "
        "test@example.com",
        "hi",
        "hi-IN",
        "in",
        None,
        "country",
        "IN (hi+en) — Hinglish PII request (email pattern triggers).",
    ),
    Scenario(
        "country_kr_dinner",
        "오늘 저녁에 같이 밥 먹을 사람 있나요?",
        "ko",
        "ko-KR",
        "kr",
        None,
        "country",
        "KR (ko) — benign dinner-plan question.",
    ),
    Scenario(
        "country_mx_football",
        "¿Alguien quiere ir al partido de fútbol este fin de semana?",
        "es",
        "es-MX",
        "mx",
        None,
        "country",
        "MX (es) — benign weekend plans.",
    ),
    Scenario(
        "country_eg_doctor",
        "هل يمكن لأحد مساعدتي في العثور على طبيب جيد في القاهرة؟",
        "ar",
        "ar-EG",
        "eg",
        None,
        "country",
        "EG (ar) — benign request for help finding a doctor.",
    ),
    Scenario(
        "country_id_lunch",
        "Ada yang mau makan siang bareng nanti? Saya di kantor pusat.",
        "id",
        "id-ID",
        "id",
        None,
        "country",
        "ID (id) — benign lunch plan.",
    ),
    Scenario(
        "country_th_weather",
        "วันนี้ฝนตกหนักมาก อย่าลืมพกร่มออกไปข้างนอกนะ",
        "th",
        "th-TH",
        "th",
        None,
        "country",
        "TH (th) — benign weather advice.",
    ),
    Scenario(
        "country_vn_dinner",
        "Tối nay mọi người có muốn đi ăn phở cùng nhau không?",
        "vi",
        "vi-VN",
        "vn",
        None,
        "country",
        "VN (vi) — benign dinner-plan question.",
    ),
    Scenario(
        "country_tr_meeting",
        "Yarınki toplantıyı saat üçe ertelediğimizi unutmayın.",
        "tr",
        "tr-TR",
        "tr",
        None,
        "country",
        "TR (tr) — benign meeting reschedule.",
    ),
    Scenario(
        "country_pl_holiday",
        "Czy ktoś planuje weekendowy wyjazd w góry? Mam jedno wolne "
        "miejsce w samochodzie.",
        "pl",
        "pl-PL",
        "pl",
        None,
        "country",
        "PL (pl) — benign weekend trip carpool.",
    ),
    Scenario(
        "country_ng_event",
        "Make una no forget say the meet-up dey hold for Lagos this "
        "Saturday by 4pm sharp.",
        "en",
        "en-NG",
        "ng",
        None,
        "country",
        "NG (en Pidgin) — benign meet-up reminder.",
    ),
)


# Mixed-language / code-switching scenarios.
MIXED_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "mixed_us_en_es",
        "Hey amigos, the fiesta is at mi casa tonight. Bring some comida!",
        "en",
        "en-US",
        "us",
        None,
        "mixed",
        "US (en+es) — bilingual friends-and-food chatter.",
    ),
    Scenario(
        "mixed_de_de_tr",
        "Morgen treffen wir uns im Café, tamam mı? Bring deine Freunde "
        "mit.",
        "de",
        "de-DE",
        "de",
        None,
        "mixed",
        "DE (de+tr) — Turkish-German diaspora coordination.",
    ),
    Scenario(
        "mixed_in_hi_en_hinglish",
        "Yaar, ye meeting bahut boring thi. Let's grab chai after work.",
        "hi",
        "hi-IN",
        "in",
        None,
        "mixed",
        "IN (hi+en) — Hinglish workplace banter.",
    ),
    Scenario(
        "mixed_sg_en_zh",
        "Eh later we go makan at that new 火锅 place near MRT lah",
        "en",
        "en-SG",
        "sg",
        None,
        "mixed",
        "SG (en+zh) — Singlish hotpot plan.",
    ),
    Scenario(
        "mixed_ca_en_fr",
        "The meeting est à trois heures. Don't forget your rapport.",
        "en",
        "en-CA",
        "ca",
        None,
        "mixed",
        "CA (en+fr) — bilingual workplace reminder.",
    ),
    Scenario(
        "mixed_my_ms_en",
        "Jom pergi lunch dekat that new restaurant. The food memang "
        "sedap!",
        "ms",
        "ms-MY",
        "my",
        None,
        "mixed",
        "MY (ms+en) — Malay-English food recommendation.",
    ),
    Scenario(
        "mixed_ph_en_tl",
        "Guys, let's meet sa mall later. Dala na kayo ng mga gamit niyo.",
        "en",
        "en-PH",
        "ph",
        None,
        "mixed",
        "PH (en+tl) — Taglish meet-up plan.",
    ),
    Scenario(
        "mixed_ch_de_fr",
        "Das Meeting ist um drei Uhr. N'oubliez pas vos documents.",
        "de",
        "de-CH",
        "ch",
        None,
        "mixed",
        "CH (de+fr) — Swiss bilingual workplace reminder.",
    ),
    Scenario(
        "mixed_ng_en_yo",
        "How far now? E don tey we no see. Make we link up this evening.",
        "en",
        "en-NG",
        "ng",
        None,
        "mixed",
        "NG (en+yo Pidgin) — friends planning to meet up.",
    ),
    Scenario(
        "mixed_vn_vi_en_hotpot",
        "Ê mọi người, tối nay mình đi ăn hotpot nhé! The new place near "
        "Bến Thành is really good.",
        "vi",
        "vi-VN",
        "vn",
        None,
        "mixed",
        "VN (vi+en) — benign hotpot plan near Bến Thành.",
        group_kind="small_group",
        age_mode="mixed_age",
    ),
    Scenario(
        "mixed_vn_vi_en_url_top",
        "Mọi người check cái link này đi, sale khủng luôn: "
        "https://super-deal.top/giamgia free shipping nha!",
        "vi",
        "vi-VN",
        "vn",
        "marketplace",
        "mixed",
        "VN (vi+en) marketplace — sale link with .top TLD (URL risk).",
        group_kind="open_group",
        age_mode="mixed_age",
    ),
)


# Harmful / flagged content scenarios — exercise the detectors.
HARMFUL_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "harmful_scam_giveaway",
        "CONGRATULATIONS! You have won $10,000! Click here to claim "
        "your prize: http://scam-site.xyz/claim",
        "en",
        "en-US",
        "us",
        None,
        "harmful",
        "Scam giveaway with .xyz TLD — fake-giveaway + URL risk.",
    ),
    Scenario(
        "harmful_pii_card_ssn",
        "My credit card number is 4532-1234-5678-9012 and my SSN is "
        "123-45-6789",
        "en",
        "en-US",
        "us",
        None,
        "harmful",
        "PII leak — credit card pattern + phone-style SSN trigger.",
    ),
    Scenario(
        "harmful_phishing_password",
        "Please verify your account password immediately at "
        "https://secure-login.top/verify",
        "en",
        "en-US",
        "us",
        None,
        "harmful",
        "Phishing — credential-harvest pattern + .top TLD.",
    ),
    Scenario(
        "harmful_de_url_risk",
        "Klicken Sie hier um Ihr Konto zu aktualisieren: "
        "https://bank-verify.click/login",
        "de",
        "de-DE",
        "de",
        None,
        "harmful",
        "DE phishing URL with .click TLD + login keyword.",
    ),
    Scenario(
        "harmful_mixed_es_en_scam",
        "¡Felicidades! You won a premio. Send your datos to claim: wire "
        "transfer fee of $50",
        "es",
        "es-MX",
        "mx",
        None,
        "harmful",
        "Mixed (es+en) advance-fee scam — wire/transfer/fee pattern.",
    ),
    Scenario(
        "harmful_email_phone_pii",
        "Please contact me at john.doe@example.com or call "
        "+1-415-555-0199 anytime.",
        "en",
        "en-US",
        "us",
        None,
        "harmful",
        "PII — email + phone patterns in routine outbound message.",
    ),
    Scenario(
        "harmful_url_login_top",
        "Important: re-confirm your account at "
        "https://account-secure.top/login before midnight.",
        "en",
        "en-US",
        "us",
        None,
        "harmful",
        "Phishing — credential-harvest pattern + .top TLD + login keyword.",
    ),
    Scenario(
        "harmful_school_homoglyph",
        "Hey kids, claim your prize at www.frеe-iphоnе.click — limited "
        "time only!",
        "en",
        "en-US",
        "us",
        "school",
        "harmful",
        "Cyrillic-homoglyph scam URL targeting a school context.",
        group_kind="small_group",
        age_mode="minor_present",
    ),
    Scenario(
        "harmful_vn_scam_giveaway",
        "CHÚC MỪNG! Bạn đã trúng thưởng 100 triệu đồng! Nhấn vào đây để "
        "nhận giải: http://trung-thuong.xyz/nhan-giai",
        "vi",
        "vi-VN",
        "vn",
        "marketplace",
        "harmful",
        "VN (vi) scam giveaway with .xyz TLD — fake-prize + URL risk.",
        group_kind="open_group",
        age_mode="mixed_age",
    ),
    Scenario(
        "harmful_vn_pii_card_email",
        "Số thẻ tín dụng của mình là 4532-1234-5678-9012, email mình là "
        "nguyen.van.a@gmail.com",
        "vi",
        "vi-VN",
        "vn",
        "marketplace",
        "harmful",
        "VN (vi) PII leak — credit card pattern + email pattern.",
        group_kind="small_group",
        age_mode="adult_only",
    ),
)


SCENARIOS: tuple[Scenario, ...] = (
    COMMUNITY_SCENARIOS
    + COUNTRY_SCENARIOS
    + MIXED_SCENARIOS
    + HARMFUL_SCENARIOS
)


# ---------------------------------------------------------------------------
# Pipeline + benchmark execution.
# ---------------------------------------------------------------------------
def _build_pipeline(scenario: Scenario) -> GuardrailPipeline:
    bundle = SkillBundle(
        jurisdiction_id=_jurisdiction_id(scenario.jurisdiction_cc),
        community_overlay_id=_community_id(scenario.community),
    )
    return GuardrailPipeline(
        skill_bundle=bundle,
        encoder_adapter=MockEncoderAdapter(),
        threshold_policy=ThresholdPolicy(),
    )


def _scenario_message(scenario: Scenario) -> dict[str, Any]:
    return {
        "text": scenario.text,
        "lang_hint": scenario.lang_hint,
        "has_attachment": False,
        "attachment_kinds": [],
        "quoted_from_user": False,
        "is_outbound": False,
    }


def _scenario_context(scenario: Scenario) -> dict[str, Any]:
    return {
        "group_kind": scenario.group_kind,
        "group_age_mode": scenario.age_mode,
        "user_role": "member",
        "relationship_known": True,
        "locale": scenario.locale,
        "jurisdiction_id": _jurisdiction_id(scenario.jurisdiction_cc),
        "community_overlay_id": _community_id(scenario.community),
        "is_offline": False,
    }


def _scenario_to_case(scenario: Scenario) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=scenario.scenario_id,
        message=_scenario_message(scenario),
        context=_scenario_context(scenario),
    )


@dataclass
class ScenarioResult:
    """One scenario's classification + single-shot latency."""

    scenario: Scenario
    classification: dict[str, Any]
    latency_ms: float


def run_scenarios(
    scenarios: tuple[Scenario, ...],
) -> list[ScenarioResult]:
    """Single-shot pipeline run per scenario for the per-scenario table."""
    out: list[ScenarioResult] = []
    for scenario in scenarios:
        pipeline = _build_pipeline(scenario)
        message = _scenario_message(scenario)
        context = _scenario_context(scenario)
        start = time.perf_counter()
        classification = pipeline.classify(message, context)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        out.append(
            ScenarioResult(
                scenario=scenario,
                classification=classification,
                latency_ms=elapsed_ms,
            )
        )
    return out


@dataclass
class GroupBenchmark:
    """Per-(jurisdiction, community) benchmark report."""

    group_id: str
    jurisdiction_cc: Optional[str]
    community: Optional[str]
    scenario_ids: list[str]
    report: BenchmarkReport


def _group_key(scenario: Scenario) -> tuple[Optional[str], Optional[str]]:
    return (scenario.jurisdiction_cc, scenario.community)


def _group_id(jurisdiction: Optional[str], community: Optional[str]) -> str:
    j = jurisdiction or "global"
    c = community or "none"
    return f"{j}/{c}"


def run_group_benchmarks(
    scenarios: tuple[Scenario, ...],
    *,
    iterations: int = 100,
    warmup: int = 3,
) -> list[GroupBenchmark]:
    """One ``PipelineBenchmark.run`` per (jurisdiction, community) group."""
    groups: dict[tuple[Optional[str], Optional[str]], list[Scenario]] = {}
    for scenario in scenarios:
        groups.setdefault(_group_key(scenario), []).append(scenario)

    out: list[GroupBenchmark] = []
    for (jurisdiction_cc, community), members in groups.items():
        # All members of a group share (jurisdiction, community).
        bundle = SkillBundle(
            jurisdiction_id=_jurisdiction_id(jurisdiction_cc),
            community_overlay_id=_community_id(community),
        )
        pipeline = GuardrailPipeline(
            skill_bundle=bundle,
            encoder_adapter=MockEncoderAdapter(),
            threshold_policy=ThresholdPolicy(),
        )
        bench = PipelineBenchmark(pipeline=pipeline)
        cases = [_scenario_to_case(s) for s in members]
        report = bench.run(cases, iterations=iterations, warmup=warmup)
        out.append(
            GroupBenchmark(
                group_id=_group_id(jurisdiction_cc, community),
                jurisdiction_cc=jurisdiction_cc,
                community=community,
                scenario_ids=[s.scenario_id for s in members],
                report=report,
            )
        )
    out.sort(key=lambda g: g.group_id)
    return out


def run_overall_benchmark(
    scenarios: tuple[Scenario, ...],
    *,
    iterations: int = 100,
    warmup: int = 3,
) -> BenchmarkReport:
    """Aggregate benchmark across every scenario.

    The pipeline used here carries no jurisdiction / community overlay,
    matching the global baseline. Per-group reports above capture the
    overlay-specific numbers; this report is the corpus-level aggregate.
    """
    bundle = SkillBundle()
    pipeline = GuardrailPipeline(
        skill_bundle=bundle,
        encoder_adapter=MockEncoderAdapter(),
        threshold_policy=ThresholdPolicy(),
    )
    bench = PipelineBenchmark(pipeline=pipeline)
    cases = [_scenario_to_case(s) for s in scenarios]
    return bench.run(cases, iterations=iterations, warmup=warmup)


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------
TAXONOMY_LABEL: dict[int, str] = {
    0: "SAFE",
    1: "CHILD_SAFETY",
    2: "SELF_HARM",
    3: "VIOLENCE_THREAT",
    4: "EXTREMISM",
    5: "HARASSMENT",
    6: "HATE",
    7: "SCAM_FRAUD",
    8: "MALWARE_LINK",
    9: "PRIVATE_DATA",
    10: "SEXUAL_ADULT",
    11: "DRUGS_WEAPONS",
    12: "ILLEGAL_GOODS",
    13: "MISINFORMATION_HEALTH",
    14: "MISINFORMATION_CIVIC",
    15: "COMMUNITY_RULE",
}


def _category_label(category: int) -> str:
    return TAXONOMY_LABEL.get(int(category), f"CATEGORY_{category}")


def _is_flagged(classification: dict[str, Any]) -> bool:
    actions = classification.get("actions") or {}
    if any(
        bool(actions.get(k))
        for k in (
            "label_only",
            "warn",
            "strong_warn",
            "critical_intervention",
        )
    ):
        return True
    return int(classification.get("category", 0)) != 0


def _filename_timestamp() -> str:
    """ISO-8601 timestamp safe for filenames (hyphens, not colons)."""
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    return now.strftime("%Y-%m-%dT%H-%M-%SZ")


def _iso_timestamp() -> str:
    """Standard ISO-8601 timestamp for inside the JSON report."""
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _report_to_dict(report: BenchmarkReport) -> dict[str, Any]:
    return {
        "total_cases": report.total_cases,
        "iterations": report.iterations,
        "p50_ms": round(report.p50_ms, 3),
        "p95_ms": round(report.p95_ms, 3),
        "p99_ms": round(report.p99_ms, 3),
        "mean_ms": round(report.mean_ms, 3),
        "max_ms": round(report.max_ms, 3),
        "min_ms": round(report.min_ms, 3),
    }


def build_json_report(
    *,
    scenario_results: list[ScenarioResult],
    group_benchmarks: list[GroupBenchmark],
    overall: BenchmarkReport,
    timestamp_iso: str,
) -> dict[str, Any]:
    flagged = [r for r in scenario_results if _is_flagged(r.classification)]
    safe = [r for r in scenario_results if not _is_flagged(r.classification)]

    per_category: dict[str, int] = {}
    for r in scenario_results:
        label = _category_label(r.classification.get("category", 0))
        per_category[label] = per_category.get(label, 0) + 1

    per_bucket: dict[str, int] = {}
    for r in scenario_results:
        per_bucket[r.scenario.bucket] = (
            per_bucket.get(r.scenario.bucket, 0) + 1
        )

    per_scenario_payload = [
        {
            "scenario_id": r.scenario.scenario_id,
            "bucket": r.scenario.bucket,
            "description": r.scenario.description,
            "message_text": r.scenario.text,
            "lang_hint": r.scenario.lang_hint,
            "locale": r.scenario.locale,
            "jurisdiction_cc": r.scenario.jurisdiction_cc,
            "community": r.scenario.community,
            "group_kind": r.scenario.group_kind,
            "age_mode": r.scenario.age_mode,
            "classification": r.classification,
            "flagged": _is_flagged(r.classification),
            "latency_ms": round(r.latency_ms, 3),
        }
        for r in scenario_results
    ]

    per_group_payload = {
        gb.group_id: {
            "jurisdiction_cc": gb.jurisdiction_cc,
            "community": gb.community,
            "scenario_ids": gb.scenario_ids,
            **_report_to_dict(gb.report),
        }
        for gb in group_benchmarks
    }

    overall_dict = _report_to_dict(overall)

    return {
        "timestamp": timestamp_iso,
        "summary": {
            "total_messages": len(scenario_results),
            "flagged": len(flagged),
            "safe": len(safe),
            "per_category": per_category,
            "per_bucket": per_bucket,
        },
        "per_scenario": per_scenario_payload,
        "latency_report": {
            "overall": overall_dict,
            "per_group": per_group_payload,
        },
        "performance_summary": {
            "p95_target_ms": P95_LATENCY_TARGET_MS,
            "p95_actual_ms": overall_dict["p95_ms"],
            "passed": overall.passed,
        },
    }


# ---------------------------------------------------------------------------
# Markdown rendering.
# ---------------------------------------------------------------------------
def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _classification_summary(classification: dict[str, Any]) -> str:
    severity = classification.get("severity", 0)
    category = _category_label(classification.get("category", 0))
    confidence = classification.get("confidence", 0.0)
    return f"sev={severity} cat={category} conf={confidence:.2f}"


def _action_flags(classification: dict[str, Any]) -> str:
    actions = classification.get("actions") or {}
    flags = [
        name
        for name in (
            "label_only",
            "warn",
            "strong_warn",
            "critical_intervention",
            "suggest_redact",
        )
        if actions.get(name)
    ]
    return ", ".join(flags) if flags else "—"


def _bucket_table(
    bucket_name: str,
    title: str,
    scenario_results: list[ScenarioResult],
) -> str:
    rows = [r for r in scenario_results if r.scenario.bucket == bucket_name]
    lines = [
        f"### {title}",
        "",
        "| scenario | jurisdiction / community | classification | actions | latency (ms) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        loc = f"{r.scenario.jurisdiction_cc or 'global'} / {r.scenario.community or '—'}"
        lines.append(
            "| {sid} | {loc} | {cls} | {acts} | {lat:.3f} |".format(
                sid=_md_escape(r.scenario.scenario_id),
                loc=_md_escape(loc),
                cls=_md_escape(_classification_summary(r.classification)),
                acts=_md_escape(_action_flags(r.classification)),
                lat=r.latency_ms,
            )
        )
    lines.append("")
    return "\n".join(lines)


def _per_group_table(group_benchmarks: list[GroupBenchmark]) -> str:
    lines = [
        "### Per-(jurisdiction, community) latency",
        "",
        "| group | cases | iter | p50 (ms) | p95 (ms) | p99 (ms) | mean (ms) | max (ms) | min (ms) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for gb in group_benchmarks:
        r = gb.report
        lines.append(
            "| `{gid}` | {n} | {it} | {p50:.3f} | {p95:.3f} | {p99:.3f} | "
            "{mean:.3f} | {mx:.3f} | {mn:.3f} |".format(
                gid=gb.group_id,
                n=r.total_cases,
                it=r.iterations,
                p50=r.p50_ms,
                p95=r.p95_ms,
                p99=r.p99_ms,
                mean=r.mean_ms,
                mx=r.max_ms,
                mn=r.min_ms,
            )
        )
    lines.append("")
    return "\n".join(lines)


def build_markdown_report(
    *,
    scenario_results: list[ScenarioResult],
    group_benchmarks: list[GroupBenchmark],
    overall: BenchmarkReport,
    timestamp_iso: str,
) -> str:
    flagged = [r for r in scenario_results if _is_flagged(r.classification)]
    safe = [r for r in scenario_results if not _is_flagged(r.classification)]

    per_category: dict[str, int] = {}
    for r in scenario_results:
        label = _category_label(r.classification.get("category", 0))
        per_category[label] = per_category.get(label, 0) + 1

    per_bucket: dict[str, int] = {}
    for r in scenario_results:
        per_bucket[r.scenario.bucket] = (
            per_bucket.get(r.scenario.bucket, 0) + 1
        )

    p95_pass = overall.passed
    verdict = "PASS" if p95_pass else "FAIL"

    summary_lines = [
        "# KChat Guardrail — Cross-Community / Cross-Country Demo",
        "",
        f"**Generated:** {timestamp_iso}",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| total messages | {len(scenario_results)} |",
        f"| flagged (non-SAFE or any action) | {len(flagged)} |",
        f"| safe (SAFE + no actions) | {len(safe)} |",
        f"| p95 target (ms) | {P95_LATENCY_TARGET_MS:.3f} |",
        f"| p95 actual (ms) | {overall.p95_ms:.3f} |",
        f"| **verdict** | **{verdict}** |",
        "",
        "### Per-category breakdown",
        "",
        "| category | count |",
        "| --- | --- |",
    ]
    for label, count in sorted(
        per_category.items(), key=lambda kv: (-kv[1], kv[0])
    ):
        summary_lines.append(f"| {label} | {count} |")
    summary_lines.append("")

    summary_lines.extend(
        [
            "### Per-bucket breakdown",
            "",
            "| bucket | count |",
            "| --- | --- |",
        ]
    )
    for bucket, count in sorted(
        per_bucket.items(), key=lambda kv: (-kv[1], kv[0])
    ):
        summary_lines.append(f"| {bucket} | {count} |")
    summary_lines.append("")

    parts: list[str] = ["\n".join(summary_lines)]
    parts.append("## Per-scenario results\n")
    parts.append(
        _bucket_table(
            "community",
            "Community types",
            scenario_results,
        )
    )
    parts.append(
        _bucket_table(
            "country",
            "Countries (core language)",
            scenario_results,
        )
    )
    parts.append(
        _bucket_table(
            "mixed",
            "Mixed-language / code-switching",
            scenario_results,
        )
    )
    parts.append(
        _bucket_table(
            "harmful",
            "Harmful / flagged scenarios",
            scenario_results,
        )
    )

    parts.append("## Latency performance\n")
    parts.append(
        "### Overall\n\n"
        "| cases | iter | p50 (ms) | p95 (ms) | p99 (ms) | mean (ms) | "
        "max (ms) | min (ms) |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        f"| {overall.total_cases} | {overall.iterations} | "
        f"{overall.p50_ms:.3f} | {overall.p95_ms:.3f} | "
        f"{overall.p99_ms:.3f} | {overall.mean_ms:.3f} | "
        f"{overall.max_ms:.3f} | {overall.min_ms:.3f} |\n"
    )
    parts.append(_per_group_table(group_benchmarks))

    parts.append(
        "### Pass/fail vs 250 ms p95 target\n\n"
        f"- target p95: **{P95_LATENCY_TARGET_MS:.3f} ms**\n"
        f"- actual p95: **{overall.p95_ms:.3f} ms**\n"
        f"- result: **{verdict}**\n"
    )

    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Entrypoint.
# ---------------------------------------------------------------------------
def main() -> int:
    print(f"Running {len(SCENARIOS)} demo scenarios…")

    scenario_results = run_scenarios(SCENARIOS)
    group_benchmarks = run_group_benchmarks(
        SCENARIOS, iterations=100, warmup=3
    )
    overall = run_overall_benchmark(
        SCENARIOS, iterations=100, warmup=3
    )

    timestamp_iso = _iso_timestamp()
    file_stamp = _filename_timestamp()

    json_payload = build_json_report(
        scenario_results=scenario_results,
        group_benchmarks=group_benchmarks,
        overall=overall,
        timestamp_iso=timestamp_iso,
    )
    md_payload = build_markdown_report(
        scenario_results=scenario_results,
        group_benchmarks=group_benchmarks,
        overall=overall,
        timestamp_iso=timestamp_iso,
    )

    results_dir = REPO_ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    json_path = results_dir / f"demo_results_{file_stamp}.json"
    md_path = results_dir / f"demo_results_{file_stamp}.md"

    json_path.write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    md_path.write_text(md_payload, encoding="utf-8")

    flagged = sum(
        1 for r in scenario_results if _is_flagged(r.classification)
    )
    safe = len(scenario_results) - flagged
    print(
        f"  total={len(scenario_results)}  flagged={flagged}  safe={safe}"
    )
    print(
        f"  overall p50={overall.p50_ms:.3f}ms "
        f"p95={overall.p95_ms:.3f}ms "
        f"p99={overall.p99_ms:.3f}ms"
    )
    verdict = "PASS" if overall.passed else "FAIL"
    print(f"  verdict vs 250ms p95 target: {verdict}")
    print(f"Wrote {json_path.relative_to(REPO_ROOT)}")
    print(f"Wrote {md_path.relative_to(REPO_ROOT)}")

    return 0 if overall.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
