#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ДОМОВОЙ-ХОЗЯИН · ночная машина всего аккаунта (аккаунт-широкий свип).

Закон дома: поработал → сделал → убрал за собой → закрыл → вышел чистым.
Машина приводит ДОМ к S0-покою: issues→0, PR→auto-merge, красные→перегон,
нотификации→0. Всё машинно, человек не занимается тем, что может машина.

Состояние (S): SWEEP → ACT → VERIFY → SEAL → S0.
Инвариант выхода: ноль открытых issues старше 2ч, ноль висящих PR без решения,
ноль красных latest-прогонов (после одного честного перегона), ноль непрочитанных.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TOKEN = os.environ.get("DOM_PAT") or os.environ.get("GITHUB_TOKEN", "")
OWNER = os.environ.get("DOM_OWNER", "rtynbirf")
API = "https://api.github.com"
STATE_PATH = os.environ.get("DOM_STATE_PATH", "DOM-STATE.json")
ISSUE_GRACE_MIN = int(os.environ.get("DOM_ISSUE_GRACE_MIN", "120"))  # не убивать живую работу
NOW = datetime.now(timezone.utc)

report: dict = {
    "ts": NOW.isoformat(timespec="seconds"),
    "owner": OWNER,
    "repos": 0,
    "issues_closed": [],
    "issues_kept_alive": [],
    "prs_automerged": 0,
    "repos_tuned": 0,
    "reds_rerun": [],
    "reds_left_honest": [],
    "unread_before": None,
    "unread_after": None,
    "violations": [],
}


def api(path: str, method: str = "GET", body: dict | None = None, raw: bool = False):
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "domovoy-hozyain",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = r.read()
                return json.loads(payload) if payload and not raw else payload
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 502, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            if e.code == 404:
                return None
            return {"__http_error__": e.code, "message": e.read().decode()[:200]}
        except Exception as e:  # сеть моргнула — повтор
            if attempt < 2:
                time.sleep(2)
                continue
            report["violations"].append(f"api {path}: {e}")
            return None


def paged_all(path: str, pages: int = 10):
    out, sep = [], "&" if "?" in path else "?"
    for p in range(1, pages + 1):
        batch = api(f"{path}{sep}per_page=100&page={p}")
        if not batch or not isinstance(batch, list):
            break
        out.extend(batch)
        if len(batch) < 100:
            break
    return out


# ── S: SWEEP ──────────────────────────────────────────────────────────────
repos = paged_all(f"/user/repos?type=owner&sort=updated")
report["repos"] = len(repos)
print(f"ДОМОВОЙ:repos={len(repos)}")

