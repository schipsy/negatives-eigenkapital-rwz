"""
Stufe 3: Bewertung der Brauchbarkeit mit GPT-5.2 (Azure OpenAI).

Die Substanztiefe (SUBSTANZIELL | PAUSCHAL | LEER) vergibt das Modell bereits im
Extraktionsaufruf der Stufe 2 (meta.qualitaet). Dieses Skript bewertet die
Brauchbarkeit nach dem Schema von Rebernig/Scheinecker mit zwei Entscheidungsregeln
und sechs Konstellationen (a)-(f), siehe prompts/stufe3_brauchbarkeit_*.txt.

Ablauf:
- drei vollständige, unabhängige Durchläufe über alle Anhangangaben
  (Eingabe je Aufruf: Anhangtext, in Stufe 2 extrahierte Begründungen, negatives Eigenkapital);
- Mehrheitsentscheid je Anhangangabe; bei drei verschiedenen Ergebnissen gilt die
  niedrigste Stufe (UNBRAUCHBAR); Score 0-10 als Median der drei Durchläufe.

Eingabe: Pickle aus extraktion_stufe2.py
Ausgabe: je Durchlauf ein Zwischenstand (fortsetzbar) sowie das Gesamtergebnis
         als Pickle und Excel-Tabelle.

Aufruf:
  python 04_bewertung_stufe3/bewertung_stufe3.py
         [--eingabe output/stufe2_extraktion.pkl] [--ausgabe-dir output] [--workers 60]
"""
import argparse
import json
import os
import pickle
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from openai import AzureOpenAI

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env")

AZURE_API_VERSION = "2024-12-01-preview"
MAX_RETRIES = 3
MAX_COMPLETION_TOKENS = 3500
MAX_ANHANGTEXT_CHARS = 6000
CHECKPOINT_EVERY = 200
NUM_RUNS = 3

STAGES_3 = ["EHER BRAUCHBAR", "EHER UNBRAUCHBAR", "UNBRAUCHBAR"]
STAGES_3_NUM = {s: i for i, s in enumerate(STAGES_3)}


def read_prompt(name: str) -> str:
    return (REPO / "prompts" / name).read_text(encoding="utf-8").rstrip("\n")


SYSTEM_PROMPT = read_prompt("stufe3_brauchbarkeit_system.txt")
USER_PROMPT_TEMPLATE = read_prompt("stufe3_brauchbarkeit_user.txt")


def fill(template: str, **values: str) -> str:
    """Setzt die Platzhalter {name} der Prompt-Vorlage ein (wie str.format)."""
    pattern = re.compile(r"\{(" + "|".join(map(re.escape, values)) + r")\}")
    return pattern.sub(lambda m: values[m.group(1)], template)


def get_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_version=AZURE_API_VERSION,
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
    )


def load_all_data(path: Path) -> pd.DataFrame:
    df = pd.read_pickle(path)

    def safe_parse(s):
        try:
            return json.loads(s) if pd.notna(s) else {}
        except Exception:
            return {}

    df["parsed"] = df["extraction_result"].apply(safe_parse)
    df["begruendungen_list"] = df["parsed"].apply(lambda x: x.get("begruendungen", []))
    df["anzahl_begruendungen"] = df["begruendungen_list"].apply(len)
    df["qualitaet"] = df["parsed"].apply(lambda x: x.get("meta", {}).get("qualitaet", "UNBEKANNT"))
    df = df[df["extraction_result"].notna()].copy()
    df = df[~df["parsed"].apply(lambda x: "error" in (x or {}))]
    df = df.reset_index(drop=True)
    df["case_id"] = [f"C{i:05d}" for i in range(len(df))]
    return df


def evaluate_one(anhangtext, begruendungen_list, neg_ek, client):
    text = (str(anhangtext) if pd.notna(anhangtext) else "")[:MAX_ANHANGTEXT_CHARS]
    if not text.strip():
        return {"_status": "EMPTY", "begruendungen_bewertung": [], "decision_rule_1_passed": False,
                "konstellation_erfuellt": "keine",
                "gesamturteil": {"stufe": "UNBRAUCHBAR", "score": 0,
                                 "mangelprofil": ["Kein Anhangtext"], "kommentar": ""}}
    user_prompt = fill(
        USER_PROMPT_TEMPLATE,
        anhangtext=text,
        begruendungen_json=json.dumps(begruendungen_list, ensure_ascii=False, indent=1) if begruendungen_list else "[]",
        neg_ek_eur=f"{neg_ek:,.0f}" if pd.notna(neg_ek) else "n/a",
    )
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"],
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": user_prompt}],
                response_format={"type": "json_object"},
                max_completion_tokens=MAX_COMPLETION_TOKENS,
            )
            result = json.loads(response.choices[0].message.content)
            result["_status"] = "OK"
            return result
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                return {"_status": f"ERROR: {str(e)[:200]}", "begruendungen_bewertung": [],
                        "decision_rule_1_passed": False, "konstellation_erfuellt": "keine",
                        "gesamturteil": {"stufe": "UNBRAUCHBAR", "score": -1, "mangelprofil": [], "kommentar": ""}}


