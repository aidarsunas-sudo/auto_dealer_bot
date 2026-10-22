"""Единый запуск цепочки SEO-агента Pick+ Auto.

  python run_chain.py                                   # --dry-run по умолчанию: только показывает, что запустил бы
  python run_chain.py --run                             # реальный запуск всех шагов
  python run_chain.py --run --steps collect,topics,analyze
  python run_chain.py --steps publish                   # dry-run одного шага

Шаги (всегда в таком порядке): collect, topics, analyze, generate, approve, publish, stats, report.
  generate — одна статья за запуск: первая тема из selected_topics.csv без записи в publications.csv, иначе следующая
             из topics.csv по частоте (белый список/стоп-слова; строка дописывается в selected_topics.csv); только если есть
             ANTHROPIC_API_KEY в .env или claude -p залогинен (иначе пропуск с записью в лог)
  approve  — publishing/auto_approve.py --run: ворота для черновиков, уведомление в личный чат и статус «запланировано»
             с окном отмены (publishing/publications.csv); публикует запланированное шаг publish
  collect  — wordstat/wordstat_collect.py
  topics   — wordstat/topics_analysis.py topics (перед запуском копия wordstat/topics.csv -> topics.csv.bak;
             selected_topics.csv и competitors.csv шаг не трогает)
  analyze  — wordstat/topics_analysis.py analyze (только темы из wordstat/selected_topics.csv без analysis/<slug>.md,
             адреса из wordstat/competitors.csv; темы без адресов пропускаются, в поиск Яндекса не ходит)
  publish  — publishing/publish.py --publish (только при --run и только если есть записи «одобрено» или
             «запланировано» со сроком; HOLD, статус «стоп», день недели, время и лимит «одна статья в день» проверяет
             сам publish.py)
  stats    — publishing/stats.py
  report   — publishing/report.py

Лог: logs/chain_YYYY-MM-DD.log (время, шаг, результат, код выхода; значения токенов и ключей из .env маскируются).
Упал шаг — остальные всё равно запускаются, итог помечается «с ошибками» (код выхода 1).
Блокировка logs/chain.lock: если предыдущий запуск ещё идёт, второй завершается с сообщением (код 2).
"""
import argparse
import csv
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
LOCK = LOGS / "chain.lock"
LOCK_MAX_AGE_SEC = 6 * 3600
STEPS = ["collect", "topics", "analyze", "generate", "approve", "publish", "stats", "report"]
TIMEOUT_SEC = {"collect": 1800, "topics": 600, "analyze": 1800, "generate": 1800, "approve": 600, "publish": 600,
               "stats": 600, "report": 600}
HOLD = ROOT / "publishing" / "HOLD"
ARTICLES_DIR = ROOT / "articles"
TOPICS_CSV = ROOT / "wordstat" / "topics.csv"
SELECTED = ROOT / "wordstat" / "selected_topics.csv"
COMPETITORS = ROOT / "wordstat" / "competitors.csv"
ANALYSIS = ROOT / "wordstat" / "analysis"
PUBLICATIONS = ROOT / "publishing" / "publications.csv"
PY = sys.executable
CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


# ---------- секреты ----------
def _secrets():
    vals = dotenv_values(ROOT / ".env")
    return [v for k, v in vals.items() if v and len(v) >= 6 and any(x in k.upper() for x in ("TOKEN", "KEY", "SECRET", "PASSWORD", "CSRF"))]


SECRETS = _secrets()


def mask(text):
    text = str(text)
    for s in SECRETS:
        text = text.replace(s, "***")
    return text


# ---------- лог ----------
def log(line, to_file=True):
    stamp = datetime.now().strftime("%H:%M:%S")
    out = mask(f"{stamp} | {line}")
    print(out)
    if to_file:
        LOGS.mkdir(exist_ok=True)
        with open(LOGS / f"chain_{datetime.now():%Y-%m-%d}.log", "a", encoding="utf-8") as f:
            f.write(out + "\n")


