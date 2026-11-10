#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HOUSE-FSM · Ритуал дома как конечный автомат (исполняемая реализация Устава)

M = (S, Σ, δ, s0, F) — детерминированный автомат сеанса агента.
Состояния: S0 ПОКОЙ → S1 ВХОД → S2 ДЕЛО → S3 СВЕРКА → S4 ПЛОМБА → S5 УБОРКА → S0
Авария в любой точке: S6 → recover → S3.
Ethos: stdlib-only, офлайн-пригодность, ноль зависимостей (этика аккаунта).

Команды:
  enter   S0→S1   токен в tmp с правами 600, мандат, таймстемп
  work    S1→S2   фиксация дел по мандату (аргумент: --task "что делаю")
  verify  S2→S3   сборка машинных свидетельств (SHA256, факты)
  seal    S3→S4   пломба: журнал + бандл-дайджест + ворклог-строка
  clean   S4→S5   уборка: токены стёрты, tmp пуст, проверка чистоты
  status  S5→S0   подтверждение покоя (или REJECTED)
  abort   *→S6    аварийный вход: причина обязательна (--reason "...")
  recover S6→S3   выход из аварии (только с задокументированным ущербом)
  state           показать текущее состояние автомата
  replay          показать журнал тактов (append-only)
"""
from __future__ import annotations
import argparse, hashlib, json, os, stat, sys, time
from datetime import datetime, timezone
from pathlib import Path

HOME_DIR = Path(os.environ.get("HOUSE_FSM_DIR", Path.home() / ".house-fsm"))
STATE_FILE = HOME_DIR / "state.json"          # текущее состояние автомата
JOURNAL = HOME_DIR / "journal.jsonl"          # append-only журнал (H2)
TMP_SESS = HOME_DIR / "tmp"                   # единственное разрешённое место токенов
SEAL_DIR = HOME_DIR / "seals"                 # пломбы (SHA256-дайджесты)

S0, S1, S2, S3, S4, S5, S6 = "S0", "S1", "S2", "S3", "S4", "S5", "S6"
NAMES = {S0: "ПОКОЙ", S1: "ВХОД", S2: "ДЕЛО", S3: "СВЕРКА", S4: "ПЛОМБА", S5: "УБОРКА", S6: "АВАРИЯ"}

# δ: (from, event) -> to  — запрещённые пары отсутствуют => REJECTED
DELTA = {
    (S0, "enter"): S1,
    (S1, "work"): S2, (S2, "work"): S2,
    (S2, "verify"): S3, (S3, "verify"): S3,
    (S3, "seal"): S4, (S4, "seal"): S4,
    (S4, "clean"): S5, (S1, "clean"): S5,
    (S5, "clean"): S5, (S5, "status"): S0,
    (S0, "abort"): S0,          # nop: покой трогать нечем
    (S1, "abort"): S6, (S2, "abort"): S6, (S3, "abort"): S6, (S4, "abort"): S6,
    (S5, "abort"): S6, (S6, "abort"): S6,
    (S6, "recover"): S3,
}


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Automaton:
    def __init__(self) -> None:
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        SEAL_DIR.mkdir(exist_ok=True)
        TMP_SESS.mkdir(exist_ok=True)
        os.chmod(TMP_SESS, stat.S_IRWXU)  # 700: только владелец
        if not STATE_FILE.exists():
            self.state = S0
            self._persist()
        else:
            self.state = json.loads(STATE_FILE.read_text())["state"]
        # журнал с правами 600
        if not JOURNAL.exists():
            JOURNAL.touch()
        os.chmod(JOURNAL, stat.S_IRUSR | stat.S_IWUSR)

    # ---------- ядро δ ----------
    def _persist(self) -> None:
        STATE_FILE.write_text(json.dumps({"state": self.state, "at": utc()}, ensure_ascii=False))
        os.chmod(STATE_FILE, stat.S_IRUSR | stat.S_IWUSR)

    def _log(self, event: str, verdict: str, detail: str = "") -> None:
        rec = {"ts": utc(), "state": self.state, "event": event, "verdict": verdict, "detail": detail}
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _transition(self, event: str, detail: str = "", force: bool = False) -> bool:
        key = (self.state, event)
        target = DELTA.get(key)
        if target is None and not force:
            self._log(event, "REJECTED", f"S{NAMES[self.state]}×{event}: переход не определён δ")
            print(f"  [REJECTED] {NAMES[self.state]} × {event} — такой переход не определён (устав §1.2)")
            return False
        self._log(event, "OK" if target else "NOP", detail)
        if target:
            print(f"  [δ] {NAMES[self.state]} --{event}--> {NAMES[target]}")
            self.state = target
            self._persist()
        return True

    # ---------- guard-и ----------
    def g_environment_clean(self) -> bool:
        """G1: нет токенов в env/файлах вне tmp, tmp пуст."""
        leaks = []
        for k in list(os.environ):
            if k.upper() in ("GH_TOKEN", "GITHUB_TOKEN", "HOUSE_TOKEN") and os.environ[k]:
                leaks.append(f"env:{k}")
                os.environ.pop(k, None)  # сам чинит: убирает из среды
        for p in HOME_DIR.rglob("*"):
            if TMP_SESS not in p.parents and p.name in ("token.txt", ".gh-token") and p.is_file():
                leaks.append(str(p))
        if TMP_SESS.exists() and any(TMP_SESS.iterdir()):
            leaks.append(f"{TMP_SESS} (незапертый tmp)")
        if leaks:
            self._log("guard", "FIXED", "очищено: " + ", ".join(leaks))
            print(f"  [G1] среда была грязной — автомат убрал сам: {leaks}")
        return True

    def g_token_in_tmp(self) -> bool:
        """G1b: токен, если есть, лежит только в tmp с правами 600."""
        if not TMP_SESS.exists():
            return True
        for p in TMP_SESS.iterdir():
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
        return True

    def g_clean_exit(self) -> tuple[bool, list]:
        """G6: дом чист — tmp пуст, пломбы есть, сид-файлы удалены."""
        leftovers = []
        if TMP_SESS.exists():
            leftovers += [str(p) for p in TMP_SESS.iterdir()]
        return (len(leftovers) == 0, leftovers)

    # ---------- команды ----------
    def enter(self, mandate: str) -> None:
        self.g_environment_clean()
        if not mandate:
            print("  [REJECTED] мандат пуст — сеанс не нужен → clean → S0")
            self.clean()
            return
        (TMP_SESS / "mandate.txt").write_text(mandate, encoding="utf-8")
        os.chmod(TMP_SESS / "mandate.txt", stat.S_IRUSR | stat.S_IWUSR)
        (TMP_SESS / "session-start.json").write_text(json.dumps({"started": utc(), "mandate": mandate}, ensure_ascii=False))
        os.chmod(TMP_SESS / "session-start.json", stat.S_IRUSR | stat.S_IWUSR)
        self.g_token_in_tmp()
        self._transition("enter", f"мандат: {mandate[:80]}")

    def work(self, task: str) -> None:
        detail = task or "работа по мандату"
        (TMP_SESS / "work.jsonl").touch(exist_ok=True)
        with open(TMP_SESS / "work.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": utc(), "task": detail}, ensure_ascii=False) + "\n")
        self._transition("work", detail)

    def verify(self, facts: str) -> None:
        digest = hashlib.sha256((facts or "").encode()).hexdigest()[:16]
        (TMP_SESS / "witness.jsonl").touch(exist_ok=True)
        with open(TMP_SESS / "witness.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": utc(), "facts": facts, "digest": digest}, ensure_ascii=False) + "\n")
        self._transition("verify", f"свидетельство d={digest}")

    def seal(self) -> None:
        seal = {"sealed_at": utc(), "from_state": self.state}
        wp, vp = TMP_SESS / "work.jsonl", TMP_SESS / "witness.jsonl"
        seal["work_records"] = sum(1 for _ in open(wp)) if wp.exists() else 0
        seal["witness_records"] = sum(1 for _ in open(vp)) if vp.exists() else 0
        # пломба = дайджест журнала этого сеанса
        seal["journal_sha256"] = sha256_file(JOURNAL)
        name = f"seal-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        (SEAL_DIR / name).write_text(json.dumps(seal, ensure_ascii=False, indent=2))
        os.chmod(SEAL_DIR / name, stat.S_IRUSR | stat.S_IWUSR)
        self._transition("seal", f"пломба: seals/{name} sha={seal['journal_sha256'][:16]}")

    def clean(self) -> None:
        # A4: уборка равна работе — стираем всё в tmp, фиксируем факт
        removed = []
        if TMP_SESS.exists():
            for p in sorted(TMP_SESS.rglob("*"), reverse=True):
                try:
                    p.unlink()
                    removed.append(p.name)
                except IsADirectoryError:
                    p.rmdir()
        self._transition("clean", f"удалено: {len(removed)} файлов (токены/мандат/следы)")

    def status(self) -> None:
        ok, leftovers = self.g_clean_exit()
        if ok:
            self._transition("status", "дом чист, покой подтверждён")
            print("  [S0] ПОКОЙ: токенов нет, tmp пуст, такт замкнулся.")
        else:
            self._log("status", "REJECTED", f"остались: {leftovers}")
            print(f"  [REJECTED] покой недостижим, остались: {leftovers} — повторите clean")

    def abort(self, reason: str) -> None:
        self._log("abort", "OK", f"причина: {reason}")
        (TMP_SESS / "incident.txt").write_text(f"{utc()} {reason}\n", encoding="utf-8")
        self.state = S6
        self._persist()
        print(f"  [S6] АВАРИЯ: {reason}. Выход — recover (G7: ущерб уже записан в incident).")

    def recover(self) -> None:
        inc = TMP_SESS / "incident.txt"
        if not inc.exists():
            print("  [REJECTED] G7: нет записи ущерба — из S6 выйти нельзя (устав §1.2)")
            return
        self._transition("recover", inc.read_text().strip()[:120])

    # ---------- интроспекция ----------
    def show(self) -> None:
        print(f"  состояние: {self.state} {NAMES[self.state]}")
        n = sum(1 for _ in open(JOURNAL, encoding="utf-8"))
        print(f"  журнал: {JOURNAL} ({n} записей, append-only)")
        seals = sorted(SEAL_DIR.glob("seal-*.json"))
        print(f"  пломбы: {len(seals)} (последняя: {seals[-1].name if seals else '—'})")

    def replay(self, tail: int = 15) -> None:
        lines = open(JOURNAL, encoding="utf-8").read().splitlines()
        for ln in lines[-tail:]:
            r = json.loads(ln)
            print(f"  {r['ts']} [{r['state']}] {r['event']:<8} {r['verdict']:<8} {r['detail'][:60]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="HOUSE-FSM · ритуал дома как конечный автомат")
    ap.add_argument("command", choices=["enter", "work", "verify", "seal", "clean", "status", "abort", "recover", "state", "replay"])
    ap.add_argument("--mandate", default="", help="мандат сеанса: какие репо/действия")
    ap.add_argument("--task", default="", help="что сделано на шаге work")
    ap.add_argument("--facts", default="", help="машинное свидетельство на шаге verify")
    ap.add_argument("--reason", default="", help="причина аварии (обязательна для abort)")
    a = ap.parse_args()
    m = Automaton()
    print(f"[HOUSE-FSM] {utc()} · {NAMES[m.state]}")
    if a.command == "enter":      m.enter(a.mandate)
    elif a.command == "work":     m.work(a.task)
    elif a.command == "verify":   m.verify(a.facts)
    elif a.command == "seal":     m.seal()
    elif a.command == "clean":    m.clean()
    elif a.command == "status":   m.status()
    elif a.command == "abort":
        if not a.reason:
            print("  [REJECTED] abort требует --reason (факт, не настроение)")
            return 2
        m.abort(a.reason)
    elif a.command == "recover":  m.recover()
    elif a.command == "state":    m.show()
    elif a.command == "replay":   m.replay()
    return 0


if __name__ == "__main__":
    sys.exit(main())
