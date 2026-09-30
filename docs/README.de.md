# DAS Meeting Assistant

**Meeting-Notizen über dein PC-Audio. Automatisch für Microsoft Teams, manuell für Zoom und mehr.**

Der DAS Meeting Assistant macht aus Gesprächen Zusammenfassungen und bearbeitbare
Aufgaben. Die quelloffene Windows-Anwendung erfasst Mikrofon und PC-Audiowiedergabe.
Damit kannst du Teams, Zoom und andere Gespräche über die gewählten Audiogeräte
zusammenfassen. Bei Teams-Meetings und Telefonaten über Teams Phone kommt der
besonders praktische automatische Start hinzu, auch bei extern organisierten Meetings.
Du nutzt deine eigenen Azure-Ressourcen und speicherst die Notizen auf deinem PC
oder in OneNote.

[English](../README.md) · [Installation für KI-Agenten](agent-install.md) ·
[Data Assessment Solutions](https://www.data-assessment.com/)

## PC-Audio erfassen, bei Teams automatisch starten

Der Assistent erfasst **dein Mikrofon und die Audiowiedergabe deines PCs**, unabhängig
von der verwendeten Konferenzanwendung. Bei Teams benötigt er weder eine vom
Organisator gestartete Teams-Transkription noch Zugriff auf dessen Teams-Transkript.

- **Automatischer Start für Teams:** Bei aktivierter Automatik erkennt der laufende Assistent
  über deinen angemeldeten Teams-Präsenzstatus ein aktives Gespräch und beginnt mit
  den Notizen. Du musst die Transkription nicht für jedes Meeting einzeln starten.
- **Manueller Start für Zoom und mehr:** Starte die Aufnahme von Hand, um ein
  Zoom-Meeting oder andere Gespräche und Audioinhalte über das gewählte
  PC-Wiedergabegerät zusammen mit deinem Mikrofon zusammenzufassen. Am Ende stoppst
  du sie von Hand. Die automatische Gesprächserkennung gibt es derzeit nur für Teams.
- **Auch bei extern organisierten Meetings:** Ob Kunde, Partner oder eine andere
  Organisation eingeladen hat, spielt für die Audioerfassung keine Rolle. Du bist
  nicht auf die Freigabe des Transkripts durch den Organisator angewiesen.
- **Auch bei Telefonaten:** Der Assistent funktioniert ebenso bei Anrufen zu oder
  von normalen Telefonnummern über Teams Phone. Ein Teams-Meeting oder Kalendereintrag
  ist dafür nicht erforderlich.
- **Läuft lokal auf deinem PC:** Die Windows-Anwendung erfasst das gewählte Mikrofon
  und Wiedergabegerät. Das Gespräch muss über diese Geräte auf diesem PC laufen.

Die Audioerfassung erfolgt lokal; Spracherkennung und Zusammenfassung übernehmen
**deine konfigurierten Azure-Dienste**. Die KI-Verarbeitung arbeitet also nicht offline.
Für den automatischen Teams-Start sind Microsoft-Anmeldung und die Erkennung eines
aktiven Gesprächs erforderlich. Die manuelle Audioaufnahme benötigt weder ein
Teams-Meeting noch die Teams-Präsenzerkennung.

![Meeting-Notizen mit Zusammenfassung und bearbeitbaren Aufgaben](assets/meeting-notes.png)

*Echte Anwendungsoberfläche mit Beispieldaten. Die Meeting-Notizen gibt es auf
Deutsch und Englisch; die App-Sprache wählst du über die Flagge oben im Fenster.*

## Was die Anwendung kann

- Mikrofon und Gesprächswiedergabe über Azure Speech transkribieren und während
  des Meetings Notizen und Aufgabenvorschläge aktualisieren.
- Zusammenfassungen bearbeiten, Aufgaben auswählen und Verantwortliche zuordnen.
- Titel und eingeladene Personen aus einem passenden Outlook-Termin übernehmen.
  Die Einladung ersetzt keine Anwesenheitsliste.
- Notizen lokal speichern oder ein OneNote-Notizbuch und einen Abschnitt wählen.
  Aufgabenkorrekturen lassen sich mit der angelegten Seite synchronisieren.

## Mit deinem KI-Agenten installieren

**[Community-Installer für Windows x64 herunterladen](https://github.com/data-assessment/DAS-Meeting-Assistant/releases/tag/v0.40.16-community-preview.1).**
Die Testversion 0.40.15 enthält Installer, SHA-256-Prüfsumme und Installationshinweise.
Python, Node.js und Git werden dafür nicht benötigt. Der Installer ist unsigniert;
der externe Installationstest auf einem frischen Windows-PC steht noch aus.
Solange das Repo privat ist, brauchst du zum Download ein GitHub-Konto mit Repo-Zugriff.

Gib einem KI-Agenten mit Zugriff auf deinen Windows-PC diesen Auftrag:

> Installiere https://github.com/data-assessment/DAS-Meeting-Assistant für mich.

Die [Agentenanleitung](agent-install.md) führt ihn durch Installation,
Azure-Einrichtung, Microsoft-Anmeldung, Konfiguration und Funktionstests.
`AGENTS.md` im Hauptverzeichnis verweist ebenfalls darauf. Verwende für den externen
Test den verlinkten Community-Installer. Die Anleitung beschreibt außerdem die
Installation aus dem Quellcode mit einer dauerhaften Startverknüpfung.

Du benötigst Windows x64, eine Azure-Speech-Ressource und ein kompatibles
Textmodell-Deployment in Azure. Für Teams-Präsenz, Outlook und OneNote kommen ein
Microsoft-365-Geschäfts- oder Schulkonto und die jeweiligen Berechtigungen hinzu.
Anmeldung, MFA und notwendige Kostenfreigaben erledigst du selbst; dein Agent
übernimmt die übrigen Schritte, soweit seine Werkzeuge es ermöglichen.

Der Quellcode steht unter Apache-2.0. **Azure-Nutzung wird separat abgerechnet.**
Ein DAS-Service-Abonnement ist für den Desktop-Client nicht erforderlich.
Ein Agent ohne Zugriff auf deinen PC kann die lokale Installation nicht durchführen.

Der Agent muss zuerst prüfen, ob er Befehle **auf deinem Windows-PC** ausführen kann.
Ein sichtbares Terminal allein reicht nicht aus. Fehlt dieser Zugriff, beschreibt
die Anleitung den Wechsel zu einem Agenten mit lokaler Shell oder eine begleitete
manuelle Installation mit Befehlen direkt im Chat, ohne einen erzeugten Skript-Anhang.

## Datenverarbeitung

Meeting Notes verarbeitet Roh-Audio und Transkripttext im Arbeitsspeicher und
überträgt sie an die konfigurierten Azure-Dienste. Abgeleitete Notizen, Aufgaben und
ausgewählte Personen werden gespeichert; OneNote ist ein optionales Ziel.
Es handelt sich nicht um eine offline arbeitende KI.

Der separat wählbare ältere Aufzeichnungsmodus kann Audiodateien und Transkripte
speichern. Die Geräteaufnahme aktiviert selbst keine Aufnahmebenachrichtigung in
Teams. Starte sie mit dem Einverständnis der Beteiligten.

Notizen entstehen in der App-Sprache (Deutsch oder Englisch), die beim Start des
Meetings gilt; jedes Meeting behält diese Sprache. Die getrennte Meeting-Sprache sagt
nur der Spracherkennung, welche Sprache gesprochen wird. Ohne gespeicherte Auswahl
folgt die App-Sprache der Windows-Anzeigesprache.
Weitere Einzelheiten: [Data handling](../README.md#data-handling).

## Über DAS und Mitarbeit

Entwickelt von [Data Assessment Solutions GmbH](https://www.data-assessment.com/)
in Hannover. Wir entwickeln KI-Assistenten für Geschäftsabläufe und mit
[decídalo](https://www.decidalo.com/de/) eine Plattform für Skill-, Profil- und
Ressourcenmanagement. Unternehmensspezifische Integrationen auf der DAS-Website
sind nicht automatisch Bestandteil dieses Desktop-Repos.

Fehler und Vorschläge gehören in die
[GitHub Issues](https://github.com/data-assessment/DAS-Meeting-Assistant/issues).
Bitte Version, Windows-Version und Schritte zum Nachstellen angeben und keine
Zugangsdaten oder echten Gesprächsinhalte veröffentlichen. Für größere Änderungen
zuerst den Ansatz abstimmen; Entwicklung und Tests beschreibt die
[englische README](../README.md#development).

Lizenz: [Apache-2.0](../LICENSE), mit eigenen Bedingungen für
[Drittanbieter-Komponenten](../THIRD_PARTY_NOTICES.md).