def run_round(df, round_num, output_dir: Path, workers: int) -> dict:
    """Ein vollständiger Durchlauf mit Zwischenstand alle CHECKPOINT_EVERY Fälle."""
    ckpt = output_dir / f"stufe3_durchlauf{round_num}.pkl"
    results = pickle.loads(ckpt.read_bytes()) if ckpt.exists() else {}
    todo = [(r["case_id"], r["Anhangtext"], r["begruendungen_list"], r["Negatives Eigenkapital"])
            for _, r in df.iterrows() if r["case_id"] not in results]
    print(f"Durchlauf {round_num}: {len(results)} bereits verarbeitet, {len(todo)} offen")
    if not todo:
        return results

    client = get_client()
    lock = threading.Lock()
    completed = errors = 0
    with ThreadPoolExecutor(max_workers=workers) as exe:
        futures = {exe.submit(evaluate_one, t, br, ek, client): cid for cid, t, br, ek in todo}
        for f in as_completed(futures):
            cid = futures[f]
            try:
                res = f.result()
            except Exception as e:
                res = {"_status": f"WORKER_ERROR: {e}",
                       "gesamturteil": {"stufe": "UNBRAUCHBAR", "score": -1, "mangelprofil": [], "kommentar": ""}}
            with lock:
                results[cid] = res
                errors += not res.get("_status", "").startswith(("OK", "EMPTY"))
                completed += 1
                if completed % CHECKPOINT_EVERY == 0:
                    ckpt.write_bytes(pickle.dumps(results))
                    print(f"Durchlauf {round_num}: {completed} von {len(todo)} ({errors} Fehler)")
    ckpt.write_bytes(pickle.dumps(results))
    print(f"Durchlauf {round_num} abgeschlossen, {errors} Fehler")
    return results


def extract_stufe(result):
    s = ((result or {}).get("gesamturteil") or {}).get("stufe") if isinstance(result, dict) else None
    return s if s in STAGES_3 else None


def extract_score(result):
    s = ((result or {}).get("gesamturteil") or {}).get("score") if isinstance(result, dict) else None
    try:
        return int(s) if s is not None else None
    except (ValueError, TypeError):
        return None


def majority_vote_stage(stage_list):
    """Mehrheit der Durchläufe; bei Gleichstand die niedrigste Stufe."""
    valid = [s for s in stage_list if s is not None]
    if not valid:
        return None
    top = Counter(valid).most_common()
    if len(top) >= 2 and top[0][1] == top[1][1]:
        tied = [t[0] for t in top if t[1] == top[0][1]]
        return max(tied, key=lambda s: STAGES_3_NUM[s])
    return top[0][0]


def majority_vote_score(score_list):
    valid = [s for s in score_list if s is not None and s >= 0]
    return int(np.median(valid)) if valid else None


def aggregate(df, rounds) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        cid = row["case_id"]
        res = [r.get(cid, {}) for r in rounds]
        stages = [extract_stufe(r) for r in res]
        scores = [extract_score(r) for r in res]

        mangel = Counter(m.lower().strip() for r in res for m in ((r.get("gesamturteil") or {}).get("mangelprofil") or []))
        # Konstellation, Entscheidungsregel 1 und Einzelbewertungen aus dem Durchlauf mit dem höchsten Score
        best = res[int(np.argmax([s if s is not None and s >= 0 else -1 for s in scores]))]

        rows.append({
            "case_id": cid,
            "Firma": row.get("Firma", ""),
            "Negatives_EK": row.get("Negatives Eigenkapital", None),
            "anzahl_begruendungen": row.get("anzahl_begruendungen", 0),
            "original_qualitaet": row.get("qualitaet", ""),  # Substanztiefe aus Stufe 2
            **{f"stage_R{i + 1}": s or "ERROR" for i, s in enumerate(stages)},
            **{f"score_R{i + 1}": s for i, s in enumerate(scores)},
            "stage_voted": majority_vote_stage(stages) or "ERROR",
            "score_voted": majority_vote_score(scores),
            "decision_rule_1_passed": best.get("decision_rule_1_passed", False),
            "konstellation_erfuellt": best.get("konstellation_erfuellt", "keine"),
            "mangelprofil_top": "; ".join(m for m, _ in mangel.most_common(8)),
            "kommentar_best": ((best.get("gesamturteil") or {}).get("kommentar") or "")[:300],
            "begruendungen_bewertung": best.get("begruendungen_bewertung", []),
        })
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eingabe", default="output/stufe2_extraktion.pkl")
    parser.add_argument("--ausgabe-dir", default="output")
    parser.add_argument("--workers", type=int, default=60)
    args = parser.parse_args()
    out = Path(args.ausgabe_dir)
    out.mkdir(parents=True, exist_ok=True)

    df = load_all_data(Path(args.eingabe))
    print(f"{len(df):,} Anhangangaben")
    rounds = [run_round(df, r, out, args.workers) for r in range(1, NUM_RUNS + 1)]
    df_agg = aggregate(df, rounds)

    with open(out / "stufe3_brauchbarkeit.pkl", "wb") as f:
        pickle.dump({"df_agg": df_agg, "rounds": rounds, "system_prompt": SYSTEM_PROMPT}, f)

    illegal = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # in Excel unzulässige Steuerzeichen
    df_excel = df_agg.copy()
    df_excel["begruendungen_bewertung"] = df_excel["begruendungen_bewertung"].apply(
        lambda lst: json.dumps(lst, ensure_ascii=False) if lst else "")
    for c in df_excel.columns:
        if df_excel[c].dtype == "object":
            df_excel[c] = df_excel[c].apply(lambda v: illegal.sub("", v) if isinstance(v, str) else v)
    df_excel.to_excel(out / "stufe3_brauchbarkeit.xlsx", index=False)
    print(f"Ausgabe: {out / 'stufe3_brauchbarkeit.pkl'} und {out / 'stufe3_brauchbarkeit.xlsx'}")


if __name__ == "__main__":
    main()
