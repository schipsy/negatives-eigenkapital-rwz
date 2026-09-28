# Prompts

Hier liegen die deutschsprachigen Original-Prompts, jeweils als reine Textdatei. Die Skripte lesen die Prompts direkt aus diesem Verzeichnis.

Ausnahme: Die Volltexte der sieben Prompts des finalen Stufe-1-Laufs (31.08.2025) sind nicht erhalten, weil die Klassifikationsanwendung sie nicht archiviert hat. Die Dateien in `stufe1_qwen/` enthalten deshalb nur Kommentarzeilen mit dem erhaltenen Anfang des Prompts (die ersten 50 Zeichen), dem Merkmal und dem Antwortformat (0 oder 1). Die Prompts wurden nicht nachträglich rekonstruiert (siehe README, Abschnitt Stufe 1).

| Datei bzw. Verzeichnis | Stufe | Verwendung |
|---|---|---|
| `stufe1_qwen/` | 1 | eine Datei je Merkmal; Volltext nicht erhalten, nur Kommentarzeilen mit dem erhaltenen Anfang; für einen eigenen Lauf ist der eigene Prompt unterhalb der Kommentarzeilen einzufügen |
| `stufe1_qwen_folgelauf_stille_reserven/` | 1 | Folgelauf zu stillen Reserven mit zwei präzisierten Prompts (Volltext erhalten) |
| `stufe2_extraktion_system.txt` | 2 | Systemnachricht der Extraktion |
| `stufe2_extraktion_user.txt` | 2 | Nutzernachricht der Extraktion; `{anhangtext}` wird ersetzt |
| `stufe3_brauchbarkeit_system.txt` | 3 | Systemnachricht der Brauchbarkeitsbewertung (Entscheidungsregeln, Konstellationen, Ankerbeispiele) |
| `stufe3_brauchbarkeit_user.txt` | 3 | Nutzernachricht; `{anhangtext}`, `{begruendungen_json}` und `{neg_ek_eur}` werden ersetzt |

Stufe 1: Die Nutzernachricht besteht aus dem Prompt, einem Zeilenumbruch und dem Anhangtext. Zeilen, die mit `#` beginnen, sind Kommentare und werden nicht an das Modell geschickt. Enthält eine Datei keinen Prompt-Text, bricht `klassifikation_stufe1.js` mit einem Hinweis ab.

Die Ankerbeispiele im Prompt der Stufe 3 folgen Rebernig/Scheinecker, Zur Erläuterungspflicht des Geschäftsführers bei Überschuldung – Theorie und Praxis, ÖRPfl 2/2014, 27.
