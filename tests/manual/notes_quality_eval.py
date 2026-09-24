"""Opt-in live-model regression using synthetic German conversations only.
Run evaluate(provider) with an in-memory Azure endpoint/model/key dictionary.
Output contains counts, never model text, real meetings or credentials.
"""
from engine.meeting_notes import generate_draft

CASES = [
    ("questions_only", "A: Wie funktioniert die Migration? B: Die Tabellen werden kopiert. A: Ist Azure dafür besser? B: Wissen wir noch nicht. A: Okay, nur eine Verständnisfrage, kein Arbeitsauftrag.", 0, 0),
    ("brainstorm", "A: Man könnte irgendwann einen CSV-Export anbieten und vielleicht eine Demo bauen. B: Wäre nett. A: Heute beschließen wir dazu nichts.", 0, 0),
    ("already_done", "A: Ist das Angebot schon raus? B: Ja, gestern an Frau Müller. A: Gut. Auch das Ticket? B: Eben angelegt.", 0, 0),
    ("explicit_assignment", "Alex: Robin, bitte baue den CSV-Export für die Meeting-Notizen. Robin: Ja, übernehme ich, mit Titel und Beschreibung.", 1, 0),
    ("research", "Alex: Moritz, kannst du prüfen, ob die Graph API Gruppenteilnehmer liefert? Moritz: Ja, ich prüfe das bis morgen und melde das Ergebnis.", 1, 0),
    ("bundle", "Robin: Ich sende Frau Müller morgen das Angebot mit der Preisübersicht und dem bereits vorhandenen Datenblatt als Anhang. Alex: Genau, danke.", 1, 0),
    ("known_order", "Alex: Robin, bitte ändere das Deployment so, dass es Datenmigrationen vor dem Start ausführt, teste das und merge nach dem Review. Robin: Mache ich. Alex: Review immer vor dem Merge.", 1, 0),
    ("canceled", "Alex: Moritz, erstelle bitte einen PoC. Moritz: Okay. Alex: Halt, der PoC ist nicht mehr nötig, streichen wir komplett. Moritz: Gut.", 0, 0),
    ("missing_scope", "Alex: Robin, baue bitte den besprochenen Export. Robin: Ja, mache ich. Ist das Ziel CSV oder OneNote? Alex: Das ist noch offen und muss vor der Umsetzung festgelegt werden.", 1, 1),
    ("unassigned_agreement", "A: Wir beschließen, dem Kunden bis Freitag ein schriftliches Angebot zu senden. B: Einverstanden. A: Wer das übernimmt, legen wir später fest.", 1, 0),
    ("tool_automation", "A: Der Meeting-Assistent speichert nach dem Anruf automatisch die Notizen. B: Gut, und das Transkript wird automatisch gelöscht. A: Genau, dafür müssen wir nichts tun.", 0, 0),
    ("question_not_research", "A: Gibt es Alternativen zu Azure? B: Vielleicht. A: Welche können Deutsch? B: Weiß ich nicht. A: Interessant, wir sprechen später mal darüber.", 0, 0),
]


def evaluate(provider):
    import concurrent.futures
    def run(case):
        name,text,tasks,questions=case
        draft=generate_draft(text,provider)
        actual=(len(draft["tasks"]),sum(len(t["questions"]) for t in draft["tasks"]))
        return dict(case=name,expected=[tasks,questions],actual=list(actual),passed=actual==(tasks,questions))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(run,CASES))
    return dict(results=results,passed=all(r["passed"] for r in results))
