# -*- coding: utf-8 -*-
"""규칙 기반 검출 / 문체 점검 / 유사도 검사 (API 불필요, 순수 로직)."""

import difflib
import re
from collections import Counter

# 점검 기준 학년도 표시 (2027학년도 규정은 미반영)
RULES_BASIS_LABEL = "2026학년도 기재요령(교육부훈령 제555호) 기준"

# NEIS 항목별 입력 제한 (공백 포함 글자 수 기준)
NEIS_LIMITS = {
    "세특 (과목별·개인별, 500자)": 500,
    "자율·자치활동 (500자)": 500,
    "동아리활동 (500자)": 500,
    "진로활동 (700자)": 700,
    "행동특성 및 종합의견 (500자)": 500,
    "제한 없음": 0,
}

# 학생 간 유사도 경고 기준 (0~1)
SIMILARITY_THRESHOLD = 0.55


def neis_bytes(text: str) -> int:
    """NEIS 기준 바이트 수 (한글 등 멀티바이트 문자 3바이트, 그 외 1바이트)."""
    return sum(3 if ord(ch) > 127 else 1 for ch in text)


# ──────────────────────────────────────────────
# 규칙 기반 고속 필터링
# ──────────────────────────────────────────────
RULE_PATTERNS = [
    # 공인어학성적 / 모의고사 성적 패턴 (예: TOEIC 900점, 국어 1등급, 백분위 98%)
    (
        r"[A-Za-z가-힣]+\s*\d+\s*(?:점|급|등급|%)",
        "성적/점수 표기 의심 (공인어학성적·모의고사 성적 기재 금지)",
        "기재요령: 공인어학시험·교외 성적·석차 기재 불가 (2026 기재요령 p.18-19)",
        "score",
    ),
    # 대표적인 공인어학시험 명칭
    (
        r"(?:TOEIC|TOEFL|TEPS|IELTS|HSK|JLPT|JPT|DELE|DELF|G-?TELP|OPIc|토익|토플|텝스|아이엘츠)",
        "공인어학시험 명칭 (기재 금지)",
        "기재요령: 공인어학시험 성적 기재 불가 (2026 기재요령 p.18-19)",
        "language_test",
    ),
    # 교내·외 인증시험 (기재요령 '라'항: 공인어학시험보다 넓은 범주)
    # '매경TEST'/'TESAT'은 re.IGNORECASE를 받지만, 앞에 한글이 붙거나(매경) 5글자
    # 라틴 토큰이라 평범한 영단어의 부분문자열로 등장할 위험이 없어 (?-i:) 스코프를
    # 씌우지 않는다 (WHO/UN/MOS 같은 2~3글자 약어와 다름).
    (
        r"(?:인증시험|한자능력검정시험|매경TEST|TESAT|KBS한국어능력시험)",
        "교내외 인증시험 참여/성적 의심",
        "기재요령: 교내·외 인증시험 참여 사실이나 성적 기재 불가 (2026 기재요령 p.18-19)",
        "certification_test",
    ),
    # 모의고사 언급
    (
        r"(?:전국연합학력평가|모의고사|모의평가|학력평가)\s*(?:성적|점수|등급|결과)?",
        "모의고사 관련 표현 의심",
        "기재요령: 교외 성적·석차 기재 불가 (2026 기재요령 p.18-19)",
        "mock_exam",
    ),
    # 학교 명칭 (재학 중인 학교명 기재 금지 — 의심 수준으로 표시)
    (
        r"[가-힣]{2,}(?:여자고등학교|고등학교|여고|고교)",
        "학교 명칭 의심 (재학 중인 학교명 기재 금지)",
        "기재요령: 재학 학교명 등 특정 가능 정보 기재 불가 (2026 기재요령 p.18-19)",
        "school_name",
    ),
    # 주요 대학명
    (
        r"(?:서울대|연세대|고려대|성균관대|한양대|서강대|중앙대|경희대|이화여대|한국외대|"
        r"서울시립대|건국대|동국대|홍익대|카이스트|KAIST|포스텍|POSTECH|유니스트|UNIST|지스트|GIST)(?:학교)?",
        "특정 대학명 의심 (기재 금지)",
        "기재요령: 특정 대학·기관명 기재 불가 (2026 기재요령 p.18-19)",
        "university_name",
    ),
    # 상업적 명칭/브랜드
    (
        # '줌'(Zoom 한글 표기)은 '보여 줌' 같은 개조식 종결과 겹쳐 오탐이 잦아 규칙에서 제외.
        # Gemini LLM 단계(core/gemini.py SYSTEM_PROMPT)가 이미 '줌'을 브랜드 예시로 명시해 판단함.
        r"(?:구글|유튜브|네이버|카카오톡?|인스타그램|페이스북|틱톡|챗GPT|ChatGPT|GPT|제미나이|"
        r"Zoom|넷플릭스|아이폰|아이패드|갤럭시|파워포인트|엑셀|"
        r"오픈AI|OpenAI|마이크로소프트|Microsoft|패들렛|Padlet|삼성(?!전자고|디지털))",
        "상업적 명칭/브랜드 의심",
        "기재요령: 상업적 명칭 기재 불가 (2026 기재요령 p.18-19)",
        "brand",
    ),
    # 국제/공공기관명 (교육관련기관 외 기관명 기재 금지)
    # 라틴 약어는 (?-i:...)로 대소문자 구분을 되살려 'who', 'un' 같은 영단어 오탐을 막는다.
    (
        r"(?:유네스코|통계청|세계보건기구|세계경제포럼|"
        r"(?-i:(?<![A-Za-z])(?:UNESCO|OECD|UN|IMF|WHO)(?![A-Za-z])))",
        "국제/공공기관명 의심 (교육관련기관 외 기관명 기재 금지)",
        "기재요령: 교육관련기관 외 기관명 기재 불가 (2026 기재요령 p.18-19)",
        "institution",
    ),
    # 자격증 명칭 (자격증 취득상황 이외 항목에는 기재 금지)
    # 'MOS'는 3글자 라틴 약어라 IGNORECASE 상태로 두면 'almost', 'atmosphere' 등
    # 평범한 영단어 안의 'mos'를 잡는다 → ITQ/TOPCIT/GTQ와 같은 (?-i:) 블록에 넣는다.
    (
        r"(?:컴퓨터활용능력|정보처리기사|정보처리기능사|컴퓨터그래픽스운용기능사|"
        r"워드프로세서|한국사능력검정시험|한국어능력시험|"
        r"(?-i:(?<![A-Za-z])(?:ITQ|TOPCIT|GTQ|MOS)(?![A-Za-z])))",
        "자격증 명칭 의심 (자격증 취득상황 이외 항목 기재 금지)",
        "기재요령: 자격증 명칭·취득 사실은 해당 항목 외 기재 불가 (2026 기재요령 p.18-19)",
        "license",
    ),
    # 대회 참여·수상 실적
    (
        r"[가-힣A-Za-z]*(?:경시대회|올림피아드|콘테스트|공모전)(?:에서)?\s*"
        r"(?:수상|입상|우승|최우수상|대상|금상|은상|동상)?",
        "대회 참여·수상 표현 의심 (교외상 등 수상실적 기재 금지)",
        "기재요령: 교내외 대회 참여·성적·수상실적 기재 불가 (2026 기재요령 p.18-19, p.118·149)",
        "competition",
    ),
    # 교외상 문서 명칭 (기재요령 '다'항: 교외 기관·단체 수여 표창장·감사장·공로상 등)
    (
        r"(?:표창장|감사장|공로상)",
        "교외상 문서 명칭 의심 (교외상 기재 금지)",
        "기재요령: 교외 기관·단체가 수여한 표창장·감사장·공로상 등 기재 불가 (2026 기재요령 p.18-19)",
        "outside_award",
    ),
    # 논문 투고·등재 / 도서 출간
    (
        r"(?:논문(?:을)?\s*(?:투고|등재|게재|발표)|학회지에|도서\s*출간|출판사에서\s*출간)",
        "논문 등재·도서출간 의심",
        "기재요령: 논문 투고/등재, 도서출간 사실 기재 불가 (2026 기재요령 p.18-19)",
        "paper_book",
    ),
    # 지식재산권 출원·등록
    (
        r"(?:특허(?:출원|등록)|실용신안|상표권|디자인권|지식재산권)",
        "지식재산권 출원/등록 의심",
        "기재요령: 지식재산권 출원 또는 등록 사실 기재 불가 (2026 기재요령 p.18-19)",
        "ip_right",
    ),
    # 해외 활동 실적
    (
        r"(?:해외\s*(?:봉사|어학연수|캠프|탐방)|어학연수|교환학생(?:으로)?\s*(?:파견|선발|참여))",
        "해외 활동 실적 의심",
        "기재요령: 어학연수·해외봉사 등 해외 활동실적 기재 불가 (2026 기재요령 p.18-19)",
        "overseas",
    ),
    # 장학생·장학금
    (
        r"(?:장학생(?:으로)?\s*(?:선발|선정)|장학금(?:을)?\s*(?:수혜|받))",
        "장학생/장학금 관련 표현 의심",
        "기재요령: 장학생·장학금 관련 내용 기재 불가 (2026 기재요령 p.18-19)",
        "scholarship",
    ),
    # 온라인 강좌(MOOC)·방과후학교 — 세특 입력 불가 항목
    (
        r"(?:(?-i:(?<![A-Za-z])(?:K-MOOC|KOCW|MOOC)(?![A-Za-z]))|방과후\s*학교)",
        "MOOC/방과후학교 활동 의심 (세특 입력 불가 항목)",
        "기재요령: K-MOOC·MOOC·KOCW, 방과후학교 활동은 세특에 입력 불가 (2026 기재요령 p.118·149)",
        "mooc_afterschool",
    ),
    # 소논문(자율탐구활동 연구보고서)
    (
        r"소논문",
        "소논문 관련 표현 (세특 입력 불가, 지정 6개 과목 예외 존재)",
        "기재요령: 자율탐구활동 연구보고서(소논문) 실적은 기재 불가 (2026 기재요령 p.118·149)",
        "thesis",
    ),
    # 청소년단체 활동 (기재요령 p.78: 정규교육과정 이외의 청소년단체 활동은
    # 단체명·특기사항 모두 기재 불가) — 금지 대상은 '구체적 단체명'이므로
    # 'RCY'의 정식 명칭인 '청소년적십자'만 넣고, 봉사·헌혈 맥락에서 흔히 쓰이는
    # 맨 '적십자'는 과탐지가 되어 제외한다.
    # 'RCY'는 3글자 라틴 약어라 IGNORECASE면 'mercy' 같은 영단어의 'rcy'를 잡으므로
    # UN/WHO/MOS와 동일하게 (?-i:) + 알파벳 경계로 대문자 표기만 검출한다.
    (
        r"(?:보이스카우트|걸스카우트|청소년적십자|우주소년단|해양소년단|"
        r"(?-i:(?<![A-Za-z])RCY(?![A-Za-z])))",
        "청소년단체 활동명 의심 (정규교육과정 외 활동은 단체명 기재 금지)",
        "기재요령: 정규교육과정 이외의 청소년단체 활동은 단체명·특기사항 모두 기재 불가",
        "youth_org",
    ),
    # 대학명 일반 패턴 (고정 목록에 없는 'OO대학교/OO대학원'까지 포착)
    # 앞에 공백 없이 한글이 2~8자 붙은 경우만 잡아 '진학할 대학교' 같은 일반어를 피한다.
    (
        r"[가-힣]{2,8}(?:대학교|대학원)",
        "특정 대학명 의심 (일반 패턴)",
        "기재요령: 구체적인 특정 대학명 기재 불가 (2026 기재요령 p.18-19)",
        "university_name",
    ),
]