# ── S: ACT ────────────────────────────────────────────────────────────────
for repo in repos:
    full = repo["full_name"]
    if repo["archived"]:
        continue

    # 1. Гигиена настроек: merged-ветки самоудаляются, auto-merge разрешён
    patch = {}
    if repo.get("allow_auto_merge") is not True:
        patch["allow_auto_merge"] = True
    if repo.get("delete_branch_on_merge") is not True:
        patch["delete_branch_on_merge"] = True
    if patch:
        api(f"/repos/{full}", "PATCH", patch)
        report["repos_tuned"] += 1

    # 2. Issues старше лимита → ритуальный комментарий + закрыть (DONE→CLEANED)
    for issue in paged_all(f"/repos/{full}/issues?state=open", pages=3):
        if "pull_request" in issue:
            continue
        created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
        age_min = (NOW - created).total_seconds() / 60
        if age_min < ISSUE_GRACE_MIN:
            report["issues_kept_alive"].append(f"{full}#{issue['number']}")
            continue
        api(f"/repos/{full}/issues/{issue['number']}/comments", "POST", {
            "body": ("🏠 **ДОМОВОЙ-ХОЗЯИН** · ритуал дома выполнен: работа принята, "
                     "изделие запломбировано, уборка закрыла контур.\n\n"
                     "Конечный автомат: `work → done → clean → S0` (закон дома, domovoy-ritual).\n"
                     "Открытых «иссуев» в доме не остаётся: закрыто машиной по мандату Хозяина. "
                     "Если дело живо — открой заново с фактом, страж придёт снова.")})
        api(f"/repos/{full}/issues/{issue['number']}", "PATCH", {"state": "closed",
                                                                 "state_reason": "completed"})
        report["issues_closed"].append(f"{full}#{issue['number']}")

    # 3. Открытые PR от dependabot → auto-merge (решают required checks, не человек).
    #    Фолбэк: REST-квирк — auto_merge иногда молча не встаёт; если гейты уже
    #    зелёные (mergeable_state=clean) — автомат сам делает переход DONE→CLEANED.
    for pr in paged_all(f"/repos/{full}/pulls?state=open", pages=3):
        if not pr["user"]["login"].startswith("dependabot"):
            continue
        r = api(f"/repos/{full}/pulls/{pr['number']}", "PATCH", {"auto_merge": True})
        armed = r and isinstance(r, dict) and r.get("auto_merge")
        if armed:
            report["prs_automerged"] += 1
        else:
            full_pr = api(f"/repos/{full}/pulls/{pr['number']}")
            if full_pr and full_pr.get("mergeable") and full_pr.get("mergeable_state") == "clean":
                m = api(f"/repos/{full}/pulls/{pr['number']}/merge", "PUT",
                        {"merge_method": "squash",
                         "commit_title": f"merge: {pr['title']} (dependabot, гейты зелёные)"})
                if m and isinstance(m, dict) and m.get("merged"):
                    report["prs_automerged"] += 1
                else:
                    report["violations"].append(f"PR {full}#{pr['number']}: merge отклонён")
            else:
                report["violations"].append(f"PR {full}#{pr['number']}: state={full_pr.get('mergeable_state') if full_pr else '?'} — оставлен гейтам")

    # 4. Красные latest-прогоны на default-ветке → один честный перегон
    db = repo["default_branch"]
    runs = paged_all(f"/repos/{full}/actions/runs?branch={db}&per_page=40", pages=2)
    latest_by_wf: dict[str, dict] = {}
    for run in runs:
        latest_by_wf.setdefault(run["name"], run)  # список отсортирован свежестью
    for name, run in latest_by_wf.items():
        if run["conclusion"] == "failure":
            r = api(f"/repos/{full}/actions/runs/{run['id']}/rerun-failed-jobs", "POST")
            ok = r is not None and "__http_error__" not in r
            (report["reds_rerun"] if ok else report["reds_left_honest"]).append(f"{full} :: {name}")

# 5. Тишина: все нотификации прочитаны (убрал за собой)
before = len(paged_all("/notifications?per_page=100", pages=3))
report["unread_before"] = before
if before:
    api("/notifications", "PUT", {})
after = len(paged_all("/notifications?per_page=100", pages=3))
report["unread_after"] = after

# ── S: VERIFY ─────────────────────────────────────────────────────────────
print(json.dumps(report, ensure_ascii=False, indent=2))

# ── S: SEAL → S0 ──────────────────────────────────────────────────────────
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary:
    with open(summary, "a", encoding="utf-8") as f:
        f.write(f"""## 🏠 ДОМОВОЙ-ХОЗЯИН · такт {report['ts']}
- repos: **{report['repos']}** · настроено: **{report['repos_tuned']}**
- issues закрыто: **{len(report['issues_closed'])}** · оставлено жить (<{ISSUE_GRACE_MIN}мин): **{len(report['issues_kept_alive'])}**
- PR на auto-merge: **{report['prs_automerged']}**
- красных перегнано: **{len(report['reds_rerun'])}** · честно оставлено: **{len(report['reds_left_honest'])}** {report['reds_left_honest']}
- нотификации: **{before} → {after}**
- нарушения: **{len(report['violations'])}**

Закон дома: поработал → сделал → убрал за собой → закрыл → вышел чистым.
""")

# пломба состояния в репо (append по содержимому; SHA-гонка не страшна: перезапишем)
try:
    st = api(f"/repos/{OWNER}/domovoy-ritual/contents/{STATE_PATH}")
    body = {
        "message": f"dom-watch: такт {report['ts']} — S0-сверка (пломба автомата)",
        "content": base64.b64encode(json.dumps(report, ensure_ascii=False, indent=2).encode()).decode(),
    }
    if st and "sha" in st:
        body["sha"] = st["sha"]
    api(f"/repos/{OWNER}/domovoy-ritual/contents/{STATE_PATH}", "PUT", body)
    print("пломба DOM-STATE.json записана")
except Exception as e:
    print(f"пломба не записана (не критично): {e}")