# ---------- блокировка ----------
def _pid_alive(pid):
    if os.name == "nt":
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lock_holder():
    """None — свободно; иначе описание живого владельца блокировки (устаревшую блокировку удаляет)."""
    if not LOCK.exists():
        return None
    try:
        info = json.loads(LOCK.read_text(encoding="utf-8"))
        pid, started = int(info["pid"]), float(info["started"])
    except (OSError, ValueError, KeyError):
        pid, started = 0, 0
    if pid and _pid_alive(pid) and time.time() - started < LOCK_MAX_AGE_SEC:
        return f"pid {pid}, запущен {datetime.fromtimestamp(started):%Y-%m-%d %H:%M:%S}"
    LOCK.unlink(missing_ok=True)  # процесса нет или блокировка старше LOCK_MAX_AGE_SEC — устарела
    return None


def acquire_lock():
    LOGS.mkdir(exist_ok=True)
    if lock_holder():
        return False
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "started": time.time()}, f)
    return True


def release_lock():
    LOCK.unlink(missing_ok=True)


# ---------- данные для шагов ----------
def _read_csv(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def analyze_plan():
    """(темы к запуску, [(slug, причина пропуска)]) по selected_topics.csv."""
    have_urls = {}
    for r in _read_csv(COMPETITORS):
        have_urls.setdefault((r.get("тема") or "").strip().lower(), []).append((r.get("URL") or "").strip())
    todo, skipped = [], []
    for t in _read_csv(SELECTED):
        slug = t["тема"]
        if (ANALYSIS / f"{slug}.md").exists():
            skipped.append((slug, "analysis/%s.md уже есть" % slug))
        elif not (have_urls.get(slug.lower()) or have_urls.get(t["главный запрос"].strip().lower())):
            skipped.append((slug, "нет адресов в competitors.csv"))
        else:
            todo.append(t)
    return todo, skipped


def _due(value):
    """«опубликовать после» (YYYY-MM-DD HH:MM, время Москвы) уже наступило?"""
    from zoneinfo import ZoneInfo
    try:
        t = datetime.strptime((value or "").strip(), "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Europe/Moscow"))
    except ValueError:
        return False
    return t <= datetime.now(ZoneInfo("Europe/Moscow"))


def publishable():
    """(одобрено вручную, запланировано и срок настал). «стоп» не считается; при publishing/HOLD запланированные не публикуются."""
    rows = _read_csv(PUBLICATIONS)
    approved = sum(1 for r in rows if (r.get("статус") or "").strip().lower() == "одобрено")
    due = 0 if HOLD.exists() else sum(1 for r in rows if (r.get("статус") or "").strip().lower() == "запланировано"
                                       and _due(r.get("опубликовать после")))
    return approved, due


PENDING_TOPIC_ROW = None   # строка из topics.csv, которую шаг generate допишет в selected_topics.csv при реальном запуске


def next_free_topic():
    """Следующая тема для generate: (slug, строка для дописывания в selected_topics.csv или None).

    1) первая тема из selected_topics.csv без записи в publications.csv (в любом статусе) и без articles/<slug>/article.md;
    2) иначе следующая из topics.csv по частоте главного запроса, проходящая белый список и стоп-слова из
       wordstat/config.yaml (строка допишется в selected_topics.csv; существующие строки не меняются)."""
    used = {(r.get("slug") or "").strip() for r in _read_csv(PUBLICATIONS)}
    selected = _read_csv(SELECTED)
    for t in selected:
        if t["тема"] not in used and not (ARTICLES_DIR / t["тема"] / "article.md").exists():
            return t["тема"], None
    known = used | {t["тема"] for t in selected}
    sys.path.insert(0, str(ROOT / "wordstat"))
    import topics_analysis
    for t in sorted(_read_csv(TOPICS_CSV), key=lambda r: -int(r.get("частота главного") or 0)):
        if t["тема"] in known or (ARTICLES_DIR / t["тема"] / "article.md").exists():
            continue
        if topics_analysis.topic_reject_reason(t["главный запрос"]) is None:
            return t["тема"], t
    return None, None