# ──────────────────────────────────────────────
# 2026-11-24 일반화 보강 패턴 (띄어쓰기·표기 변형, 우회 표현)
# 특정 문장 리터럴이 아니라 어휘군·문형으로 쓴다. 정밀도 우선 — 오탐 위험이 큰
# 패턴은 아래 _PATTERN_VALIDATORS에서 매치 단위로 한 번 더 거른다.
# ──────────────────────────────────────────────
_B_COMMON = "(2026 기재요령 p.18-19)"
_B_SETEUK = "(2026 기재요령 p.118·149)"

# 기관명 뒤에 올 수 있는 경계(조사·문장부호·일부 동반 명사)
_ORG_FOLLOW = (
    r"(?=$|[\s,.)'\"’”·]|의|에|와|과|를|을|은|는|이|가|로|으로|에서|견학|방문|주최|주관|소속|부설)"
)
_PARENT = (
    r"(?:부모님?|아버지|어머니|아빠|엄마|부친|모친|외삼촌|삼촌|이모|고모|큰아버지|작은아버지|"
    r"할아버지|할머니|친척)"
)
_OCCUPATION = (
    r"(?:한의사|치과의사|수의사|의사|약사|간호사|변호사|판사|검사|법조인|회계사|세무사|변리사|"
    r"교수|교사|기자|경찰관|경찰|소방관|군인|장교|공무원|외교관|목사|사업가|기업가|사장|대표|임원|"
    r"연구원|엔지니어|건축가|기술자|농부|어부|자영업자|국회의원|조종사|파일럿|은행원|회사원|"
    r"작가|디자이너|요리사|셰프|프로그래머|개발자|과학자|공학자)"
)
_SURNAMES = (
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진나지"
    "엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경사부복태"
)

