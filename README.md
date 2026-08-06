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
- eine von der Oberfläche getrennte GPIO-Eingabe,
- einen lokalen Webserver mit synchroner Bedienung über mehrere Geräte,
- eine responsive React-Browser-Oberfläche mit derselben deutschen Bedienlogik,
- ein optionales Startsignal über AUX oder einen gekoppelten Bluetooth-Lautsprecher
  mit einstellbarer Startverzögerung,
- eine automatisierte Raspberry-Pi-Installation mit `takt.local`, Systemdienst
  und Vollbildstart.

## Raspberry Pi mit einem Skript einrichten

Voraussetzung ist **Raspberry Pi OS Desktop 64-bit**. `uname -m` muss
`aarch64` ausgeben. Das Projekt muss auf dem Pi beispielsweise unter
`/home/msmidt/takt` liegen.

Im Projektverzeichnis genügt:

```bash
./scripts/install_raspberry_pi.sh
```

Alternativ kann der Laptop das Projekt übertragen und anschließend das komplette
Setup auf dem Pi ausführen. Beim ersten Mal gilt üblicherweise:

```bash
./scripts/deploy_to_raspberry_pi.sh msmidt@raspberrypi.local
```

Nach der Einrichtung lautet dieselbe Adresse:

```bash
./scripts/deploy_to_raspberry_pi.sh msmidt@takt.local
```

Damit bleibt für Installation und spätere Updates jeweils ein einziger
Laptop-Befehl. Eventuelle SSH- und `sudo`-Passwortabfragen erscheinen während
dieses einen Ablaufs.

Das Skript installiert alle System- und Python-Pakete, richtet `lgpio`,
Bluetooth-Audio, `takt.service`, die feste lokale Adresse, Desktop-Autologin
und Chromium im Kioskmodus ein. Eine vorhandene Konfiguration oder Laufdaten
werden nicht überschrieben. Am Ende bietet das Skript den erforderlichen
Neustart an.

Danach ist TAKT auf Geräten im selben lokalen Netzwerk erreichbar:

```text
http://takt.local
```

Die IP-Adresse darf sich ändern; `takt.local` wird über mDNS aufgelöst. Falls
ein Gerät `.local` nicht unterstützt, empfiehlt sich zusätzlich eine
DHCP-Reservierung im Router.

Alle Browser und der physische GPIO-Taster steuern denselben autoritativen
Timerprozess. Änderungen erscheinen über WebSockets unmittelbar auf allen
verbundenen Bildschirmen. Da jeder Client im lokalen Netz TAKT bedienen und
auch das Herunterfahren bestätigen kann, gehört TAKT in ein vertrauenswürdiges,
nicht öffentliches WLAN.

Das Installationsskript kann nach einem Programmupdate erneut ausgeführt werden.

## Transportpaket für den Raspberry Pi erstellen

Wenn das Projekt per USB-Stick, Netzlaufwerk oder manuell auf den Raspberry Pi
übertragen werden soll, genügt auf dem Laptop:

```bash
./scripts/package_for_raspberry_pi.sh
```

Das Skript baut zuerst die aktuelle React-Oberfläche und prüft anschließend, ob
Server, Installer und beide Audiodateien vorhanden sind. Danach erstellt es:

```text
dist/takt-raspberry-pi.tar.gz
dist/takt-raspberry-pi.tar.gz.sha256
```

Git-Daten, virtuelle Umgebung, Node-Module, Caches, Screenshots und lokale
Testdaten werden nicht eingepackt. Das Archiv enthält oben einen Ordner `takt`
und kann auf dem Raspberry Pi so installiert werden:

```bash
tar -xzf ~/takt-raspberry-pi.tar.gz -C ~
cd ~/takt
./scripts/install_raspberry_pi.sh
```

Ein eigener Ausgabepfad ist optional:

```bash
./scripts/package_for_raspberry_pi.sh /Pfad/zum/takt-paket.tar.gz
```

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

## Weboberfläche auf dem Laptop testen

Der Webserver lässt sich mit Mock-Taster und Mock-Summer starten:

```bash
./scripts/launch_web_dev.sh
```

Anschließend im Browser öffnen:

```text
http://127.0.0.1:8080
```

Der Entwicklungsschnellstart bindet den Server absichtlich nur an den Laptop
selbst. Auf dem Pi übernimmt das Installationsskript den Netzwerkzugriff.

### Browser-Oberfläche weiterentwickeln

Der React-Quellcode liegt unter `webui/`. Die fertig gebauten, vollständig
offline nutzbaren Dateien werden unter `src/takt/web/static/` abgelegt und
zusammen mit TAKT auf den Pi übertragen. Der Pi benötigt deshalb weder Node.js
noch eine Internetverbindung.

Nach Änderungen an der Browser-Oberfläche genügt:

```bash
./scripts/build_web_ui.sh
```

Das Skript installiert bei Bedarf die festgeschriebenen Pakete, prüft den
React-Code und erzeugt anschließend die produktiven Dateien.

### Startargumente

