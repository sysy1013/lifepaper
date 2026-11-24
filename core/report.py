# -*- coding: utf-8 -*-
"""줄·문장 위치 기반 통합 오류 리포트 (순수 로직, API 불필요).

기재금지 검출(규칙+Gemini), 문체·서식·맞춤법·과장 점검, Gemini 오탈자 검사 결과를
"몇 번째 줄·몇 번째 문장 / 원문 / 오류 내용 / 수정 제안 / 공식 근거·예외 / 판정" 한 표로 합친다.
판정은 기재요령 기준의 참고값이며 최종 판단은 교사가 한다.
"""

import bisect
import re

from core.ocr import UNCERTAIN_MARK
from core.rulebook import RULEBOOK, apply_exceptions, rule_id_for_finding
from core.rules import collect_style_issues

VERDICT_BANNED = "기재 불가"
VERDICT_CHECK = "확인 필요"
VERDICT_FIX = "수정 필요"
VERDICT_ADVICE = "권고"
VERDICT_ORDER = (VERDICT_BANNED, VERDICT_CHECK, VERDICT_FIX, VERDICT_ADVICE)
# '확인 필요' 중 학교·교사 판단으로 넘긴 항목(힌트 규칙·문맥 확인 띄어쓰기)에 붙는 배지
JUDGMENT_BADGE = "교사 판단 필요"
# 명문 규정 없이 추정으로 잡는 서식 항목 → 권고
_ADVICE_RULES = frozenset({"format_missing_period"})

_CATEGORY_VERDICT = {
    "맞춤법": VERDICT_FIX,
    "서식": VERDICT_FIX,
    "문체": VERDICT_ADVICE,
    "과장·허위": VERDICT_ADVICE,
}

_EXCERPT_RADIUS = 20
# 문장 경계: 마침표·물음표·느낌표 뒤에 공백(또는 줄 끝)이 오는 곳. 소수점(3.5)은 경계가 아니다.
_SENTENCE_END_RE = re.compile(r"[.!?…]+(?=\s|$)")


class _Locator:
    """문자 오프셋 → (줄 번호, 줄 안 문장 번호) 변환기. 둘 다 1부터."""

    def __init__(self, text: str):
        self.text = text
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
        # 각 줄 안의 문장 경계 위치(경계 문자 끝 오프셋) 목록
        self.sentence_ends: list[list[int]] = []
        for i, start in enumerate(self.line_starts):
            end = (
                self.line_starts[i + 1] - 1
                if i + 1 < len(self.line_starts)
                else len(text)
            )
            line = text[start:end]
            self.sentence_ends.append(
                [start + m.end() for m in _SENTENCE_END_RE.finditer(line)]
            )

    def locate(self, offset: int) -> tuple[int, int]:
        idx = bisect.bisect_right(self.line_starts, offset) - 1
        idx = max(0, idx)
        # 이 오프셋 이전(이하)에 끝난 문장 수 + 1. 마침표 자체는 그 문장에 속한다.
        return idx + 1, bisect.bisect_right(self.sentence_ends[idx], offset) + 1


def _excerpt(text: str, start: int, end: int) -> str:
    before = text[max(0, start - _EXCERPT_RADIUS) : start]
    after = text[end : end + _EXCERPT_RADIUS]
    word = text[start:end]
    if not word.strip():
        word = "⎵" * len(word) if word else ""
    s = f"{before}「{word}」{after}"
    s = re.sub(r"\s+", " ", s).strip()
    prefix = "…" if start - _EXCERPT_RADIUS > 0 else ""
    suffix = "…" if end + _EXCERPT_RADIUS < len(text) else ""
    return f"{prefix}{s}{suffix}"


def _find_all(text: str, word: str, lo: int = 0, hi: int | None = None) -> list[int]:
    hi = len(text) if hi is None else hi
    out = []
    if not word:
        return out
    i = text.find(word, lo, hi)
    while i != -1:
        out.append(i)
        i = text.find(word, i + len(word), hi)
    return out


def _segment_ranges(text: str, segments) -> list[tuple[str, int, int]]:
    """(과목, 본문) 목록을 원문 전체 기준 (과목, 시작, 끝) 범위로 바꾼다."""
    ranges = []
    cursor = 0
    for subject, content in segments or []:
        pos = text.find(content, cursor) if content else -1
        if pos == -1:
            pos = text.find(content) if content else -1
        if pos == -1:
            continue
        ranges.append((subject, pos, pos + len(content)))
        cursor = pos + len(content)
    return ranges


