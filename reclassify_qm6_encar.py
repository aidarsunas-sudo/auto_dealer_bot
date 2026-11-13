# -*- coding: utf-8 -*-
"""
reclassify_qm6_encar.py — переклассификация Encar-лотов отчёта renault_qm6_report
по сохранённым inspection JSON (inspection/{id}_inspection.json):

- окрасы: paintPartTypes/paintPartTypes-like + outers пусто → «окрасов нет»
- ДТП: master.accdient == False → «нет»
- страховые выплаты (보험처리 이력): машинно НЕ отдаётся; для IMAGE-формата со
  сканом I1 — выводится список таких лотов для ручного чтения скана (см. ниже),
  для TABLE-формата — статус остаётся «требует проверки» с точной пометкой.

Запуск: python reclassify_qm6_encar.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "renault_qm6_report" / "collected.json"
INSP = ROOT / "renault_qm6_report" / "inspection"


def main() -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    image_format = []
    for bk in ("main_2026", "alt_2025"):
        for c in data[bk]["needs_check"]:
            if c.get("source") != "encar":
                continue
            cid = str(c["external_id"])
            jf = INSP / f"{cid}_inspection.json"
            if not jf.exists():
                c["_condition_note"] = ("inspection (성능점검표) не загрузился с Encar (лимит запросов) — "
                                        "проверить ДТП/окрасы/страховую вручную на карточке лота")
                continue
            ins = json.loads(jf.read_text(encoding="utf-8"))
            fmts = ins.get("formats") or []
            master = ins.get("master") or {}
            detail = master.get("detail") or {}
            paint_parts = detail.get("paintPartTypes") or []
            outers = ins.get("outers") or []
            accd = master.get("accdient")
            waterlog = detail.get("waterlog")

            c["_accdient_flag"] = accd
            c["_waterlog"] = waterlog
            c["_outers_panels"] = [o.get("type", {}).get("title", "?") for o in outers]
            c["_paint_parts"] = [p.get("title") if isinstance(p, dict) else str(p) for p in paint_parts]

            dt_txt = "нет" if accd is False else ("ЕСТЬ (техпаспорт)" if accd else "не ясно")
            paint_txt = "нет" if (not paint_parts and not outers) else "ЕСТЬ"
            if waterlog:
                c["_condition_note"] = "потоп (техпаспорт) — исключён бы"
                continue

            if "IMAGE" in fmts:
                i1 = INSP / f"{cid}_I1.jpg"
                if i1.exists():
                    image_format.append((bk, cid, c.get("trim"), c.get("mileage_km")))
                    c["_condition_note"] = (f"성능점검표 (IMAGE): ДТП — {dt_txt}, окрасы — {paint_txt}; "
                                            "страховые выплаты — читается со скана I1 (см. отдельную пометку)")
                else:
                    c["_condition_note"] = (f"성능점검표 (IMAGE): ДТП — {dt_txt}, окрасы — {paint_txt}; "
                                            "скан I1 не скачан — страховые выплаты проверить вручную")
            else:
                c["_condition_note"] = (
                    f"성능점검표 получен ({'/'.join(fmts)}): ДТП — {dt_txt}, окрасы — {paint_txt}; "
                    "страховые выплаты (보험처리 이력) Encar машинно не отдаёт — проверить на car365.go.kr "
                    "по номеру или запросить скан у продавца")

    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("IMAGE-формат со сканами (читать I1):", len(image_format))
    for row in image_format:
        print("  ", row)
    print("сохранено:", DATA)


if __name__ == "__main__":
    main()
