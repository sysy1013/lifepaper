# -*- coding: utf-8 -*-
"""세특 점검 파이프라인 골든셋 평가 하니스.

기본(오프라인): core.rules.rule_based_filter + style_check 결과로 판정을 추정한다.
  - 오프라인 모드는 Gemini 없이 '기재 불가'를 확정할 수 없으므로
    예측값은 '확인 필요'(검출 있음) / '기재 가능'(검출 없음) 두 가지뿐이다.
  - 3분류 혼동행렬과 함께, 이진 지표(검출 여부 vs 기대 라벨)를 별도로 낸다.
      * flag 지표: 기대 판정 ∈ {기재 불가, 확인 필요} 를 양성으로 본 recall/precision
      * content 지표: 기재금지 내용 범주 케이스 ↔ 규칙(RULE_PATTERNS) 매치 유무
      * style 지표: 맞춤법·서식 범주 케이스 ↔ style_check 형식 경고 유무
--llm: core.gemini.analyze_with_gemini 결과를 반영(위반→기재 불가, 주의→확인 필요).
  API 키는 .streamlit/secrets.toml 의 GEMINI_API_KEY 를 읽되 어디에도 출력하지 않는다.
  비용이 들므로 명시적으로 --llm 을 줄 때만 실행한다.

사용 예:
  python scripts/eval_golden.py
  python scripts/eval_golden.py --difficulty subtle --limit 10 --seed 1 --out result.json
  python scripts/eval_golden.py --validate-only
"""

import argparse
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_CASES = os.path.join(ROOT, "tests", "golden", "seteuk_cases.jsonl")

VERDICTS = ("기재 불가", "확인 필요", "기재 가능")
DIFFICULTIES = ("blatant", "subtle", "ambiguous", "clean")
LABEL_BASES = ("official", "inferred", "school_decides")
CATEGORIES = (
    "공인어학시험", "대회·수상", "교외상", "인증시험", "모의고사", "논문·출판", "지식재산권",
    "해외활동", "부모지위", "장학금", "대학명", "기관명", "상호명", "강사명", "자격증",
    "학교식별", "MOOC", "방과후", "소논문", "생성형AI", "허위·과장", "맞춤법", "띄어쓰기",
    "서식", "특수문자", "영문표기", "문장부호", "없음",
)
FORMAT_CATEGORIES = {"맞춤법", "띄어쓰기", "서식", "특수문자", "영문표기", "문장부호"}
# 기재금지 '내용' 범주 (규칙/LLM 기재금지 탐지 대상). 허위·과장·생성형AI 는 규칙 탐지 대상 아님.
CONTENT_CATEGORIES = set(CATEGORIES) - FORMAT_CATEGORIES - {"없음", "허위·과장", "생성형AI"}
REQUIRED = ("id", "text", "expected_verdict", "expected_categories", "difficulty",
            "label_basis", "rationale", "source")


# ──────────────────────────────────────────────
# 데이터 로드·검증
# ──────────────────────────────────────────────
def load_cases(path: str) -> list[dict]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"[schema] {path}:{ln} JSON 파싱 실패: {e}")
    return cases


def validate_cases(cases: list[dict]) -> list[str]:
    """스키마 오류 메시지 목록 (빈 목록 = 통과)."""
    errors = []
    ids = Counter(c.get("id") for c in cases)
    for cid, n in ids.items():
        if n > 1:
            errors.append(f"id 중복: {cid} ({n}회)")
    for c in cases:
        cid = c.get("id", "?")
        for k in REQUIRED:
            if k not in c or c[k] in (None, "", []):
                errors.append(f"{cid}: 필수 필드 누락/빈값 '{k}'")
        if c.get("expected_verdict") not in VERDICTS:
            errors.append(f"{cid}: expected_verdict 허용값 아님 {c.get('expected_verdict')!r}")
        if c.get("difficulty") not in DIFFICULTIES:
            errors.append(f"{cid}: difficulty 허용값 아님 {c.get('difficulty')!r}")
        if c.get("label_basis") not in LABEL_BASES:
            errors.append(f"{cid}: label_basis 허용값 아님 {c.get('label_basis')!r}")
        cats = c.get("expected_categories") or []
        if not isinstance(cats, list):
            errors.append(f"{cid}: expected_categories 는 list 여야 함")
            cats = []
        for cat in cats:
            if cat not in CATEGORIES:
                errors.append(f"{cid}: 허용되지 않은 범주 {cat!r}")
        if "없음" in cats and len(cats) > 1:
            errors.append(f"{cid}: '없음'은 단독으로만 사용")
        if c.get("expected_verdict") == "기재 가능" and cats != ["없음"]:
            errors.append(f"{cid}: 기재 가능 케이스의 범주는 ['없음'] 이어야 함")
        if c.get("expected_verdict") != "기재 가능" and "없음" in cats:
            errors.append(f"{cid}: 위반/확인 케이스에 '없음' 범주 불가")
        if c.get("difficulty") == "clean" and c.get("expected_verdict") != "기재 가능":
            errors.append(f"{cid}: clean 케이스는 기재 가능이어야 함")
        text = c.get("text", "")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{cid}: text 비어 있음")
        if "subject" in c and not isinstance(c["subject"], str):
            errors.append(f"{cid}: subject 는 문자열")
    return errors


