#!/usr/bin/env bash
# tyrell · instalación y arranque del dump diario (systemd de usuario)
set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$TOOLS_DIR")"
PY="$(command -v python3)"
UNIT_DIR="$HOME/.config/systemd/user"

echo "◆ tyrell  →  $ROOT_DIR"
echo "◆ python  →  $PY"

# --- 1. estructura y primer volcado ----------------------------------------
"$PY" "$TOOLS_DIR/tyrell.py" init
echo "◆ primer volcado de conversaciones…"
"$PY" "$TOOLS_DIR/tyrell.py" dump || echo "  (el volcado falló; revisa los avisos de arriba)"

# --- 2. unidades de systemd --------------------------------------------------
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/tyrell-dump.service" <<EOF
[Unit]
Description=tyrell: volcado diario de conversaciones CLI a Markdown + reindexado
After=local-fs.target

[Service]
Type=oneshot
ExecStart=$PY $TOOLS_DIR/tyrell.py dump --quiet
Environment=PYTHONDONTWRITEBYTECODE=1
Nice=10
IOSchedulingClass=idle
EOF

cat > "$UNIT_DIR/tyrell-dump.timer" <<EOF
[Unit]
Description=tyrell: agenda el volcado diario

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=30min

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now tyrell-dump.timer

echo "◆ timer systemd activo:"
systemctl --user list-timers tyrell-dump.timer --no-pager

# --- 3. primer arranque del servicio (prueba) -------------------------------
echo "◆ arrancando tyrell-dump.service una vez (prueba)…"
systemctl --user stop tyrell-dump.service 2>/dev/null || true
systemctl --user start tyrell-dump.service
systemctl --user status tyrell-dump.service --no-pager | sed -n '1,8p' || true

echo
echo "◆ listo. Comandos útiles:"
echo "   systemctl --user status tyrell-dump.service   # estado del último volcado"
echo "   systemctl --user list-timers                  # ver el timer"
echo "   $PY $TOOLS_DIR/tyrell.py status               # estado del índice"
echo "   $PY $TOOLS_DIR/tyrell.py search 'tu busqueda' --semantic"