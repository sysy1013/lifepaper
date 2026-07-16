# -*- coding: utf-8 -*-
"""프로젝트 루트를 sys.path에 추가하여 core 패키지를 임포트할 수 있게 한다."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