EXTRA_RULE_PATTERNS = [
    # a. 지식재산권: 띄어쓰기·조사 변형('특허 출원', '특허를 받아', '특허 취득')
    (
        r"특허(?:권)?(?:를|을)?\s*(?:출원|등록|취득|획득)|특허(?:권)?(?:를|을)\s*(?:받아|받았|받음|받고|따)"
        r"|상표\s*(?:출원|등록)|디자인\s*(?:출원|등록)|지식\s*재산권|지식\s*재산\s*(?:출원|등록)",
        "지식재산권 출원/등록 의심 (표기 변형)",
        f"기재요령: 지식재산권 출원 또는 등록 사실 기재 불가 {_B_COMMON}",
        "ip_right",
    ),
    # b. 줄임 대학명: 'OO공대·OO의대·OO과기대·OO대학' 등 접미 변형
    (
        r"(?<![가-힣])[가-힣]{2,6}(?:과학기술대|과기대|공과대|공대|의과대|의대|교육대|교대(?!로)|여자대|여대|"
        r"외국어대|외대|예술대|예대|체육대|체대|과학대(?![회학]))(?:학)?"
        r"(?=$|[\s,.)'\"’”]|의|에|와|과|를|을|은|는|이|가|로|생|학생|교수|출신|에서|부설|캠퍼스)",
        "특정 대학명 의심 (줄임 표기)",
        f"기재요령: 구체적인 특정 대학명 기재 불가(줄임 표기 포함) {_B_COMMON}",
        "university_name",
    ),
    (
        r"(?<![가-힣])[가-힣]{2,5}대학(?=[\s의에과와을를은는이가로]|$)",
        "특정 대학명 의심 ('OO대학' 표기)",
        f"기재요령: 구체적인 특정 대학명 기재 불가 {_B_COMMON}",
        "university_name",
    ),
    (
        r"(?<![가-힣])[가-힣]{2,5}대\s?(?:[가-힣]{1,8}(?:학과|학부)|교수|총장|캠퍼스|연구실|입학처)",
        "특정 대학명 의심 ('OO대 + 학과·교수' 문맥)",
        f"기재요령: 구체적인 특정 대학명 기재 불가(줄임 표기 포함) {_B_COMMON}",
        "university_name",
    ),
    (
        r"(?-i:(?<![A-Za-z])[A-Z]대(?:학교?)?)(?=$|[\s,.]|의|에|와|과|를|을|생|학생|교수)",
        "대학명 이니셜 표기 의심 (특정 가능 여부 확인)",
        f"기재요령: 구체적인 특정 대학명 기재 불가 {_B_COMMON}",
        "university_name",
    ),
    # b. 기관명 접미(연구원·연구소·재단·공단·진흥원·협회·학회·위원회 등).
    # 교육관련기관(교육청·교육지원청·교육연수원 등)은 검증 함수와 rulebook 예외에서 뺀다.
    (
        r"(?<![가-힣])[가-힣]{2,12}(?:연구원|연구소|연구센터|과학원|진흥원|재단|공단|협회|학회|위원회)"
        + _ORG_FOLLOW,
        "기관명 의심 (교육관련기관 외 기관명 기재 금지)",
        f"기재요령: 교육관련기관 외 기관명(기구·단체·조직) 기재 불가 {_B_COMMON}",
        "institution",
    ),
    # c. 교외상 변형: 감사패·공로패, 표창·포상, 교외 기관장 수여 맥락
    (
        r"감사패|공로패|(?<![가-힣])표창(?!장)(?=[을를이은]|\s*(?:을|를)?\s*(?:받|수상|수여))"
        r"|포상(?:을|를)?\s*(?:받|수여)",
        "교외상(표창·포상·감사패 등) 의심",
        f"기재요령: 교외 기관·단체(장)가 수여한 상(표창·감사패 포함) 기재 불가 {_B_COMMON}",
        "outside_award",
    ),
    (
        r"[가-힣]{0,8}(?:구청장|청장|시장|군수|도지사|장관|의장|이사장|총재|협회장|기관장)"
        r"(?:으로부터|로부터|에게서|명의의?|이\s*수여한|가\s*수여한)\s*[가-힣\s]{0,10}?(?:상장|상|표창|감사패|공로패)",
        "교외 기관장 수여 상 의심",
        f"기재요령: 교외 기관·단체(장)가 수여한 상 기재 불가 {_B_COMMON}",
        "outside_award",
    ),
    # d. 장학 변형: 장학 혜택·학비/등록금 지원
    (
        r"장학(?:생|금|\s*혜택|\s*증서|\s*지원)(?:을|를|으로|에|의)?\s*(?:[가-힣]{0,4}\s)?"
        r"(?:선발|선정|수혜|받|지급|수여|혜택|대상|추천)"
        r"|(?:학비|등록금|교육비|수업료)(?:를|을)?\s*(?:전액\s*)?(?:지원|감면|면제)(?:을|를)?\s*(?:받|대상|혜택)"
        r"|장학\s*재단",
        "장학 혜택·학비 지원 관련 표현 의심",
        f"기재요령: 장학생·장학금 관련 내용 기재 불가 {_B_COMMON}",
        "scholarship",
    ),
    # d. 출간 변형: 집필·출판·서점 유통·인세, 학술지·학회 발표
    (
        r"(?:집필|저술|직접\s*쓴|자신이\s*쓴|직접\s*지은|창작한)[가-힣\s]{0,15}?(?:출간|출판|발간|발행|펴내|펴냄|펴낸)"
        r"|(?:출간|출판|발간)(?:되어|돼|되고|됨)[가-힣\s,]{0,12}?(?:서점|판매|유통|시중)"
        r"|(?<![가-힣])인세(?:를|로|가|\s*수익|\s*수입)"
        r"|서점에\s*(?:유통|판매|입고|진열|출시)"
        r"|(?-i:(?<![A-Za-z])ISBN(?![A-Za-z]))"
        r"|(?:책|도서|저서|교양서|소설|시집|에세이)(?:을|를)?\s*(?:출간|출판|발간|펴내|펴냄)(?:함|했|하여|해|하고|냄|내)?"
        r"|학술지에\s*(?:게재|등재|투고)(?:함|됨|되어|하여|했)"
        r"|학회에서\s*(?:직접\s*)?(?:구두\s*|포스터\s*)?발표(?:함|하여|해|했)",
        "논문 게재·도서 출간 의심 (표기 변형)",
        f"기재요령: 논문 투고/등재·학회 발표, 도서출간 사실 기재 불가 {_B_COMMON}",
        "paper_book",
    ),
    # d. 자격 취득 우회 서술
    (
        r"국가(?:기술|공인|전문)?\s?자격(?:증)?|자격증(?:을|를)?\s*(?:취득|획득|따|땄|준비|보유)"
        r"|자격(?:을|를)?\s*(?:취득|획득)",
        "자격 취득 사실 의심 (자격증 취득상황 이외 항목 기재 금지)",
        f"기재요령: 자격증 명칭·취득 사실은 해당 항목 외 기재 불가 {_B_COMMON}",
        "license",
    ),
    # d. 급수·검정·실기 시험 (교내·외 인증시험 참여 사실)
    (
        r"급수\s*시험|[가-힣]{1,6}\s?급수(?:를|을)?\s*(?:취득|따|획득|합격)"
        r"|(?<![0-9])[0-9]\s*급\s*(?:시험)?(?:에|을|를)?\s*(?:합격|취득|응시)"
        r"|(?:능력|실력)\s*검정|검정\s*시험|(?:실기|필기)\s*시험(?:에|을|를)?\s*(?:합격|응시)|(?:실기|필기)\s*합격",
        "급수·검정 시험 참여/합격 의심",
        f"기재요령: 교내·외 인증시험 참여 사실이나 성적 기재 불가 {_B_COMMON}",
        "certification_test",
    ),
    # d. 강사명: 초빙·외부 강사 + 이름 + 호칭, '성명 + 작가님·강사님·교수님'
    (
        r"(?:초빙|초청|외부|특강|명예)\s*(?:강사|작가|교수|강연자|연사)\s*[가-힣]{2,4}\s?"
        r"(?:선생님|강사님|작가님|교수님|박사님|님|씨)",
        "특정 강사명 의심 (초빙·외부 강사 이름)",
        f"기재요령: 학생이 참여한 강의의 특정 강사명 기재 불가 {_B_COMMON}",
        "lecturer",
    ),
    (
        r"(?<![가-힣])[" + _SURNAMES + r"][가-힣]{1,2}\s?(?:작가|강사|교수|박사)님",
        "특정 강사명 의심 (성명 + 호칭)",
        f"기재요령: 학생이 참여한 강의의 특정 강사명 기재 불가 {_B_COMMON}",
        "lecturer",
    ),
    # d. 학교 축제명: 따옴표 안 고유 명칭 + 축제·한마당 등
    (
        r"['\"‘“][가-힣A-Za-z0-9 ]{1,12}(?:축제|한마당|페스티벌|문화제|예술제|가요제|체육대회)['\"’”]"
        r"|['\"‘“][가-힣]{1,5}제['\"’”](?=[가-힣\s]{0,12}(?:축제|행사|부스|공연|무대))",
        "학교 축제명 의심 (재학 학교 식별 정보)",
        f"기재요령: 학교 축제명 등 재학 고교를 알 수 있는 내용 기재 불가 {_B_COMMON}",
        "school_name",
    ),
    # d. 대회 변형: 대회·경연·해커톤, 본선·예선·결선 진출, 챌린지 참가, 우수 팀 선정, 상 이름
    (
        r"[가-힣A-Za-z]*(?:대회(?!의실)|경연|해커톤)",
        "대회·경연 참여 표현 의심",
        f"기재요령: 교내외 대회 참여·성적·수상실적 기재 불가 {_B_COMMON}",
        "competition",
    ),
    (
        r"(?:본선|예선|결선|준결승|결승(?![점선]))(?:에|까지|으로|을|를|에서)?\s*(?:진출|참가|통과|출전|탈락|올라)"
        r"|챌린지(?:에|에서|의)?\s*(?:참가|출전|진출|본선|예선|결선|우수|입상|수상|우승|최우수)"
        r"|(?:최우수|우수|우승|준우승|수상)\s*팀(?:으로|에)?\s*(?:선정|선발|뽑)",
        "대회 진출·입상 문맥 의심 (행사명으로 바꾼 편법 기재 포함)",
        f"기재요령: 대회명을 단순행사로 바꾼 편법 기재 포함, 대회 참여·수상 기재 불가 {_B_COMMON}",
        "competition",
    ),
    (
        r"(?:최우수상|우수상|장려상|금상|은상|특별상|입선|인기상|봉사상|모범상|선행상|효행상|노력상|아이디어상)"
        r"(?:을|를|으로)?\s*(?:수상|받|차지)|(?:대상|동상)(?:을)?\s*수상",
        "수상 실적 표현 의심",
        f"기재요령: 교내외 대회 수상실적은 수상경력 외 기재 불가 {_B_COMMON}",
        "competition",
    ),
    # d. 부모·친인척 직업 암시
    (
        _OCCUPATION
        + r"(?:이신|인|이셨던|이었던|이자|(?:으로|로)\s*(?:일하(?:시)?는|근무하(?:시)?는|재직\s*중인))\s*"
        + _PARENT,
        "부모(친인척) 직업명 의심 (사회·경제적 지위 암시)",
        f"기재요령: 부모(친인척 포함)의 직종·직업·직장·직위명 등 사회·경제적 지위 암시 기재 불가 {_B_COMMON}",
        "parent_status",
    ),
    (
        r"[가-힣]{1,6}(?:계|업계|업종|분야|회사|기업|병원|은행|공장|방송국|신문사|법원|약국|사무소)"
        r"(?:에|에서)\s*(?:종사|근무|재직|일하|다니)[가-힣]*\s*" + _PARENT
        + r"|" + _PARENT + r"(?:께서|가|이|는|은)?\s*(?:[가-힣]{1,6}\s)?(?:운영|경영)(?:하시|하)는\s*[가-힣]{1,6}"
        r"|" + _PARENT + r"(?:의|께서)\s*(?:회사|가게|식당|병원|매장|공장|농장|사업장|사무실)"
        r"|가업(?:을|를)?\s*(?:이어|잇|물려|계승)",
        "부모(친인척) 직종·직장 암시 의심",
        f"기재요령: 부모(친인척 포함)의 직종·직업·직장·직위명 등 사회·경제적 지위 암시 기재 불가 {_B_COMMON}",
        "parent_status",
    ),
    # d. 해외활동 변형: 해외·외국·현지 + 체류·봉사·연수·교환, 국가명 + 봉사·연수
    (
        r"(?:해외|외국|국외)\s*(?:[가-힣]{1,4}\s)?(?:봉사|연수|캠프|홈스테이|인턴십|교환)"
        r"|(?:해외|외국|국외)\s*(?:체류|생활|거주|탐방)|현지\s*(?:봉사|연수|체류|홈스테이)",
        "해외 활동 실적 의심 (표기 변형)",
        f"기재요령: 어학연수·해외봉사 등 해외 활동실적 및 관련 내용 기재 불가 {_B_COMMON}",
        "overseas",
    ),
    (
        r"(?:필리핀|캄보디아|베트남|라오스|네팔|몽골|인도네시아|태국|캐나다|미국|영국|호주|뉴질랜드|"
        r"일본|중국|싱가포르|동남아시아|동남아|아프리카|유럽)(?:의?\s*[가-힣]{1,6}?){0,2}(?:에서|으로|에)\s*"
        r"[가-힣\s]{0,12}?(?:봉사|어학연수|홈스테이|교육\s*나눔|나눔\s*활동|단기\s*연수)",
        "해외 활동 실적 의심 (국가명 + 봉사·연수)",
        f"기재요령: 어학연수·해외봉사 등 해외 활동실적 및 관련 내용 기재 불가 {_B_COMMON}",
        "overseas",
    ),
    # MOOC·방과후학교 우회 서술
    (
        r"온라인\s*공개\s*강좌|공개\s*온라인\s*강좌|방과\s+후\s*(?:학교|수업|강좌|프로그램|교실)"
        r"|(?:정규\s*)?수업이?\s*(?:끝난|마친)\s*(?:뒤|후)[가-힣\s]{0,12}?(?:강좌|프로그램)",
        "MOOC/방과후학교 활동 의심 (우회 표기)",
        f"기재요령: K-MOOC·MOOC·KOCW, 방과후학교 활동은 세특에 입력 불가 {_B_SETEUK}",
        "mooc_afterschool",
    ),
    # 연구보고서(소논문) 작성 — 허용 6과목은 rulebook 예외로 '확인 필요'
    (
        r"연구\s?보고서(?:를|을)?\s*(?:작성|제출|완성|집필)",
        "연구보고서(소논문) 작성 표현 (세특 입력 불가, 지정 6개 과목 예외 존재)",
        f"기재요령: 자율탐구활동 연구보고서(소논문) 관련 사항 기재 불가 {_B_SETEUK}",
        "thesis",
    ),
    # 영재·발명교육 (해당 기록사항에만, 기관명 불가) — 쪽수 미확인 → 조항명만
    (
        r"영재\s?교육원|영재\s?학급|발명\s?교육\s?센터|발명\s?교실",
        "영재·발명교육 관련 표현 (영재·발명교육 기록사항에만 기재)",
        "기재요령: 영재·발명교육 수료 사항은 '영재·발명교육 기록사항'에만 기재(기관명 불가)",
        "gifted_edu",
    ),
]
_BASE_PATTERNS = frozenset(p for p, *_ in RULE_PATTERNS)
RULE_PATTERNS.extend(EXTRA_RULE_PATTERNS)

