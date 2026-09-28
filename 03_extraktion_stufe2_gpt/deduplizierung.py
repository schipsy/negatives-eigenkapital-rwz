"""
Stufe 2 (Fortsetzung): semantische Deduplizierung der Begründungstypen.

1. Alle Kurzbezeichnungen ("typ") aus der Extraktion sammeln und zählen.
2. Jede eindeutige Bezeichnung mit dem Embedding-Modell kodieren
   (Azure OpenAI, text-embedding-ada-002, 1.536 Dimensionen).
3. Greedy-Zusammenführung: Bezeichnungen mit einer Kosinus-Ähnlichkeit >= 0,92
   werden der häufigsten noch nicht zugeordneten Bezeichnung ihrer Gruppe zugeordnet.
4. Ketten (A -> B, B -> C) werden transitiv aufgelöst, sodass jede Bezeichnung auf
   ihren endgültigen Zieltyp zeigt (Anzahl Cluster = eindeutige Typen - Zuordnungen).
5. Anwendung auf die Anhangangaben: Typ ersetzen, gleiche Kombination aus Typ und
   Kategorie je Anhangangabe nur einmal zählen, Kennzeichen der sechs
   Oberkategorien neu bilden.

Eingabe: Pickle aus extraktion_stufe2.py
Ausgabe: Pickle mit den deduplizierten Begründungen je Anhangangabe sowie ein
         Zwischenstand mit Embeddings und Zuordnungen (wird bei erneutem Aufruf verwendet).

Aufruf:
  python 03_extraktion_stufe2_gpt/deduplizierung.py
         [--eingabe output/stufe2_extraktion.pkl] [--ausgabe output/stufe2_dedup.pkl]
"""
import argparse
import json
import os
import pickle
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from sklearn.metrics.pairwise import cosine_similarity

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env")

EMBEDDING_API_VERSION = "2023-05-15"
EMBEDDING_DIM = 1536
THRESHOLD = 0.92
BLOCK_SIZE = 2000
KATEGORIEN = ["VERMÖGEN", "FINANZIERUNG", "PROGNOSE", "HAFTUNG", "RANGRUECKTRITT", "SONSTIGE"]


def load_data(path: Path) -> pd.DataFrame:
    df = pd.read_pickle(path)

    def safe_parse(s):
        try:
            return json.loads(s) if pd.notna(s) else {}
        except Exception:
            return {}

    df["begruendungen_list"] = df["extraction_result"].apply(safe_parse).apply(lambda x: x.get("begruendungen", []))
    return df[df["extraction_result"].notna()].copy()


def get_embeddings_batch(texts, max_retries=5, batch_size=500) -> np.ndarray:
    """Embeddings über die Azure-OpenAI-REST-Schnittstelle, stapelweise."""
    url = (f"{os.environ['AZURE_OPENAI_ENDPOINT'].rstrip('/')}/openai/deployments/"
           f"{os.environ['AZURE_OPENAI_EMBEDDING_DEPLOYMENT']}/embeddings?api-version={EMBEDDING_API_VERSION}")
    headers = {"Content-Type": "application/json", "api-key": os.environ["AZURE_OPENAI_API_KEY"]}
    batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
    all_emb = []
    for n, batch in enumerate(batches, start=1):
        for attempt in range(max_retries):
            try:
                response = requests.post(url, headers=headers, json={"input": batch}, timeout=60)
                response.raise_for_status()
                all_emb.extend(item["embedding"] for item in response.json()["data"])
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(5 * (attempt + 1))
                else:
                    print(f"WARNUNG: Embedding-Fehler in Stapel {n}: {e} (Nullvektoren eingesetzt)")
                    all_emb.extend([[0.0] * EMBEDDING_DIM] * len(batch))
        time.sleep(1)
    return np.array(all_emb)


