# Prompts

Hier liegen die deutschsprachigen Original-Prompts, jeweils als reine Textdatei. Die Skripte lesen die Prompts direkt aus diesem Verzeichnis.

| Datei bzw. Verzeichnis | Stufe | Verwendung |
|---|---|---|
| `stufe1_qwen/` | 1 | eine Datei je Merkmal; die Nutzernachricht besteht aus dem Prompt, einem Zeilenumbruch und dem Anhangtext |
| `stufe1_qwen_folgelauf_stille_reserven/` | 1 | Folgelauf zu stillen Reserven mit zwei präzisierten Prompts |
| `stufe2_extraktion_system.txt` | 2 | Systemnachricht der Extraktion |
| `stufe2_extraktion_user.txt` | 2 | Nutzernachricht der Extraktion; `{anhangtext}` wird ersetzt |
| `stufe3_brauchbarkeit_system.txt` | 3 | Systemnachricht der Brauchbarkeitsbewertung (Entscheidungsregeln, Konstellationen, Ankerbeispiele) |
| `stufe3_brauchbarkeit_user.txt` | 3 | Nutzernachricht; `{anhangtext}`, `{begruendungen_json}` und `{neg_ek_eur}` werden ersetzt |

Die Ankerbeispiele im Prompt der Stufe 3 folgen Rebernig/Scheinecker, Zur Erläuterungspflicht des Geschäftsführers bei Überschuldung – Theorie und Praxis, ÖRPfl 2/2014, 27.
