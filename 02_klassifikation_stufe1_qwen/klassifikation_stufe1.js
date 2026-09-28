'use strict';
/**
 * Stufe 1: deduktive Klassifikation mit einem Qwen-Modell, lokal über Ollama.
 *
 * Jede Anhangangabe wird mit jedem Prompt einzeln an das Modell geschickt;
 * die Nutzernachricht besteht aus dem Prompt, einem Zeilenumbruch und dem
 * Anhangtext. Das Modell antwortet mit 0 oder 1. Ein vom Modell ausgegebener
 * Denkprozess (<think>…</think>) wird abgetrennt und in einer eigenen Spalte
 * gespeichert. Je Anhangangabe und Prompt erfolgt ein Aufruf.
 * (Bereinigte Fassung der Stapelverarbeitung der ursprünglich verwendeten
 * Web-Oberfläche; nur der Ollama-Weg.)
 *
 * Eingabe:  Excel-Tabelle, erste Zeile Spaltenköpfe, Anhangtext in Spalte B
 *           (Spalten: Firma | Anhangtext | Negatives Eigenkapital)
 * Prompts:  Verzeichnis mit einer .txt-Datei je Prompt (alphabetische Reihenfolge)
 * Ausgabe:  Excel-Tabelle = Eingabespalten + je Prompt Antwort und Denkprozess
 *
 * Aufruf:
 *   node 02_klassifikation_stufe1_qwen/klassifikation_stufe1.js --modell <ollama-modell>
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
        console.error('Bitte das Ollama-Modell angeben, z. B. --modell qwen2.5:14b');
        process.exit(1);
    }
    return args;
}

function loadPrompts(dir) {
    const files = fs.readdirSync(dir).filter((f) => f.endsWith('.txt')).sort();
    return files.map((file) => {
        const text = fs.readFileSync(path.join(dir, file), 'utf-8').replace(/\r\n/g, '\n').trim();
        if (text.startsWith('# PLATZHALTER')) {
            console.error(`${path.join(dir, file)} enthält noch keinen Prompt-Text (Platzhalter).`);
            process.exit(1);
        }
        return { name: path.basename(file, '.txt'), text };
    });
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
    return data?.message?.content || '';
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
                answer = `Error: ${String(err)}`;
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