def greedy_merge(unique_typen, embeddings, counter, threshold=THRESHOLD, block_size=BLOCK_SIZE) -> dict:
    """Greedy-Zusammenführung; Rückgabe {typ: kanonischer_typ}."""
    merge_groups = {}
    processed = set()
    for i in range(0, len(unique_typen), block_size):
        sim = cosine_similarity(embeddings[i:i + block_size], embeddings)
        for bi in range(sim.shape[0]):
            if i + bi in processed:
                continue
            similar = np.where(sim[bi] >= threshold)[0]
            if len(similar) > 1:
                candidates = [unique_typen[si] for si in similar if si not in processed]
                if not candidates:
                    continue
                canonical = max(candidates, key=lambda t: counter[t])
                for si in similar:
                    if unique_typen[si] != canonical:
                        merge_groups[unique_typen[si]] = canonical
                        processed.add(si)
    return merge_groups


def resolve_transitive(merge_groups: dict) -> dict:
    """Löst Zuordnungsketten auf: jeder Typ zeigt auf seinen endgültigen Zieltyp."""
    resolved = {}
    for typ in merge_groups:
        target, seen = typ, set()
        while target in merge_groups and target not in seen:
            seen.add(target)
            target = merge_groups[target]
        resolved[typ] = target
    return resolved


def apply_deduplication(df: pd.DataFrame, merge_groups: dict) -> pd.DataFrame:
    """Ersetzt die Typen und zählt gleiche (Typ, Kategorie) je Anhangangabe nur einmal."""
    def dedup(lst):
        seen, result = set(), []
        for b in lst:
            b = dict(b)
            if "typ" in b:
                b["typ_original"] = b["typ"]
                b["typ"] = merge_groups.get(b["typ"], b["typ"])
            key = (b.get("typ", ""), b.get("kategorie", ""))
            if key not in seen:
                seen.add(key)
                result.append(b)
        return result

    df = df.copy()
    df["begruendungen_list"] = df["begruendungen_list"].apply(dedup)
    df["anzahl_begruendungen"] = df["begruendungen_list"].apply(len)
    for kat in KATEGORIEN:
        df[f"hat_{kat.lower()}"] = df["begruendungen_list"].apply(
            lambda lst, k=kat: any(b.get("kategorie", "").upper() == k for b in lst))
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eingabe", default="output/stufe2_extraktion.pkl")
    parser.add_argument("--ausgabe", default="output/stufe2_dedup.pkl")
    args = parser.parse_args()
    ausgabe = Path(args.ausgabe)
    ausgabe.parent.mkdir(parents=True, exist_ok=True)
    zwischenstand = ausgabe.with_name(ausgabe.stem + "_embeddings.pkl")

    df = load_data(Path(args.eingabe))
    if zwischenstand.exists():
        with open(zwischenstand, "rb") as f:
            saved = pickle.load(f)
        merge_groups, unique_typen, counter = saved["merge_groups"], saved["unique_typen"], saved["counter"]
    else:
        counter = Counter(b["typ"] for lst in df["begruendungen_list"] for b in lst if "typ" in b)
        unique_typen = list(counter.keys())
        embeddings = get_embeddings_batch(unique_typen)
        merge_groups = greedy_merge(unique_typen, embeddings, counter)
        with open(zwischenstand, "wb") as f:
            pickle.dump({"merge_groups": merge_groups, "embeddings": embeddings,
                         "unique_typen": unique_typen, "counter": counter}, f)

    merge_transitiv = resolve_transitive(merge_groups)
    n_cluster = len({merge_transitiv.get(t, t) for t in unique_typen})
    print(f"{sum(counter.values()):,} Nennungen, {len(unique_typen):,} eindeutige Typen, "
          f"{len(merge_groups):,} Zuordnungen -> {n_cluster:,} Cluster")

    df = apply_deduplication(df, merge_transitiv)
    df.to_pickle(ausgabe)
    print(f"Ausgabe: {ausgabe}")


if __name__ == "__main__":
    main()
