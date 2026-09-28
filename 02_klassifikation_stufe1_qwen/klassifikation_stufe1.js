'use strict';
/**
 * Stufe 1: deduktive Klassifikation mit Qwen3 (8 Mrd. Parameter; qwen3:8b),
 * lokal über Ollama.
 *
 * Jede Anhangangabe wird mit jedem Prompt einzeln an das Modell geschickt;
 * die Nutzernachricht besteht aus dem Prompt, einem Zeilenumbruch und dem
 * Anhangtext. Das Modell antwortet mit 0 oder 1. Qwen3 arbeitet standardmäßig
 * mit einer Denkphase; deren Protokoll (<think>…</think> bzw. das Feld
 * "thinking" der Ollama-Antwort) wird abgetrennt und in einer eigenen Spalte
 * gespeichert. Je Anhangangabe und Prompt erfolgt ein Aufruf.
 * (Bereinigte Fassung der Stapelverarbeitung der ursprünglich verwendeten
 * Web-Oberfläche; nur der Ollama-Weg.)
 *
 * Eingabe:  Excel-Tabelle, erste Zeile Spaltenköpfe, Anhangtext in Spalte B
 *           (Spalten: Firma | Anhangtext | Negatives Eigenkapital)
 * Prompts:  Verzeichnis mit einer .txt-Datei je Prompt (alphabetische Reihenfolge).
 *           Zeilen, die mit # beginnen, sind Kommentare und werden nicht an das
 *           Modell geschickt. Enthält eine Datei keinen Prompt-Text, bricht das
 *           Skript ab (die Volltexte der finalen Stufe-1-Prompts sind nicht
 *           erhalten; siehe README).
 * Ausgabe:  Excel-Tabelle = Eingabespalten + je Prompt Antwort und Denkprozess
 *
 * Aufruf:
 *   node 02_klassifikation_stufe1_qwen/klassifikation_stufe1.js [--modell qwen3:8b]
 *        [--eingabe data/anhangangaben.xlsx] [--prompts prompts/stufe1_qwen]
 *        [--ausgabe output/stufe1_klassifikation.xlsx]
 *        [--temperatur 0.7] [--system <datei>] [--limit <n>]
 */
const fs = require('fs');
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });
const XLSX = require('xlsx');

const OLLAMA_HOST = (process.env.OLLAMA_HOST || 'http://localhost:11434').replace(/\/+$/, '');
const EXCEL_CELL_LIMIT = 32760; // Excel erlaubt höchstens 32.767 Zeichen je Zelle
const SAVE_EVERY = 25;          // Zwischenstand alle n Anhangangaben speichern

function parseArgs(argv) {
    const args = {
        modell: 'qwen3:8b', // im Beitrag verwendetes Modell
        eingabe: 'data/anhangangaben.xlsx',
        prompts: 'prompts/stufe1_qwen',
        ausgabe: 'output/stufe1_klassifikation.xlsx',
        temperatur: 0.7, // Voreinstellung der ursprünglichen Anwendung
        system: null,
        limit: null
    };
    for (let i = 0; i < argv.length; i++) {
        const next = () => argv[++i];
        switch (argv[i]) {
            case '--modell': args.modell = next(); break;
            case '--eingabe': args.eingabe = next(); break;
            case '--prompts': args.prompts = next(); break;
            case '--ausgabe': args.ausgabe = next(); break;
            case '--temperatur': args.temperatur = Number(next()); break;
            case '--system': args.system = next(); break;
            case '--limit': args.limit = Number(next()); break;
        }
    }
    if (!args.modell) {
        console.error('Bitte das Ollama-Modell angeben, z. B. --modell qwen3:8b');
        process.exit(1);
    }
    return args;
}

