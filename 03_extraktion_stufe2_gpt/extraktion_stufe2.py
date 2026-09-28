"""
Stufe 2: induktive Extraktion der Begründungen mit GPT-5.2 (Azure OpenAI).

Je Anhangangabe ein Aufruf. Das Modell gibt als JSON zurück:
- je Begründung: Kurzbezeichnung ("typ"), Zitat, Oberkategorie, Quantifizierung, Betrag;
- Metaangaben, darunter die Substanztiefe ("qualitaet": SUBSTANZIELL | PAUSCHAL | LEER).
Die Zuordnung zu den sechs Oberkategorien erfolgt je Nennung im selben Aufruf.

Eingabe: Tabelle (.xlsx oder .csv) mit den Spalten Firma, Anhangtext, Negatives Eigenkapital
Ausgabe: Pickle mit der Eingabetabelle und der Spalte extraction_result (JSON je
         Anhangangabe); ein abgebrochener Lauf wird fortgesetzt. Zusätzlich eine
         Excel-Übersicht je Anhangangabe.

Aufruf:
  python 03_extraktion_stufe2_gpt/extraktion_stufe2.py
         [--eingabe data/anhangangaben.xlsx] [--ausgabe output/stufe2_extraktion.pkl] [--workers 60]
"""
import argparse
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import AzureOpenAI

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env")

AZURE_API_VERSION = "2024-12-01-preview"
MAX_RETRIES = 3
MAX_COMPLETION_TOKENS = 2000
CHECKPOINT_EVERY = 200


def read_prompt(name: str) -> str:
    return (REPO / "prompts" / name).read_text(encoding="utf-8").rstrip("\n")


SYSTEM_PROMPT = read_prompt("stufe2_extraktion_system.txt")
USER_PROMPT_TEMPLATE = read_prompt("stufe2_extraktion_user.txt")

# Kategoriecodes im Prompt -> Oberkategorien im Beitrag
KATEGORIEN = {
    "PROGNOSE": "Fortbestehensprognose",
    "RANGRUECKTRITT": "Rangrücktrittserklärung",
    "FINANZIERUNG": "Gesellschafterfinanzierung",
    "VERMÖGEN": "Stille Reserven / Vermögenswerte",
    "HAFTUNG": "Patronats- / Haftungserklärung",
    "SONSTIGE": "Sonstige Begründungen",
}

EMPTY_RESULT = {"begruendungen": [], "meta": {"hat_begruendung": False, "nur_standardsatz": False, "qualitaet": "LEER"}}


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


def extract_single(text: str, client: AzureOpenAI) -> dict:
    """Extrahiert die Begründungen einer Anhangangabe (bis zu drei Versuche)."""
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"],
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": fill(USER_PROMPT_TEMPLATE, anhangtext=text)},
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=MAX_COMPLETION_TOKENS,
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                return {"error": str(e), "begruendungen": [], "meta": {"qualitaet": "ERROR"}}


def _worker(idx, text, client):
    if pd.isna(text) or str(text).strip() in ("", "nan"):
        return idx, EMPTY_RESULT
    return idx, extract_single(str(text), client)


def extract_all(df: pd.DataFrame, checkpoint: Path, workers: int) -> pd.DataFrame:
    """Extraktion parallel; Zwischenstand alle CHECKPOINT_EVERY Anhangangaben."""
    if checkpoint.exists():
        df_result = pd.read_pickle(checkpoint)
        done = set(df_result.index[df_result["extraction_result"].notna()])
    else:
        df_result = df.copy()
        df_result["extraction_result"] = None
        done = set()

    todo = [idx for idx in df_result.index if idx not in done]
    print(f"{len(done)} bereits verarbeitet, {len(todo)} offen, {workers} parallele Aufrufe")
    if not todo:
        return df_result

    client = get_client()
    lock = threading.Lock()
    completed = errors = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_worker, idx, df_result.at[idx, "Anhangtext"], client): idx for idx in todo}
        for future in as_completed(futures):
            try:
                idx, result = future.result()
            except Exception as e:
                idx, result = futures[future], {"error": str(e), "begruendungen": [], "meta": {"qualitaet": "ERROR"}}
            df_result.at[idx, "extraction_result"] = json.dumps(result, ensure_ascii=False)
            with lock:
                errors += "error" in result
                completed += 1
                if completed % CHECKPOINT_EVERY == 0:
                    df_result.to_pickle(checkpoint)
                    print(f"{completed} von {len(todo)} verarbeitet ({errors} Fehler)")

    df_result.to_pickle(checkpoint)
    print(f"Fertig: {errors} Fehler bei {len(todo)} Anhangangaben")
    return df_result


def parse_results(df: pd.DataFrame) -> pd.DataFrame:
    """Zerlegt die JSON-Antworten; Kennzeichen je Oberkategorie und Anhangangabe."""
    def safe_parse(s):
        try:
            return json.loads(s) if pd.notna(s) else {}
        except Exception:
            return {}

    df = df.copy()
    parsed = df["extraction_result"].apply(safe_parse)
    df["begruendungen_list"] = parsed.apply(lambda x: x.get("begruendungen", []))
    df["anzahl_begruendungen"] = df["begruendungen_list"].apply(len)
    df["hat_begruendung"] = parsed.apply(lambda x: x.get("meta", {}).get("hat_begruendung", False))
    df["nur_standardsatz"] = parsed.apply(lambda x: x.get("meta", {}).get("nur_standardsatz", False))
    df["qualitaet"] = parsed.apply(lambda x: x.get("meta", {}).get("qualitaet", "UNBEKANNT"))
    for kat in KATEGORIEN:
        df[f"hat_{kat.lower()}"] = df["begruendungen_list"].apply(
            lambda lst, k=kat: any(b.get("kategorie", "").upper() == k for b in lst)
        )
    return df


def export_excel(df: pd.DataFrame, path: Path) -> None:
    illegal = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # in Excel unzulässige Steuerzeichen
    cols = ["Firma", "Negatives Eigenkapital", "anzahl_begruendungen", "hat_begruendung",
            "nur_standardsatz", "qualitaet"] + [f"hat_{k.lower()}" for k in KATEGORIEN]
    out = df[[c for c in cols if c in df.columns]].copy()
    out["begruendungen"] = df["begruendungen_list"].apply(
        lambda lst: illegal.sub("", json.dumps(lst, ensure_ascii=False, indent=1)) if lst else "")
    out.to_excel(path, index=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eingabe", default="data/anhangangaben.xlsx")
    parser.add_argument("--ausgabe", default="output/stufe2_extraktion.pkl")
    parser.add_argument("--workers", type=int, default=60)
    args = parser.parse_args()

    eingabe, ausgabe = Path(args.eingabe), Path(args.ausgabe)
    ausgabe.parent.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(eingabe) if eingabe.suffix.lower() == ".csv" else pd.read_excel(eingabe)
    print(f"{len(df):,} Anhangangaben geladen")

    df = parse_results(extract_all(df, ausgabe, args.workers))
    export_excel(df, ausgabe.with_suffix(".xlsx"))
    print(f"Ausgabe: {ausgabe} und {ausgabe.with_suffix('.xlsx')}")


if __name__ == "__main__":
    main()