def _subject_at(ranges, offset: int) -> str:
    for subject, s, e in ranges:
        if s <= offset < e:
            return subject
    return ""


def build_issue_report(
    text: str,
    findings,
    proofread_items=(),
    subject_segments=None,
    ignored=(),
) -> list[dict]:
    """모든 점검 결과를 위치 기반 이슈 목록으로 통합한다.

    - text: 검토 원문 전체 (줄 번호는 항상 이 원문 기준)
    - findings: 기재금지 검출 목록(규칙+Gemini, subject 태그 가능)
    - proofread_items: Gemini 오탈자 검사 결과 [{wrong, correct, reason}]
    - subject_segments: split_by_subject 결과 (2개 이상이면 과목별 위치·과목명 표시)
    - ignored: 오탐 무시 목록(이 단어의 검출은 제외)
    같은 단어가 여러 번 나오면 발생 위치마다 행을 만든다. 정렬: 줄 → 문장 → 시작 위치.
    """
    text = text or ""
    ignore_set = {w for w in (ignored or ()) if w}
    loc = _Locator(text)
    segments = subject_segments if subject_segments and len(subject_segments) > 1 else None
    ranges = _segment_ranges(text, segments) if segments else []

    rows: list[dict] = []
    seen: set[tuple] = set()

    def add_row(start, end, subject, category, problem, suggestion, basis, exception, verdict, word, rule_id,
                judgment=""):
        key = (start, end, rule_id, problem)
        if key in seen:
            return
        seen.add(key)
        if start is None:
            line = sentence = None
            excerpt = f"「{word}」"
        else:
            line, sentence = loc.locate(start)
            excerpt = _excerpt(text, start, end)
        rows.append(
            {
                "line": line,
                "sentence": sentence,
                "subject": subject or "",
                "start": start,
                "end": end,
                "word": word,
                "excerpt": excerpt,
                "category": category,
                "problem": problem,
                "suggestion": suggestion,
                "basis": basis,
                "exception": exception,
                "verdict": verdict,
                "rule_id": rule_id,
                "judgment": judgment if verdict == VERDICT_CHECK else "",
            }
        )

    # 1) 기재금지 검출
    for f in findings or []:
        word = (f.get("word") or "").strip()
        if not word or word in ignore_set:
            continue
        rule_id = rule_id_for_finding(f)
        entry = RULEBOOK.get(rule_id, {})
        base_verdict = VERDICT_BANNED if f.get("severity", "위반") == "위반" else VERDICT_CHECK
        basis = entry.get("source", "") if rule_id != "gemini_other" else (f.get("basis") or entry.get("source", ""))
        sugg = " / ".join(s for s in (f.get("suggestion_1"), f.get("suggestion_2")) if s)
        f_subject = f.get("subject") or ""
        judgment = f.get("judgment") or (entry.get("summary", "") if entry.get("judgment") else "")
        lo, hi = 0, len(text)
        if ranges and f_subject:
            for subj, s, e in ranges:
                if subj == f_subject:
                    lo, hi = s, e
                    break
        positions = _find_all(text, word, lo, hi)
        if not positions and (lo, hi) != (0, len(text)):
            positions = _find_all(text, word)
        if not positions:
            verdict, exception, excluded = apply_exceptions(
                rule_id, base_verdict, word, text, -1, -1, f_subject
            )
            if not excluded:
                add_row(None, None, f_subject, "기재금지", f.get("reason", ""), sugg,
                        basis, exception, verdict, word, rule_id, judgment)
            continue
        for pos in positions:
            subject = f_subject or _subject_at(ranges, pos)
            verdict, exception, excluded = apply_exceptions(
                rule_id, base_verdict, word, text, pos, pos + len(word), subject
            )
            if excluded:
                continue
            add_row(pos, pos + len(word), subject, "기재금지", f.get("reason", ""),
                    sugg, basis, exception, verdict, word, rule_id, judgment)

    # 2) 문체·서식·맞춤법·과장 점검 (과목 분리 시 과목 본문별로 점검하고 원문 오프셋으로 환산)
    blocks = [(subj, s, e) for subj, s, e in ranges] if ranges else [("", 0, len(text))]
    local_spelling_spans: list[tuple[int, int]] = []
    for subj, s, e in blocks:
        for issue in collect_style_issues(text[s:e]):
            if issue["word"] in ignore_set:
                continue
            start, end = s + issue["start"], s + issue["end"]
            entry = RULEBOOK.get(issue["rule_id"], {})
            if issue["category"] == "맞춤법":
                local_spelling_spans.append((start, end))
            judgment = issue.get("judgment", "")
            if judgment:
                verdict = VERDICT_CHECK
            elif issue["rule_id"] in _ADVICE_RULES:
                verdict = VERDICT_ADVICE
            else:
                verdict = _CATEGORY_VERDICT.get(issue["category"], VERDICT_ADVICE)
            add_row(
                start, end, subj, issue["category"], issue["problem"], issue["suggestion"],
                issue.get("basis") or entry.get("source", ""), entry.get("exceptions", ""),
                verdict, issue["word"], issue["rule_id"], judgment,
            )

    # 3) 이미지 전사본의 판독 불확실 표시(⟦?⟧) — 이미지와 대조해 직접 고쳐야 한다
    entry = RULEBOOK["uncertain_char"]
    for pos in _find_all(text, UNCERTAIN_MARK):
        add_row(pos, pos + len(UNCERTAIN_MARK), _subject_at(ranges, pos), "전사 확인",
                entry["summary"], "이미지와 대조해 실제 글자로 고치기", entry["source"],
                "", VERDICT_CHECK, UNCERTAIN_MARK, "uncertain_char")

    # 4) Gemini 오탈자 검사 (로컬 맞춤법 규칙과 같은 위치는 중복 제외)
    entry = RULEBOOK["proofread"]
    for p in proofread_items or []:
        wrong = (p.get("wrong") or "").strip()
        if not wrong or wrong in ignore_set:
            continue
        correct = (p.get("correct") or "").strip()
        problem = p.get("reason") or "오탈자·맞춤법 오류"
        for pos in _find_all(text, wrong):
            end = pos + len(wrong)
            if any(pos < e2 and s2 < end for s2, e2 in local_spelling_spans):
                continue
            add_row(pos, end, _subject_at(ranges, pos), "맞춤법", problem, correct,
                    entry["source"], entry["exceptions"], VERDICT_FIX, wrong, "proofread")

    big = 10**12
    rows.sort(
        key=lambda r: (
            r["line"] if r["line"] is not None else big,
            r["sentence"] if r["sentence"] is not None else big,
            r["start"] if r["start"] is not None else big,
        )
    )
    return rows