# ── 매치 단위 검증(오탐 제거) ──
# 시험 성적 맥락 어휘: '%' 표기는 이 어휘가 가까이 있을 때만 성적으로 본다
# (실험 오차·농도·비율 같은 측정값 백분율은 성적이 아님).
_SCORE_CONTEXT_RE = re.compile(r"백분위|성적|점수|등급|석차|시험|모의|학력평가|상위|원점수|표준점수")
_SCORE_PATTERN = RULE_PATTERNS[0][0]

# '-대'로 끝나는 일반어(대학명 줄임 아님)
_COMMON_DAE_WORDS = frozenset(
    "현대 근대 고대 시대 세대 최대 확대 극대 절대 상대 반대 기대 무대 지대 열대 온대 한대 연대 부대 군대 "
    "거대 광대 관대 등대 중대 강대 학대 환대 접대 교대 과대 초대 대대 당대 후대 역대 침대 장대 막대 성대 "
    "본대 분대 명문 지방 국립 사립 공립 전문 신설 인근 지역 외국 해외 유명 희망 목표 진학 일반 종합 원격 "
    "사이버 여자 상위권 하위권 지망 다른 여러 모든 해당 이공 인문".split()
)
_DAE_SUFFIX_RE = re.compile(
    r"(?:과학기술대|과기대|공과대|공대|의과대|의대|교육대|교대|여자대|여대|외국어대|외대|예술대|예대|"
    r"체육대|체대|과학대)(?:학)?$|대학$|대$"
)
# 기관 접미 앞의 일반어(고유 기관명이 아님)
_GENERIC_ORG_PREFIX = frozenset(
    "국립 관련 해당 지역 민간 공공 정부 국가 전문 여러 다양한 각종 연구 과학 의료 교육 국내 해외 외국 "
    "정부출연 공공기관 비영리 사설 산하 협력 주요 유관 학술".split()
)
# '연구원'이 사람(직함)인 경우의 앞말
_PERSON_RESEARCHER_RE = re.compile(r"(?:책임|선임|수석|전임|객원|박사|인턴|현직|주임|보조|학생|연구)$")
# 학교 안 자치 조직(위원회) 앞말
_SCHOOL_COMMITTEE_RE = re.compile(
    r"학생|학급|자치|선거|학교|동아리|축제|준비|기획|운영|편집|행사|교내|모둠|학년|대의원|급식|도서|반$"
)
_EDU_ORG_RE = re.compile(
    r"(?:교육부|[가-힣]*교육청|[가-힣]*교육지원청|[가-힣]*교육연구정보원|[가-힣]*교육연수원|"
    r"[가-힣]*교육과학연구원|[가-힣]*학생교육원|한국교육과정평가원|한국교육개발원|한국교육학술정보원)"
)
# 강사명 패턴의 '성씨로 시작하는 일반어'(이번·유명·전문 …) 및 관형형 어미
_LECTURER_NOT_NAME = frozenset(
    "이번 이분 이날 유명 전문 현직 원로 신진 지역 공동 우리 모든 정규 정식 최근 한국 서울 강연 강의 "
    "담당 주임 초청 초빙 외부 명예 대학 고등 해당 여러 다른 방문 객원 겸임 전임 유능 연구".split()
)
_LECTURER_BAD_LAST = frozenset("은는을를의된던운할한인적")
_FESTIVAL_NOT_NAME_RE = re.compile(r"(?:문제|경제|과제|주제|규제|체제|통제|해제|억제|전제|명제|세제|의제|편제|학제)$")
_COMMON_COUNT_WORDS = frozenset("세계 관계 단계 경계 체계 한계 생태계 학계 업계 각계".split())


def _strip_tail_particles(word: str) -> str:
    return re.sub(r"(?:으로|에서|의|에|와|과|를|을|은|는|이|가|로)$", "", word)


def _valid_score(text: str, m: re.Match) -> bool:
    if not m.group().rstrip().endswith("%"):
        return True
    window = text[max(0, m.start() - 12) : m.end()]
    return bool(_SCORE_CONTEXT_RE.search(window))


def _valid_university(text: str, m: re.Match) -> bool:
    word = re.sub(r"\s.*$", "", m.group())  # 'OO대 OO학과'에서 대학명 토큰만
    prefix = _DAE_SUFFIX_RE.sub("", word)
    return prefix not in _COMMON_DAE_WORDS and word not in _COMMON_DAE_WORDS


def _valid_institution(text: str, m: re.Match) -> bool:
    word = m.group()
    if _EDU_ORG_RE.fullmatch(word):
        return False
    suffix_m = re.search(r"(?:연구원|연구소|연구센터|과학원|진흥원|재단|공단|협회|학회|위원회)$", word)
    prefix = word[: suffix_m.start()] if suffix_m else word
    if prefix in _GENERIC_ORG_PREFIX:
        return False
    if word.endswith("연구원") and _PERSON_RESEARCHER_RE.search(prefix):
        return False
    if word.endswith("위원회") and _SCHOOL_COMMITTEE_RE.search(prefix):
        return False
    return True


def _valid_lecturer_name(text: str, m: re.Match) -> bool:
    name = re.match(r"[가-힣]+", m.group()).group()
    return name not in _LECTURER_NOT_NAME and name[-1] not in _LECTURER_BAD_LAST


def _valid_festival(text: str, m: re.Match) -> bool:
    inner = m.group().strip("'\"‘’“”")
    return not _FESTIVAL_NOT_NAME_RE.search(inner)


def _valid_parent_domain(text: str, m: re.Match) -> bool:
    head = re.match(r"[가-힣]+", m.group())
    if head and head.group() in _COMMON_COUNT_WORDS:
        return False
    return True


