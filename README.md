# TAKT

**TAKT** ist eine vollständig lokale Stoppuhr für
Feuerwehr-Training und Bewerbsläufe. Die Anwendung ist für einen Raspberry Pi 3
mit großem, normalerweise offenem Taster ausgelegt und lässt sich auf einem
Laptop vollständig simulieren.

## Aktueller Stand

Der erste funktionsfähige Stand enthält:

- die Zustände Bereit, Läuft, Gestoppt, Gespeichert und Verwerfen bestätigen,
- genaue Messung mit monotoner Uhr und Speicherung in Millisekunden,
- Zuschläge in 5- oder 10-Sekunden-Schritten, die wieder reduziert werden können,
- lokale, transaktionale Speicherung und tägliche Sicherung,
- heutige Läufe, Bestzeiten und jeden Lauf als eigenen Diagrammpunkt,
- einen fokussierten Vollflächen-Timer während eines laufenden Durchgangs,
- Einstellungen zur Korrektur von Zuschlägen und zum Löschen fehlerhafter Läufe,
- Tastatur-, Maus- und Mock-Taster-Steuerung,
- optionalen sicht- und hörbaren Summer-Mock,
- eine von der Oberfläche getrennte GPIO-Eingabe.

Die Raspberry-Pi-Installation und eine auf echter Hardware geprüfte Autostart-
Konfiguration folgen in einem späteren Meilenstein.

## Schnellstart auf dem Laptop

Voraussetzung ist Python 3.11 oder neuer.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/takt --windowed --mock-gpio --mock-buzzer
```

Alternativ übernimmt `./scripts/launch_dev.sh` Einrichtung und Start.

Der blaue **Mock-Taster** am unteren Rand verhält sich wie der spätere
Mushroom-Taster. Der **Summer-Mock** zeigt bei Start, Stopp, Speichern und
Verwerfen einen Statusimpuls und löst zusätzlich den Laptop-Systemton aus.
Beide Mock-Anzeigen erscheinen nur, wenn die zugehörigen Startargumente gesetzt
sind.

### Startargumente

| Argument | Bedeutung |
|---|---|
| `--windowed` | Startet in einem normalen Fenster statt im Vollbild. |
| `--mock-gpio` | Verwendet und zeigt den blauen Laptop-Mock-Taster. |
| `--mock-buzzer` | Simuliert den Summer sichtbar und über den Systemton. |
| `--database PFAD` | Verwendet für einen isolierten Test eine andere Datendatei. |

## Bedienung

| Taste | Aktion |
|---|---|
| Leertaste | Lauf starten oder stoppen |
| `5` | 5 Sekunden Zuschlag |
| `0` | 10 Sekunden Zuschlag |
| Strg+`5` | 5 Sekunden vom Zuschlag abziehen |
| Strg+`0` | 10 Sekunden vom Zuschlag abziehen |
| Enter | gestoppten Lauf speichern / Verwerfen bestätigen |
| `R` oder Escape | Verwerfen anfordern |
| F11 | Vollbild umschalten |
| Strg+Q | Anwendung beenden |

Nach dem Stoppen verwirft ein Doppeldruck auf den Mock- oder GPIO-Taster
innerhalb von 600 ms den aktuellen Lauf. Ein einzelner Druck tut nichts.
Beim Reduzieren bleibt der Zuschlag immer mindestens bei `+00:00.00`; die
gemessene Ist-Zeit wird dabei nie verändert.

## Laufdaten kuratieren

Über **Einstellungen** können gespeicherte Läufe ausgewählt werden. Der Zuschlag
kann nachträglich in 5- oder 10-Sekunden-Schritten korrigiert werden; die
ursprünglich gemessene Ist-Zeit bleibt dabei unverändert. Fehlerhafte Läufe
können nach einer zusätzlichen Sicherheitsabfrage endgültig gelöscht werden.
Jede Änderung an einem bereits gespeicherten Lauf muss ausdrücklich bestätigt
werden.

## Raspberry Pi sicher herunterfahren

In **Einstellungen → System** steht auf Raspberry-Pi-Hardware die Aktion
**Raspberry Pi herunterfahren** zur Verfügung. Sie verlangt eine Bestätigung,
weist auf einen eventuell ungespeicherten Lauf hin und fordert anschließend ein
geordnetes Herunterfahren über `systemctl poweroff` an. Auf Entwicklungsrechnern
ist die Schaltfläche deaktiviert.

Wenn Raspberry Pi OS die Anfrage wegen fehlender Berechtigung ablehnt, bleibt
TAKT geöffnet und zeigt einen Fehler. Die produktive Installationsroutine muss
die lokale Desktop-Sitzung beziehungsweise deren PolicyKit-Berechtigung für das
Herunterfahren validieren.

## Raspberry-Pi-Taster

Der normalerweise offene Taster wird so angeschlossen:

```text
GPIO17 (BCM) ↔ COM
GND          ↔ NO
```

Die Eingabe ist active-low und verwendet den internen Pull-up-Widerstand.
Pin, Entprellzeit und Doppeldruckfenster stehen in `config.example.toml`.

## Raspberry-Pi-Deployment

Der aktuelle Stand ist noch eine Entwicklungsinstallation. `launch_dev.sh` ist
kein unbeaufsichtigter Raspberry-Pi-Installer; im Repository fehlen derzeit noch
der produktive Installationsablauf und die Autostart-Unit.

Der vorgesehene Deployment-Ablauf ist:

1. TAKT und die benötigten Systempakete auf dem Pi installieren.
2. Eine eigene virtuelle Python-Umgebung für TAKT anlegen.
3. `config.toml` mit GPIO-Taster und optionalem Summer konfigurieren.
4. Daten-, Sicherungs- und Protokollverzeichnisse anlegen.
5. TAKT nach dem Start der grafischen Sitzung automatisch im Vollbild öffnen.
6. GPIO, Entprellung, Doppeldruck und Neustartverhalten auf echter Hardware prüfen.

Vor einem produktiven Einsatz muss insbesondere die Verfügbarkeit von PySide6
auf dem konkret verwendeten Raspberry Pi OS 32-bit Image geprüft werden. Der
nächste Deployment-Meilenstein umfasst `scripts/install_raspberry_pi.sh`, eine
Autostart-Konfiguration und dokumentierte Hardwaretests.

## Konfiguration und Daten

Eine eigene Konfiguration kann unter `~/.config/takt/config.toml` abgelegt
werden. Ohne Datei gelten sichere Standardwerte. Die Laufdaten liegen unter
`~/.local/share/takt/takt.db`, tägliche Sicherungen unter
`~/.local/share/takt/backups/` und rotierende Protokolle unter
`~/.local/state/takt/logs/`.

Für einen isolierten Test lässt sich ein anderer Speicherort angeben:

```bash
.venv/bin/takt --windowed --mock-gpio --mock-buzzer \
  --database /tmp/takt-test.db
```

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Mit installierten Entwicklungsabhängigkeiten:

```bash
.venv/bin/ruff check .
.venv/bin/pytest
```

## Noch offen

- Installation und Autostart auf einem echten Raspberry Pi OS 32-bit prüfen,
- GPIO und optionalen echten Summer in wiederholten Hardware-Zyklen validieren,
- visuelle Screenshot-Tests bei 1280×720 und 1920×1080 ergänzen,
- Single-Instance-Schutz und Anzeige-Wachhaltung ergänzen.