def summarize_report(rows: list[dict]) -> str:
    """종합 판정 한 줄."""
    counts = {v: 0 for v in VERDICT_ORDER}
    judged = 0
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        if r.get("judgment"):
            judged += 1
    return (
        f"기재요령 기준 판정(참고) — {VERDICT_BANNED} {counts[VERDICT_BANNED]}건 · "
        f"{VERDICT_CHECK} {counts[VERDICT_CHECK]}건(그중 {JUDGMENT_BADGE} {judged}건) · "
        f"{VERDICT_FIX} {counts[VERDICT_FIX]}건 · "
        f"{VERDICT_ADVICE} {counts[VERDICT_ADVICE]}건 — 최종 판단은 교사"
    )


def count_judgment_rows(rows: list[dict]) -> int:
    """교사 판단 필요(힌트) 행 수."""
    return sum(1 for r in rows if r.get("judgment"))


REPORT_COLUMNS = ("줄", "문장", "과목", "구분", "원문", "오류 내용", "수정 제안", "근거", "판정")


def report_table_rows(rows: list[dict]) -> list[dict]:
    """UI 표·CSV용 한국어 컬럼 행 목록. 예외 안내는 근거 칸에 함께 적는다."""
    out = []
    for r in rows:
        basis = r["basis"]
        if r["exception"]:
            basis = f"{basis} · 예외: {r['exception']}" if basis else f"예외: {r['exception']}"
        problem = r["problem"]
        verdict = r["verdict"]
        if r.get("judgment"):
            problem = f"{problem} — {JUDGMENT_BADGE}: {r['judgment']}"
            verdict = f"{verdict} · {JUDGMENT_BADGE}"
        out.append(
            {
                "줄": r["line"] if r["line"] is not None else "-",
                "문장": r["sentence"] if r["sentence"] is not None else "-",
                "과목": r["subject"],
                "구분": r["category"],
                "원문": r["excerpt"],
                "오류 내용": problem,
                "수정 제안": r["suggestion"],
                "근거": basis,
                "판정": verdict,
            }
        )
    return out
