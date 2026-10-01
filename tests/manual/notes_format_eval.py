"""Opt-in Azure formatting evaluation with synthetic content only; no real meetings."""
import json
import re
from engine import prompts
from engine.meeting_notes import generate_draft

TOPICS = [
    "Migration: Die Bestandsdaten enthalten Dubletten und fehlende Kundennummern. Eine direkte Übernahme wäre riskant. Die Gruppe bevorzugt einen stufenweisen Import mit Validierung. Der Pilot umfasst nur die aktiven Kunden. Historische Datensätze bleiben vorerst im bisherigen System. Eine produktive Umstellung wurde noch nicht beschlossen.",
    "Betrieb: Für die Einführung wurden ein einmaliger Komplettwechsel und ein paralleler Betrieb verglichen. Der Parallelbetrieb ermöglicht Rückfragen und einen Rückfall auf das bestehende System. Er verursacht aber zusätzlichen Abstimmungsbedarf. Die Beteiligten entscheiden sich für einen begrenzten Parallelbetrieb während des Piloten.",
    "Schnittstellen: CSV wird als Ziel für technische Arbeitsaufträge bevorzugt. OneNote dient längeren Gesprächsnotizen. Das Adressbuch soll die Kundenkontakte führen. Eine automatische Übertragung ist noch nicht beschlossen. Offene Frage ist, ob das Adressbuch bereits einen geeigneten Import unterstützt.",
    "Datenschutz: Für den Pilot sollen nur erforderliche Kontaktdaten übernommen werden. Gesprächsaufnahmen sind nicht Bestandteil des Imports. Die Aufbewahrungsdauer der Notizen ist noch offen. Zugriffe sollen über bestehende Benutzergruppen geregelt werden. Die Gruppe hält dieses Vorgehen als Rahmen für den Pilot fest.",
    "Budget: Externe Beratung und laufender Betrieb sind getrennte Kostenpositionen. Ein Festpreisangebot liegt nicht vor. Die Gruppe will keine ungeprüfte Kostenzusage treffen. Die bisherigen Zahlen sind lediglich Schätzungen. Über eine Freigabe wird erst entschieden, wenn Umfang und Kosten ausreichend geklärt sind.",
    "Einführung: Vertrieb und Support sollen im Pilot vertreten sein. Die Oberfläche muss mit Tastatur bedienbar sein. Die Beteiligten halten Verständlichkeit und eine kurze Einarbeitung für wesentlich. Schulungstermine sind noch nicht vereinbart. Ein gemeinsamer Start für alle Abteilungen wurde ausdrücklich verworfen.",
]

def evaluate(provider):
    # Revisited topics throughout a simulated hour, with a deliberately flat prior summary.
    conversation = "\n".join(f"[{minute:02d}:00] Person A: {TOPICS[index % len(TOPICS)]} Person B: Das bestätigt den bisherigen Diskussionsstand. Wir unterscheiden weiterhin zwischen Beschlüssen und offenen Fragen. Dazu wird kein zusätzlicher Arbeitsauftrag vergeben."
                             for index, minute in enumerate(range(0, 60, 3)))
    flat = {"summary":" ".join(TOPICS),"decisions":"","openQuestions":"","tasks":[]}
    cases = {
        "final_hour_scenario": conversation,
        "update_flat_previous_notes": prompts.NOTES_PREVIOUS + "\n" + json.dumps(flat, ensure_ascii=False) + "\n" + prompts.NOTES_SEGMENTS + "\nPerson A: Wir halten an den genannten Beschlüssen fest. Bitte den Piloten nicht mit einer vollständigen Produktivfreigabe verwechseln. Person B: Einverstanden; die Kostenfreigabe bleibt offen.",
    }
    results, drafts = [], {}
    for name, text in cases.items():
        draft = generate_draft(text, provider); drafts[name] = draft
        summary = draft["summary"]
        blocks = [part for part in re.split(r"\n\s*\n", summary) if part.strip()]
        lists = sum(len(re.findall(r"(?m)^\s*- \S", draft[field])) for field in ("summary", "decisions", "openQuestions"))
        passed = len(blocks) >= 3 and max(map(len, blocks), default=0) <= 1200 and lists >= 2 and len(draft["tasks"]) == 0
        results.append(dict(case=name, blocks=len(blocks), listItemsAcrossNotes=lists, longestBlock=max(map(len,blocks),default=0), tasks=len(draft["tasks"]), passed=passed))
    return {"results":results,"passed":all(r["passed"] for r in results)}, drafts