_PATTERN_VALIDATORS = {
    _SCORE_PATTERN: _valid_score,
    EXTRA_RULE_PATTERNS[1][0]: _valid_university,
    EXTRA_RULE_PATTERNS[2][0]: _valid_university,
    EXTRA_RULE_PATTERNS[3][0]: _valid_university,
    EXTRA_RULE_PATTERNS[5][0]: _valid_institution,
    EXTRA_RULE_PATTERNS[13][0]: _valid_lecturer_name,
    EXTRA_RULE_PATTERNS[14][0]: _valid_festival,
    EXTRA_RULE_PATTERNS[19][0]: _valid_parent_domain,
}
assert EXTRA_RULE_PATTERNS[13][3] == "lecturer" and EXTRA_RULE_PATTERNS[14][3] == "school_name"
assert EXTRA_RULE_PATTERNS[5][3] == "institution" and EXTRA_RULE_PATTERNS[19][3] == "parent_status"

# ── 문맥 결합 규칙: 소논문 실적(연구주제·참여인원·소요시간) ──
_SENT_RE = re.compile(r"[^.!?\n]+")
_QUOTED_TOPIC_RE = re.compile(r"['\"‘“][^'\"‘’“”\n]{3,40}['\"’”]")
_RESEARCH_WORD_RE = re.compile(r"탐구|연구|실험|보고서|논문")
_HEADCOUNT_RE = re.compile(r"(?<![0-9])[0-9]{1,2}\s*명(?!씩)|(?:두|세|네|다섯|여섯)\s*명(?!씩)")
_DURATION_RE = re.compile(r"[0-9]+\s*(?:시간|일|주|개월|달)\s*(?:에\s*걸쳐|동안|간)")
_REPORT_WORD_RE = re.compile(r"(?:탐구|연구)\s?보고서")
_CLASSROOM_RE = re.compile(r"모둠|수업\s*시간|씩")

THESIS_RECORD_REASON = "소논문 실적(연구주제·참여인원·소요시간) 기재 의심"
THESIS_RECORD_BASIS = (
    "기재요령: 연구보고서(소논문) 실적(제목·연구주제·참여인원·소요시간)은 허용 과목도 기재 불가 "
    + _B_SETEUK
)


def _thesis_record_findings(text: str) -> list[tuple[str, str, str, str]]:
    """연구주제(인용부호)와 참여인원·소요시간·보고서 중 하나 이상이 한 문장에 함께 있으면 검출."""
    out = []
    for sm in _SENT_RE.finditer(text):
        sent = sm.group()
        topic = _QUOTED_TOPIC_RE.search(sent)
        if not topic or not _RESEARCH_WORD_RE.search(sent) or _CLASSROOM_RE.search(sent):
            continue
        extra = sum(
            bool(rx.search(sent)) for rx in (_HEADCOUNT_RE, _DURATION_RE, _REPORT_WORD_RE)
        )
        if extra >= 1:
            out.append((topic.group(), THESIS_RECORD_REASON, THESIS_RECORD_BASIS, "thesis_record"))
    return out


_CONTEXT_RULES = (_thesis_record_findings,)


def rule_based_filter(text: str, custom_words: list[str] | None = None) -> list[dict]:
    """정규표현식 + 문맥 결합 규칙 + 사용자 정의 금칙어로 명백한 금지 패턴을 우선 검출한다."""
    findings = []
    seen = set()
    text = text or ""

    def add(word, reason, basis, rule_id):
        if word and word not in seen:
            seen.add(word)
            findings.append(
                {
                    "word": word,
                    "reason": reason,
                    "basis": basis,
                    "rule_id": rule_id,
                    "suggestion_1": "해당 표현 삭제 후 학습 과정 중심으로 서술",
                    "suggestion_2": "구체적 명칭·수치 대신 성장 과정과 노력을 서술",
                }
            )

    spans: list[tuple[int, int]] = []
    for pattern, reason, basis, rule_id in RULE_PATTERNS:
        validator = _PATTERN_VALIDATORS.get(pattern)
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            if validator is not None and not validator(text, m):
                continue
            # 보강 패턴이 기존 검출 범위 안쪽을 다시 잡으면 중복 행이 되므로 건너뛴다
            if pattern not in _BASE_PATTERNS and any(
                s <= m.start() and m.end() <= e for s, e in spans
            ):
                continue
            spans.append(m.span())
            add(m.group().strip(), reason, basis, rule_id)
    for rule in _CONTEXT_RULES:
        for word, reason, basis, rule_id in rule(text):
            add(word, reason, basis, rule_id)
    for word in custom_words or []:
        word = word.strip()
        if word and word not in seen and word in text:
            seen.add(word)
            findings.append(
                {
                    "word": word,
                    "reason": "사용자 정의 금칙어",
                    "basis": "사용자 정의 금칙어",
                    "rule_id": "custom_word",
                    "suggestion_1": "해당 표현 삭제 또는 일반 명사로 대체",
                    "suggestion_2": "맥락에 맞는 중립적 표현으로 대체",
                }
            )
    return findings


def parse_custom_words(raw: str) -> list[str]:
    """쉼표/줄바꿈으로 구분된 사용자 정의 금칙어 문자열을 리스트로 변환한다."""
    return [w.strip() for w in re.split(r"[,\n]", raw) if w.strip()]


def filter_ignored(findings: list[dict], ignored) -> list[dict]:
    """ignored(단어 집합)에 포함된 검출어를 제외한다."""
    ignore_set = set(ignored)
    return [f for f in findings if f.get("word") not in ignore_set]


# ──────────────────────────────────────────────
# 문체·상투 표현 점검 (규칙 기반, API 불필요)
# ──────────────────────────────────────────────
CLICHE_EXPRESSIONS = [
    "적극적으로 참여함",
    "적극적으로 참여하",
    "성실하게 임",
    "성실한 태도",
    "열심히 노력",
    "최선을 다함",
    "최선을 다하는",
    "눈에 띄",
    "매우 우수함",
    "탁월한 능력",
    "뛰어난 역량을 보임",
    "훌륭한 자세",
    "많은 것을 배움",
    "큰 도움이 됨",
    "모범이 됨",
]


# 한글 표기 원칙(기재요령 p.28)의 예외로 널리 쓰이는 영문 약어·단위
ALLOWED_ENGLISH_TERMS = {
    "CEO", "PD", "UCC", "IT", "POP", "CF", "TV", "PAPS", "SNS", "PPT", "AI",
    "OECD", "CD", "DVD", "GPS", "ID", "URL", "PDF", "USB", "VR", "AR", "IoT",
    "km", "cm", "mm", "kg", "cc", "ml",
}
_ALLOWED_ENGLISH_UPPER = {t.upper() for t in ALLOWED_ENGLISH_TERMS}

# 미매칭 괄호를 최대 몇 개까지 보고할지 (병리적 입력에서 경고 폭주 방지)
_MAX_BRACKET_WARNINGS = 3

_QUOTE_LABELS = {"'": "작은따옴표", '"': "큰따옴표"}

# 물결표와 그 변형: ~(U+007E) ∼(U+223C) 〜(U+301C) ～(U+FF5E)
TILDE_CHARS = "~∼〜～"
TILDE_GUIDE = "범위는 '~' 대신 붙임표 '-'로 표기(예: 3-4시간, 2026.3.-7.)"

# 지양 대상 특수문자: 원문자·불릿류 + 물결표(변형 포함).
# 물결표는 숫자 범위(3~4시간)여도 항상 경고한다 — 현장 지침이 특수기호 지양이고,
# 문장 부호 제17항이 붙임표(-) 대체를 허용하며, 기재요령 예시도 기간을
# '2026.03.04.-2026.07.08.'처럼 붙임표로 적는다.
_SPECIAL_CHAR_RE = re.compile(r"[①-⑳★☆●○◆◇▶▷■□※" + TILDE_CHARS + r"]")
# 줄 시작의 문단 구분 번호 (1., 2), 一. 등)
_PARA_NUMBER_RE = re.compile(
    r"^\s*(?:[0-9]{1,2}[.)]|[一二三四五六七八九十]+[.)])\s", re.MULTILINE
)
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")
# 대문자로 시작하는 영단어 2개 이상 연속(외국인 성명·서명 가능): 'Ernest Hemingway', 'J. K. Rowling'
_PROPER_NAME_SEQ_RE = re.compile(
    r"(?<![A-Za-z])(?:[A-Z][a-z]+|[A-Z]\.)(?:[ \-](?:[A-Z][a-z]+|[A-Z]\.))+(?![A-Za-z])"
)
_BOOK_TITLE_SPAN_RE = re.compile(r"『[^』\n]*』|《[^》\n]*》|<[^>\n]*>|〈[^〉\n]*〉")

# 숫자~숫자 범위 (2026.3.~7. 처럼 앞 숫자 뒤에 마침표가 붙은 날짜 포함)
_TILDE_RANGE_RE = re.compile(r"(\d\.?)[ \t]*[" + TILDE_CHARS + r"][ \t]*(?=\d)")