# ──────────────────────────────────────────────
# 오프라인 예측 (규칙 + 문체 점검)
# ──────────────────────────────────────────────
def _try_import_rulebook():
    try:
        from core import rulebook  # noqa: WPS433

        if hasattr(rulebook, "apply_exceptions"):
            return rulebook
    except Exception:
        pass
    return None


def _style_kind(msg: str) -> str:
    """style_check 경고 문자열을 형식/권고 종류로 분류한다."""
    if "상투적" in msg or ("종결 어미" in msg and "반복" in msg):
        return "문체"  # 판정에 반영하지 않는 권고
    if "과장" in msg or "허위" in msg:
        return "허위·과장"
    if "맞춤법" in msg:
        return "맞춤법"
    if "띄어" in msg:
        return "띄어쓰기"
    if "영문" in msg:
        return "영문표기"
    if "특수문자" in msg or "물결" in msg:
        return "특수문자"
    if "따옴표" in msg or "괄호" in msg or "문장부호" in msg or "문장 부호" in msg:
        return "문장부호"
    if "개조식" in msg or "공백" in msg or "마침표" in msg:
        return "서식"
    return "기타"


def offline_analyze(case: dict, rulebook) -> dict:
    from core.rules import rule_based_filter, style_check

    text = case["text"]
    subject = case.get("subject", "") or ""
    findings = []
    for f in rule_based_filter(text):
        word = f.get("word", "")
        rid = f.get("rule_id", "")
        excluded = False
        note = ""
        if rulebook is not None:
            start = text.find(word)
            end = start + len(word) if start >= 0 else -1
            try:
                _, note, excluded = rulebook.apply_exceptions(
                    rid, "기재 불가", word, text, start, end, subject
                )
            except Exception:
                excluded = False
        findings.append({"word": word, "rule_id": rid, "excluded": bool(excluded), "exception": note})
    active = [f for f in findings if not f["excluded"]]

    styles = []
    for msg in style_check(text):
        styles.append({"kind": _style_kind(msg), "message": msg})
    verdict_styles = [s for s in styles if s["kind"] != "문체"]

    rule_flag = bool(active)
    style_flag = bool(verdict_styles)
    pred = "확인 필요" if (rule_flag or style_flag) else "기재 가능"
    return {
        "predicted": pred,
        "rule_flag": rule_flag,
        "style_flag": style_flag,
        "rule_findings": findings,
        "style_warnings": styles,
    }


# ──────────────────────────────────────────────
# LLM 예측 (--llm)
# ──────────────────────────────────────────────
def _read_api_key() -> str:
    path = os.path.join(ROOT, ".streamlit", "secrets.toml")
    try:
        import tomllib

        with open(path, "rb") as f:
            return str(tomllib.load(f).get("GEMINI_API_KEY", "") or "")
    except Exception:
        return ""


def _redact(msg: str, secret: str) -> str:
    if secret:
        msg = msg.replace(secret, "***")
    return re.sub(r"AIza[0-9A-Za-z_\-]{20,}", "***", msg)


