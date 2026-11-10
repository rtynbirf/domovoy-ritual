#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СТРАЖ РИТУАЛА · ДКА-приёмка артефактов domovoy-ritual (stdlib-only, fail-closed).

G=(Q,Σ,δ,q₀,F): q₀=START, F={ACCEPT}
Переходы:
  START --RITUAL_OK--> FSM_OK --AUTOMAT_OK--> MANIFEST_OK --> ACCEPT
Любой инвариант ложен → REJECT (exit 1). Инварианты артефактов — fail-closed.
"""
from __future__ import annotations
import hashlib, py_compile, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS: list[str] = []


def q(name: str, ok: bool, detail: str = "") -> bool:
    mark = "OK " if ok else "FAIL"
    print(f"  [{mark}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)
    return ok


print("СТРАЖ РИТУАЛА · q₀=START")

# δ1: устав существует и формален
ritual = ROOT / "RITUAL-FSM.md"
if q("δ1: RITUAL-FSM.md существует", ritual.is_file()):
    t = ritual.read_text(encoding="utf-8")
    q("δ1a: аксиомы A1–A7", all(f"A{i}" in t for i in range(1, 8)))
    q("δ1b: формальная запись M=(S,Σ,δ,s₀,F)", "M = (S, Σ, δ, s₀, F)" in t)
    q("δ1c: все 7 состояний S0–S6", all(f"S{i}" in t for i in range(7)))
    q("δ1d: guard-ы G1–G7", all(f"G{i}" in t for i in range(1, 8)))
    q("δ1e: инварианты дома H1–H5", all(f"H{i}" in t for i in range(1, 6)))
    q("δ1f: 12 дефолтов «всё решаю сам»", "| Случай |" in t and t.count("| **") + t.count("| `") >= 10)

# δ2: автомат компилируется и структурен
fsm = ROOT / "house_fsm.py"
if q("δ2: house_fsm.py существует", fsm.is_file()):
    try:
        py_compile.compile(str(fsm), doraise=True)
        q("δ2a: компиляция", True)
    except py_compile.PyCompileError as e:
        q("δ2a: компиляция", False, str(e)[:80])
    src = fsm.read_text(encoding="utf-8")
    q("δ2b: δ-таблица есть", "DELTA" in src)
    pairs = len(re.findall(r'\((S\d),\s*"(?:enter|work|verify|seal|clean|status|abort|recover)"\):\s*S\d', src))
    q("δ2c: переходов δ ≥ 16", pairs >= 16, f"найдено {pairs}")
    q("δ2d: append-only журнал", "journal.jsonl" in src and '"a"' in src)
    q("δ2e: права 600 на секретное", "S_IRUSR" in src)
    q("δ2f: REJECTED вместо исключений", "REJECTED" in src)

# δ3: README целостен
readme = ROOT / "README.md"
if q("δ3: README.md существует", readme.is_file()):
    r = readme.read_text(encoding="utf-8")
    q("δ3a: такт описан", "enter" in r and "clean" in r and "status" in r)
    q("δ3b: связь с домом", "repo-doctrine" in r or "polimat-hub" in r)

# δ4: манифест SHA256 (если есть) — честен
manifest = ROOT / "MANIFEST_SHA256.txt"
if manifest.is_file():
    mt = manifest.read_text(encoding="utf-8")
    ok = True
    for line in mt.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        try:
            h, name = line.split(maxsplit=1)
            actual = hashlib.sha256((ROOT / name.strip()).read_bytes()).hexdigest()
            if actual != h:
                ok = False
                FAILS.append(f"manifest:{name}")
                print(f"  [FAIL] манифест: {name} sha расходится")
        except Exception as e:
            ok = False
            FAILS.append(f"manifest:{e}")
    q("δ4: манифест SHA256 честен", ok)
else:
    q("δ4: манифест отсутствует (допустимо)", True)

# Финал: ACCEPT или REJECT (fail-closed)
if FAILS:
    print(f"\n  [REJECT] страж красный: {len(FAILS)} инвариантов сломаны → δ(q,a)→REJECT")
    sys.exit(1)
print("\n  [ACCEPT] ω ∈ L(G) — артефакты ритуала формальны, дом в порядке")
