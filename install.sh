#!/bin/sh
# Standalone-Build nach ~/.local/share/cadence + Eintrag im Startmenü. Erneut ausführen = Update (cadence.cfg, eigene Strecken und Workouts bleiben).
# Python-Skripte, routes/, models/, workouts/ liegen neben der ausführbaren Datei (main.gd::data), uv muss installiert sein.
set -e
cd "$(dirname "$0")"
D="${XDG_DATA_HOME:-$HOME/.local/share}"
APP="$D/cadence"
mkdir -p "$APP/models" "$D/applications"
# erst daneben exportieren, dann umbenennen: geht auch, während das Spiel läuft (es behält die alte Datei bis zum Neustart)
godot --headless --path . --export-release Linux "$APP/cadence.new.x86_64"
mv -f "$APP/cadence.new.x86_64" "$APP/cadence.x86_64"
cp ./*.py "$APP/"
cp models/*.glb models/leaves.png "$APP/models/"
cp -r --reflink=auto workouts routes "$APP/"
[ -f "$APP/cadence.cfg" ] || cp cadence.cfg "$APP/"
cat > "$D/applications/cadence.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Cadence
Comment=Indoor-Radsimulator
Exec=$APP/cadence.x86_64
Path=$APP
Icon=applications-games
Categories=Game;Sports;
Terminal=false
DESKTOP
update-desktop-database "$D/applications" 2>/dev/null || true
echo "installiert: $APP/cadence.x86_64"