def fix_tilde_ranges(text: str) -> str:
    """숫자~숫자 범위의 물결표를 붙임표(-)로 바꾼 문자열을 돌려준다.

    예: '3~4시간' → '3-4시간', '2026.3.~7.' → '2026.3.-7.'. 숫자 범위가 아닌 '~'는 그대로 둔다.
    """
    return _TILDE_RANGE_RE.sub(r"\1-", text or "")


def _context(text: str, i: int) -> str:
    """위치 i 주변 컨텍스트(앞뒤 15자)를 한 줄로 만들어 돌려준다."""
    return re.sub(r"\s+", " ", text[max(0, i - 15) : i + 16]).strip()


def _issue(
    rule_id: str,
    category: str,
    start: int,
    end: int,
    word: str,
    problem: str,
    suggestion: str = "",
) -> dict:
    return {
        "rule_id": rule_id,
        "category": category,
        "start": start,
        "end": end,
        "word": word,
        "problem": problem,
        "suggestion": suggestion,
    }


def _sample(words, limit: int = 5) -> str:
    uniq = list(dict.fromkeys(words))
    return ", ".join(f"「{w}」" for w in uniq[:limit])


# 각 점검 함수는 (이슈 목록, 경고 문자열 목록)을 돌려준다.
# 이슈는 위치(span)를 가진 구조화 결과(오류 리포트용), 경고 문자열은 기존 UI용이다.
_Group = tuple[list[dict], list[str]]


# ── 1) 개조식이 아닌 종결어미 ──
_BAD_ENDING_RE = re.compile(
    r"[가-힣]+(?:했다|하였다|한다|이다|입니다|합니다|했습니다|하였습니다|있다|있었다|해요|했어요)(?=[.\s]|$)"
)


def _check_endings(text: str) -> _Group:
    issues = [
        _issue(
            "style_ending",
            "문체",
            m.start(),
            m.end(),
            m.group(),
            "개조식이 아닌 종결어미",
            "명사형 어미('~함', '~임')로 통일",
        )
        for m in _BAD_ENDING_RE.finditer(text)
    ]
    if not issues:
        return [], []
    return issues, [
        f"개조식이 아닌 종결어미 {len(issues)}회 발견 — 명사형 어미('~함', '~임')로 통일 권장: "
        + _sample(i["word"] for i in issues)
    ]


# ── 2) 같은 종결 어미의 과도한 반복 ──
def _check_repeated_endings(text: str) -> _Group:
    ends = list(re.finditer(r"([가-힣])(?=\.)", text))
    if len(ends) < 5:
        return [], []
    counter = Counter(m.group(1) for m in ends)
    top_char, top_count = counter.most_common(1)[0]
    if top_count / len(ends) <= 0.6:
        return [], []
    first = next(m for m in ends if m.group(1) == top_char)
    msg = (
        f"종결 어미 '~{top_char}.'이 {top_count}/{len(ends)}문장에서 반복됨 — "
        "'~보임', '~드러남', '~기름' 등으로 다양화 권장"
    )
    return [
        _issue(
            "style_repeat",
            "문체",
            first.start(),
            first.end(),
            top_char,
            f"종결 어미 '~{top_char}.' 반복({top_count}/{len(ends)}문장)",
            "'~보임', '~드러남', '~기름' 등으로 다양화",
        )
    ], [msg]


# ── 3) 상투적(클리셰) 표현 ──
def _check_cliches(text: str) -> _Group:
    issues = []
    found = []
    for c in CLICHE_EXPRESSIONS:
        hits = list(re.finditer(re.escape(c), text))
        if hits:
            found.append(c)
        for m in hits:
            issues.append(
                _issue(
                    "style_cliche",
                    "문체",
                    m.start(),
                    m.end(),
                    c,
                    "상투적 표현",
                    "구체적 행동·산출물 서술로 대체",
                )
            )
    if not found:
        return [], []
    return issues, [
        "상투적 표현 발견 — 구체적 행동·산출물 서술로 대체 권장: "
        + ", ".join(f"「{c}」" for c in found)
    ]


# ── 4) 따옴표/괄호 짝 ──
def _check_quote_bracket_balance_issues(text: str) -> _Group:
    """따옴표 개수(홀짝)와 괄호 짝(스택 기반)을 검사한다."""
    issues: list[dict] = []
    warnings: list[str] = []

    # 1) 따옴표: 개수가 홀수면 마지막 등장 위치를 짝이 어긋난 지점으로 지목한다.
    for ch, label in _QUOTE_LABELS.items():
        positions = [i for i, c in enumerate(text) if c == ch]
        if len(positions) % 2 == 1:
            last = positions[-1]
            warnings.append(
                f"따옴표 짝 불일치 의심 — {label}({ch})이(가) 홀수 개"
                f"({len(positions)}개) 발견됨. 마지막 위치 부근: "
                f"「{_context(text, last)}」"
            )
            issues.append(
                _issue(
                    "quote_balance",
                    "서식",
                    last,
                    last + 1,
                    ch,
                    f"{label} 짝 불일치 의심({len(positions)}개)",
                    "여는·닫는 따옴표 짝 맞추기",
                )
            )

    # 2) 괄호: 스택으로 미매칭 위치를 정확히 찾는다 (따옴표보다 신뢰도가 높다).
    stack: list[int] = []
    unmatched_close: list[int] = []
    for i, c in enumerate(text):
        if c == "(":
            stack.append(i)
        elif c == ")":
            if stack:
                stack.pop()
            else:
                unmatched_close.append(i)

    shown = 0
    for i in unmatched_close:
        if shown >= _MAX_BRACKET_WARNINGS:
            break
        warnings.append(
            "괄호 짝 불일치 — 닫는 괄호 ')'가 여는 괄호 없이 나타남: "
            f"「{_context(text, i)}」"
        )
        issues.append(
            _issue(
                "bracket_balance",
                "서식",
                i,
                i + 1,
                ")",
                "닫는 괄호 ')'가 여는 괄호 없이 나타남",
                "여는 괄호 추가 또는 닫는 괄호 삭제",
            )
        )
        shown += 1
    for i in stack:
        if shown >= _MAX_BRACKET_WARNINGS:
            break
        warnings.append(
            "괄호 짝 불일치 — 여는 괄호 '('가 닫히지 않음: " f"「{_context(text, i)}」"
        )
        issues.append(
            _issue(
                "bracket_balance",
                "서식",
                i,
                i + 1,
                "(",
                "여는 괄호 '('가 닫히지 않음",
                "닫는 괄호 추가 또는 여는 괄호 삭제",
            )
        )
        shown += 1

    return issues, warnings


def _check_quote_bracket_balance(text: str) -> list[str]:
    """(호환용) 따옴표/괄호 짝 경고 문자열 목록."""
    return _check_quote_bracket_balance_issues(text)[1]


# ── 5) 특수문자·문단구분기호 (물결표 포함) ──
def _check_special_characters_issues(text: str) -> _Group:
    issues: list[dict] = []
    found: list[str] = []
    has_tilde = False
    for m in _SPECIAL_CHAR_RE.finditer(text):
        ch = m.group()
        found.append(ch)
        if ch in TILDE_CHARS:
            has_tilde = True
            # 치환안: 앞뒤 낱말 단위로 잘라 숫자 범위면 붙임표로 바꾼 예를 보여 준다.
            ls = m.start()
            while ls > 0 and not text[ls - 1].isspace():
                ls -= 1
            le = m.end()
            while le < len(text) and not text[le].isspace():
                le += 1
            chunk = text[ls:le]
            fixed = fix_tilde_ranges(chunk)
            suggestion = (
                f"{chunk} → {fixed}"
                if fixed != chunk
                else "'~' 삭제 또는 붙임표 '-'·'에서 …까지'로 고쳐 쓰기"
            )
            issues.append(
                _issue(
                    "tilde",
                    "서식",
                    m.start(),
                    m.end(),
                    ch,
                    "물결표(~) 사용 — 특수기호 지양",
                    suggestion,
                )
            )
        else:
            issues.append(
                _issue(
                    "special_char",
                    "서식",
                    m.start(),
                    m.end(),
                    ch,
                    f"특수문자 「{ch}」 사용 지양",
                    "특수문자 삭제 후 문장으로 서술",
                )
            )
    for m in _PARA_NUMBER_RE.finditer(text):
        token = m.group().strip()
        s = m.start() + (len(m.group()) - len(m.group().lstrip()))
        found.append(token)
        issues.append(
            _issue(
                "special_char",
                "서식",
                s,
                s + len(token),
                token,
                f"문단구분기호(번호) 「{token}」 사용 지양",
                "번호 삭제 후 문장으로 이어 쓰기",
            )
        )
    if not found:
        return [], []
    msg = (
        f"특수문자·문단구분기호 {len(found)}회 발견 — 기재요령상 입력을 지양합니다: "
        + _sample(found)
    )
    if has_tilde:
        msg += f" / {TILDE_GUIDE}"
    return issues, [msg]


