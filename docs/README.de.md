# DAS Meeting Assistant

**Aus Teams-Gesprächen werden Notizen und Aufgaben.**

Der DAS Meeting Assistant ist eine quelloffene Windows-Anwendung für
Live-Transkription, Zusammenfassungen und bearbeitbare Aufgaben. Du nutzt deine
eigenen Azure-Ressourcen und speicherst die Notizen auf deinem PC oder in OneNote.

[English](../README.md) · [Installation für KI-Agenten](agent-install.md) ·
[Data Assessment Solutions](https://www.data-assessment.com/)

![Meeting-Notizen mit Zusammenfassung und bearbeitbaren Aufgaben](assets/meeting-notes.png)

*Echte Anwendungsoberfläche mit Beispieldaten. Oberfläche und Zusammenfassungen
sind derzeit überwiegend deutschsprachig.*

## Was die Anwendung kann

- Mikrofon und Gesprächswiedergabe über Azure Speech transkribieren und während
  des Meetings Notizen und Aufgabenvorschläge aktualisieren.
- Zusammenfassungen bearbeiten, Aufgaben auswählen und Verantwortliche zuordnen.
- Titel und eingeladene Personen aus einem passenden Outlook-Termin übernehmen.
  Die Einladung ersetzt keine Anwesenheitsliste.
- Notizen lokal speichern oder ein OneNote-Notizbuch und einen Abschnitt wählen.
  Aufgabenkorrekturen lassen sich mit der angelegten Seite synchronisieren.

## Mit deinem KI-Agenten installieren

Gib einem KI-Agenten mit Zugriff auf deinen Windows-PC diesen Auftrag:

> Installiere https://github.com/data-assessment/DAS-Meeting-Assistant für mich.

Die [Agentenanleitung](agent-install.md) führt ihn durch Installation,
Azure-Einrichtung, Microsoft-Anmeldung, Konfiguration und Funktionstests.
`AGENTS.md` im Hauptverzeichnis verweist ebenfalls darauf. Falls noch kein
Community-Installer veröffentlicht ist, beschreibt die Anleitung die Installation
aus dem Quellcode mit einer dauerhaften Startverknüpfung.

Du benötigst Windows x64, eine Azure-Speech-Ressource und ein kompatibles
Textmodell-Deployment in Azure. Für Teams-Präsenz, Outlook und OneNote kommen ein
Microsoft-365-Geschäfts- oder Schulkonto und die jeweiligen Berechtigungen hinzu.
Anmeldung, MFA und notwendige Kostenfreigaben erledigst du selbst; dein Agent
übernimmt die übrigen Schritte, soweit seine Werkzeuge es ermöglichen.

Der Quellcode steht unter Apache-2.0. **Azure-Nutzung wird separat abgerechnet.**
Ein DAS-Service-Abonnement ist für den Desktop-Client nicht erforderlich.
Ein Agent ohne Zugriff auf deinen PC kann die lokale Installation nicht durchführen.

## Datenverarbeitung

Meeting Notes verarbeitet Roh-Audio und Transkripttext im Arbeitsspeicher und
überträgt sie an die konfigurierten Azure-Dienste. Abgeleitete Notizen, Aufgaben und
ausgewählte Personen werden gespeichert; OneNote ist ein optionales Ziel.
Es handelt sich nicht um eine offline arbeitende KI.

Der separat wählbare ältere Aufzeichnungsmodus kann Audiodateien und Transkripte
speichern. Die Geräteaufnahme aktiviert selbst keine Aufnahmebenachrichtigung in
Teams. Starte sie mit dem Einverständnis der Beteiligten.

Der aktuelle Zusammenfassungs-Prompt erzeugt deutsche Notizen. Eine andere
Erkennungssprache ändert nicht automatisch die Sprache der Zusammenfassung.
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
