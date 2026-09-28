'use strict';
/**
 * Schritt 1: HVD-Abruf der strukturierten Jahresabschlüsse und Extraktion der
 * Anhangangabe nach § 225 Abs 1 UGB samt Eigenkapital (HGB_224_3_A).
 *
 * Ausgangspunkt ist ein Verzeichnis mit Firmenbuch-Kurzinformationen
 * (XML „AUSZUG_V2_RESPONSE“, Umfang „Kurzinformation“, eine Datei je Rechtsträger).
 * Je Rechtsträger:
 *   1. Firmenbuchnummer und Status aus der Kurzinformation lesen;
 *      gelöschte Rechtsträger werden ohne Abruf übersprungen.
 *   2. SUCHEURKUNDE: Urkundenliste abfragen und die strukturierte Einreichung
 *      (CONTENTTYPE application/xml) mit dem jüngsten Bilanzstichtag wählen.
 *   3. URKUNDE: Jahresabschluss-XML abrufen (Base64) und dekodieren.
 *   4. Eigenkapital (HGB_224_3_A/BETRAG) lesen; ist es negativ, den Text der
 *      Anhangangabe übernehmen (HGB_224_3_A/ANHANG, ersatzweise HGB_Form_3_5/TEXT).
 * Ergebnis: eine JSON-Zeile je Rechtsträger (NDJSON). Ein abgebrochener Lauf
 * wird beim nächsten Aufruf fortgesetzt.
 *
 * Aufruf:
 *   node 01_hvd_abruf/hvd_abruf.js --kurzinfo <verzeichnis>
 *        [--ausgabe output/hvd_abruf.ndjson] [--pause-ms 3000]
 */
const fs = require('fs');
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });
const axios = require('axios');
const { DOMParser } = require('@xmldom/xmldom');

const SOAP_ENDPOINT = 'https://justizonline.gv.at/jop/api/at.gv.justiz.fbw/ws';