def append_selected(row):
    """Дописывает строку темы в конец selected_topics.csv (только добавление)."""
    with open(SELECTED, encoding="utf-8-sig", newline="") as f:
        fields = csv.DictReader(f).fieldnames
    raw = SELECTED.read_bytes()
    with open(SELECTED, "a", encoding="utf-8", newline="") as f:
        if raw and not raw.endswith(b"\n"):
            f.write("\r\n")
        csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\r\n").writerow(row)


def claude_logged_in():
    """claude -p может работать: выполнен вход (claude auth status) или в окружении есть ANTHROPIC_API_KEY."""
    if (dotenv_values(ROOT / ".env").get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return True
    exe = shutil.which("claude.cmd") or shutil.which("claude")
    if not exe:
        return False
    try:
        p = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        return bool(json.loads(p.stdout or "{}").get("loggedIn"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def command_for(step):
    """(команда, env-поправки, пояснение) или (None, {}, причина пропуска)."""
    if step == "collect":
        return [PY, "wordstat/wordstat_collect.py"], {}, ""
    if step == "topics":
        return [PY, "wordstat/topics_analysis.py", "topics"], {}, ""
    if step == "analyze":
        todo, skipped = analyze_plan()
        note = "; ".join(f"пропуск {s}: {why}" for s, why in skipped)
        if not todo:
            return None, {}, "нет тем для анализа" + (f" ({note})" if note else "")
        # без ключа Яндекса analyze не ходит в поиск и берёт адреса только из competitors.csv
        env = {"YANDEX_SEARCH_API_KEY": "", "YANDEX_SEARCH_FOLDER_ID": ""}
        return [PY, "wordstat/topics_analysis.py", "analyze", "<временный файл тем>"], env, \
            f"темы: {', '.join(t['тема'] for t in todo)}" + (f"; {note}" if note else "")
    if step == "generate":
        global PENDING_TOPIC_ROW
        slug, PENDING_TOPIC_ROW = next_free_topic()
        if slug is None:
            return None, {}, "нет свободных тем в selected_topics.csv и подходящих в topics.csv"
        if not claude_logged_in():
            PENDING_TOPIC_ROW = None
            return None, {}, f"пропуск: нет ANTHROPIC_API_KEY в .env и claude -p не залогинен (нужна тема {slug}): черновик не генерируется"
        extra = " (будет дописана в selected_topics.csv из topics.csv)" if PENDING_TOPIC_ROW else ""
        return [PY, "generate_draft.py", "--slug", slug], {}, f"тема: {slug}{extra}"
    if step == "approve":
        drafts = [r["slug"] for r in _read_csv(PUBLICATIONS) if (r.get("статус") or "").strip().lower() == "черновик"]
        if not drafts:
            return None, {}, "нет записей со статусом «черновик»"
        return [PY, "publishing/auto_approve.py", "--run"], {}, f"черновики: {', '.join(drafts)}"
    if step == "publish":
        approved, due = publishable()
        if not (approved or due):
            return None, {}, "нет записей «одобрено» и нет «запланировано» со сроком"
        return [PY, "publishing/publish.py", "--publish"], {}, f"одобрено: {approved}, запланировано со сроком: {due}"
    if step == "stats":
        return [PY, "publishing/stats.py"], {}, ""
    if step == "report":
        return [PY, "publishing/report.py"], {}, ""
    raise ValueError(step)


# ---------- запуск ----------
def run_step(step):
    """Возвращает (результат, код)."""
    cmd, env_extra, note = command_for(step)
    if cmd is None:
        log(f"{step} | пропущен: {note} | код -")
        return "пропущен", None
    tmp_file = None
    if step == "topics" and TOPICS_CSV.exists():  # topics перезаписывает topics.csv — сохраняем копию
        shutil.copy2(TOPICS_CSV, TOPICS_CSV.with_name("topics.csv.bak"))
        log("topics | копия wordstat/topics.csv -> wordstat/topics.csv.bak")
    if step == "analyze":
        todo, _ = analyze_plan()
        fd, name = tempfile.mkstemp(prefix="chain_topics_", suffix=".csv", dir=LOGS)
        tmp_file = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(todo[0]))
            w.writeheader()
            w.writerows(todo)
        cmd = cmd[:-1] + [str(tmp_file)]
    if step == "generate" and PENDING_TOPIC_ROW:
        append_selected(PENDING_TOPIC_ROW)
        log(f"generate | тема {PENDING_TOPIC_ROW['тема']} дописана в selected_topics.csv")
    log(f"{step} | старт: {note}" if note else f"{step} | старт")
    try:
        p = subprocess.run(cmd, cwd=ROOT, env={**CHILD_ENV, **env_extra}, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=TIMEOUT_SEC[step])
        code, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        code, out = -1, f"тайм-аут {TIMEOUT_SEC[step]} с"
    except OSError as e:
        code, out = -1, f"не запустился: {type(e).__name__}"
    finally:
        if tmp_file:
            tmp_file.unlink(missing_ok=True)
    result = "ок" if code == 0 else "ОШИБКА"
    for line in [l for l in out.strip().splitlines() if l.strip()][-6:]:
        log(f"{step} |   > {line[:200]}")
    log(f"{step} | {result} | код {code}")
    return result, code


def main():
    ap = argparse.ArgumentParser(description="Единый запуск цепочки SEO-агента Pick+ Auto")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только показать, что запустил бы (по умолчанию)")
    mode.add_argument("--run", action="store_true", help="реальный запуск")
    ap.add_argument("--steps", default=",".join(STEPS), help="шаги через запятую: " + ",".join(STEPS))
    args = ap.parse_args()

    wanted = [s.strip() for s in args.steps.split(",") if s.strip()]
    bad = [s for s in wanted if s not in STEPS]
    if bad:
        ap.error(f"неизвестные шаги: {', '.join(bad)}; допустимы: {', '.join(STEPS)}")
    steps = [s for s in STEPS if s in wanted]  # порядок всегда канонический

    if not args.run:
        print(f"[dry-run] шаги: {', '.join(steps)}; ничего не запускается и не отправляется (для запуска нужен --run)")
        holder = lock_holder()
        print(f"[dry-run] блокировка: {'занята (' + holder + ')' if holder else 'свободна'}")
        for step in steps:
            cmd, env_extra, note = command_for(step)
            if cmd is None:
                print(f"[dry-run] {step}: был бы пропущен — {note}")
            else:
                shown = " ".join(Path(c).name if c == PY else c for c in cmd)
                extra = f" (env: {', '.join(k + '=<пусто>' for k in env_extra)})" if env_extra else ""
                print(f"[dry-run] {step}: {shown}{extra}" + (f" — {note}" if note else ""))
        return 0

    if not acquire_lock():
        msg = f"Предыдущий запуск ещё идёт ({lock_holder()}) — завершаюсь."
        print(msg)
        LOGS.mkdir(exist_ok=True)
        log("цепочка | " + msg)
        return 2
    try:
        log(f"=== цепочка: старт, шаги: {', '.join(steps)}")
        results = {}
        for step in steps:
            try:
                results[step] = run_step(step)
            except Exception as e:  # шаг не должен ронять остальные
                log(f"{step} | ОШИБКА | исключение {type(e).__name__}")
                results[step] = ("ОШИБКА", None)
        errors = [s for s, (r, _) in results.items() if r == "ОШИБКА"]
        summary = "с ошибками: " + ", ".join(errors) if errors else "успешно"
        log(f"=== цепочка: итог — {summary} ({', '.join(f'{s}: {r}' for s, (r, _) in results.items())})")
        return 1 if errors else 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