def llm_analyze(case: dict, offline: dict, api_key: str, use_fewshot: bool = True) -> dict:
    from core.gemini import analyze_with_gemini

    out = dict(offline)
    try:
        items = analyze_with_gemini(case["text"], major="", api_key=api_key, use_fewshot=use_fewshot)
        out["llm_findings"] = [
            {"word": i.get("word", ""), "severity": i.get("severity", ""), "reason": i.get("reason", "")}
            for i in items
        ]
        sev = {i.get("severity", "") for i in items}
        if "위반" in sev:
            out["predicted"] = "기재 불가"
        elif items:
            out["predicted"] = "확인 필요"
        # LLM 무검출이면 오프라인(규칙·형식) 판정 유지
    except Exception as e:  # 키 노출 방지: 메시지 정리
        out["llm_error"] = _redact(f"{type(e).__name__}: {e}", api_key)[:300]
    return out


# ──────────────────────────────────────────────
# 지표
# ──────────────────────────────────────────────
def _binary(pairs: list[tuple[bool, bool]]) -> dict:
    tp = sum(1 for g, p in pairs if g and p)
    fp = sum(1 for g, p in pairs if not g and p)
    fn = sum(1 for g, p in pairs if g and not p)
    tn = sum(1 for g, p in pairs if not g and not p)
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": prec, "recall": rec, "f1": f1}


def _collapse(v: str) -> str:
    return "flag" if v in ("기재 불가", "확인 필요") else "pass"


def compute_metrics(results: list[dict], llm: bool) -> dict:
    by_diff = defaultdict(list)
    for r in results:
        by_diff[r["difficulty"]].append(r)

    def acc(rows, strict):
        if not rows:
            return None
        if strict:
            ok = sum(1 for r in rows if r["expected"] == r["predicted"])
        else:
            ok = sum(1 for r in rows if _collapse(r["expected"]) == _collapse(r["predicted"]))
        return ok / len(rows)

    per_diff = {}
    for d in DIFFICULTIES:
        rows = by_diff.get(d, [])
        per_diff[d] = {
            "n": len(rows),
            "strict_acc": acc(rows, True),
            "flag_acc": acc(rows, False),
        }
    per_basis = {}
    for b in LABEL_BASES:
        rows = [r for r in results if r["label_basis"] == b]
        per_basis[b] = {"n": len(rows), "strict_acc": acc(rows, True), "flag_acc": acc(rows, False)}

    confusion = {e: {p: 0 for p in VERDICTS} for e in VERDICTS}
    for r in results:
        confusion[r["expected"]][r["predicted"]] += 1

    flag_bin = _binary([(_collapse(r["expected"]) == "flag", _collapse(r["predicted"]) == "flag") for r in results])
    content_bin = _binary([
        (bool(set(r["expected_categories"]) & CONTENT_CATEGORIES), r["rule_flag"]) for r in results
    ])
    style_bin = _binary([
        (bool(set(r["expected_categories"]) & FORMAT_CATEGORIES), r["style_flag"]) for r in results
    ])
    return {
        "mode": "llm" if llm else "offline",
        "n": len(results),
        "overall": {"strict_acc": acc(results, True), "flag_acc": acc(results, False)},
        "per_difficulty": per_diff,
        "per_label_basis": per_basis,
        "confusion_expected_x_predicted": confusion,
        "binary_flag": flag_bin,
        "binary_content_rule": content_bin,
        "binary_style": style_bin,
    }


def _fmt(x) -> str:
    return "  -  " if x is None else f"{x:5.1%}"