function parseArgs(argv) {
    const args = { ausgabe: 'output/hvd_abruf.ndjson', pauseMs: 3000 };
    for (let i = 0; i < argv.length; i++) {
        if (argv[i] === '--kurzinfo') args.kurzinfo = argv[++i];
        else if (argv[i] === '--ausgabe') args.ausgabe = argv[++i];
        else if (argv[i] === '--pause-ms') args.pauseMs = Number(argv[++i]);
    }
    if (!args.kurzinfo) {
        console.error('Aufruf: node 01_hvd_abruf/hvd_abruf.js --kurzinfo <verzeichnis> [--ausgabe <datei.ndjson>] [--pause-ms 3000]');
        process.exit(1);
    }
    return args;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function createClient() {
    const apiKey = process.env.HVD_API_KEY;
    if (!apiKey) {
        console.error('HVD_API_KEY ist nicht gesetzt (siehe .env.example).');
        process.exit(1);
    }
    const client = axios.create({
        timeout: 60000,
        headers: {
            'Content-Type': 'application/soap+xml;charset=UTF-8',
            'X-API-KEY': apiKey,
            'Accept': 'application/soap+xml'
        }
    });
    // Bis zu drei Wiederholungen bei Serverfehlern (5xx) oder Verbindungsabbruch
    client.interceptors.response.use(
        (response) => response,
        async (error) => {
            const config = error.config;
            if (!config) return Promise.reject(error);
            config.retry = config.retry || 0;
            if (config.retry < 3 && (error.response?.status >= 500 || error.code === 'ECONNRESET')) {
                config.retry++;
                await sleep(1000 * config.retry);
                return client.request(config);
            }
            return Promise.reject(error);
        }
    );
    return client;
}

// Firmenbuchnummer und Status aus einer Kurzinformation
function readKurzinformation(xmlContent) {
    const xmlDoc = new DOMParser().parseFromString(xmlContent, 'text/xml');
    const root = xmlDoc.documentElement;
    const fnr = root.getAttribute('ns1:FNR') || root.getAttribute('FNR');
    if (!fnr) throw new Error('FNR not found in XML file');

    // Gelöscht, wenn die Firma das Kennzeichen MIT_FIRMA_GELOESCHT_DURCH_VNR trägt
    // oder ein Vollzug einen Löschungsantrag enthält
    let isDeleted = false;
    const firmaElements = xmlDoc.getElementsByTagName('ns1:FI_DKZ02');
    for (let i = 0; i < firmaElements.length; i++) {
        if (firmaElements[i].getAttribute('ns1:MIT_FIRMA_GELOESCHT_DURCH_VNR')) {
            isDeleted = true;
            break;
        }
    }
    if (!isDeleted) {
        const vollzElements = xmlDoc.getElementsByTagName('ns1:VOLLZ');
        for (let i = 0; i < vollzElements.length; i++) {
            const antragstext = vollzElements[i].getElementsByTagName('ns1:ANTRAGSTEXT')[0]?.textContent || '';
            if (antragstext.toLowerCase().includes('löschung')) {
                isDeleted = true;
                break;
            }
        }
    }
    return { fnr, status: isDeleted ? 'GELÖSCHT' : 'AKTIV' };
}

// SUCHEURKUNDE: Schlüssel der strukturierten Einreichung mit dem jüngsten Stichtag
async function getLatestXmlKey(client, fnr) {
    const request = `<?xml version="1.0" encoding="UTF-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Header/>
  <soap12:Body>
    <ns17:SUCHEURKUNDEREQUEST xmlns:ns17="ns://firmenbuch.justiz.gv.at/Abfrage/SucheUrkundeRequest">
      <ns17:FNR>${fnr.replace(/\s+/g, '')}</ns17:FNR>
    </ns17:SUCHEURKUNDEREQUEST>
  </soap12:Body>
</soap12:Envelope>`;
    const response = await client.post(SOAP_ENDPOINT, request);
    const xmlDoc = new DOMParser().parseFromString(response.data, 'text/xml');
    const results = xmlDoc.getElementsByTagName('ns16:ERGEBNIS');
    let latestKey = null;
    let latestDate = null;
    let eingereicht = null;
    for (let i = 0; i < results.length; i++) {
        const r = results[i];
        const contentType = r.getElementsByTagName('ns16:CONTENTTYPE')[0]?.textContent;
        const stichtag = r.getElementsByTagName('ns16:STICHTAG')[0]?.textContent;
        const key = r.getElementsByTagName('ns16:KEY')[0]?.textContent;
        if (contentType === 'application/xml' && stichtag && key) {
            if (!latestDate || stichtag > latestDate) {
                latestDate = stichtag;
                latestKey = key;
                eingereicht = r.getElementsByTagName('ns16:EINGEREICHT')[0]?.textContent || 'Unbekannt';
            }
        }
    }
    return { key: latestKey, eingereicht };
}

// URKUNDE: Inhalt (Base64) abrufen und dekodieren
async function getXmlContent(client, key) {
    const request = `<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <sch11:URKUNDEREQUEST xmlns:sch11="ns://firmenbuch.justiz.gv.at/Abfrage/UrkundeRequest">
      <sch11:KEY>${key}</sch11:KEY>
    </sch11:URKUNDEREQUEST>
  </soap12:Body>
</soap12:Envelope>`;
    const response = await client.post(SOAP_ENDPOINT, request);
    const xmlDoc = new DOMParser().parseFromString(response.data, 'text/xml');
    const content = xmlDoc.getElementsByTagName('ns8:CONTENT')[0]?.textContent;
    return content ? Buffer.from(content, 'base64').toString('utf-8') : null;
}

// Eigenkapital (HGB_224_3_A) und Anhangangabe nach § 225 Abs 1 UGB
function extractEquityAndAnnex(xmlDoc) {
    const getText = (tag) => xmlDoc.getElementsByTagName(tag)[0]?.textContent || '';
    let hgb224Analysis = '';
    let hgb224Betrag = null;

    const hgb224Element = xmlDoc.getElementsByTagName('HGB_224_3_A')[0];
    if (hgb224Element) {
        const betrag = hgb224Element.getElementsByTagName('BETRAG')[0]?.textContent;
        hgb224Betrag = betrag ? parseFloat(betrag) : null;
        if (betrag && parseFloat(betrag) < 0) {
            const anhang = hgb224Element.getElementsByTagName('ANHANG')[0]?.textContent;
            if (anhang) {
                hgb224Analysis = anhang;
            } else {
                const form3_5Text = xmlDoc.getElementsByTagName('HGB_Form_3_5')[0]
                    ?.getElementsByTagName('TEXT')[0]?.textContent;
                hgb224Analysis = form3_5Text || 'Anhang nicht gefunden';
            }
        } else {
            hgb224Analysis = 'HGB_224_3_A ist nicht negativ';
        }
    } else if (getText('HGBFORM') === 'JAB') {
        // Ersatzweise Eigenkapital aus HGB_224_3, falls HGB_224_3_A fehlt
        const betrag = xmlDoc.getElementsByTagName('HGB_224_3')[0]?.getElementsByTagName('BETRAG')[0]?.textContent;
        hgb224Betrag = betrag ? parseFloat(betrag) : null;
        if (hgb224Betrag && hgb224Betrag < 0) {
            hgb224Analysis = 'Eigenkapital negativ (JAB-Format, fallback)';
        } else if (hgb224Betrag !== null) {
            hgb224Analysis = 'Eigenkapital positiv oder null (JAB-Format, fallback)';
        }
    }
    return { hgb224Analysis, hgb224Betrag };
}

async function analyzeCompany(client, fnr, pauseMs) {
    try {
        await sleep(pauseMs);
        const key = await getLatestXmlKey(client, fnr);
        if (!key.key) return { error: 'No XML key found' };

        await sleep(pauseMs);
        const content = await getXmlContent(client, key.key);
        if (!content) return { error: 'No XML content found' };

        const xmlDoc = new DOMParser().parseFromString(content, 'text/xml');
        const getText = (tag) => xmlDoc.getElementsByTagName(tag)[0]?.textContent || '';
        const getAttr = (tag, attr) => xmlDoc.getElementsByTagName(tag)[0]?.getAttribute(attr) || '';
        return {
            rechtsform: getText('FORM'),
            stichtag: getText('ENDE'),
            eingereicht: key.eingereicht,
            einordnungUGB: getAttr('ALLG_JUSTIZ', 'EINORDNUNG'),
            klassifizierung: getAttr('ALLG_JUSTIZ', 'EINSTUFUNG'),
            hgbform: getText('HGBFORM'),
            abschlussart: getText('ABSCHLUSSART'),
            ...extractEquityAndAnnex(xmlDoc),
            error: null
        };
    } catch (error) {
        return { error: error.message };
    }
}

function listXmlFiles(dir) {
    const files = [];
    for (const item of fs.readdirSync(dir)) {
        const fullPath = path.join(dir, item);
        if (fs.statSync(fullPath).isDirectory()) files.push(...listXmlFiles(fullPath));
        else if (item.endsWith('.xml')) files.push(fullPath);
    }
    return files;
}

function alreadyProcessed(outputFile) {
    const done = new Set();
    if (!fs.existsSync(outputFile)) return done;
    for (const line of fs.readFileSync(outputFile, 'utf-8').split('\n')) {
        if (!line.trim()) continue;
        try { done.add(JSON.parse(line).filename); } catch (_) { /* unvollständige Zeile */ }
    }
    return done;
}

async function main() {
    const args = parseArgs(process.argv.slice(2));
    const client = createClient();
    fs.mkdirSync(path.dirname(path.resolve(args.ausgabe)), { recursive: true });

    const files = listXmlFiles(args.kurzinfo);
    const done = alreadyProcessed(args.ausgabe);
    const todo = files.filter((f) => !done.has(path.basename(f)));
    console.log(`${files.length} Kurzinformationen, davon ${todo.length} offen.`);

    let count = 0;
    for (const file of todo) {
        const filename = path.basename(file);
        let company;
        try {
            company = readKurzinformation(fs.readFileSync(file, 'utf-8'));
        } catch (error) {
            console.error(`${filename}: ${error.message}`);
            continue;
        }
        const result = company.status === 'GELÖSCHT'
            ? { filename, ...company, analysis: 'SKIPPED_DELETED' }
            : { filename, ...company, ...(await analyzeCompany(client, company.fnr, args.pauseMs)) };
        result.timestamp = new Date().toISOString();
        fs.appendFileSync(args.ausgabe, JSON.stringify(result) + '\n');
        if (++count % 1000 === 0) console.log(`${count} von ${todo.length} verarbeitet`);
    }
    console.log(`Fertig: ${count} Rechtsträger verarbeitet -> ${args.ausgabe}`);
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
