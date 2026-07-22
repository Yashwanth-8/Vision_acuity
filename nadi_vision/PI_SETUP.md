# NadiVision — Raspberry Pi 4 Setup Guide

Fresh clone → working kiosk, step by step.

**Target:** Raspberry Pi 4 (4 GB), Pi OS Bookworm (64-bit), CSI camera, HC-SR04 on BCM 17/27.

---

## 1. System prerequisites

```bash
sudo apt update && sudo apt upgrade -y

# Core system libraries
sudo apt install -y \
  python3-picamera2 \
  python3-numpy \
  python3-pil \
  python3-gpiozero \
  python3-lgpio \
  libcamera-apps \
  git \
  curl \
  build-essential

# Node.js 20 LTS (for frontend)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# uv (fast Python package manager — installs Python 3.11 in an isolated venv)
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env        # add uv to PATH for this session
echo 'source $HOME/.local/bin/env' >> ~/.bashrc
```

---

## 2. Clone the repository

```bash
cd ~
git clone <your-repo-url> nadivision
cd nadivision/NewBuild/nadi_vision
```

---

## 3. Backend — Python 3.11 venv with uv

The app service (MediaPipe, scoring, WebSocket) must run in Python 3.11.  
Picamera2 stays on system Python — it is **not** installed into the venv.

```bash
# Create a Python 3.11 virtual environment managed by uv
uv venv --python 3.11 .venv

# Install all app-service dependencies
uv pip install -r requirements.txt
```

Verify the venv has mediapipe and websockets:

```bash
uv run python -c "import mediapipe, websockets, numpy; print('backend deps OK')"
```

---

## 4. Frontend — Node.js production build

```bash
cd frontend
npm install
npm run build
cd ..
```

---

## 5. Verify camera and GPIO

```bash
# Test CSI camera is visible
rpicam-hello --timeout 2000

# Test HC-SR04 GPIO (BCM 17 = trigger, BCM 27 = echo)
/usr/bin/python3 - <<'EOF'
from gpiozero import DistanceSensor
s = DistanceSensor(echo=27, trigger=17)
print(f"Distance: {s.distance * 100:.1f} cm")
s.close()
EOF
```

If the GPIO pins differ on your enclosure, update these defaults in
`backend/scoring/constants.py` → `UltrasonicWorker` trigger/echo pin args,
or pass them explicitly in `backend/main.py`.

---

## 6. Create the runtime socket directory

The camera service writes its Unix-domain socket to `/run/nadivision/`.
On Pi OS this directory does not exist by default:

```bash
sudo mkdir -p /run/nadivision
sudo chown $USER:$USER /run/nadivision
```

This is only needed for manual/test runs. The systemd units below create it
automatically on every boot via `RuntimeDirectory=`.

---

## 7. Manual test run (three terminals)

Open three terminal sessions (SSH or local).

### Terminal 1 — Camera service (system Python)

```bash
cd ~/nadivision/NewBuild/nadi_vision
/usr/bin/python3 scripts/camera_service.py
```

Expected output: silence (no errors). The MJPEG preview will be available at
`http://<pi-ip>:8766/preview` in a browser.

### Terminal 2 — App service (uv Python 3.11)

```bash
cd ~/nadivision/NewBuild/nadi_vision
uv run python -m backend.main
```

Expected output: `INFO:websockets.server:server listening on 0.0.0.0:8765`

### Terminal 3 — Frontend

```bash
cd ~/nadivision/NewBuild/nadi_vision/frontend
node .next/standalone/server.js
# OR:
npm start
```

Open `http://localhost:3000` in Chromium (kiosk browser).

---

## 8. Systemd services (auto-start on boot)

Create the two service unit files so the device starts automatically.

### Camera service unit

```bash
sudo tee /etc/systemd/system/nadivision-camera.service > /dev/null <<'EOF'
[Unit]
Description=NadiVision Camera Service
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/nadivision/NewBuild/nadi_vision
ExecStart=/usr/bin/python3 scripts/camera_service.py
Restart=always
RestartSec=3
RuntimeDirectory=nadivision
RuntimeDirectoryMode=0755
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

### App service unit

```bash
sudo tee /etc/systemd/system/nadivision-app.service > /dev/null <<'EOF'
[Unit]
Description=NadiVision App Service
After=nadivision-camera.service
Requires=nadivision-camera.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/nadivision/NewBuild/nadi_vision
ExecStart=/home/pi/.local/bin/uv run python -m backend.main
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

### Frontend service unit

```bash
sudo tee /etc/systemd/system/nadivision-frontend.service > /dev/null <<'EOF'
[Unit]
Description=NadiVision Frontend
After=nadivision-app.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/nadivision/NewBuild/nadi_vision/frontend
ExecStart=/usr/bin/node .next/standalone/server.js
Restart=always
RestartSec=5
Environment=PORT=3000
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

### Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable nadivision-camera nadivision-app nadivision-frontend
sudo systemctl start  nadivision-camera nadivision-app nadivision-frontend

# Check status
sudo systemctl status nadivision-camera
sudo systemctl status nadivision-app
sudo systemctl status nadivision-frontend
```

### View logs

```bash
journalctl -u nadivision-camera -f    # camera service live log
journalctl -u nadivision-app -f       # app service live log
journalctl -u nadivision-frontend -f  # frontend live log
```

---

## 9. Kiosk browser (auto-launch Chromium fullscreen)

```bash
sudo tee /etc/systemd/system/nadivision-kiosk.service > /dev/null <<'EOF'
[Unit]
Description=NadiVision Kiosk Browser
After=nadivision-frontend.service graphical.target
Wants=graphical.target

[Service]
Type=simple
User=pi
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/pi/.Xauthority
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/chromium-browser \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --no-first-run \
  http://localhost:3000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable nadivision-kiosk
sudo systemctl start  nadivision-kiosk
```

---

## 10. Physical configuration to verify before clinical use

Edit `backend/scoring/constants.py` to match your physical enclosure:

```python
# Measure these on the actual device with a ruler
SENSOR_TO_SCREEN_OFFSET_M = 0.0    # signed offset: sensor face → screen plane (m)
SENSOR_TO_EYE_OFFSET_M    = 0.013  # signed offset: screen plane → patient eye plane (m)
```

Also confirm the display's mm/px value. The auto-detect PPI heuristic on the
landing screen is prototype-mode only. For a locked clinical device, measure
the actual screen with calipers and hard-code the value in the device manifest.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Camera service crashes immediately | CSI cable not seated / camera not enabled | Run `sudo raspi-config` → Interface Options → Camera → Enable; verify with `rpicam-hello --timeout 2000` |
| `SharedMemory` attach fails in app service | Camera service not started yet | Start `nadivision-camera` before `nadivision-app` |
| `/run/nadivision` permission denied | Directory not created | `sudo mkdir -p /run/nadivision && sudo chown pi:pi /run/nadivision` |
| `mediapipe` import error | Wrong Python interpreter | Confirm `uv run python -c "import mediapipe"` works |
| WebSocket not connecting from browser | App service not running or wrong port | Check `sudo systemctl status nadivision-app` |
| Preview image not loading in browser | Camera service MJPEG server not up | Visit `http://localhost:8766/preview` directly |
| HC-SR04 reads 0 or error | Wrong GPIO pins | Check BCM 17 (trig) / 27 (echo) wiring; update pins in `main.py` if different |
| `gpiozero` not found | Missing system package | `sudo apt install -y python3-gpiozero python3-lgpio` |