def print_report(metrics: dict, results: list[dict], llm: bool, rulebook_on: bool, max_mismatch: int):
    print(f"=== 골든셋 평가 ({metrics['mode']}) — {metrics['n']}건, rulebook 예외 {'적용' if rulebook_on else '미적용'} ===")
    if not llm:
        print("※ 오프라인 모드는 '기재 불가'를 예측하지 않음 → strict 정확도는 상한이 낮음. flag 정확도(불가·확인=flag) 위주로 볼 것.")
    o = metrics["overall"]
    print(f"전체: strict {_fmt(o['strict_acc'])} | flag {_fmt(o['flag_acc'])}")
    print("\n[난이도별]  n   strict   flag")
    for d, m in metrics["per_difficulty"].items():
        print(f"  {d:<10}{m['n']:>3}   {_fmt(m['strict_acc'])}   {_fmt(m['flag_acc'])}")
    print("\n[라벨 근거별]  n   strict   flag")
    for b, m in metrics["per_label_basis"].items():
        print(f"  {b:<15}{m['n']:>3}   {_fmt(m['strict_acc'])}   {_fmt(m['flag_acc'])}")
    print("\n[혼동행렬] 행=기대, 열=예측")
    print("            " + "".join(f"{p:>8}" for p in VERDICTS))
    for e, row in metrics["confusion_expected_x_predicted"].items():
        print(f"  {e:<8}" + "".join(f"{row[p]:>10}" for p in VERDICTS))
    for key, label in (("binary_flag", "flag(불가·확인 vs 가능)"),
                       ("binary_content_rule", "내용 규칙 매치 vs 기재금지 범주"),
                       ("binary_style", "형식 경고 vs 맞춤법·서식 범주")):
        b = metrics[key]
        print(f"\n[이진] {label}: P {_fmt(b['precision'])} R {_fmt(b['recall'])} F1 {_fmt(b['f1'])} "
              f"(TP {b['tp']} FP {b['fp']} FN {b['fn']} TN {b['tn']})")

    mism = [r for r in results if _collapse(r["expected"]) != _collapse(r["predicted"])]
    print(f"\n[flag 불일치 {len(mism)}건] (최대 {max_mismatch}건 표시)")
    for r in mism[:max_mismatch]:
        hits = [f["word"] for f in r["rule_findings"] if not f["excluded"]]
        sty = [s["kind"] for s in r["style_warnings"] if s["kind"] != "문체"]
        print(f"- {r['id']} [{r['difficulty']}/{r['label_basis']}] 기대={r['expected']} 예측={r['predicted']}")
        print(f"    문장: {r['text']}")
        print(f"    검출: 규칙={hits or '-'} 형식={sty or '-'}")
        print(f"    근거: {r['rationale']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="세특 점검 골든셋 평가")
    ap.add_argument("--cases", default=DEFAULT_CASES)
    ap.add_argument("--llm", action="store_true", help="Gemini 심사 반영 (비용 발생)")
    ap.add_argument("--limit", type=int, default=0, help="평가할 최대 케이스 수 (0=전체)")
    ap.add_argument("--difficulty", choices=DIFFICULTIES, action="append",
                    help="난이도 필터 (여러 번 지정 가능)")
    ap.add_argument("--seed", type=int, default=None, help="지정 시 케이스 순서를 섞음 (limit 표본 추출용)")
    ap.add_argument("--out", default="", help="결과 JSON 저장 경로")
    ap.add_argument("--max-mismatch", type=int, default=40)
    ap.add_argument("--no-fewshot", action="store_true", help="--llm 시 few-shot·조항 발췌 주입 끄기(비교용)")
    ap.add_argument("--validate-only", action="store_true", help="스키마 검증만 실행")
    args = ap.parse_args(argv)

    cases = load_cases(args.cases)
    errors = validate_cases(cases)
    if errors:
        print(f"[schema] 검증 실패 {len(errors)}건:")
        for e in errors:
            print("  -", e)
        return 1
    dist = Counter(c["difficulty"] for c in cases)
    basis = Counter(c["label_basis"] for c in cases)
    print(f"[schema] OK — {len(cases)}건, 난이도 {dict(dist)}, 라벨근거 {dict(basis)}")
    if args.validate_only:
        return 0

    if args.difficulty:
        cases = [c for c in cases if c["difficulty"] in args.difficulty]
    if args.seed is not None:
        random.Random(args.seed).shuffle(cases)
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]

    api_key = ""
    if args.llm:
        api_key = _read_api_key()
        if not api_key:
            print("[llm] .streamlit/secrets.toml 에 GEMINI_API_KEY 없음 → 중단")
            return 1

    rulebook = _try_import_rulebook()
    results = []
    for c in cases:
        pred = offline_analyze(c, rulebook)
        if args.llm:
            pred = llm_analyze(c, pred, api_key, use_fewshot=not args.no_fewshot)
        results.append({
            "id": c["id"],
            "text": c["text"],
            "subject": c.get("subject", ""),
            "difficulty": c["difficulty"],
            "label_basis": c["label_basis"],
            "expected": c["expected_verdict"],
            "expected_categories": c["expected_categories"],
            "rationale": c["rationale"],
            **pred,
        })

    metrics = compute_metrics(results, args.llm)
    print_report(metrics, results, args.llm, rulebook is not None, args.max_mismatch)

    if args.out:
        mism = [r for r in results if r["expected"] != r["predicted"]]
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"metrics": metrics, "mismatches_strict": mism, "results": results},
                      f, ensure_ascii=False, indent=2)
        print(f"\n결과 저장: {args.out}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