// Prompt-Dateien einlesen; Zeilen, die mit # beginnen, sind Kommentare
function loadPrompts(dir) {
    const files = fs.readdirSync(dir).filter((f) => f.endsWith('.txt')).sort();
    if (files.length === 0) {
        console.error(`Im Verzeichnis ${dir} liegt keine Prompt-Datei (.txt).`);
        process.exit(1);
    }
    const prompts = files.map((file) => {
        const raw = fs.readFileSync(path.join(dir, file), 'utf-8').replace(/^\uFEFF/, '').replace(/\r\n/g, '\n');
        const text = raw.split('\n').filter((line) => !line.startsWith('#')).join('\n').trim();
        return { name: path.basename(file, '.txt'), file: path.join(dir, file), text };
    });
    const empty = prompts.filter((p) => !p.text);
    if (empty.length > 0) {
        console.error('Folgende Prompt-Dateien enthalten keinen Prompt-Text, sondern nur Kommentarzeilen (#):');
        for (const p of empty) console.error(`  ${p.file}`);
        console.error('Die Volltexte der im Beitrag verwendeten Stufe-1-Prompts sind nicht erhalten (siehe README, Abschnitt Stufe 1).');
        console.error('Bitte in jede dieser Dateien unterhalb der Kommentarzeilen einen eigenen Prompt einfügen und das Skript erneut starten.');
        process.exit(1);
    }
    return prompts;
}

// Denkprozess (<think>…</think>) von der eigentlichen Antwort trennen
function parseThinking(rawText) {
    const text = typeof rawText === 'string' ? rawText : String(rawText || '');
    const thinkRegex = /<think>[\s\S]*?<\/think>/gi;
    const thinking = (text.match(thinkRegex) || [])
        .map((seg) => seg.replace(/^<think>/i, '').replace(/<\/think>$/i, '').trim())
        .filter(Boolean)
        .join('\n\n');
    return { final: text.replace(thinkRegex, '').trim(), thinking };
}

async function callOllama(model, messages, temperature) {
    const response = await fetch(`${OLLAMA_HOST}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, messages, stream: false, options: { temperature } })
    });
    const data = await response.json();
    if (!response.ok || data?.error) {
        throw new Error(data?.error || `HTTP ${response.status}`);
    }
    const content = data?.message?.content || '';
    // Neuere Ollama-Versionen liefern die Denkphase getrennt im Feld "thinking"
    const thinking = data?.message?.thinking || '';
    return thinking ? `<think>${thinking}</think>${content}` : content;
}

const truncateCell = (value) => {
    const s = value == null ? '' : String(value);
    return s.length <= EXCEL_CELL_LIMIT ? s : s.slice(0, EXCEL_CELL_LIMIT);
};

function writeWorkbook(rows, file) {
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), 'Results');
    XLSX.writeFile(wb, file);
}

async function main() {
    const args = parseArgs(process.argv.slice(2));
    const prompts = loadPrompts(args.prompts);
    const systemPrompt = args.system ? fs.readFileSync(args.system, 'utf-8').trim() : '';

    const wb = XLSX.readFile(args.eingabe);
    const inputRows = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, defval: '' });
    const header = inputRows[0] || [];
    let dataRows = inputRows.slice(1);
    if (args.limit) dataRows = dataRows.slice(0, args.limit);

    const outputRows = [[...header, ...prompts.flatMap((p) => [p.name, `${p.name} (thinking)`])]];
    fs.mkdirSync(path.dirname(path.resolve(args.ausgabe)), { recursive: true });
    console.log(`${dataRows.length} Anhangangaben x ${prompts.length} Prompts, Modell ${args.modell}`);

    for (let i = 0; i < dataRows.length; i++) {
        const row = dataRows[i];
        const context = String(row[1] ?? '');
        const outRow = row.map(truncateCell);
        for (const prompt of prompts) {
            const messages = [
                { role: 'system', content: systemPrompt },
                { role: 'user', content: `${prompt.text}\n${context}` }
            ];
            let answer;
            try {
                answer = await callOllama(args.modell, messages, args.temperatur);
            } catch (err) {
                answer = `Error: ${err?.message || String(err)}`;
            }
            const { final, thinking } = parseThinking(answer);
            outRow.push(truncateCell(final), truncateCell(thinking));
        }
        outputRows.push(outRow);
        if ((i + 1) % SAVE_EVERY === 0) {
            writeWorkbook(outputRows, args.ausgabe);
            console.log(`${i + 1} von ${dataRows.length} verarbeitet`);
        }
    }
    writeWorkbook(outputRows, args.ausgabe);
    console.log(`Fertig -> ${args.ausgabe}`);
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