def _check_special_characters(text: str) -> list[str]:
    """(호환용) 특수문자·문단구분기호 경고 문자열 목록."""
    return _check_special_characters_issues(text)[1]


# ── 6) 한글 표기 원칙(영문 오남용) ──
def _check_english_usage_issues(text: str) -> _Group:
    issues: list[dict] = []
    flagged: dict[str, str] = {}
    # 예외(기재요령 p.28): 외국인 성명·도서명·저자명. 대문자로 시작하는 영단어가
    # 2개 이상 이어지면(예: 'Ernest Hemingway') 성명·서명일 수 있어 경고하지 않는다.
    # 도서명 표기(『』《》<>〈〉) 안의 영문도 제외한다.
    allowed_spans = [m.span() for m in _PROPER_NAME_SEQ_RE.finditer(text)]
    allowed_spans += [m.span() for m in _BOOK_TITLE_SPAN_RE.finditer(text)]
    for m in _LATIN_TOKEN_RE.finditer(text):
        token = m.group()
        key = token.upper()
        if key in _ALLOWED_ENGLISH_UPPER:
            continue
        if any(s <= m.start() and m.end() <= e for s, e in allowed_spans):
            continue
        flagged.setdefault(key, token)
        issues.append(
            _issue(
                "english",
                "문체",
                m.start(),
                m.end(),
                token,
                f"영문 표기 「{token}」 — 한글 표기 원칙",
                "한글 표기로 바꾸기(외국인 성명·도서명·일반화된 약어는 예외)",
            )
        )
    if not flagged:
        return [], []
    sample = ", ".join(f"「{t}」" for t in list(flagged.values())[:5])
    return issues, [
        "영문 표기 검토 필요 — 한글 표기가 원칙이며 부득이한 경우"
        "(외국인 성명/도서명·저자명/일반화된 명사 등)만 영문이 허용됨: " + sample
    ]


def _check_english_usage(text: str) -> list[str]:
    """(호환용) 영문 표기 경고 문자열 목록."""
    return _check_english_usage_issues(text)[1]


# ── 7) 허위·과장 표현 (참고용 권고, 위반 아님) ──
# 오탐을 줄이려 소수의 명백한 최상급·과장어만, 낱말 경계를 엄격히 둔다.
# '최고차항'(수학)·'최고 기온'·'완벽주의'·'천재지변'·'천재교육'은 잡지 않는다.
EXAGGERATION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?<![가-힣])최고(?:의|로|인|임)(?![가-힣])", "최고"),
    (r"(?<![가-힣])완벽(?:하게|한|히|함|하다|하며|하여)", "완벽"),
    (r"(?<![가-힣])독보적", "독보적"),
    (r"(?<![가-힣])압도적", "압도적"),
    (r"(?<![가-힣])누구보다(?:도)?", "누구보다"),
    (r"(?<![가-힣])천재(?:적|성)", "천재"),
    (r"타의\s*추종을\s*불허", "타의 추종을 불허"),
)
_EXAGGERATION_RES = tuple((re.compile(p), label) for p, label in EXAGGERATION_PATTERNS)


def _check_exaggeration(text: str) -> _Group:
    issues = []
    for rx, _label in _EXAGGERATION_RES:
        for m in rx.finditer(text):
            issues.append(
                _issue(
                    "exaggeration",
                    "과장·허위",
                    m.start(),
                    m.end(),
                    m.group(),
                    "최상급·과장 표현 — 허위·과장 기재 금지",
                    "직접 관찰한 구체적 행동·근거로 바꿔 쓰기",
                )
            )
    if not issues:
        return [], []
    issues.sort(key=lambda i: i["start"])
    return issues, [
        "허위·과장 기재 금지: 직접 관찰 근거 확인 — 최상급·과장 표현이 있습니다"
        "(위반 아닌 참고): " + _sample(i["word"] for i in issues)
    ]


# ── 8) 서식 점검 ──
_MULTI_SPACE_RE = re.compile(r"(?<=\S)[ \t]{2,}(?=\S)")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"(?<=[가-힣A-Za-z0-9)\]'\"])[ \t]+(?=[.,!?](?![.\d]))")
# 한글 바로 뒤 마침표에 공백 없이 글자가 이어짐 (소수점·날짜·영문 약어는 앞이 숫자/영문이라 제외)
_NO_SPACE_AFTER_PERIOD_RE = re.compile(r"(?<=[가-힣])\.(?=[가-힣A-Za-z])")
# '..'(정확히 2개)와 ',,'(2개 이상). 3개 이상 마침표('...', '......')와 '…'는 줄임표로 허용.
_DOUBLE_PUNCT_RE = re.compile(r"(?<!\.)\.\.(?!\.)|,{2,}")
_SENTENCE_PERIOD_RE = re.compile(r"[가-힣]\.")


def _jong(ch: str) -> int:
    """한글 음절의 종성 인덱스(0=받침 없음). 한글 음절이 아니면 -1."""
    code = ord(ch) - 0xAC00
    return code % 28 if 0 <= code < 11172 else -1


_JONG_N, _JONG_L, _JONG_LM, _JONG_M = 4, 8, 10, 16


def _check_format(text: str) -> _Group:
    issues: list[dict] = []
    warnings: list[str] = []

    def add(rule_id, rx, problem, suggestion, label):
        hits = list(rx.finditer(text))
        for m in hits:
            issues.append(
                _issue(rule_id, "서식", m.start(), m.end(), m.group(), problem, suggestion)
            )
        if hits:
            # 공백 문제는 _context가 공백을 접어 버리므로 문제 위치를 '⎵'로 드러낸다.
            def show(m):
                if m.group().strip():
                    return _context(text, m.start())
                before = re.sub(r"\s+", " ", text[max(0, m.start() - 10) : m.start()])
                after = re.sub(r"\s+", " ", text[m.end() : m.end() + 10])
                return f"{before.lstrip()}{'⎵' * len(m.group())}{after.rstrip()}"

            ctx = _sample(show(m) for m in hits)
            warnings.append(f"서식 점검 — {label} {len(hits)}곳: {ctx}")

    add("format_spaces", _MULTI_SPACE_RE, "연속 공백", "공백 한 칸으로", "연속 공백(2칸 이상)")
    add(
        "format_space_before_punct",
        _SPACE_BEFORE_PUNCT_RE,
        "문장부호 앞 공백",
        "부호를 앞말에 붙여 쓰기",
        "문장부호 앞 공백",
    )
    add(
        "format_space_after_period",
        _NO_SPACE_AFTER_PERIOD_RE,
        "마침표 뒤 공백 누락",
        "마침표 뒤 한 칸 띄우기",
        "마침표 뒤 공백 누락",
    )
    add(
        "format_double_punct",
        _DOUBLE_PUNCT_RE,
        "중복 문장부호",
        "부호 하나만 남기기",
        "중복 문장부호",
    )

    # 문장 끝 마침표 누락: 다른 문장이 마침표로 끝나는 글에서, 줄 끝이 명사형 종결
    # (받침 ㅁ·ㄻ — '~함', '~임', '~만듦')이나 '~다'인데 부호가 없을 때만 본다.
    # 줄바꿈된 문장 중간('연계하여')이나 마침표를 전혀 쓰지 않는 한 줄 메모는 잡지 않는다.
    if _SENTENCE_PERIOD_RE.search(text):
        hits = []
        pos = 0
        for line in text.split("\n"):
            stripped = line.rstrip()
            if stripped:
                last = stripped[-1]
                if _jong(last) in (_JONG_M, _JONG_LM) or last == "다":
                    end = pos + len(stripped)
                    start = end - 1
                    issues.append(
                        _issue(
                            "format_missing_period",
                            "서식",
                            start,
                            end,
                            last,
                            "문장 끝 마침표 누락",
                            f"「{last}」 뒤에 마침표(.) 추가",
                        )
                    )
                    hits.append(_context(text, start))
            pos += len(line) + 1
        if hits:
            warnings.append(
                f"서식 점검 — 문장 끝 마침표 누락 {len(hits)}곳: " + _sample(hits)
            )

    return issues, warnings