| Argument | Bedeutung |
|---|---|
| `--windowed` | Startet in einem normalen Fenster statt im Vollbild. |
| `--mock-gpio` | Verwendet und zeigt den blauen Laptop-Mock-Taster. |
| `--mock-buzzer` | Simuliert den Summer sichtbar und über den Systemton. |
| `--database PFAD` | Verwendet für einen isolierten Test eine andere Datendatei. |

`takt-server` unterstützt zusätzlich:

| Argument | Bedeutung |
|---|---|
| `--host ADRESSE` | Bindeadresse; produktiv `0.0.0.0`. |
| `--port PORT` | HTTP-Port; der Installer verwendet Port 80. |

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

## Startsignal über AUX oder Bluetooth

Unter **Einstellungen → Startsignal** stehen drei Ausgänge zur Auswahl:

- **Aus** startet den Timer wie bisher sofort.
- **AUX** verwendet den analogen beziehungsweise aktuell eingerichteten
  Systemausgang. Ein AUX-Kabel muss nur eingesteckt werden; es gibt kein Pairing.
- **Bluetooth** sucht nach Lautsprechern in der Nähe und kann das gewählte Gerät
  koppeln, als vertrauenswürdig speichern und verbinden.

Die feste **Startverzögerung** wird in Millisekunden eingestellt. Ihr
Wertebereich reicht von 0 ms bis zur automatisch erkannten Länge des Clips, beim
mitgelieferten Signal also bis 17.512 ms. Beim ersten Tasterdruck beginnt das
Startsignal. Nach der eingestellten Zeit startet die Messung automatisch,
während der Ton weiterläuft. Der Ton endet entweder mit dem Clip oder sofort,
wenn die laufende Stoppuhr gestoppt wird. Während der Verzögerung werden weitere
Tasterdrücke ignoriert.

Mit **Testton** lässt sich der Ausgang vor dem Lauf prüfen. Ein gespeicherter
Bluetooth-Lautsprecher wird bei Bedarf vor dem Startsignal erneut verbunden.
Raspberry Pi OS Desktop verwendet PipeWire; das Installationsskript richtet die
benötigten Bluetooth-, ALSA- und PulseAudio-Kompatibilitätswerkzeuge ein.

Die unveränderte Quelldatei liegt unter
`src/takt/assets/start_signal_source.mp3`. Für eine zuverlässige Wiedergabe ohne
zusätzlichen MP3-Decoder liefert TAKT außerdem
`src/takt/assets/start_signal.wav` mit demselben Audioinhalt aus.

## Raspberry Pi sicher herunterfahren

In **Einstellungen → System** steht auf Raspberry-Pi-Hardware die Aktion
**Raspberry Pi herunterfahren** zur Verfügung. Sie verlangt eine Bestätigung,
weist auf einen eventuell ungespeicherten Lauf hin und fordert anschließend ein
geordnetes Herunterfahren über `systemctl poweroff` an. Auf Entwicklungsrechnern
ist die Schaltfläche deaktiviert.

Das Installationsskript vergibt dafür ausschließlich die passwortlose
Berechtigung für `systemctl poweroff`; andere Administratorbefehle werden nicht
freigeschaltet. Wenn Raspberry Pi OS die Anfrage ablehnt, bleibt TAKT geöffnet
und zeigt einen Fehler.

## Raspberry-Pi-Taster

Der normalerweise offene Taster wird so angeschlossen:

```text
GPIO17 (BCM) ↔ COM
GND          ↔ NO
```

Die Eingabe ist active-low und verwendet den internen Pull-up-Widerstand.
Pin, Entprellzeit und Doppeldruckfenster stehen in `config.example.toml`.

## Raspberry-Pi-Betrieb prüfen

Der Serverstatus kann bei der Fehlersuche angezeigt werden:

```bash
systemctl status takt.service
```

Die letzten Meldungen stehen im Systemprotokoll:

```bash
journalctl -u takt.service -n 100 --no-pager
```

TAKT verwendet produktiv ausdrücklich `GPIOZERO_PIN_FACTORY=lgpio`. Der
Systemdienst startet nach einem Fehler automatisch neu. Der frühere manuelle
PySide-Autostart wird vom Installationsskript entfernt, damit nur ein Prozess
GPIO und Datenbank kontrolliert.

## Konfiguration und Daten

Eine eigene Konfiguration kann unter `~/.config/takt/config.toml` abgelegt
werden. Ohne Datei gelten sichere Standardwerte. Die Laufdaten liegen unter
`~/.local/share/takt/takt.db`, tägliche Sicherungen unter
`~/.local/share/takt/backups/` und rotierende Protokolle unter
`~/.local/state/takt/logs/`.

Der Webserver kann ebenfalls konfiguriert werden:

```toml
[server]
host = "0.0.0.0"
port = 8080
```

Der installierte Systemdienst überschreibt den Port mit 80, damit
`http://takt.local` ohne Portangabe funktioniert.

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

- Installationsskript und Autostart auf dem konkreten Raspberry Pi wiederholt
  validieren,
- GPIO-Taster in wiederholten Hardware-Zyklen validieren,
- visuelle Screenshot-Tests bei 1280×720 und 1920×1080 ergänzen,
- optionalen Nur-Anzeige-Zugriff für zusätzliche Netzwerkgeräte ergänzen.
