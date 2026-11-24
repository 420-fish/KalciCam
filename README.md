# KalciCam

A lightweight, Bandicam-style screen recorder for Linux, with a Windows test build.

- Records full screen, a region, or a single window, at 60 fps by default
- Desktop sound and microphone, each with its own volume
- Webcam bubble, REC timer badge, click ripples
- Hotkeys: **F12** start/stop, **Shift+F12** pause, **F11** screenshot, **F10** save replay
- Replay buffer: save the last 30 seconds after something cool happens
- Recordings browser with thumbnails, tray icon, five picture themes
- Uses the graphics card to encode (NVIDIA NVENC, AMD/Intel VA-API), so the CPU stays almost idle
- Crash-safe: records to MKV, converts to MP4 when you stop, and recovers the video if the computer crashes

---

## Installing on Linux

1. Download `KalciCam-x86_64.AppImage`.
2. Move it somewhere permanent, for example a folder called `Applications` in your home folder.
3. Make it runnable: right-click it, open **Properties**, and turn on **Allow executing as program**.
   Or in a terminal:
   ```
   chmod +x KalciCam-x86_64.AppImage
   ```
4. Double-click it.

The setup wizard opens the first time and walks you through:

- picking a theme
- checking that FFmpeg and the other tools are installed (it can install them for you after asking for your password)
- testing your graphics card to find the fastest encoder
- choosing where videos are saved
- setting up hotkeys
- adding KalciCam to your app menu

Run it again any time from **About > Run setup again**.

### Wayland (Ubuntu, Fedora, newer KDE)

- When you press REC the first time, the system asks which screen to share. Tick "remember", and it won't ask again.
- Wayland doesn't let apps listen for keys in the background, so the hotkeys become desktop keyboard shortcuts. On GNOME the wizard sets them up for you. On KDE and others, bind keys to these commands in your system's shortcut settings:
  ```
  /path/to/KalciCam-x86_64.AppImage --toggle
  /path/to/KalciCam-x86_64.AppImage --pause
  /path/to/KalciCam-x86_64.AppImage --screenshot
  /path/to/KalciCam-x86_64.AppImage --replay
  ```
- Click ripples only work in an X11 session.

### If something goes wrong

There's a log at `~/.cache/kalcicam/kalcicam.log`. It shows the exact capture command and any error.

---

## Windows test build

Unzip `KalciCam-Windows-Test.zip` and run `KalciCam\KalciCam.exe`. It needs FFmpeg installed:
```
winget install Gyan.FFmpeg
```
Desktop sound on Windows needs a loopback device such as "Stereo Mix". The Linux version records desktop sound without any extra setup.

---

## Building

**Windows test zip** (on Windows, from this folder):
```
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```
Add `-BundleFfmpeg` to put ffmpeg.exe inside the folder, which adds about 200 MB.

**Linux AppImage** (on any x86_64 Linux, Ubuntu 22.04 or older is best):
```
sudo apt install python3-venv libxcb-cursor0 curl
bash packaging/linux/build_appimage.sh
```

**Without a Linux PC:** push this folder to a GitHub repository, open **Actions > Build KalciCam > Run workflow**, and download both builds from the finished run.

**Run from source:**
```
pip install -r requirements.txt
python main.py
```

Fonts: Bungee and Barlow Condensed, under the SIL Open Font License (license files are in `kalcicam/assets/fonts`).