# ── 9) 맞춤법 로컬 규칙 (정밀도 우선, 근거가 분명한 흔한 오류만) ──
# (정규식, 교정안, 근거). 근거 조항이 없는 표준어 문제는 표준국어대사전 표제어로 적는다.
SPELLING_RULES: tuple[tuple[str, str, str], ...] = (
    # 한글 맞춤법 제27항 [붙임 2]: 어원이 분명하지 않은 '며칠'은 소리대로 적음 ('몇일' 아님)
    (r"몇일", "며칠", "한글 맞춤법 제27항 [붙임 2]"),
    # 한글 맞춤법 제35항 [붙임 2]: '되어'가 줄면 '돼', '되었-'이 줄면 '됐-' ('됬'은 없는 표기)
    (r"됬", "됐", "한글 맞춤법 제35항 [붙임 2]"),
    # 같은 조항: 낱말 끝의 '되'는 '되어'의 준말 '돼'로 적음 ('안 되' → '안 돼')
    (r"안\s?되(?=$|[\s.,!?])", "안 돼/안돼", "한글 맞춤법 제35항 [붙임 2]"),
    # 표준국어대사전 '설레다': 명사형은 '설렘' ('설레이다·설레임'은 비표준)
    (r"설레임", "설렘", "표준국어대사전 '설레다'(명사형 '설렘')"),
    # 표준국어대사전 '금세': '금시에'의 준말. 명사 '금새(물건값)'는 조사가 붙으므로 제외
    (r"금새(?=$|[\s.,!?])", "금세", "표준국어대사전 '금세'('금시에'의 준말)"),
    # 표준국어대사전 '왠지': '왜인지'의 준말 ('웬지'는 없는 말)
    (r"웬지", "왠지", "표준국어대사전 '왠지'('왜인지'의 준말)"),
    # 표준국어대사전 '어떡하다': '어떻게 하다'의 준말 ('어떻해'는 없는 말)
    (r"어떻해", "어떻게/어떡해", "표준국어대사전 '어떡하다'('어떻게 하다'의 준말)"),
    # 한글 맞춤법 제2항: 문장의 각 단어는 띄어 씀 ('있다'는 독립된 용언).
    # 사전에 한 단어로 오른 '재미있다·맛있다·관계있다' 등은 목록에 넣지 않는다.
    (r"(심도|의미|가치|깊이|관심)있(?=[가-힣])", r"\1 있", "한글 맞춤법 제2항(단어별 띄어쓰기)"),
    # 한글 맞춤법 제42항: 의존 명사 '것'은 띄어 씀 + 제2항 '같다'는 독립된 단어
    (r"것같(?=다|은|음|아|았|습)", "것 같", "한글 맞춤법 제2항·제42항"),
)
_SPELLING_RES = tuple((re.compile(p), fix, basis) for p, fix, basis in SPELLING_RULES)

# '것'과 붙어 한 단어가 된 명사(날것·탈것·들것·별것·헌것·군것질)는 제외
_GEOT_COMPOUND_PREV = frozenset("날탈들별헌군")


def _check_spelling(text: str) -> _Group:
    """맞춤법 로컬 규칙 검사. 경고에는 교정안과 근거 조항을 함께 표기한다."""
    issues: list[dict] = []

    def add(start, end, wrong, fix, basis):
        issues.append(
            _issue(
                "spelling",
                "맞춤법",
                start,
                end,
                wrong,
                f"맞춤법: 「{wrong}」 → 「{fix}」 ({basis})",
                fix,
            )
        )
        issues[-1]["basis"] = basis

    for rx, fix, basis in _SPELLING_RES:
        for m in rx.finditer(text):
            add(m.start(), m.end(), m.group(), m.expand(fix), basis)

    # 한글 맞춤법 제11항 [붙임 1]: 모음이나 'ㄴ' 받침 뒤의 '렬·률'은 '열·율'로,
    # 그 밖의 받침 뒤에서는 '렬·률'로 적음 (백분율·실패율 / 합격률·성공률).
    # 사람 이름(김율 등) 오탐을 피하려고 앞에 한글이 2자 이상 있는 낱말만 본다.
    for m in re.finditer(r"(?<=[가-힣])([가-힣])(율|률)", text):
        prev, suffix = m.group(1), m.group(2)
        jong = _jong(prev)
        expected = "율" if jong in (0, _JONG_N) else "률"
        if suffix != expected:
            add(
                m.start(),
                m.end(),
                m.group(),
                prev + expected,
                "한글 맞춤법 제11항 [붙임 1]",
            )

    # 한글 맞춤법 제42항: 의존 명사 '수'는 띄어 씀 ('할수 있다' → '할 수 있다').
    # '-ㄹ수록'(어미)과 '수없이'(한 단어 부사)는 제외.
    for m in re.finditer(r"([가-힣])([ \t]?)수([ \t]?)(있|없(?!이)|밖에)", text):
        prev, sp1, sp2, tail = m.groups()
        if _jong(prev) != _JONG_L:
            continue
        if sp1 == "" or (sp2 == "" and tail != "밖에"):
            fixed = f"{prev} 수" + ("" if tail == "밖에" else " ") + tail
            add(m.start(), m.end(), m.group(), fixed, "한글 맞춤법 제42항(의존 명사)")

    # 한글 맞춤법 제42항: 관형형 뒤 의존 명사 '것'은 띄어 씀 ('궁금한것' → '궁금한 것').
    for m in re.finditer(r"([가-힣])것(?!질)", text):
        prev = m.group(1)
        if _jong(prev) in (_JONG_N, _JONG_L) and prev not in _GEOT_COMPOUND_PREV:
            add(m.start(), m.end(), m.group(), f"{prev} 것", "한글 맞춤법 제42항(의존 명사)")

    if not issues:
        return [], []
    issues.sort(key=lambda i: i["start"])
    sample = ", ".join(
        f"「{i['word']}」→「{i['suggestion']}」({i['basis']})" for i in issues[:5]
    )
    return issues, [f"맞춤법 점검(로컬 규칙) {len(issues)}건 — {sample}"]


def check_spelling(text: str) -> list[dict]:
    """맞춤법 로컬 규칙 이슈 목록 (테스트·리포트용)."""
    return _check_spelling(text)[0]


_STYLE_CHECKS = (
    _check_endings,
    _check_repeated_endings,
    _check_cliches,
    _check_quote_bracket_balance_issues,
    _check_special_characters_issues,
    _check_english_usage_issues,
    _check_exaggeration,
    _check_format,
    _check_spelling,
)


def collect_style_issues(text: str) -> list[dict]:
    """문체·서식·맞춤법·과장 점검 결과를 위치(span)를 가진 구조화 이슈 목록으로 돌려준다.

    각 이슈: rule_id, category(문체/서식/맞춤법/과장·허위), start, end, word,
    problem, suggestion (맞춤법 이슈는 basis도 포함).
    """
    text = text or ""
    issues: list[dict] = []
    for check in _STYLE_CHECKS:
        issues.extend(check(text)[0])
    return issues


def style_check(text: str) -> list[str]:
    """문체·표기 점검 경고 목록을 반환한다 (collect_style_issues와 같은 점검의 문자열판).

    개조식 어미 위반, 어미 반복, 상투적 표현, 따옴표/괄호 짝, 특수문자(물결표 포함)·
    문단구분기호, 한글 표기 원칙(영문 오남용), 허위·과장 표현, 서식, 맞춤법 로컬 규칙.
    """
    text = text or ""
    warnings: list[str] = []
    for check in _STYLE_CHECKS:
        warnings.extend(check(text)[1])
    return warnings


# ──────────────────────────────────────────────
# 학생 간 유사도 검사 (동일·유사 문장 복붙 방지)
# ──────────────────────────────────────────────
def _normalize_for_similarity(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]+")


def find_shared_sentences(
    items: list[tuple[str, str]], min_chars: int = 12, min_students: int = 2
) -> list[tuple[str, list[str]]]:
    """여러 학생에게 공통으로 나타나는 문장을 찾는다.

    (학생명, 텍스트) 목록에서 문장을 나누고, 같은 문장을 쓴 학생이
    min_students명 이상인 경우만 (문장, 학생명 목록)으로 돌려준다.
    """
    groups: dict[str, tuple[str, list[str]]] = {}
    for name, text in items:
        seen_here: set[str] = set()
        for raw in _SENTENCE_SPLIT_RE.split(text or ""):
            sentence = _normalize_for_similarity(raw)
            if len(sentence) < min_chars or sentence in seen_here:
                continue
            seen_here.add(sentence)
            # 표시용 대표 문장은 처음 등장한 원문(앞뒤 공백만 제거)을 쓴다.
            groups.setdefault(sentence, (raw.strip(), []))[1].append(name)
    shared = [
        (repr_text, names)
        for repr_text, names in groups.values()
        if len(names) >= min_students
    ]
    return sorted(shared, key=lambda p: (-len(p[1]), -len(p[0])))


def find_similar_pairs(
    items: list[tuple[str, str]], threshold: float = SIMILARITY_THRESHOLD
) -> list[tuple[str, str, float]]:
    """(이름, 텍스트) 목록에서 유사도가 임계값을 넘는 쌍을 찾는다."""
    pairs = []
    normalized = [(name, _normalize_for_similarity(t)) for name, t in items]
    for i in range(len(normalized)):
        for j in range(i + 1, len(normalized)):
            ratio = difflib.SequenceMatcher(
                None, normalized[i][1], normalized[j][1]
            ).ratio()
            if ratio >= threshold:
                pairs.append((normalized[i][0], normalized[j][0], ratio))
    return sorted(pairs, key=lambda p: -p[2])
